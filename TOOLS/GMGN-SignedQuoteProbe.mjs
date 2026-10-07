// Single-attempt quote reader: API authentication, never transaction signing.
// Credential loading remains inside the existing host-side broker child.
import {isAbsolute, join, resolve} from 'node:path';
import {pathToFileURL} from 'node:url';
import {performance} from 'node:perf_hooks';
import {parseUniqueJSON} from './GMGN-SingleSwapTransport.mjs';

const origin = 'https://openapi.gmgn.ai';
const route = '/v1/trade/quote';
const optionNames = {'--chain':'chain', '--from':'from_address',
  '--input-token':'input_token', '--output-token':'output_token',
  '--amount':'input_amount', '--slippage':'slippage'};
const address = /^[1-9A-HJ-NP-Za-km-z]{32,44}$/;

export function parseQuoteArgs(args) {
  if (args[0] !== 'order' || args[1] !== 'quote') throw Error('GMGN_COMMAND_BLOCKED');
  const query = {}; let raw = false;
  for (let i=2; i<args.length; i++) {
    const key = args[i];
    if (key === '--raw' && !raw) {raw = true; continue;}
    const name = Object.hasOwn(optionNames,key) ? optionNames[key] : null;
    if (!name || Object.hasOwn(query,name) || ++i >= args.length) throw Error('GMGN_COMMAND_BLOCKED');
    query[name] = args[i];
  }
  if (!raw || Object.keys(query).length !== 6 || query.chain !== 'sol' ||
      query.slippage !== '1' || !/^[1-9][0-9]{0,19}$/.test(query.input_amount) || BigInt(query.input_amount) > 18446744073709551615n ||
      ![query.from_address,query.input_token,query.output_token].every(x=>address.test(x)) ||
      query.input_token === query.output_token) throw Error('GMGN_COMMAND_BLOCKED');
  return Object.freeze(query);
}

// Accept only unambiguous delay-seconds or canonical IMF-fixdate. Date.parse
// alone accepts invalid calendars, partial dates and non-HTTP syntax.
function retryAfterReset(value, receivedAt) {
  if (value == null) return null;
  if (/^(?:0|[1-9][0-9]{0,12})$/.test(value)) {
    const seconds=Number(value);
    return seconds <= 86400 ? Math.ceil(receivedAt/1000)+seconds : NaN;
  }
  if (!/^(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun), [0-9]{2} (?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec) [0-9]{4} [0-9]{2}:[0-9]{2}:[0-9]{2} GMT$/.test(value)) return NaN;
  const date=Date.parse(value);
  if (!Number.isFinite(date) || new Date(date).toUTCString() !== value ||
      date-receivedAt > 86400000) return NaN;
  return date/1000;
}

export function quoteFetchGuard(fetchImpl, query, {onRateHeaders=()=>{}, now=Date.now}={}) {
  let attempts = 0; let evidence = Object.freeze({});
  const guard = async (input, options = {}) => {
    if (typeof input !== 'string') throw Error('GMGN_PROBE_TRANSPORT_BLOCKED');
    const url = new URL(input);
    const entries = [...url.searchParams];
    if (url.origin !== origin || url.pathname !== route || url.username || url.password || url.hash ||
        options.method !== 'GET' || options.body != null || entries.length !== 8 ||
        new Set(entries.map(([k])=>k)).size !== 8 ||
        entries.some(([k,v])=>Object.hasOwn(query,k) ? query[k] !== v :
          k === 'timestamp' ? !/^[1-9][0-9]{9}$/.test(v) :
          k === 'client_id' ? !/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/.test(v) : true)) {
      throw Error('GMGN_PROBE_TRANSPORT_BLOCKED');
    }
    if (attempts++) throw Error('GMGN_PROBE_RETRY_BLOCKED');
    const abort=new AbortController();
    const response = await fetchImpl(input, {...options, redirect:'error',signal:abort.signal});
    const receivedAt=now();
    const reset = response.headers.get('x-ratelimit-reset');
    const resetNumber = value => typeof value === 'number' && Number.isSafeInteger(value) && value > 0 ? value :
      typeof value === 'string' && /^[1-9][0-9]{0,12}$/.test(value) && Number.isSafeInteger(Number(value)) ? Number(value) : null;
    let resetAtUnix = resetNumber(reset), invalidReset = reset != null && resetAtUnix == null;
    const retryAfter=response.headers.get('retry-after');
    const retryReset=retryAfterReset(retryAfter,receivedAt);
    invalidReset ||= Number.isNaN(retryReset);
    if (retryReset != null && Number.isFinite(retryReset))
      resetAtUnix=Math.max(resetAtUnix ?? 0,retryReset);
    const headerReset=reset != null || retryAfter != null;
    const hint = name => {
      const value=response.headers.get(name);
      return value != null && /^(?:0|[1-9][0-9]{0,9})$/.test(value) && Number(value) <= 1000000000 ? Number(value) : null;
    };
    evidence = Object.freeze({status:response.status,resetAtUnix,invalidReset,apiCode:null,apiError:null,
      resetSource:invalidReset || resetAtUnix == null ? 'unknown' : 'header',
      quotaLimit:hint('x-ratelimit-limit'),quotaRemaining:hint('x-ratelimit-remaining')});
    if (response.status === 429) onRateHeaders();
    if (response.status >= 300 && response.status < 400 || response.redirected) {
      abort.abort();
      response.body?.cancel().catch(()=>{});
      throw Error('GMGN_PROBE_REDIRECT_BLOCKED');
    }
    let text, envelope;
    try {
      if (!response.body?.getReader) throw Error('BODY_UNAVAILABLE');
      const reader=response.body.getReader(), chunks=[]; let size=0;
      const bodyDeadline=performance.now()+1000;
      try {
        while (true) {
          let timer;
          const timeout=new Promise((_,reject)=>{timer=setTimeout(()=>reject(Error('BODY_TIMEOUT')),
            Math.max(0,bodyDeadline-performance.now()));});
          let item;
          try {item=await Promise.race([reader.read(),timeout]);} finally {clearTimeout(timer);}
          if (performance.now() >= bodyDeadline) throw Error('BODY_TIMEOUT');
          const {done,value}=item; if (done) break;
          size+=value.byteLength;
          if (size > 65536) throw Error('BODY_LIMIT');
          chunks.push(value);
        }
      } catch (error) {
        abort.abort();
        // Cancellation can itself hang. It must not delay the fixed UNKNOWN
        // result or erase the 429 already reported to the host.
        reader.cancel().catch(()=>{});
        throw error;
      } finally {reader.releaseLock();}
      const bytes=new Uint8Array(size); let offset=0;
      for (const chunk of chunks) {bytes.set(chunk,offset);offset+=chunk.byteLength;}
      text=new TextDecoder('utf-8',{fatal:true}).decode(bytes);
      envelope=parseUniqueJSON(text);
      if (!envelope || Array.isArray(envelope) || typeof envelope !== 'object') throw Error('BODY_INVALID');
      if (Object.hasOwn(envelope,'reset_at')) {
        const bodyReset=resetNumber(envelope.reset_at);
        invalidReset ||= bodyReset == null;
        if (bodyReset != null) resetAtUnix=Math.max(resetAtUnix ?? 0,bodyReset);
      }
      const code=typeof envelope.code === 'number' ? envelope.code :
        typeof envelope.code === 'string' && /^(?:0|[1-9][0-9]{0,8})$/.test(envelope.code) ? Number(envelope.code) : null;
      const apiCode=Number.isInteger(code) && code >= 0 && code <= 999999999 ? code : null;
      const apiError=['RATE_LIMIT_EXCEEDED','RATE_LIMIT_BANNED','ERROR_RATE_LIMIT_BLOCKED'].includes(envelope.error) ? envelope.error : null;
      evidence=Object.freeze({...evidence,apiCode,apiError,resetAtUnix,invalidReset,
        resetSource:invalidReset || resetAtUnix == null ? 'unknown' : headerReset && Object.hasOwn(envelope,'reset_at') ? 'header_and_body' :
          Object.hasOwn(envelope,'reset_at') ? 'body' : 'header'});
    } catch {
      // Non-JSON errors still preserve a valid header. Ambiguous JSON or an
      // unavailable/oversized body could hide a conflicting reset: UNKNOWN.
      if (text == null || /^[\[\{"0-9tfn-]/.test(text.trimStart()))
        evidence=Object.freeze({...evidence,invalidReset:true,resetSource:'unknown'});
      if (response.status !== 429) throw Error('GMGN_PROBE_RESPONSE_INVALID');
    }
    if (response.status === 429 || evidence.apiCode === 429 || evidence.apiError)
      throw Error('GMGN_QUOTE_RATE_LIMIT');
    if (response.status !== 200 || evidence.apiCode !== 0) throw Error('GMGN_PROBE_RESPONSE_INVALID');
    return new Response(text,{status:response.status,headers:response.headers});
  };
  Object.defineProperty(guard,'evidence',{get:()=>evidence});
  return guard;
}

export function safeFailure(error, now = Date.now(), evidence = {}) {
  // Never copy message, response, URL, headers, stack or private config.
  const fields = ['GMGN_QUOTE_PROBE_FAILED'];
  const status = Number.isInteger(evidence.status) ? evidence.status : error?.status;
  if (Number.isInteger(status) && status >= 100 && status <= 599) fields.push('HTTP '+status);
  const apiCode=Object.hasOwn(evidence,'apiCode') ? evidence.apiCode : error?.apiCode;
  if (Number.isInteger(apiCode) && apiCode >= 0 && apiCode <= 999999999) fields.push('code='+apiCode);
  const apiError=Object.hasOwn(evidence,'apiError') ? evidence.apiError : error?.apiError;
  if (['RATE_LIMIT_EXCEEDED','RATE_LIMIT_BANNED','ERROR_RATE_LIMIT_BLOCKED'].includes(apiError)) fields.push(apiError);
  const reset = Object.hasOwn(evidence,'resetAtUnix') ? evidence.resetAtUnix : error?.resetAtUnix;
  if (evidence.invalidReset) fields.push('(~999999999s remaining)'); // persistent UNKNOWN cooldown
  else if (Number.isSafeInteger(reset) && reset > 0) {
    const remaining=Math.max(0,Math.ceil(reset-now/1000));
    // The broker parser accepts nine digits; never fall back to 40 seconds.
    fields.push('(~'+(remaining > 86400 ? 999999999 : remaining)+'s remaining)');
  }
  // Hints only from this fetch, never error properties or arbitrary headers.
  for (const [field,token] of [['quotaLimit','quota_limit'],['quotaRemaining','quota_remaining']]) {
    if (Number.isInteger(evidence[field]) && evidence[field] >= 0 && evidence[field] <= 1000000000)
      fields.push(token+'='+evidence[field]);
  }
  if (['header','body','header_and_body','unknown'].includes(evidence.resetSource)) fields.push('reset_source='+evidence.resetSource);
  return fields.join(' ');
}

async function main() {
  const query = parseQuoteArgs(process.argv.slice(2));
  const dist = process.env.IMC_GMGN_SIGNED_PROBE_DIST;
  if (!dist || !isAbsolute(dist) || !/[/\\]node_modules[/\\]gmgn-cli[/\\]dist$/.test(dist))
    throw Error('GMGN_PROBE_MODULE_BLOCKED');
  // The guard is installed before any external module can issue a request.
  const mode=process.env.IMC_GMGN_QUOTE_MODE ?? 'SIGNED_QUOTE_PROBE';
  if (!['EXIST_QUOTE','SIGNED_QUOTE_PROBE'].includes(mode)) throw Error('GMGN_PROBE_MODE_BLOCKED');
  globalThis.fetch = quoteFetchGuard(globalThis.fetch,query,{onRateHeaders:()=>{
    // Fixed, wrapper-bound observation. The host persists provisional UNKNOWN
    // before waiting for completion. No headers, body or credential strings.
    process.stderr.write('GMGN_QUOTE_RATE_OBSERVED\n');
  }});
  delete process.env.GMGN_DEBUG;
  const {OpenApiClient} = await import(pathToFileURL(join(dist,'client','OpenApiClient.js')));
  const {getConfig} = await import(pathToFileURL(join(dist,'config.js')));
  // dotenv may override the inherited environment; pin controls after loading.
  delete process.env.GMGN_DEBUG;
  process.env.GMGN_RATE_LIMIT_AUTO_RETRY_MAX_WAIT_MS = '0';
  const client = new OpenApiClient(getConfig(mode === 'SIGNED_QUOTE_PROBE'));
  let data;
  try { data = mode === 'SIGNED_QUOTE_PROBE' ? await client.authSignedRequest('GET',route,query,null) :
        await client.authExistRequest('GET',route,query); }
  catch (error) { process.stderr.write(safeFailure(error,Date.now(),globalThis.fetch.evidence)); process.exitCode=1; return; }
  const output = JSON.stringify(data);
  if (!output || Buffer.byteLength(output,'utf8') > 1048576) throw Error('GMGN_PROBE_OUTPUT_BLOCKED');
  process.stdout.write(output);
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  main().catch(error=>{process.stderr.write(safeFailure(error)); process.exitCode=1;});
}
