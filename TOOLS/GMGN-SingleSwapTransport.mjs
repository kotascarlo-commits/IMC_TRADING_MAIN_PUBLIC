// Instance-bound SDK boundary. No entrypoint, config loading or production wiring.
// A future owned host must authenticate the handoff against the durable ledger.
// This object consumes one in-process contract; it is NOT restart deduplication.
import {createHash} from 'node:crypto';
import {performance} from 'node:perf_hooks';

const origin = 'https://openapi.gmgn.ai';
const route = '/v1/trade/swap';
const sol = 'So11111111111111111111111111111111111111112';
const fields = ['auto_slippage','chain','from_address','input_amount','input_token','is_anti_mev',
  'min_output_amount','output_token','priority_fee','slippage','tip_fee'];
const maxBody = 65536;
const integer = value => typeof value === 'string' && /^[1-9][0-9]{0,19}$/.test(value) &&
  BigInt(value) <= 18446744073709551615n;

class BoundaryError extends Error {
  constructor(code, httpStatus = null, apiCode = null) {
    super(code); this.httpStatus = httpStatus; this.apiCode = apiCode;
  }
}
const fail = code => {throw new BoundaryError(code);};

function address(value) {
  const alphabet = '123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz';
  if (typeof value !== 'string' || value.length < 32 || value.length > 44) return false;
  let n = 0n;
  for (const ch of value) {const digit = alphabet.indexOf(ch); if (digit < 0) return false; n=n*58n+BigInt(digit);}
  let bytes = 0; for (; n; n >>= 8n) bytes++;
  return bytes + (value.match(/^1*/)[0].length) === 32;
}

// Parse the entire bounded document, rejecting duplicates even in unused API
// metadata. No normal JSON.parse() may erase a conflicting order identity.
export function parseUniqueJSON(text) {
  if (typeof text !== 'string' || Buffer.byteLength(text) > maxBody) fail('RESPONSE_INVALID');
  let i=0;
  const space = () => {while (/[ \t\r\n]/.test(text[i] ?? '') && i < text.length) i++;};
  const string = () => {
    const start=i++;
    while (i < text.length) {
      const ch=text[i++];
      if (ch === '\\') {i++; continue;}
      if (ch === '"') return JSON.parse(text.slice(start,i));
    }
    fail('RESPONSE_INVALID');
  };
  const value = depth => {
    if (depth > 32) fail('RESPONSE_INVALID'); space();
    if (text[i] === '"') return string();
    if (text[i] === '{' || text[i] === '[') {
      const object=text[i++] === '{', end=object ? '}' : ']', result=object ? Object.create(null) : [];
      space(); if (text[i] === end) {i++; return result;}
      while (true) {
        space(); let key;
        if (object) {
          if (text[i] !== '"') fail('RESPONSE_INVALID'); key=string(); space();
          if (text[i++] !== ':' || Object.hasOwn(result,key)) fail('RESPONSE_INVALID');
        }
        const item=value(depth+1); if (object) result[key]=item; else result.push(item);
        space(); const ch=text[i++]; if (ch === end) return result;
        if (ch !== ',') fail('RESPONSE_INVALID');
      }
    }
    const start=i; while (i < text.length && !/[ \t\r\n,\]}]/.test(text[i])) i++;
    const result=JSON.parse(text.slice(start,i));
    if (typeof result === 'number' && !Number.isFinite(result)) fail('RESPONSE_INVALID');
    return result;
  };
  try {const result=value(0); space(); if (i !== text.length) fail('RESPONSE_INVALID'); return result;}
  catch {fail('RESPONSE_INVALID');}
}

function payloadFromBytes(text) {
  const payload=parseUniqueJSON(text);
  if (!payload || Array.isArray(payload) || typeof payload !== 'object' ||
      Object.keys(payload).sort().join(',') !== fields.join(',') || payload.chain !== 'sol' ||
      ![payload.from_address,payload.input_token,payload.output_token].every(address) ||
      payload.input_token === payload.output_token ||
      ![payload.input_token,payload.output_token].includes(sol) ||
      !integer(payload.input_amount) || !integer(payload.min_output_amount) ||
      payload.auto_slippage !== false || payload.is_anti_mev !== true ||
      payload.priority_fee !== '0.00001' || payload.tip_fee !== '0.00001' ||
      typeof payload.slippage !== 'number' || !Number.isFinite(payload.slippage) ||
      payload.slippage < 0 || payload.slippage > 1.5) fail('PAYLOAD_INVALID');
  // This runs before any SDK authentication signature. Never change signed bytes.
  if (JSON.stringify(payload) !== text) fail('PAYLOAD_BYTES_INCOMPATIBLE');
  return Object.freeze(payload);
}

export function createSingleSwapTransport(contract, dependencies) {
  // Primitive copies sever mutable caller references. No inherited grant fields.
  const separateResponse = contract && Object.hasOwn(contract,'responseUntilMs');
  if (!contract || Object.keys(contract).sort().join(',') !== (separateResponse ?
      'eventId,expiresAtMs,issuedAtMs,payloadHash,payloadJson,requestId,responseUntilMs,tradeId' :
      'eventId,expiresAtMs,issuedAtMs,payloadHash,payloadJson,requestId,tradeId')) fail('CONTRACT_INVALID');
  const {eventId,requestId,tradeId,payloadJson,payloadHash,issuedAtMs,expiresAtMs}=contract;
  // Older seven-field contracts retain their stricter whole-response deadline.
  // No optional field may change the original SEND freshness window.
  const responseUntilMs = separateResponse ? contract.responseUntilMs : expiresAtMs;
  if (![eventId,requestId,tradeId].every(x=>typeof x === 'string' && /^[A-Za-z0-9_:.-]{1,128}$/.test(x)) ||
      typeof payloadJson !== 'string' || typeof payloadHash !== 'string' || !/^[0-9a-f]{64}$/.test(payloadHash) ||
      createHash('sha256').update(payloadJson).digest('hex') !== payloadHash ||
      !Number.isSafeInteger(issuedAtMs) || !Number.isSafeInteger(expiresAtMs) ||
      issuedAtMs < 0 || expiresAtMs <= issuedAtMs || expiresAtMs-issuedAtMs > 2000 ||
      !Number.isSafeInteger(responseUntilMs) || responseUntilMs < expiresAtMs ||
      responseUntilMs-issuedAtMs > 30000) fail('CONTRACT_INVALID');
  const payload=payloadFromBytes(payloadJson);
  const {OpenApiClient,confirmTrade,config,fetchImpl,readOwnerGate,readCloseOnlyAuthority,
    wallClock=Date.now,monotonicClock=()=>performance.now(),automationEnv=process.env}=dependencies ?? {};
  if (![OpenApiClient,confirmTrade,fetchImpl,readOwnerGate,wallClock,monotonicClock].every(x=>typeof x === 'function'))
    fail('DEPENDENCY_MISSING');
  const startWall=wallClock(), startMono=monotonicClock();
  if (!Number.isFinite(startMono) || !Number.isSafeInteger(startWall)) fail('CLOCK_INVALID');
  const localDeadline=startMono+Math.max(0,expiresAtMs-startWall);
  const responseDeadline=startMono+Math.max(0,responseUntilMs-startWall);
  let lastMono=startMono, lastWall=startWall, consumed=false, attempts=0;
  const verifiedReplies=new WeakSet();
  function check(requireGate=true) {
    // Read the gate before the final clock pair: filesystem latency consumes
    // the original SEND window too. Compute response wait from this same pair.
    const gate=requireGate ? readOwnerGate() : null;
    const wall=wallClock(), mono=monotonicClock();
    if (!Number.isSafeInteger(wall) || !Number.isFinite(mono) || wall < issuedAtMs ||
        wall < lastWall || mono < lastMono) fail('CLOCK_INVALID');
    lastMono=mono; lastWall=wall;
    const wallDeadline=requireGate ? expiresAtMs : responseUntilMs;
    const monoDeadline=requireGate ? localDeadline : responseDeadline;
    if (wall >= wallDeadline || mono >= monoDeadline) fail('DEADLINE_EXPIRED');
    if (requireGate) {
      if (!gate || gate.valid !== true) fail('OWNER_GATE_INVALID');
      if (gate.value !== 'ON' && !(gate.value === 'OFF' && payload.output_token === sol &&
          payload.input_token !== sol && typeof readCloseOnlyAuthority === 'function' &&
          readCloseOnlyAuthority(Object.freeze({eventId,requestId,tradeId,payloadHash,
            wallet:payload.from_address,mint:payload.input_token,amount:payload.input_amount})) === true))
        fail('OWNER_GATE_OFF');
    }
    return Math.min(responseUntilMs-wall,responseDeadline-mono);
  }
  async function guardedFetch(input, options) {
    if (attempts) fail('SEND_ALREADY_STARTED');
    if (typeof input !== 'string') fail('REQUEST_INVALID');
    const url=new URL(input), entries=[...url.searchParams];
    const headers=options.headers;
    if (url.origin !== origin || url.pathname !== route || url.username || url.password || url.hash ||
        options.method !== 'POST' || options.body !== payloadJson || entries.length !== 2 ||
        new Set(entries.map(([k])=>k)).size !== 2 ||
        entries.some(([k,v])=>k === 'timestamp' ? !/^[1-9][0-9]{9}$/.test(v) ||
          Math.abs(Number(v)-Math.floor(wallClock()/1000)) > 1 :
          k === 'client_id' ? !/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/.test(v) : true) ||
        !headers || Object.keys(headers).sort().join(',') !== 'Content-Type,User-Agent,X-APIKEY,X-Signature' ||
        headers['Content-Type'] !== 'application/json' ||
        !Object.values(headers).every(x=>typeof x === 'string' && x.length > 0 && x.length <= 8192 && !/[\r\n]/.test(x)))
      fail('REQUEST_INVALID');
    const remaining=check();
    const abort=new AbortController(); let timer, reader;
    const timeout=new Promise((_,reject)=>{timer=setTimeout(()=>{
      abort.abort(); reject(new BoundaryError('DEADLINE_EXPIRED'));
    },remaining);});
    attempts++; // Consumption precedes I/O, including failures and HTTP 429.
    try {
      return await Promise.race([timeout,(async()=>{
        const response=await fetchImpl(input,{method:'POST',headers:{...headers},body:payloadJson,
          redirect:'error',signal:abort.signal});
        if (response.redirected || response.status >= 300 && response.status < 400) fail('REDIRECT_BLOCKED');
        if (response.status !== 200) {
          throw new BoundaryError('HTTP_FAILURE',Number.isInteger(response.status) &&
            response.status >= 100 && response.status <= 599 ? response.status : null);
        }
        if (!response.body || typeof response.body.getReader !== 'function') fail('RESPONSE_INVALID');
        reader=response.body.getReader(); const chunks=[]; let length=0;
        while (true) {
          const {value,done}=await reader.read(); if (done) break;
          if (!(value instanceof Uint8Array) || (length+=value.byteLength) > maxBody) fail('RESPONSE_TOO_LARGE');
          chunks.push(value); check(false);
        }
        check(false);
        const text=new TextDecoder('utf-8',{fatal:true}).decode(Buffer.concat(chunks,length));
        const body=parseUniqueJSON(text);
        if (!body || typeof body !== 'object' || Array.isArray(body) || body.code !== 0) {
          throw new BoundaryError('API_FAILURE',200,Number.isSafeInteger(body?.code) &&
            body.code >= 0 && body.code <= 999999999 ? body.code : null);
        }
        const orderId=body.data?.order_id;
        if (typeof orderId !== 'string' || !/^[A-Za-z0-9_-]{1,128}$/.test(orderId)) fail('ORDER_ID_INVALID');
        check(false); const reply=Object.freeze({order_id:orderId}); verifiedReplies.add(reply); return reply;
      })()]);
    } finally {
      clearTimeout(timer); abort.abort();
      // A hung cancel must not extend the single fixed deadline.
      if (reader) Promise.resolve(reader.cancel()).catch(()=>{});
    }
  }
  class GuardedClient extends OpenApiClient {
    // SDK may construct curlStr in RAM; its raw fetch/parser/output never run,
    // even when GMGN_DEBUG is enabled.
    async doFetch(method, _path, url, headers, body, _curlStr) {
      return guardedFetch(url,{method,headers,body});
    }
    async parseResponse(_method, _path, response, _curlStr) {
      if (!verifiedReplies.has(response)) fail('RESPONSE_INVALID'); return response;
    }
  }
  return Object.freeze({
    async dispatch() {
      if (consumed) return {state:'BLOCKED',reason:'CONTRACT_CONSUMED'};
      consumed=true;
      try {
        check();
        if (automationEnv?.GMGN_ALLOW_AUTOMATED_TRADES !== '1') fail('AUTOMATION_CONFIRMATION_MISSING');
        // Future host derives opt-in from the owner's ON; no CLI/TTY bypass.
        confirmTrade({action:'SWAP',lines:[]},true);
        check(); const client=new GuardedClient(config);
        check(); const response=await client.swap(payload);
        if (!verifiedReplies.has(response)) fail('RESPONSE_INVALID');
        check(false); return {state:'ACKNOWLEDGED',order_id:response.order_id,eventId,requestId,tradeId,payloadHash};
      } catch (error) {
        const safe=error instanceof BoundaryError ? error : new BoundaryError('TRANSPORT_FAILURE');
        return {state:attempts ? 'UNKNOWN' : safe.message === 'OWNER_GATE_OFF' ? 'NO_BROADCAST' : 'BLOCKED',reason:safe.message,
          http_status:safe.httpStatus,api_code:safe.apiCode};
      }
    }
  });
}
