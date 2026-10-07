// Pure fake transport: no SDK config/credentials, no network, no broker.
import assert from 'node:assert/strict';
import {parseQuoteArgs,quoteFetchGuard,safeFailure} from '../TOOLS/GMGN-SignedQuoteProbe.mjs';
const args=['order','quote','--chain','sol','--from','So11111111111111111111111111111111111111112',
  '--input-token','So11111111111111111111111111111111111111112',
  '--output-token','EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v',
  '--amount','10000000','--slippage','1','--raw'];
const query=parseQuoteArgs(args);
const url=new URL('https://openapi.gmgn.ai/v1/trade/quote');
Object.entries({...query,timestamp:'1790920000',client_id:'a1111111-1111-4111-8111-111111111111'})
  .forEach(([k,v])=>url.searchParams.set(k,v));
for (const bad of [['swap'],args.concat('--raw'),args.concat('--from','other'),
                  args.slice(0,-1),args.map(x=>x==='1'?'2':x)]) {
  assert.throws(()=>parseQuoteArgs(bad),/GMGN_COMMAND_BLOCKED/);
}
let count=0;
const guard=quoteFetchGuard(async(input,options)=>{
  count++;
  assert.equal(options.redirect,'error');
  assert.equal(options.method,'GET');
  return new Response('{"code":0,"data":{}}',{status:200});
},query);
for (const [input,options] of [
  [url.href,{method:'POST'}],[url.href,{method:'GET',body:'secret'}],
  [url.href.replace('openapi.gmgn.ai','other.example'),{method:'GET'}],
  [url.href.replace('/quote','/swap'),{method:'GET'}],
  [url.href+'&input_amount=1',{method:'GET'}],
  [url.href.replace('10000000','10000001'),{method:'GET'}],
  [url.href+'#fragment',{method:'GET'}],
  [new Request(url),{method:'GET'}],
  [url.href+'&extra=1',{method:'GET'}]
]) await assert.rejects(()=>guard(input,options),/GMGN_PROBE_TRANSPORT_BLOCKED/);
assert.equal(count,0);
await guard(url.href,{method:'GET',headers:{'X-APIKEY':'fake-fixture'}});
await assert.rejects(()=>guard(url.href,{method:'GET'}),/GMGN_PROBE_RETRY_BLOCKED/);
assert.equal(count,1);
let redirects=0;
const redirect=quoteFetchGuard(async()=>{redirects++;return new Response('',{status:302});},query);
await assert.rejects(()=>redirect(url.href,{method:'GET'}),/GMGN_PROBE_REDIRECT_BLOCKED/);
assert.equal(redirects,1);
await assert.rejects(()=>redirect(url.href,{method:'GET'}),/GMGN_PROBE_RETRY_BLOCKED/);
const error={status:429,apiCode:429001,apiError:'RATE_LIMIT_EXCEEDED',resetAtUnix:32,
  apiMessage:'SECRET',message:'SECRET',upgradeUrl:'SECRET',stack:'SECRET'};
assert.equal(safeFailure(error,1000),'GMGN_QUOTE_PROBE_FAILED HTTP 429 code=429001 RATE_LIMIT_EXCEEDED (~31s remaining)');
assert.equal(safeFailure({status:'SECRET',apiCode:'SECRET',apiError:'SECRET'}),'GMGN_QUOTE_PROBE_FAILED');
for (const body of ['<html>SECRET</html>','',null]) {
  const parseGuard=quoteFetchGuard(async()=>body == null ? {
    status:429,headers:new Headers({'x-ratelimit-reset':'32'}),redirected:false,
    text:async()=>{throw Error('SECRET body read failure');}
  } : new Response(body,{status:429,headers:{'x-ratelimit-reset':'32'}}),query);
  let parseError;
  try {await parseGuard(url.href,{method:'GET'});} catch (error) {parseError=error;}
  assert.ok(parseError);
  assert.equal(safeFailure(parseError,1000,parseGuard.evidence),
    body == null ? 'GMGN_QUOTE_PROBE_FAILED HTTP 429 (~999999999s remaining) reset_source=unknown' :
                  'GMGN_QUOTE_PROBE_FAILED HTTP 429 (~31s remaining) reset_source=header');
}
const invalidResetGuard=quoteFetchGuard(async()=>new Response('',{status:429,headers:{'x-ratelimit-reset':'SECRET'}}),query);
await assert.rejects(()=>invalidResetGuard(url.href,{method:'GET'}));
assert.equal(safeFailure(Error('SECRET'),1000,invalidResetGuard.evidence),
  'GMGN_QUOTE_PROBE_FAILED HTTP 429 (~999999999s remaining) reset_source=unknown');
for (const value of ['0','5','1000000000',null,'-1','01','1000000001','5, 5','5 quota_limit=6','SECRET']) {
  const hintHeaders={'x-ratelimit-reset':'32'};
  if (value != null) {hintHeaders['x-ratelimit-limit']=value;hintHeaders['x-ratelimit-remaining']=value;}
  const hintGuard=quoteFetchGuard(async()=>new Response('SECRET',{status:429,headers:hintHeaders}),query);
  await assert.rejects(()=>hintGuard(url.href,{method:'GET'}));
  const expected=['0','5','1000000000'].includes(value) ? Number(value) : null;
  assert.equal(hintGuard.evidence.quotaLimit,expected);
  assert.equal(hintGuard.evidence.quotaRemaining,expected);
  const suffix=expected == null ? '' : ' quota_limit='+expected+' quota_remaining='+expected;
  // Injected SDK error fields never replace HTTP evidence.
  assert.equal(safeFailure({message:'SECRET',quotaLimit:99,quotaRemaining:99},1000,hintGuard.evidence),
    'GMGN_QUOTE_PROBE_FAILED HTTP 429 (~31s remaining)'+suffix+' reset_source=header');
}
assert.equal(safeFailure({quotaLimit:5,quotaRemaining:0}),'GMGN_QUOTE_PROBE_FAILED');
for (const [headers,body,resetAt,source,unknown] of [
  [{}, {code:429,reset_at:32},32,'body',false],
  [{'x-ratelimit-reset':'31'}, {code:429,reset_at:32},32,'header_and_body',false],
  [{'x-ratelimit-reset':'33'}, {code:429,reset_at:32},33,'header_and_body',false],
  [{'x-ratelimit-reset':'SECRET'}, {code:429,reset_at:32},32,'unknown',true],
  [{'x-ratelimit-reset':'32'}, {code:429,reset_at:true},32,'unknown',true],
  [{}, {code:429},null,'unknown',false],
  [{'x-ratelimit-reset':'32'}, {code:0},32,'header',false],
]) {
  const rate=quoteFetchGuard(async()=>new Response(JSON.stringify(body),{status:429,headers}),query);
  await assert.rejects(()=>rate(url.href,{method:'GET'}),/GMGN_QUOTE_RATE_LIMIT/);
  assert.equal(rate.evidence.resetAtUnix,resetAt);
  assert.equal(rate.evidence.resetSource,source);
  assert.equal(rate.evidence.invalidReset,unknown);
}
const apiRate=quoteFetchGuard(async()=>new Response('{"code":429,"reset_at":32}',{status:200}),query);
await assert.rejects(()=>apiRate(url.href,{method:'GET'}),/GMGN_QUOTE_RATE_LIMIT/);
assert.equal(safeFailure(Error('SECRET'),1000,apiRate.evidence),'GMGN_QUOTE_PROBE_FAILED HTTP 200 code=429 (~31s remaining) reset_source=body');
for (const body of ['{"code":429,"reset_at":32,"reset_at":33}','x'.repeat(65537),
    '[{"reset_at":9999999999999}]','[','"SECRET"','123','true','null']) {
  const ambiguous=quoteFetchGuard(async()=>new Response(body,{status:429,headers:{'x-ratelimit-reset':'32'}}),query);
  await assert.rejects(()=>ambiguous(url.href,{method:'GET'}));
  assert.equal(ambiguous.evidence.invalidReset,true);
  assert.equal(safeFailure(Error('SECRET'),1000,ambiguous.evidence),'GMGN_QUOTE_PROBE_FAILED HTTP 429 (~999999999s remaining) reset_source=unknown');
}
assert.throws(()=>parseQuoteArgs(args.map(x=>x==='10000000'?'18446744073709551616':x)),/GMGN_COMMAND_BLOCKED/);
let headerObserved=false;
const hanging=quoteFetchGuard(async()=>new Response(new ReadableStream({cancel(){return new Promise(()=>{});}}),
    {status:429,headers:{'x-ratelimit-reset':'32'}}),query,{onRateHeaders:()=>{headerObserved=true;}});
const began=performance.now();
await assert.rejects(()=>hanging(url.href,{method:'GET'}),/GMGN_QUOTE_RATE_LIMIT/);
assert.ok(headerObserved && performance.now()-began < 1500);
assert.equal(hanging.evidence.invalidReset,true);
// Runtime rate headers never justify another fetch. A shorter provider reset
// cannot shorten Retry-After, and a malformed hint latches UNKNOWN.
const receivedAt=Date.parse('Mon, 05 Oct 2026 12:00:00 GMT')+250;
for (const [retryAfter,reset,bodyReset,expected,unknown,source] of [
  ['33',null,null,Math.ceil(receivedAt/1000)+33,false,'header'],
  ['0',null,null,Math.ceil(receivedAt/1000),false,'header'],
  ['86400',null,null,Math.ceil(receivedAt/1000)+86400,false,'header'],
  ['Mon, 05 Oct 2026 12:00:45 GMT',null,null,receivedAt/1000-.25+45,false,'header'],
  ['Mon, 05 Oct 2026 11:59:59 GMT',null,null,receivedAt/1000-1.25,false,'header'],
  ['33',String(Math.floor(receivedAt/1000)+10),Math.floor(receivedAt/1000)+20,
    Math.ceil(receivedAt/1000)+33,false,'header_and_body'],
  ['10',null,Math.floor(receivedAt/1000)+60,Math.floor(receivedAt/1000)+60,false,'header_and_body'],
  ['33','SECRET',null,Math.ceil(receivedAt/1000)+33,true,'unknown'],
  ...['-1','1.5','01','33, 34','SECRET','86401','9999999999999',
      'Tue, 05 Oct 2026 12:00:45 GMT','Mon, 32 Oct 2026 12:00:45 GMT',
      'Mon, 05 Oct 2026 25:00:45 GMT','2026-10-05','Tue, 06 Oct 2026 12:00:01 GMT']
     .map(value=>[value,null,null,null,true,'unknown']),
]) {
  let fetches=0;
  const rate=quoteFetchGuard(async()=>{
    fetches++;
    return new Response(JSON.stringify({code:429,...(bodyReset == null ? {} : {reset_at:bodyReset})}),
      {status:429,headers:{'retry-after':retryAfter,...(reset == null ? {} : {'x-ratelimit-reset':reset})}});
  },query,{now:()=>receivedAt});
  await assert.rejects(()=>rate(url.href,{method:'GET'}),/GMGN_QUOTE_RATE_LIMIT/);
  assert.equal(rate.evidence.invalidReset,unknown,retryAfter);
  assert.equal(rate.evidence.resetAtUnix,expected,retryAfter);
  assert.equal(rate.evidence.resetSource,source,retryAfter);
  const failure=safeFailure(Error('SECRET'),receivedAt,rate.evidence);
  assert.ok(!failure.includes('SECRET'));
  if (unknown || retryAfter === '86400') assert.ok(failure.includes('~999999999s remaining'));
  else if (retryAfter === '33') assert.ok(failure.includes('~34s remaining'));
  await assert.rejects(()=>rate(url.href,{method:'GET'}),/GMGN_PROBE_RETRY_BLOCKED/);
  assert.equal(fetches,1);
}
console.log('signed quote transport checks passed; actual fake fetches=1');
