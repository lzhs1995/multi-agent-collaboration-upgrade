'use strict';
const {test} = require('node:test');
const assert = require('node:assert/strict');
const {installFetchHooks, summaryParser} = require('./upgrade_claude_response_observer.cjs');
const event = v => 'data: ' + JSON.stringify(v) + '\n\n';
const payload = event({type:'message_start',message:{content:[]}}) +
  event({type:'content_block_delta',delta:{type:'text_delta',text:'private-visible-text'}}) +
  event({type:'message_stop'});
const response = () => new Response(payload,{headers:{'content-type':'text/event-stream'}});

function runtimeWithSession(fetch) {
  const bound = new WeakMap();
  class Session {
    constructor(...args) { this.args=args;bound.set(this,fetch.bind(this)); }
    close() { assert.ok(bound.has(this));this.closed=true; }
  }
  Object.defineProperty(Session.prototype,'fetch',{get(){return bound.get(this);},enumerable:true,configurable:false});
  const runtime={fetch(){throw new Error('global transport must not be used');},Bun:{}};
  Object.defineProperty(runtime.Bun,'FetchSession',{value:Session,writable:true,configurable:false});
  return {runtime,Session};
}

test('303-shaped nonconfigurable getter uses real instances and retains native bound function behavior', async () => {
  const pending=Promise.resolve(response()), signal=new AbortController().signal;
  const init={signal,body:'PRIVATE_PROMPT'},url='https://example.invalid/v1/messages';
  let calls=0,receiver;
  const {runtime,Session}=runtimeWithSession(function(input,options){calls++;receiver=this;assert.equal(input,url);assert.equal(options,init);return pending;});
  const records=[],hooks=installFetchHooks(runtime,v=>records.push(v),{});
  try {
    assert.equal(hooks.coverage.bun_fetch_session.status,'ARMED_NEW_INSTANCES');
    assert.equal(hooks.coverage.bun_fetch_session.fetch_descriptor.configurable,false);
    const s=new runtime.Bun.FetchSession({tls:'sentinel'});
    assert.ok(s instanceof Session);assert.deepEqual(s.args,[{tls:'sentinel'}]);
    const detached=s.fetch;assert.equal(detached,s.fetch);
    assert.equal(detached(url,init),pending);assert.equal(receiver,s);
    const result=await pending;assert.equal(await result.text(),payload);s.close();assert.equal(s.closed,true);
    assert.equal(calls,1);assert.equal(records.filter(x=>x.phase==='REQUEST').length,1);
    assert.deepEqual(records.at(-1).intercepted_paths,['bun_fetch_session']);
    assert.equal(records.at(-1).request_class,'UNKNOWN');
    assert.equal(records.at(-1).content_summary.non_whitespace_text_observed,true);
    assert.ok(!JSON.stringify(records).includes('private-visible-text'));
    assert.ok(!JSON.stringify(records).includes('PRIVATE_PROMPT'));
    assert.deepEqual(hooks.coverage.actual_paths,['bun_fetch_session']);
  } finally {hooks.restore();}
  assert.equal(runtime.Bun.FetchSession,Session);
});

test('both synchronous and asynchronous nested transports keep one request and one byte observation', async () => {
  for(const asynchronous of [false,true]) {
    let session;
    const {runtime}=runtimeWithSession(()=>Promise.resolve(response()));
    runtime.fetch=asynchronous ? async(...args)=>{await Promise.resolve();return session.fetch(...args);} : (...args)=>session.fetch(...args);
    const records=[],hooks=installFetchHooks(runtime,v=>records.push(v),{});
    try {
      session=new runtime.Bun.FetchSession();
      const r=await runtime.fetch('https://example.invalid/v1/messages');
      const parts=[];for await(const part of r.body)parts.push(Buffer.from(part));
      assert.equal(Buffer.concat(parts).toString(),payload);
      assert.equal(records.filter(x=>x.phase==='REQUEST').length,1);
      assert.deepEqual(records.at(-1).intercepted_paths,['global_fetch','bun_fetch_session']);
      assert.equal(records.at(-1).bytes_seen,Buffer.byteLength(payload));
      assert.equal(records.at(-1).message_stop,1);
    } finally {hooks.restore();}
  }
});

test('independent nested requests are not merged by inherited asynchronous scope', async () => {
  for (const difference of ['url', 'init', 'repeated-identical']) {
    const url='https://example.invalid/v1/messages',init={body:'PRIVATE_PROMPT'};
    let session,calls=0;
    const {runtime}=runtimeWithSession(()=>{calls++;return Promise.resolve(response());});
    runtime.fetch=async(input,options)=>{
      const first=await session.fetch(input,options);
      const second=await session.fetch(difference==='url'?input+'?separate=2':input,
        difference==='init'?{...options}:options);
      assert.equal(await second.text(),payload);
      return first;
    };
    const records=[],hooks=installFetchHooks(runtime,v=>records.push(v),{});
    try {
      session=new runtime.Bun.FetchSession();
      assert.equal(await(await runtime.fetch(url,init)).text(),payload);
      assert.equal(calls,2);
      assert.equal(records.filter(r=>r.phase==='REQUEST').length,2,difference);
      const done=records.filter(r=>r.stream_eof);
      assert.equal(done.length,2,difference);
      assert.equal(new Set(done.map(r=>r.request_sequence)).size,2);
      assert.ok(done.every(r=>r.message_stop===1 && r.bytes_seen===Buffer.byteLength(payload)));
    } finally {hooks.restore();}
  }
});

test('a delegated response consumed inside its wrapper is explicitly unobserved, not unread', async () => {
  let session;
  const {runtime}=runtimeWithSession(()=>Promise.resolve(response()));
  runtime.fetch=async(...args)=>{const result=await session.fetch(...args);await result.text();return result;};
  const records=[],hooks=installFetchHooks(runtime,v=>records.push(v),{});
  try {
    session=new runtime.Bun.FetchSession();
    const result=await runtime.fetch('https://example.invalid/v1/messages');
    assert.equal(result.bodyUsed,true);
    assert.equal(records.filter(r=>r.phase==='REQUEST').length,1);
    const last=records.at(-1);assert.equal(last.consumption,'PRECONSUMED_UNOBSERVED');
    assert.equal(last.bytes_seen,0);assert.equal(last.stream_eof,false);
    assert.equal(last.content_summary.non_whitespace_text_observed,false);
  } finally {hooks.restore();}
});

test('settled descendants and same-transport nested calls retain independent records', async () => {
  const url='https://example.invalid/v1/messages';
  let session,later,release;
  const gate=new Promise(resolve=>{release=resolve;});
  const {runtime}=runtimeWithSession(()=>Promise.resolve(response()));
  runtime.fetch=()=>{
    later=gate.then(async()=>assert.equal(await(await session.fetch(url)).text(),payload));
    return Promise.resolve(response());
  };
  const records=[],hooks=installFetchHooks(runtime,v=>records.push(v),{});
  try {
    session=new runtime.Bun.FetchSession();
    assert.equal(await(await runtime.fetch(url)).text(),payload);release();await later;
    assert.equal(records.filter(r=>r.phase==='REQUEST').length,2);
    assert.equal(records.filter(r=>r.stream_eof).length,2);
  } finally {hooks.restore();}
  let nested=false;const recursive={fetch:async()=>{
    if(!nested){nested=true;assert.equal(await(await recursive.fetch(url)).text(),payload);}
    return response();
  }};
  const ownRecords=[],ownHooks=installFetchHooks(recursive,v=>ownRecords.push(v),{});
  try {
    assert.equal(await(await recursive.fetch(url)).text(),payload);
    assert.equal(ownRecords.filter(r=>r.phase==='REQUEST').length,2);
    assert.equal(ownRecords.filter(r=>r.stream_eof).length,2);
  } finally {ownHooks.restore();}
});

test('concurrent session and global calls have independent sequence numbers and abort identity', async () => {
  const pending=new Map(), controllers=Array.from({length:64},()=>new AbortController());
  const {runtime}=runtimeWithSession(function(url,init){const i=Number(new URL(url).searchParams.get('i'));assert.equal(init.signal,controllers[i].signal);return new Promise(resolve=>pending.set(i,resolve));});
  runtime.fetch=function(url,init){const i=Number(new URL(url).searchParams.get('i'));assert.equal(init.signal,controllers[i].signal);return new Promise(resolve=>pending.set(i,resolve));};
  const records=[],hooks=installFetchHooks(runtime,v=>records.push(v),{});
  try {
    const s=new runtime.Bun.FetchSession();
    const work=controllers.map((c,i)=>(i%2?s.fetch:runtime.fetch)(`https://example.invalid/v1/messages?i=${i}`,{signal:c.signal}).then(r=>r.text()));
    for(let i=63;i>=0;i--)pending.get(i)(response());
    assert.deepEqual(await Promise.all(work),Array(64).fill(payload));
    const requests=records.filter(x=>x.phase==='REQUEST');assert.equal(requests.length,64);
    assert.equal(new Set(requests.map(x=>x.request_sequence)).size,64);
    const final=records.filter(x=>x.stream_eof);assert.equal(final.length,64);
    assert.ok(final.every(x=>x.message_stop===1));
  } finally {hooks.restore();}
});

test('constructor errors and unpatchable instances preserve transport without claiming coverage', async () => {
  const fail=new Error('constructor sentinel');
  const {runtime,Session}=runtimeWithSession(()=>Promise.resolve(response()));
  class Frozen extends Session {constructor(){super();Object.preventExtensions(this);}}
  Object.defineProperty(Frozen.prototype,'fetch',Object.getOwnPropertyDescriptor(Session.prototype,'fetch'));
  runtime.Bun.FetchSession=Frozen;
  const records=[],hooks=installFetchHooks(runtime,v=>records.push(v),{});
  try {
    const s=new runtime.Bun.FetchSession();assert.equal(await(await s.fetch('https://example.invalid/v1/messages')).text(),payload);
    assert.equal(hooks.coverage.bun_fetch_session.instance_installation,'PARTIAL_OR_FAILED');
    assert.equal(records.length,0);assert.deepEqual(hooks.coverage.actual_paths,[]);
  } finally {hooks.restore();}
  class Throwing extends Session {constructor(){throw fail;}}
  Object.defineProperty(Throwing.prototype,'fetch',Object.getOwnPropertyDescriptor(Session.prototype,'fetch'));
  runtime.Bun.FetchSession=Throwing;const other=installFetchHooks(runtime,()=>{},{});
  try {assert.throws(()=>new runtime.Bun.FetchSession(),e=>e===fail);}finally{other.restore();}
});

test('unwritable constructors are explicitly unsupported, not replaced by another transport', () => {
  const {runtime,Session}=runtimeWithSession(()=>Promise.resolve(response()));
  Object.defineProperty(runtime.Bun,'FetchSession',{writable:false});
  const hooks=installFetchHooks(runtime,()=>{},{});
  try {assert.equal(runtime.Bun.FetchSession,Session);assert.equal(hooks.coverage.bun_fetch_session.status,'UNSUPPORTED_DESCRIPTOR');}
  finally {hooks.restore();}
});

test('preexisting sessions stay uncovered and their methods remain unchanged', async () => {
  const {runtime,Session}=runtimeWithSession(()=>Promise.resolve(response()));
  const s=new Session(),original=s.fetch,records=[],hooks=installFetchHooks(runtime,v=>records.push(v),{});
  try {assert.equal(s.fetch,original);await(await s.fetch('https://example.invalid/v1/messages')).text();assert.equal(records.length,0);assert.equal(hooks.coverage.preexisting_sessions,'NOT_COVERED');}
  finally {hooks.restore();}
});

test('visible summary excludes thinking/tool input and retains gateway rate_limit_exceeded', () => {
  const snapshots=[],p=summaryParser(v=>snapshots.push(v));p.contentType('sse');
  p.feed(Buffer.from(event({type:'content_block_start',content_block:{type:'thinking',thinking:'SECRET_THOUGHT'}})+
    event({type:'content_block_start',content_block:{type:'tool_use',input:{text:'SECRET_INPUT'}}})+
    event({type:'content_block_delta',delta:{type:'thinking_delta',text:'MUST_NOT_COUNT'}})+
    event({type:'content_block_delta',delta:{type:'text_delta',text:' \n\t'}})+
    event({type:'error',error:{type:'rate_limit_exceeded',message:'SECRET_ERROR'}})));
  p.finish('EOF');const s=snapshots.at(-1);
  assert.deepEqual(s.errors,[{type:'rate_limit_exceeded',code:null}]);
  assert.equal(s.content_summary.non_whitespace_text_observed,false);
  assert.equal(s.content_summary.text_code_units,3);assert.equal(s.content_summary.tool_use_block_events,1);
  assert.ok(!JSON.stringify(snapshots).includes('SECRET'));
});

test('JSON visible-content summary is bounded and false does not invent unread completion', () => {
  const records=[],p=summaryParser(v=>records.push(v),{maxBytes:12});p.emit();
  assert.equal(records.at(-1).consumption,'NOT_READ');assert.equal(records.at(-1).content_summary.non_whitespace_text_observed,false);
  p.json({type:'message',content:[{type:'text',text:'s'.repeat(1000)}]});
  assert.equal(records.at(-1).content_summary.text_code_units,12);assert.equal(records.at(-1).truncated,true);
  assert.equal(records.at(-1).consumed_prefix_sha256,null);
});
