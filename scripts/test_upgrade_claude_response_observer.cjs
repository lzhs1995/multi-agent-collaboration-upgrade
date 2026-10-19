'use strict';
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const crypto = require('node:crypto');
const http = require('node:http');
const {summaryParser, observeResponse, observeFetch, install} = require('./upgrade_claude_response_observer.cjs');
const bytes = text => new TextEncoder().encode(text);
const event = v => 'data: ' + JSON.stringify(v) + '\n\n';
const sample = event({type: 'message_start', message: {usage: {input_tokens: 3}}}) +
  event({type: 'content_block_delta', delta: {text: '中文秘密'}}) +
  event({type: 'message_delta', usage: {output_tokens: 7}}) + event({type: 'message_stop'});
const sha = value => crypto.createHash('sha256').update(value).digest('hex');
const tick = () => new Promise(resolve => setImmediate(resolve));
function collect(type='text/event-stream', content=sample, options) {
  const records = [];
  const response = new Response(content, {headers: {'content-type': type}});
  observeResponse(response, v => records.push(v), options);
  return {response, records};
}
async function consume(response) {
  const reader = response.body.getReader(); const chunks = [];
  for (;;) { const {value, done} = await reader.read(); if (done) break; chunks.push(Buffer.from(value)); }
  return Buffer.concat(chunks);
}

test('SSE bytes and promise identities survive observation, no secret body is logged', async () => {
  const records = [];
  const response = new Response(sample, {headers: {'content-type': 'text/event-stream'}});
  const pending = Promise.resolve(response); const context = {};
  const input = 'https://example.invalid/v1/messages'; const init = {body:'DO_NOT_LOG_PROMPT',headers:{'x-api-key':'DO_NOT_LOG_KEY'}};
  const fn = observeFetch(function(i, options) {assert.equal(this,context);assert.equal(i,input);assert.equal(options,init);return pending;}, x=>records.push(x));
  assert.equal(fn.call(context,input,init), pending);
  assert.equal(await pending,response);
  assert.deepEqual(await consume(response),Buffer.from(sample));
  const last=records.at(-1);
  assert.equal(last.stream_eof,true);assert.equal(last.message_stop,1);
  assert.equal(last.consumed_prefix_sha256,sha(sample));assert.deepEqual(last.usage,{input_tokens:3,output_tokens:7});
  for(const secret of ['中文秘密','DO_NOT_LOG_PROMPT','DO_NOT_LOG_KEY']) assert.ok(!JSON.stringify(records).includes(secret));
});

test('fragmented CRLF, CR, multiline data and split UTF8 parse exactly once', () => {
  const records=[];const p=summaryParser(x=>records.push(x));p.contentType('sse');
  const text=': comment\r\ndata: {"type":\r\ndata: "message_start", "message":{"usage":{"input_tokens":2}}}\r\n\r\n'+
    'data: {"type":"content_block_delta","delta":{"text":"中"}}\r\r'+
    'data: {"type":"message_stop"}\n\n';
  for(const b of bytes(text)) p.feed(new Uint8Array([b]));p.finish('EOF');
  const v=records.at(-1);assert.equal(v.message_start,1);assert.equal(v.message_stop,1);assert.equal(v.malformed_events,0);
  assert.equal(v.trailing_frame,false);assert.equal(v.consumed_prefix_sha256,sha(text));
});

test('HTTP 200 ping then SSE provider error is retained without claiming message completion', async () => {
  const {response,records}=collect('text/event-stream',event({type:'ping'})+event({type:'error',error:{type:'rate_limit_error',message:'secret'}}));
  await consume(response);const v=records.at(-1);assert.equal(v.message_stop,0);assert.equal(v.stream_eof,true);
  assert.deepEqual(v.errors,[{type:'rate_limit_error',code:null}]);
});

test('JSON 429 preserves raw reader evidence when runtime exposes it', async () => {
  const records=[];const body={type:'error',error:{type:'rate_limit_error',message:'secret'}};
  const response=new Response(JSON.stringify(body),{status:429,headers:{'content-type':'application/json'}});
  const pending=Promise.resolve(response);const f=observeFetch(()=>pending,x=>records.push(x));
  await f('https://example.invalid/v1/messages');assert.deepEqual(await response.json(),body);
  const v=records.at(-1);assert.equal(v.http_status,429);assert.ok(['EOF','JSON_CONSUMED'].includes(v.consumption));
  if(v.observation_mode==='raw_bytes') assert.equal(v.consumed_prefix_sha256,sha(JSON.stringify(body)));
  else {assert.equal(v.observation_mode,'parsed_json');assert.equal(v.consumed_prefix_sha256,null);assert.equal(v.bytes_seen,0);} assert.equal(v.errors[0].type,'rate_limit_error');
});

test('read preserves exact original promise and synchronous/asynchronous failures', async () => {
  const rejection=new Error('sentinel');const promise=Promise.reject(rejection);promise.catch(()=>{});
  const records=[];
  const reader={read(){return promise;}};
  const response={status:200,headers:new Headers(),body:{getReader(){return reader;}}};
  observeResponse(response,x=>records.push(x));assert.equal(response.body.getReader(),reader);
  assert.equal(reader.read(),promise);await assert.rejects(promise,e=>e===rejection);await tick();
  assert.equal(records.at(-1).consumption,'READ_ERROR');
  const bad=observeFetch(()=>{throw rejection;},()=>{});assert.throws(()=>bad('https://e.test/messages'),e=>e===rejection);
});

test('unread response remains NOT_READ and observer does not trigger pulls', async () => {
  let pulls=0;const body=new ReadableStream({pull(){pulls++;}},{highWaterMark:0});
  const response=new Response(body);const records=[];observeResponse(response,x=>records.push(x));
  await tick();assert.equal(pulls,0);assert.equal(records.at(-1).consumption,'NOT_READ');
  await body.cancel();
});

test('cancel forwards the same reason and never claims EOF', async () => {
  let reason;const body=new ReadableStream({cancel(x){reason=x;}},{highWaterMark:0});
  const records=[];const response=new Response(body);observeResponse(response,x=>records.push(x));
  const reader=response.body.getReader();const why={why:'stop'};await reader.cancel(why);
  assert.equal(reason,why);assert.equal(records.at(-1).consumption,'CANCELLED');assert.equal(records.at(-1).stream_eof,false);
});

test('async iterator consumption does not change bytes or double count', async () => {
  const {response,records}=collect();const data=[];for await(const chunk of response.body)data.push(Buffer.from(chunk));
  assert.equal(Buffer.concat(data).toString(),sample);assert.equal(records.at(-1).bytes_seen,Buffer.byteLength(sample));
  assert.equal(records.at(-1).message_stop,1);
});

test('text and arrayBuffer keep caller results and identify decoded text', async () => {
  for(const name of ['text','arrayBuffer']) {
    const {response,records}=collect();const value=await response[name]();
    assert.equal(name==='text'?value:Buffer.from(value).toString(),sample);
    assert.equal(records.at(-1).message_stop,1);
    assert.ok((name==='text'?['decoded_text','raw_bytes']:['raw_bytes']).includes(records.at(-1).observation_mode));
  }
});

test('oversized bodies and frames are bounded and explicitly incomplete', async () => {
  const {response,records}=collect('text/event-stream',sample,{maxBytes:32,maxFrame:16});await consume(response);
  const v=records.at(-1);assert.equal(v.truncated,true);assert.ok(v.inspected_bytes<=32);assert.equal(v.message_stop,0);
  const p=summaryParser(()=>{},{maxBytes:128,maxFrame:8});p.contentType('sse');p.feed(bytes('data: '+ 'x'.repeat(80)));p.finish('EOF');
  assert.equal(p.state.truncated,true);
});

test('EOF inside a frame and invalid UTF8 cannot produce message_stop', () => {
  for(const body of [bytes('data: {"type":"message_stop"}'),new Uint8Array([255])]) {
    const r=[];const p=summaryParser(v=>r.push(v));p.contentType('sse');p.feed(body);p.finish('EOF');
    assert.equal(r.at(-1).message_stop,0);assert.ok(r.at(-1).trailing_frame || r.at(-1).malformed_events);
  }
});

test('telemetry failures do not alter successful fetch or stream results', async () => {
  const pending=Promise.resolve(new Response(sample));const original=pending;
  const f=observeFetch(()=>pending,()=>{throw new Error('disk full');});
  assert.equal(f('https://e.test/messages'),original);assert.equal(await(await pending).text(),sample);
});

test('only explicitly enrolled root PID loads, activation includes immutable source hash', async () => {
  const tmp=fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(),'upgrade-observer-')));fs.chmodSync(tmp,0o700);
  const original=globalThis.fetch;
  try {
    assert.equal(install({UPGRADE_RESPONSE_OBSERVATIONS_DIR:tmp,UPGRADE_RESPONSE_OBSERVER_ROOT_PID:String(process.pid+1)}),false);
    assert.equal(fs.readdirSync(tmp).length,0);
    assert.equal(install({UPGRADE_RESPONSE_OBSERVATIONS_DIR:tmp,UPGRADE_RESPONSE_OBSERVER_ROOT_PID:String(process.pid)}),true);
    const files=fs.readdirSync(tmp);assert.equal(files.length,1);
    const record=JSON.parse(fs.readFileSync(path.join(tmp,files[0])));
    assert.equal(record.phase,'LOADED');assert.equal(record.pid,process.pid);
    assert.equal(record.observer_sha256,sha(fs.readFileSync(require.resolve('./upgrade_claude_response_observer.cjs'))));
    assert.equal(fs.statSync(path.join(tmp,files[0])).mode&0o777,0o600);
  }finally{globalThis.fetch=original;fs.rmSync(tmp,{recursive:true});}
});

test('loopback HTTP consumer preserves content, status, redirect flag, EOF and usage', async () => {
  const server=http.createServer((req,res)=>{res.writeHead(200,{'content-type':'text/event-stream'});res.end(sample);});
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  try {
    const records=[];const f=observeFetch(fetch,x=>records.push(x));
    const response=await f(`http://127.0.0.1:${server.address().port}/v1/messages`);
    assert.equal((await consume(response)).toString(),sample);
    const v=records.at(-1);assert.equal(v.http_status,200);assert.equal(v.redirected,false);assert.equal(v.message_stop,1);
  }finally{await new Promise(resolve=>server.close(resolve));}
});

test('pressure: 128 concurrent streams x 1024 delta events preserve full bytes and independent summaries', async t => {
  const payload=event({type:'message_start',message:{usage:{input_tokens:99}}})+
    event({type:'content_block_delta',delta:{text:'x'.repeat(128)}}).repeat(1024)+
    event({type:'message_delta',usage:{output_tokens:1024}})+event({type:'message_stop'});
  const expected=sha(payload);const input=bytes(payload);let records=0;const start=performance.now();
  await Promise.all(Array.from({length:128},async()=>{
    let last;const p=new Response(input,{headers:{'content-type':'text/event-stream'}});
    observeResponse(p,v=>{last=v;records++;});
    assert.equal(sha(await consume(p)),expected);assert.equal(last.consumed_prefix_sha256,expected);
    assert.equal(last.message_stop,1);assert.equal(last.events.content_block_delta,1024);
    assert.equal(last.usage.output_tokens,1024);assert.equal(last.truncated,false);
  }));
  t.diagnostic(JSON.stringify({concurrent_streams:128,delta_events:131072,bytes:input.length*128,
    duration_ms:Math.round(performance.now()-start),snapshots:records,model_requests:0}));
});


test('method fallbacks distinguish parsed JSON and bounded decoded text from raw bytes', async () => {
  const parsed={type:'error',error:{type:'rate_limit_error'}};
  for(const name of ['json','text']) {
    const records=[];const result=name==='json'?parsed:sample;
    const response={headers:new Headers({'content-type':name==='json'?'application/json':'text/event-stream'}),body:{},[name](){return Promise.resolve(result);}};
    observeResponse(response,v=>records.push(v));assert.equal(await response[name](),result);
    const last=records.at(-1);assert.equal(last.consumed_prefix_sha256,null);
    assert.equal(last.observation_mode,name==='json'?'parsed_json':'decoded_text');
    if(name==='text') assert.equal(last.decoded_prefix_sha256,sha(sample));
    else assert.equal(last.errors[0].type,'rate_limit_error');
  }
  const large='中'.repeat(1000000), records=[];
  const response={headers:new Headers(),body:{},text(){return Promise.resolve(large);}};
  observeResponse(response,v=>records.push(v),{maxBytes:32});assert.equal(await response.text(),large);
  assert.ok(records.at(-1).inspected_bytes<=32);assert.equal(records.at(-1).truncated,true);
});

test('pending read completed by cancel remains cancellation, not natural EOF', async () => {
  const records=[];const response=new Response(new ReadableStream({}, {highWaterMark:0}));
  observeResponse(response,v=>records.push(v));const reader=response.body.getReader();
  const pending=reader.read();await reader.cancel('stop');assert.equal((await pending).done,true);
  assert.equal(records.at(-1).consumption,'CANCELLED');assert.equal(records.at(-1).stream_eof,false);
});

test('delegating async iterator counts bytes exactly once', async () => {
  const response=new Response(sample,{headers:{'content-type':'text/event-stream'}}), records=[];
  response.body[Symbol.asyncIterator]=function(){const reader=this.getReader();return {next(){return reader.read();},return(){return reader.cancel().then(()=>({done:true}));},[Symbol.asyncIterator](){return this;}};};
  observeResponse(response,v=>records.push(v));const chunks=[];
  for await(const chunk of response.body) chunks.push(Buffer.from(chunk));
  assert.equal(Buffer.concat(chunks).toString(),sample);assert.equal(records.at(-1).bytes_seen,Buffer.byteLength(sample));
  assert.equal(records.at(-1).message_stop,1);
});

test('frozen or throwing observation accessors never break original consumer', async () => {
  const original=Object.freeze({next(){return Promise.resolve({done:true});}});
  const response={headers:new Headers(),body:{[Symbol.asyncIterator](){return original;}}};
  assert.equal(observeResponse(response,()=>{}),response);assert.equal(response.body[Symbol.asyncIterator](),original);
  assert.deepEqual(await original.next(),{done:true});
  const reader=Object.freeze({read(){return Promise.resolve({done:true});}});
  const other={headers:new Headers(),body:{getReader(){return reader;}}};
  observeResponse(other,()=>{});assert.equal(other.body.getReader(),reader);assert.equal((await reader.read()).done,true);
  const bad={get body(){throw new Error('observer-only');}};assert.equal(observeResponse(bad,()=>{}),bad);
});

test('iterator preventCancel retains consumption-stop without claiming source cancellation', async () => {
  let cancels=0;const response=new Response(new ReadableStream({start(c){c.enqueue(bytes(sample));},cancel(){cancels++;}}));
  const records=[];observeResponse(response,v=>records.push(v));
  const iterator=response.body[Symbol.asyncIterator]({preventCancel:true});await iterator.next();await iterator.return();
  assert.equal(cancels,0);assert.equal(records.at(-1).consumption,'CONSUMPTION_STOPPED');
  assert.equal(records.at(-1).stream_eof,false);assert.equal(records.at(-1).cancel_requested,undefined);
  await response.body.cancel();
});

test('error identifiers are allowlisted, explicit SSE error event is retained', () => {
  const records=[], p=summaryParser(v=>records.push(v));p.contentType('sse');
  p.feed(bytes(event({type:'error',error:{type:'sk-example-not-real',code:'private_prompt_word'}})+
    'event: error\ndata: {"error":{"type":"overloaded_error"}}\n\n'));p.finish('EOF');
  assert.deepEqual(records.at(-1).errors,[{type:'UNKNOWN',code:null},{type:'overloaded_error',code:null}]);
  assert.ok(!JSON.stringify(records).includes('sk-example'));assert.ok(!JSON.stringify(records).includes('private_prompt'));
});

test('BYOB buffers and reader re-acquisition preserve exact consumed evidence', async () => {
  const input=bytes(sample);let offset=0;const records=[];
  const body=new ReadableStream({type:'bytes',pull(controller){
    if(offset===input.length){controller.close();controller.byobRequest?.respond(0);return;}
    const count=Math.min(17,input.length-offset);controller.enqueue(input.slice(offset,offset+count));offset+=count;
  }});
  const response=new Response(body,{headers:{'content-type':'text/event-stream'}});observeResponse(response,v=>records.push(v));
  let reader=body.getReader({mode:'byob'});const chunks=[];
  const first=await reader.read(new Uint8Array(31));chunks.push(Buffer.from(first.value));reader.releaseLock();
  reader=body.getReader({mode:'byob'});
  for(;;){const v=await reader.read(new Uint8Array(31));if(v.value?.length)chunks.push(Buffer.from(v.value));if(v.done)break;}
  assert.equal(sha(Buffer.concat(chunks)),sha(sample));assert.equal(records.at(-1).consumed_prefix_sha256,sha(sample));
  assert.equal(records.at(-1).message_stop,1);
});


test('snapshot count remains bounded and preserves EOF after prolonged semantic traffic', () => {
  const records=[],p=summaryParser(v=>records.push(v));p.contentType('sse');
  for(let i=0;i<10000;i++)p.feed(bytes(event({type:'message_stop'})));
  p.finish('EOF');assert.ok(records.length<=131);assert.equal(records.at(-1).message_stop,10000);
  assert.equal(records.at(-1).stream_eof,true);assert.equal(records.at(-1).snapshots_suppressed,true);
  let attempts=0;const failed=summaryParser(()=>{attempts++;throw new Error('disk full');});failed.contentType('sse');
  for(let i=0;i<1000;i++)failed.feed(bytes(event({type:'message_stop'})));failed.finish('EOF');assert.equal(attempts,1);
});

test('request limit stops telemetry without changing any original fetch calls', async () => {
  let calls=0;const records=[],f=observeFetch(()=>{calls++;return Promise.resolve(new Response(null,{status:204}));},v=>records.push(v));
  for(let i=0;i<600;i++)assert.equal((await f('https://example.invalid/v1/messages')).status,204);
  assert.equal(calls,600);assert.equal(records.filter(v=>v.phase==='REQUEST').length,512);
  assert.equal(Math.max(...records.map(v=>v.request_sequence)),512);
});

test('rejected body cancellation leaves the locked original reader observable', async () => {
  const {response,records}=collect();const reader=response.body.getReader();
  await assert.rejects(response.body.cancel('locked'), TypeError);
  const chunks=[];for(;;){const v=await reader.read();if(v.done)break;chunks.push(Buffer.from(v.value));}
  assert.equal(Buffer.concat(chunks).toString(),sample);
  const last=records.at(-1);assert.equal(last.cancel_failed,1);assert.equal(last.stream_eof,true);
  assert.equal(last.message_stop,1);assert.equal(last.consumed_prefix_sha256,sha(sample));
});

test('readMany EOF before iterator cleanup is distinct from a user cancelling a pending read', async () => {
  for(const asyncBatch of [false,true]) {
    const records=[];let step=0, reader;
    const response={headers:new Headers({'content-type':'text/event-stream'}),body:{
      getReader(){return {readMany(){const r=step++?{done:true,value:[]}:{done:false,value:[bytes(sample),bytes('prefetch')]};return asyncBatch?Promise.resolve(r):r;}};},
      cancel(){return Promise.resolve();},
      [Symbol.asyncIterator](){reader=this.getReader();return {next:async()=>{
        const batch=await reader.readMany();
        if(batch.done){await this.cancel();return {done:true};}
        return {done:false,value:batch.value[0]};
      }};}
    }};
    observeResponse(response,v=>records.push(v));const iterator=response.body[Symbol.asyncIterator]();
    assert.equal(Buffer.from((await iterator.next()).value).toString(),sample);assert.equal((await iterator.next()).done,true);
    const last=records.at(-1);assert.equal(last.stream_eof,true);assert.equal(last.cancel_after_reader_eof,true);
    assert.equal(last.cancel_requested,undefined);assert.equal(last.bytes_seen,Buffer.byteLength(sample));
    assert.equal(last.consumed_prefix_sha256,sha(sample));
  }
});

test('iterator cancellation without a reader EOF fact remains source-unknown', async () => {
  const records=[];const response={headers:new Headers(),body:{cancel(){return Promise.resolve();},
    [Symbol.asyncIterator](){return {next:async()=>{await Promise.resolve();await this.cancel();return {done:true};}};}}};
  observeResponse(response,v=>records.push(v));await response.body[Symbol.asyncIterator]().next();
  const last=records.at(-1);assert.equal(last.cancel_origin_unknown,true);assert.equal(last.iterator_done,true);
  assert.equal(last.stream_eof,false);assert.equal(last.consumption,'ITERATOR_DONE_CANCEL_ORIGIN_UNKNOWN');
});

test('a failed cancellation does not erase later bytes while another cancel remains pending', async () => {
  const records=[];let resolveRead,resolveCancel;const failure=new Error('rejected');
  const pending=new Promise(r=>resolveRead=r), cancellation=new Promise(r=>resolveCancel=r);
  const reader={read(){return pending;},cancel(){return cancellation;}};
  const response={headers:new Headers(),body:{getReader(){return reader;},cancel(){return Promise.reject(failure);}}};
  observeResponse(response,v=>records.push(v));response.body.getReader();
  assert.equal(reader.read(),pending);assert.equal(reader.cancel(),cancellation);
  await assert.rejects(response.body.cancel(),e=>e===failure);
  resolveRead({done:true});await pending;assert.equal(records.at(-1).stream_eof,false);
  resolveCancel();await cancellation;const last=records.at(-1);
  assert.equal(last.cancel_failed,1);assert.equal(last.cancel_succeeded,1);assert.equal(last.consumption,'CANCELLED');
});

test('activation rejects symlink directories and clears only its own exact Bun preload', () => {
  const root=fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(),'upgrade-observer-path-')));
  const dir=path.join(root,'records'),link=path.join(root,'link');fs.mkdirSync(dir,{mode:0o700});fs.symlinkSync(dir,link);
  const original=globalThis.fetch, modulePath=require.resolve('./upgrade_claude_response_observer.cjs');
  const escaped=Array.from(modulePath,c=>[' ', '\t', "'", '"'].includes(c)?'\\'+c:c).join('');
  try {
    assert.equal(install({UPGRADE_RESPONSE_OBSERVATIONS_DIR:link,UPGRADE_RESPONSE_OBSERVER_ROOT_PID:String(process.pid)}),false);
    for(const option of ['--preload '+escaped,'--preload '+escaped+' --preload /tmp/other.cjs']) {
      const env={UPGRADE_RESPONSE_OBSERVATIONS_DIR:dir,UPGRADE_RESPONSE_OBSERVER_ROOT_PID:String(process.pid),BUN_OPTIONS:option};
      assert.equal(install(env),true);assert.equal(env.UPGRADE_RESPONSE_OBSERVATIONS_DIR,undefined);
      assert.equal(env.UPGRADE_RESPONSE_OBSERVER_ROOT_PID,undefined);
      assert.equal(env.BUN_OPTIONS,option.endsWith('/tmp/other.cjs')?option:undefined);globalThis.fetch=original;
    }
  } finally {globalThis.fetch=original;fs.rmSync(root,{recursive:true});}
});

test('atomic publication cleans only its own unrenamed temporary file', async t => {
  for (const fault of ['none', 'open-exists', 'write', 'close-before', 'close-after', 'rename']) {
    await t.test(fault, () => {
      const directory = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), 'upgrade-observer-atomic-')));
      fs.chmodSync(directory, 0o700);
      const originals = Object.fromEntries(['openSync', 'writeFileSync', 'closeSync', 'renameSync', 'unlinkSync']
        .map(name => [name, fs[name]]));
      const originalFetch = globalThis.fetch;
      const sentinel = Buffer.from('pre-existing temporary file belongs to another publication');
      const injected = Object.assign(new Error('injected ' + fault), {code: 'EIO'});
      const calls = {open: 0, write: 0, close: 0, rename: 0, unlink: 0};
      let temporary, ownedFd, closeInjected = false;
      try {
        fs.openSync = function(filename, flags, mode) {
          if (typeof filename !== 'string' || !filename.startsWith(directory + path.sep) ||
              !filename.endsWith('.json.tmp')) return originals.openSync(filename, flags, mode);
          temporary = filename; calls.open++;
          assert.ok(filename.startsWith(directory + path.sep) && filename.endsWith('.json.tmp'));
          assert.ok(flags & fs.constants.O_EXCL); assert.ok(flags & fs.constants.O_NOFOLLOW);
          assert.equal(mode, 0o600);
          if (fault === 'open-exists') {
            // Let the real filesystem reject O_EXCL against a real, populated file.
            const otherFd = originals.openSync(filename, 'wx', 0o600);
            originals.writeFileSync(otherFd, sentinel); originals.closeSync(otherFd);
          }
          ownedFd = originals.openSync(filename, flags, mode);
          return ownedFd;
        };
        fs.writeFileSync = function(fd, data) {
          calls.write++; assert.equal(fd, ownedFd);
          if (fault === 'write') {
            originals.writeFileSync(fd, 'partial record');
            throw injected;
          }
          return originals.writeFileSync(fd, data);
        };
        fs.closeSync = function(fd) {
          if (fd !== ownedFd) return originals.closeSync(fd);
          calls.close++; assert.equal(fd, ownedFd);
          if (fault === 'close-before' && !closeInjected) {
            closeInjected = true; throw injected;
          }
          const result = originals.closeSync(fd);
          if (fault === 'close-after' && !closeInjected) {
            closeInjected = true; throw injected;
          }
          return result;
        };
        fs.renameSync = function(from, to) {
          calls.rename++; assert.equal(from, temporary);
          if (fault === 'rename') throw injected;
          return originals.renameSync(from, to);
        };
        fs.unlinkSync = function(filename) {
          calls.unlink++; assert.equal(filename, temporary);
          return originals.unlinkSync(filename);
        };
        const activate = () => install({UPGRADE_RESPONSE_OBSERVATIONS_DIR: directory,
          UPGRADE_RESPONSE_OBSERVER_ROOT_PID: String(process.pid)});
        if (fault === 'none') assert.equal(activate(), true);
        else if (fault === 'open-exists') assert.throws(activate, {code: 'EEXIST'});
        else assert.throws(activate, error => error === injected);
        Object.assign(fs, originals);

        const names = fs.readdirSync(directory);
        if (fault === 'none') {
          assert.equal(calls.unlink, 0); assert.equal(calls.rename, 1);
          assert.equal(names.length, 1); assert.ok(names[0].endsWith('.json'));
          const filename = path.join(directory, names[0]);
          assert.equal(JSON.parse(fs.readFileSync(filename)).phase, 'LOADED');
          assert.equal(fs.statSync(filename).mode & 0o777, 0o600);
        } else if (fault === 'open-exists') {
          assert.equal(calls.unlink, 0); assert.equal(calls.write, 0); assert.equal(calls.close, 0);
          assert.deepEqual(names, [path.basename(temporary)]);
          assert.deepEqual(fs.readFileSync(temporary), sentinel);
          assert.equal(globalThis.fetch, originalFetch);
        } else {
          assert.equal(calls.unlink, 1); assert.deepEqual(names, []);
          assert.equal(globalThis.fetch, originalFetch);
        }
        if (fault === 'close-before' || fault === 'close-after') assert.equal(calls.close, 2);
      } finally {
        Object.assign(fs, originals); globalThis.fetch = originalFetch;
        // The injected failures may leave a descriptor open if an assertion failed.
        if (ownedFd !== undefined) { try { originals.closeSync(ownedFd); } catch {} }
        fs.rmSync(directory, {recursive: true});
      }
    });
  }
});
