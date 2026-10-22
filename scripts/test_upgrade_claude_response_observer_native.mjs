// Local-only Bun runtime test. May be preloaded by the pinned native binary's
// --version command; top-level await finishes this fixture before CLI startup.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import observer from './upgrade_claude_response_observer.cjs';

if (typeof Bun === 'undefined') throw new Error('This fixture requires Bun');
const output=process.env.UPGRADE_OBSERVER_NATIVE_RESULT;
if (!output || !path.isAbsolute(output)) throw new Error('An absolute fixture result path is required');
const event=v=>'data: '+JSON.stringify(v)+'\n\n';
const payload=event({type:'message_start',message:{usage:{input_tokens:99},content:[]}})+
  event({type:'content_block_delta',delta:{type:'text_delta',text:'x'.repeat(128)}}).repeat(1024)+
  event({type:'message_delta',usage:{output_tokens:1024}})+event({type:'message_stop'});
const bytes=new TextEncoder().encode(payload),sha=x=>crypto.createHash('sha256')
  .update(x instanceof ArrayBuffer?new Uint8Array(x):x).digest('hex');
const expected=sha(bytes),result={schema:'observer-local-bun-runtime-v1',bun_version:Bun.version,
  bun_revision:Bun.revision,standalone:Bun.isStandaloneExecutable===true,
  fetch_session_available:typeof Bun.FetchSession==='function',external_requests:0,model_requests:0,
  payload_bytes:bytes.length,rounds:[],checks:[]};
let serverRequests=0,server;
try {
  server=Bun.serve({hostname:'127.0.0.1',port:0,fetch(req){
    serverRequests++;
    const kind=new URL(req.url).searchParams.get('kind');
    if(kind==='json') return new Response(JSON.stringify({type:'error',error:{type:'rate_limit_exceeded',message:'PRIVATE_ERROR'}}),
      {status:429,headers:{'content-type':'application/json'}});
    if(kind==='hang') return new Response(new ReadableStream({start(c){c.enqueue(new TextEncoder().encode(event({type:'ping'})));}}),
      {headers:{'content-type':'text/event-stream'}});
    let offset=0;
    return new Response(new ReadableStream({pull(c){
      if(offset===bytes.length){c.close();return;}
      const end=Math.min(offset+4093,bytes.length);c.enqueue(bytes.subarray(offset,end));offset=end;
    }}),{headers:{'content-type':'text/event-stream'}});
  }});
  const url=`http://127.0.0.1:${server.port}/v1/messages`;
  const paths=result.fetch_session_available?['global_fetch','bun_fetch_session']:['global_fetch'];
  for(const transport of paths) for(const mode of ['reader','iterator']) {
    const final=new Map(),requests=new Map(),changes=[];
    const hooks=observer.installFetchHooks(globalThis,r=>{
      if(r.phase==='REQUEST')requests.set(r.request_sequence,r);
      if(r.phase==='RESPONSE')final.set(r.request_sequence,r);
    },{},c=>changes.push(JSON.parse(JSON.stringify(c))));
    let session;
    try {
      if(transport==='bun_fetch_session')session=new Bun.FetchSession();
      const wn=globalThis.fetch,n=session?(...args)=>session.fetch(...args):undefined;
      // Reconstruct the native p5n selection expression with real session.fetch;
      // this does not execute the client's original business p5n function.
      const invoke=(...args)=>{const r=globalThis.fetch;return (n&&r===wn?n:r)(...args);};
      const started=performance.now(),before=serverRequests;
      await Promise.all(Array.from({length:256},async(_,i)=>{
        const response=await invoke(url+`?i=${i}`,{method:'POST',body:'LOCAL_FIXTURE'});
        const hash=crypto.createHash('sha256');
        if(mode==='reader') {
          const reader=response.body.getReader();
          for(;;){const v=await reader.read();if(v.done)break;hash.update(v.value);}
        } else for await(const chunk of response.body)hash.update(chunk);
        assert.equal(hash.digest('hex'),expected);
      }));
      assert.equal(serverRequests-before,256);assert.equal(requests.size,256);assert.equal(final.size,256);
      for(const r of final.values()) {
        assert.deepEqual(r.intercepted_paths,[transport]);assert.equal(r.request_class,'UNKNOWN');
        assert.equal(r.stream_eof,true);assert.equal(r.message_stop,1);assert.equal(r.bytes_seen,bytes.length);
        assert.equal(r.consumed_prefix_sha256,expected);assert.equal(r.truncated,false);
        assert.equal(r.content_summary.text_delta_events,1024);
        assert.equal(r.content_summary.text_code_units,131072);
        assert.equal(r.content_summary.non_whitespace_text_observed,true);
      }
      result.rounds.push({transport,mode,concurrency:256,requests:256,delta_events:262144,
        consumed_bytes:bytes.length*256,duration_ms:Math.round(performance.now()-started),
        coverage:JSON.parse(JSON.stringify(hooks.coverage)),passed:true});
    } finally {session?.close();hooks.restore();}
  }
  for(const transport of paths) {
    const records=[],hooks=observer.installFetchHooks(globalThis,r=>records.push(r),{});
    let session;
    try {
      if(transport==='bun_fetch_session')session=new Bun.FetchSession();
      const invoke=session?session.fetch:globalThis.fetch;
      const json=await invoke(url+'?kind=json');assert.equal(json.status,429);
      assert.deepEqual(await json.json(),{type:'error',error:{type:'rate_limit_exceeded',message:'PRIVATE_ERROR'}});
      assert.equal(records.at(-1).errors[0].type,'rate_limit_exceeded');
      assert.ok(!JSON.stringify(records).includes('PRIVATE_ERROR'));
      const unread=await invoke(url+'?kind=hang'),unreadRecord=records.at(-1);
      assert.equal(unreadRecord.consumption,'NOT_READ');assert.equal(unreadRecord.bytes_seen,0);
      assert.equal(unreadRecord.content_summary.non_whitespace_text_observed,false);
      await unread.body.cancel('local-unread-cancel');
      assert.equal(records.at(-1).stream_eof,false);assert.equal(records.at(-1).consumption,'CANCELLED');
      const pending=await invoke(url+'?kind=hang');const reader=pending.body.getReader();await reader.read();
      const read=reader.read();const cancel=reader.cancel('local-pending-cancel');
      await cancel;assert.equal((await read).done,true);
      assert.equal(records.at(-1).stream_eof,false);assert.equal(records.at(-1).consumption,'CANCELLED');
      const controller=new AbortController();
      const abortResponse=await invoke(url+'?kind=hang',{signal:controller.signal});
      const ar=abortResponse.body.getReader();await ar.read();const pendingRead=ar.read();
      controller.abort();await assert.rejects(pendingRead);
      assert.equal(records.at(-1).consumption,'READ_ERROR');assert.equal(records.at(-1).stream_eof,false);
      result.checks.push({transport,json_429:true,unread_preserved:true,body_cancel:true,pending_reader_cancel:true,abort_read:true});
    } finally {session?.close();hooks.restore();}
  }
  if(result.fetch_session_available) {
    const nestedChecks=[];
    for(const difference of ['delegation','url','init','repeated-identical','preconsumed']) {
      let session;
      const records=[],before=serverRequests;
      // A local wrapper exercises async scope while retaining actual native
      // FetchSession transport and real loopback response streams.
      const runtime={Bun,fetch:async(input,options)=>{
        await Promise.resolve();
        const first=await session.fetch(input,options);
        if(difference==='preconsumed') assert.equal(sha(await first.arrayBuffer()),expected);
        else if(difference!=='delegation') {
          const second=await session.fetch(difference==='url'?input+'&independent=1':input,
            difference==='init'?{...options}:options);
          assert.equal(sha(await second.arrayBuffer()),expected);
        }
        return first;
      }};
      const hooks=observer.installFetchHooks(runtime,r=>records.push(r),{});
      try {
        session=new Bun.FetchSession();
        const response=await runtime.fetch(url+'?nested=1',{method:'POST',body:'LOCAL_FIXTURE'});
        const preconsumed=difference==='preconsumed';
        if(preconsumed)assert.equal(response.bodyUsed,true);
        else assert.equal(sha(await response.arrayBuffer()),expected);
        const expectedCount=['delegation','preconsumed'].includes(difference)?1:2;
        assert.equal(serverRequests-before,expectedCount);
        assert.equal(records.filter(r=>r.phase==='REQUEST').length,expectedCount);
        const done=records.filter(r=>r.stream_eof);
        assert.equal(done.length,preconsumed?0:expectedCount);
        assert.equal(new Set(done.map(r=>r.request_sequence)).size,preconsumed?0:expectedCount);
        assert.ok(done.every(r=>r.message_stop===1 && r.bytes_seen===bytes.length));
        if(preconsumed)assert.equal(records.at(-1).consumption,'PRECONSUMED_UNOBSERVED');
        nestedChecks.push({difference,requests:expectedCount,passed:true});
      } finally {session?.close();hooks.restore();}
    }
    result.nested_checks=nestedChecks;
  }
  // Actual on-disk activation records must distinguish armed hooks from use.
  const dir=path.join(path.dirname(output),'activation-'+(result.standalone?'native':'bun'));
  fs.mkdirSync(dir,{mode:0o700});
  const originalFetch=globalThis.fetch,originalConstructor=Bun.FetchSession;
  let session;
  try {
    assert.equal(observer.install({UPGRADE_RESPONSE_OBSERVATIONS_DIR:dir,UPGRADE_RESPONSE_OBSERVER_ROOT_PID:String(process.pid)}),true);
    const records=()=>fs.readdirSync(dir).filter(n=>n.endsWith('.json')).map(n=>JSON.parse(fs.readFileSync(path.join(dir,n))));
    let loaded=records().find(r=>r.phase==='LOADED');assert.deepEqual(loaded.fetch_coverage.actual_paths,[]);
    if(result.fetch_session_available) {
      session=new Bun.FetchSession();await(await session.fetch(url+'?kind=json')).json();
    } else await(await fetch(url+'?kind=json')).json();
    loaded=records().find(r=>r.phase==='LOADED');
    assert.deepEqual(loaded.fetch_coverage.actual_paths,[result.fetch_session_available?'bun_fetch_session':'global_fetch']);
    assert.equal(records().filter(r=>r.phase==='RESPONSE').length,1);
    result.activation={directory:dir,record:loaded,passed:true};
  } finally {
    session?.close();globalThis.fetch=originalFetch;
    if(result.fetch_session_available)Bun.FetchSession=originalConstructor;
  }
  result.passed=true;
} catch(error) {
  result.passed=false;result.failure={name:error.name,message:error.message,stack:error.stack};
  throw error;
} finally {
  server?.stop(true);result.loopback_server_requests=serverRequests;
  fs.writeFileSync(output,JSON.stringify(result,null,2)+'\n',{mode:0o600});
}
