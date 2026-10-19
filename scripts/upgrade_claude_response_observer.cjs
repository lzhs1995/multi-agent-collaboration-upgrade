/* Private, opt-in observation of bytes the original Claude consumer reads.
 * Never clone/tee/drain a response, log credentials/prompts, retry, or send input. */
'use strict';
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const MAX_BYTES = 8 * 1024 * 1024;
const MAX_FRAME = 256 * 1024;
const MAX_REQUESTS = 512;
const guarded = fn => { try { return fn(); } catch { return undefined; } };
const errorTypes = new Set(['invalid_request_error', 'authentication_error', 'permission_error',
  'not_found_error', 'request_too_large', 'rate_limit_error', 'api_error', 'overloaded_error', 'timeout_error']);
const errorType = x => errorTypes.has(x) ? x : 'UNKNOWN';
const eventKinds = ['message_start', 'message_delta', 'message_stop', 'content_block_start',
  'content_block_delta', 'content_block_stop', 'ping', 'error'];
const usageKeys = ['input_tokens', 'output_tokens', 'cache_creation_input_tokens', 'cache_read_input_tokens'];

function summaryParser(publish, {maxBytes = MAX_BYTES, maxFrame = MAX_FRAME} = {}) {
  const s = {bytes_seen: 0, inspected_bytes: 0, truncated: false, malformed_events: 0,
    events: {}, message_start: 0, message_stop: 0, errors: [], usage: {},
    consumption: 'NOT_READ', stream_eof: false, trailing_frame: false, observation_mode: 'raw_bytes'};
  const hash = crypto.createHash('sha256');
  const decoder = new TextDecoder('utf-8', {fatal: true});
  let line = '', data = [], frameBytes = 0, cr = false, disabled = false, ended = false;
  let contentType = 'unknown', eventName = '', deferredCompletion = null;
  const stops = new Set();
  let lastEmitBytes = 0, lastEmitSemantic = '', progressSnapshots = 0, terminalSnapshots = 0, sinkFailed = false;
  const emit = (force = true, terminal = false) => {
    if (sinkFailed) return;
    if (terminal ? terminalSnapshots >= 3 : progressSnapshots >= 128) {
      s.snapshots_suppressed = true; return;
    }
    const semanticKey = [s.message_start, s.message_stop, s.errors.length, s.malformed_events, s.truncated].join(':');
    if (!force && semanticKey === lastEmitSemantic && s.bytes_seen - lastEmitBytes < 65536) return;
    lastEmitBytes = s.bytes_seen; lastEmitSemantic = semanticKey;
    if (terminal) terminalSnapshots++; else progressSnapshots++;
    try { publish({...s, events: {...s.events}, usage: {...s.usage}, errors: [...s.errors],
      consumed_prefix_sha256: s.observation_mode === 'raw_bytes' ? hash.copy().digest('hex') : null,
      decoded_prefix_sha256: s.observation_mode === 'decoded_text' ? hash.copy().digest('hex') : null}); } catch { sinkFailed = true; }
  };
  function addUsage(v) {
    if (!v || typeof v !== 'object') return;
    for (const key of usageKeys) if (Number.isSafeInteger(v[key]) && v[key] >= 0) s.usage[key] = v[key];
  }
  function semantic(v) {
    if (!v || typeof v !== 'object' || Array.isArray(v)) { s.malformed_events++; return; }
    if (v.type === 'message_start') { s.message_start++; addUsage(v.message?.usage); }
    if (v.type === 'message_stop') s.message_stop++;
    if (v.type === 'message_delta') addUsage(v.usage);
    if (v.type === 'message') addUsage(v.usage);
    if (v.type === 'error') {
      if (s.errors.length < 16) s.errors.push({type: errorType(v.error?.type), code: null});
      else s.truncated = true;
    }
  }
  function flushLine() {
    if (!line) {
      if (data.length) {
        try {
          const v = JSON.parse(data.join('\n'));
          const explicit = eventKinds.includes(eventName) ? eventName : null;
          const declared = eventKinds.includes(v?.type) ? v.type : null;
          if (explicit && declared && explicit !== declared)
            s.event_type_mismatches = (s.event_type_mismatches || 0) + 1;
          const kind = declared || explicit || 'unknown';
          s.events[kind] = (s.events[kind] || 0) + 1;
          semantic(v);
          if (explicit === 'error' && v?.type !== 'error') semantic({type:'error', error:v?.error});
        } catch { s.malformed_events++; }
      }
      data = []; frameBytes = 0; eventName = '';
    } else if (line.startsWith('data:')) data.push(line.slice(5).replace(/^ /, ''));
    else if (line.startsWith('event:')) { const name=line.slice(6).trim(); eventName=eventKinds.includes(name)?name:'UNKNOWN'; }
    line = '';
  }
  function textChunk(text) {
    if (contentType !== 'sse') {
      line += text;
      if (line.length > maxFrame) { s.truncated = true; disabled = true; line = ''; }
      return;
    }
    for (const c of text) {
      if (cr) { cr = false; if (c === '\n') continue; }
      if (c === '\r' || c === '\n') { flushLine(); cr = c === '\r'; }
      else { line += c; frameBytes++; }
      if (frameBytes > maxFrame) {
        s.truncated = true; disabled = true; line = ''; data = []; return;
      }
    }
  }
  function feed(value) {
    if (ended) return;
    if (!ArrayBuffer.isView(value)) { disabled = true; s.truncated = true; emit(); return; }
    s.consumption = 'READING';
    const bytes = new Uint8Array(value.buffer, value.byteOffset, value.byteLength);
    s.bytes_seen += bytes.byteLength;
    if (!disabled) {
      const room = Math.max(0, maxBytes - s.inspected_bytes);
      const part = bytes.subarray(0, room);
      hash.update(part); s.inspected_bytes += part.byteLength;
      try { textChunk(decoder.decode(part, {stream: true})); }
      catch { s.malformed_events++; disabled = true; }
      if (bytes.byteLength > room) { s.truncated = true; disabled = true; }
    }
    emit(false);
  }
  function finish(state) {
    if (ended) return;
    if (state === 'EOF' && stops.size) {
      deferredCompletion = state; s.consumption = 'STOP_PENDING'; emit(); return;
    }
    if (state === 'EOF' && s.cancel_origin_unknown) state = 'ITERATOR_DONE_CANCEL_ORIGIN_UNKNOWN';
    ended = true; s.consumption = state; s.stream_eof = state === 'EOF';
    if (!disabled) {
      try { textChunk(decoder.decode()); } catch { s.malformed_events++; }
      if (contentType === 'json' && state === 'EOF') {
        try { semantic(JSON.parse(line)); } catch { s.malformed_events++; }
        line = '';
      }
    }
    s.trailing_frame = Boolean(line || data.length);
    emit(true, true);
  }
  return {feed, finish, emit, state: s, stopRequested(cancel, origin = 'explicit') {
    const token = {cancel, origin};
    s.cancel_calls = (s.cancel_calls || 0) + Number(cancel);
    if (origin === 'after_reader_eof') s.cancel_after_reader_eof = true;
    else {
      stops.add(token);
      if (origin === 'explicit') s[cancel ? 'cancel_requested' : 'iterator_return_requested'] = true;
    }
    emit(); return token;
  }, stopSettled(token, success) {
    stops.delete(token);
    const field = token.cancel ? (success ? 'cancel_succeeded' : 'cancel_failed') :
      (success ? 'iterator_return_succeeded' : 'iterator_return_failed');
    s[field] = (s[field] || 0) + 1;
    if (success && token.origin === 'unknown') s.cancel_origin_unknown = true;
    if (success && token.origin === 'explicit') finish(token.cancel ? 'CANCELLED' : 'CONSUMPTION_STOPPED');
    else if (!stops.size && deferredCompletion) { const state=deferredCompletion; deferredCompletion=null; finish(state); }
    emit();
  }, contentType(value) { contentType = value; },
    json(value) { if (ended) return; s.observation_mode = 'parsed_json'; semantic(value);
      ended = true; s.consumption = 'JSON_CONSUMED'; emit(true, true); }};
}

function observeResponse(response, publish, options) {
  const p = summaryParser(publish, options);
  const type = guarded(() => response.headers.get('content-type')) || '';
  p.contentType(typeof type === 'string' && type.includes('text/event-stream') ? 'sse' :
    typeof type === 'string' && type.includes('json') ? 'json' : 'unknown');
  const seenReaders = new WeakSet();
  let readerOwnsBytes = false, readerEOF = false, iteratorPending = 0, iteratorReturning = 0;
  function after(result, success, failure='READ_ERROR') {
    guarded(() => result.then(v => guarded(() => success(v)),
      () => guarded(() => p.finish(failure))));
    return result;
  }
  function patch(object, name, make) {
    guarded(() => {
      const original = object[name];
      if (typeof original === 'function') object[name] = make(original);
    });
  }
  function observedRead(original, receiver, args, consumer) {
    let result;
    try { result = Reflect.apply(original, receiver, args); }
    catch (error) { guarded(() => p.finish('READ_ERROR')); throw error; }
    return after(result, consumer);
  }
  function value(v) {
    if (v.value?.byteLength) p.feed(v.value);
    if (v.done) p.finish('EOF');
  }
  function stop(original, receiver, args, cancel, origin = 'explicit') {
    const token = guarded(() => p.stopRequested(cancel, origin));
    let result;
    try { result = Reflect.apply(original, receiver, args); }
    catch (error) { guarded(() => p.stopSettled(token, false)); throw error; }
    guarded(() => result.then(() => guarded(() => p.stopSettled(token, true)),
      () => guarded(() => p.stopSettled(token, false))));
    return result;
  }
  function wrapReader(reader) {
    if (!reader || seenReaders.has(reader)) return;
    seenReaders.add(reader);
    patch(reader, 'read', original => function() {
      readerOwnsBytes = true;
      return observedRead(original, this, arguments, value);
    });
    patch(reader, 'cancel', original => function() { return stop(original, this, arguments, true); });
    // Bun's iterator batches reads internally. Observe only its EOF fact: these
    // prefetched values have not necessarily been yielded to the consumer.
    patch(reader, 'readMany', original => function() {
      const result = Reflect.apply(original, this, arguments);
      const eof = v => { if (v?.done) { readerEOF = true; p.state.reader_eof_observed = true; } };
      guarded(() => {
        if (typeof result?.then === 'function') result.then(v => guarded(() => eof(v)), () => {});
        else eof(result);
      });
      return result;
    });
  }
  const body = guarded(() => response.body);
  patch(body, 'getReader', original => function() {
    const reader = Reflect.apply(original, this, arguments);
    readerEOF = false;
    guarded(() => wrapReader(reader));
    return reader;
  });
  patch(body, 'cancel', original => function() {
    const origin = iteratorReturning ? 'explicit' : readerEOF ? 'after_reader_eof' :
      iteratorPending ? 'unknown' : 'explicit';
    return stop(original, this, arguments, true, origin);
  });
  // A delegating iterator can call public getReader either now or during next().
  // Once that reader is used it owns byte observation, including reused BYOB views.
  patch(body, Symbol.asyncIterator, original => function() {
    const iterator = Reflect.apply(original, this, arguments);
    const preventCancel = guarded(() => arguments[0]?.preventCancel) === true;
    patch(iterator, 'next', next => function() {
      iteratorPending++;
      let result;
      try { result = Reflect.apply(next, this, arguments); }
      catch (error) { iteratorPending--; guarded(() => p.finish('READ_ERROR')); throw error; }
      guarded(() => result.then(v => guarded(() => {
        iteratorPending--; if (v.done) p.state.iterator_done = true;
        if (!readerOwnsBytes) value(v);
      }), () => guarded(() => { iteratorPending--; p.finish('READ_ERROR'); })));
      return result;
    });
    patch(iterator, 'return', finish => function() {
      iteratorReturning++;
      try {
        const result = stop(finish, this, arguments, !preventCancel);
        guarded(() => result.then(() => { iteratorReturning--; }, () => { iteratorReturning--; }));
        return result;
      } catch (error) { iteratorReturning--; throw error; }
    });
    return iterator;
  });
  for (const name of ['text', 'arrayBuffer', 'bytes']) patch(response, name, original => function() {
    return observedRead(original, this, arguments, result => {
      if (readerOwnsBytes) return;
      if (typeof result === 'string') {
        p.state.observation_mode = 'decoded_text';
        p.state.text_code_units = result.length;
        const limit = Math.min(options?.maxBytes ?? MAX_BYTES, MAX_BYTES);
        const buffer = new Uint8Array(Math.min(limit, result.length * 3));
        const encoded = new TextEncoder().encodeInto(result, buffer);
        p.feed(buffer.subarray(0, encoded.written));
        if (encoded.read < result.length) p.state.truncated = true;
      } else p.feed(result instanceof ArrayBuffer ? new Uint8Array(result) : result);
      p.finish('EOF');
    });
  });
  patch(response, 'json', original => function() {
    return observedRead(original, this, arguments, result => { if (!readerOwnsBytes) p.json(result); });
  });
  if (body === null) p.finish('NO_BODY');
  else p.emit();
  return response;
}

function observeFetch(original, publish, identity = {}, options) {
  let sequence = 0;
  return function(input, init) {
    let base;
    guarded(() => {
      const url = new URL(typeof input === 'string' || input instanceof URL ? input : input.url);
      if (!['http:', 'https:'].includes(url.protocol) || !/\/messages\/?$/.test(url.pathname)) return;
      if (++sequence > MAX_REQUESTS) return;
      base = {...identity, request_sequence: sequence, started_at_ms: Date.now(),
        request_type: 'UNKNOWN', http_status: null, response_received: false};
      if (typeof init?.body === 'string' && Buffer.byteLength(init.body) <= MAX_BYTES) {
        base.request_body_sha256 = crypto.createHash('sha256').update(init.body).digest('hex');
      }
      guarded(() => publish({...base, phase: 'REQUEST'}));
    });
    let pending;
    try { pending = Reflect.apply(original, this, arguments); }
    catch (error) { if (base) guarded(() => publish({...base, phase: 'FETCH_THROW'})); throw error; }
    if (base) guarded(() => pending.then(response => guarded(() => {
      base = {...base, response_received: true, headers_at_ms: Date.now(),
        http_status: response.status, redirected: response.redirected};
      observeResponse(response, summary => publish({...base, phase: 'RESPONSE', ...summary}), options);
    }), () => guarded(() => publish({...base, phase: 'FETCH_REJECTED'}))));
    return pending;
  };
}

function noSymlinkPath(value) {
  if (!path.isAbsolute(value) || path.resolve(value) !== value) return false;
  let current = path.parse(value).root;
  for (const component of value.slice(current.length).split(path.sep).filter(Boolean)) {
    current = path.join(current, component);
    if (fs.lstatSync(current).isSymbolicLink()) return false;
  }
  return true;
}

function ownPreload(option) {
  if (typeof option !== 'string' || !option.startsWith('--preload ')) return false;
  const encoded = option.slice('--preload '.length);
  let decoded = '';
  for (let i=0; i<encoded.length; i++) {
    const c = encoded[i];
    if (c === '\\') {
      if (![' ', '\t', "'", '"'].includes(encoded[++i])) return false;
      decoded += encoded[i];
    } else {
      if ([' ', '\t', "'", '"', '\n', '\r', '\0'].includes(c)) return false;
      decoded += c;
    }
  }
  if (!noSymlinkPath(decoded) || fs.realpathSync(decoded) !== fs.realpathSync(__filename)) return false;
  const input = fs.lstatSync(decoded), self = fs.lstatSync(__filename);
  return input.dev === self.dev && input.ino === self.ino;
}

function install(env = process.env) {
  const requested = env.UPGRADE_RESPONSE_OBSERVATIONS_DIR;
  if (!requested || env.UPGRADE_RESPONSE_OBSERVER_ROOT_PID !== String(process.pid)) return false;
  if (!noSymlinkPath(requested)) return false;
  // APFS may canonicalize casing differently in Python, Node and native Bun.
  // Allow only the same nonsymlink directory; pin both names and its identity.
  const directory = fs.realpathSync(requested);
  if (!noSymlinkPath(directory)) return false;
  const info = fs.lstatSync(directory), requestedInfo = fs.lstatSync(requested);
  if (requestedInfo.dev !== info.dev || requestedInfo.ino !== info.ino) return false;
  if (!info.isDirectory() || info.uid !== process.getuid() || (info.mode & 0o777) !== 0o700) return false;
  if (typeof globalThis.fetch !== 'function') return false;
  const epoch = crypto.randomUUID();
  const identity = {schema: 'upgrade-consumed-response-v1', pid: process.pid, observer_epoch: epoch};
  function publish(record) {
    const current = fs.lstatSync(directory);
    const originalName = fs.lstatSync(requested);
    if (!current.isDirectory() || current.dev !== info.dev || current.ino !== info.ino ||
        current.uid !== info.uid || (current.mode & 0o777) !== 0o700 ||
        originalName.dev !== info.dev || originalName.ino !== info.ino ||
        originalName.uid !== info.uid || (originalName.mode & 0o777) !== 0o700 ||
        !noSymlinkPath(requested) || !noSymlinkPath(directory) ||
        fs.realpathSync(requested) !== directory || fs.realpathSync(directory) !== directory)
      throw new Error('observer directory changed');
    const number = record.request_sequence || 0;
    const destination = path.join(directory, `${process.pid}-${epoch}-${number}.json`);
    const temp = destination + '.tmp';
    let fd;
    try {
      fd = fs.openSync(temp, fs.constants.O_WRONLY | fs.constants.O_CREAT | fs.constants.O_EXCL | fs.constants.O_NOFOLLOW, 0o600);
      fs.writeFileSync(fd, JSON.stringify(record) + '\n');
      fs.closeSync(fd); fd = undefined;
      fs.renameSync(temp, destination);
    } finally {
      if (fd !== undefined) fs.closeSync(fd);
      guarded(() => fs.unlinkSync(temp));
    }
  }
  publish({...identity, phase: 'LOADED', loaded_at_ms: Date.now(),
    observer_sha256: crypto.createHash('sha256').update(fs.readFileSync(__filename)).digest('hex')});
  globalThis.fetch = observeFetch(globalThis.fetch, publish, identity);
  // The pinned launcher provides exactly this preload; children must not inherit it.
  delete env.UPGRADE_RESPONSE_OBSERVER_ROOT_PID;
  delete env.UPGRADE_RESPONSE_OBSERVATIONS_DIR;
  // BUN_OPTIONS has Bun's escape parser, not a shell parser.
  if (guarded(() => ownPreload(env.BUN_OPTIONS))) delete env.BUN_OPTIONS;
  return true;
}

guarded(() => install());
module.exports = {summaryParser, observeResponse, observeFetch, install};
