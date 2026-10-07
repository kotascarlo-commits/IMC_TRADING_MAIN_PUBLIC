// Installed SDK code + freshly generated synthetic API key; never getConfig().
// All sends terminate in the fake fetch below. Global fetch is an absolute trap.
import assert from 'node:assert/strict';
import {generateKeyPairSync,createHash} from 'node:crypto';
import {readFileSync} from 'node:fs';
import {join} from 'node:path';
import {pathToFileURL} from 'node:url';
import {performance} from 'node:perf_hooks';
import {createSingleSwapTransport,parseUniqueJSON} from '../TOOLS/GMGN-SingleSwapTransport.mjs';

const [scenario,dist]=process.argv.slice(2);
globalThis.fetch=async()=>{throw Error('GLOBAL_NETWORK_FORBIDDEN');};
const {OpenApiClient}=await import(pathToFileURL(join(dist,'client/OpenApiClient.js')));
const {confirmTrade}=await import(pathToFileURL(join(dist,'confirm.js')));
const marker='NEVER_OUTPUT_SYNTHETIC_SECRET';
const privateKeyPem=generateKeyPairSync('ed25519').privateKey.export({type:'pkcs8',format:'pem'});
process.env.GMGN_DEBUG='1';
process.env.GMGN_ALLOW_AUTOMATED_TRADES='1'; // isolated test process + synthetic config only
const input=readFileSync(0,'utf8').trim();
const supplied=['pythonContract','pythonContractLateAck'].includes(scenario) ? JSON.parse(input) : null;
const payloadJson=supplied?.payloadJson ?? input;
const now=supplied?.issuedAtMs ?? Date.now(); let wall=now, mono=1, gate='ON', confirmations=0, authCalls=0, calls=0, gateReads=0;
const contract=supplied ?? {eventId:'BUY:fixture',requestId:'fixture-request',tradeId:'fixture',payloadJson,
  payloadHash:createHash('sha256').update(payloadJson).digest('hex'),issuedAtMs:now-1,expiresAtMs:now+1500};
const expectedOrder=supplied ? 'fixture_'+contract.requestId : 'fixture-order';
let beforeAuth=()=>{}, beforeFetch=()=>{}, response=()=>new Response(
  JSON.stringify({code:0,data:{order_id:expectedOrder,status:'filled',token_amount:'999999'},message:marker}),{status:200});
const statuses={'http429':429,'http500':500,'http503':503,'redirect':302};
if (Object.hasOwn(statuses,scenario)) response=()=>new Response('{"code":0,"data":{"order_id":"fixture-order"}}',
  {status:statuses[scenario]});
const bodies={
  empty:'', invalid:'{', html:'<html>'+marker+'</html>', duplicateCode:'{"code":0,"code":1,"data":{"order_id":"fixture-order"}}',
  duplicateOrder:'{"code":0,"data":{"order_id":"a","order_id":"b"}}',
  escapedDuplicate:'{"code":0,"data":{"order_id":"a","order_\\u0069d":"b"}}',
  nestedDuplicate:'{"code":0,"data":{"order_id":"a"},"other":{"k":1,"k":2}}',
  boolCode:'{"code":false,"data":{"order_id":"a"}}', stringCode:'{"code":"0","data":{"order_id":"a"}}',
  apiError:'{"code":429001,"message":"'+marker+'","data":null}',
  noOrder:'{"code":0,"data":{}}', badOrder:'{"code":0,"data":{"order_id":"'+marker+'\\n"}}',
  longOrder:'{"code":0,"data":{"order_id":"'+'a'.repeat(129)+'"}}',
  nonfinite:'{"code":0,"data":{"order_id":"a"},"x":1e999}',
  deep:'{"code":0,"data":{"order_id":"a"},"x":'+'['.repeat(34)+'0'+']'.repeat(34)+'}'
};
if (Object.hasOwn(bodies,scenario)) response=()=>new Response(bodies[scenario],{status:200});
if (scenario === 'oversize') response=()=>new Response('x'.repeat(65537),{status:200});
if (scenario === 'invalidUtf8') response=()=>new Response(new Uint8Array([255]),{status:200});
if (scenario === 'network') response=()=>{throw Error(marker);};
if (scenario === 'off') gate='OFF';
if (scenario === 'invalidGate') gate=null;
if (scenario === 'missingAutomation') delete process.env.GMGN_ALLOW_AUTOMATED_TRADES;
if (scenario === 'expired') {contract.issuedAtMs=now-1501; contract.expiresAtMs=now-1;}
if (scenario === 'lateGate') beforeAuth=()=>{gate='OFF';};
if (scenario === 'gateOffAfterSend') beforeFetch=()=>{gate='OFF';};
if (scenario === 'wallExpiredAfterSend') beforeFetch=()=>{wall=contract.expiresAtMs;};
if (scenario === 'monoExpiredAfterSend') beforeFetch=()=>{mono=2000;};
if (scenario === 'timeoutFetch' || scenario === 'timeoutStream') {
  contract.expiresAtMs=now+30;
  response=scenario === 'timeoutFetch' ? options=>new Promise((_,reject)=>{
    options.signal.addEventListener('abort',()=>reject(Error(marker)),{once:true});
  }) : ()=>new Response(new ReadableStream({start(){}}),{status:200});
}
const responseScenarios=['lateAck','lateAckGateOff','lateSend','responseWallExpired','responseMonoExpired',
  'responseBudgetAtIntake','responseWindowShort','responseWindowLong','responseWindowFloat',
  'responseWindowBool','responseWindowUnsafe','responseWindowExtra','responseWindowUndefined',
  'realLateAck','realLateSend','realResponseTimeout','sendWallBoundary','sendMonoBoundary'];
if (responseScenarios.includes(scenario)) contract.responseUntilMs=now+6000;
if (['lateAck','lateAckGateOff','pythonContractLateAck'].includes(scenario)) beforeFetch=()=>{
  wall=contract.expiresAtMs+1; mono=contract.expiresAtMs-now+2;
  if (scenario === 'lateAckGateOff') gate='OFF';
};
if (scenario === 'lateSend') beforeAuth=()=>{wall=contract.expiresAtMs+1;};
if (scenario === 'responseWallExpired') beforeFetch=()=>{wall=contract.responseUntilMs;};
if (scenario === 'responseMonoExpired') beforeFetch=()=>{mono=contract.responseUntilMs-now+1;};
if (scenario === 'responseBudgetAtIntake') {
  contract.expiresAtMs=now+100; contract.responseUntilMs=now+1600; wall=now+50;
  beforeFetch=()=>{mono=1551;}; // Original response deadline, not intake + 1600ms.
}
if (scenario === 'wallRollbackAboveStart') {
  beforeAuth=()=>{wall=now+500;};
  beforeFetch=()=>{wall=now+250;}; // Still above start, below last checked wall time.
}
if (scenario === 'responseWindowShort') contract.responseUntilMs=contract.expiresAtMs-1;
if (scenario === 'responseWindowLong') contract.responseUntilMs=contract.issuedAtMs+30001;
if (scenario === 'responseWindowFloat') contract.responseUntilMs+=.5;
if (scenario === 'responseWindowBool') contract.responseUntilMs=true;
if (scenario === 'responseWindowUnsafe') contract.responseUntilMs=Number.MAX_SAFE_INTEGER+1;
if (scenario === 'responseWindowExtra') contract.responseDeadline=contract.responseUntilMs;
if (scenario === 'responseWindowUndefined') contract.responseUntilMs=undefined;
if (['realLateAck','realLateSend','realResponseTimeout'].includes(scenario)) {
  contract.expiresAtMs=now+200; contract.responseUntilMs=now+1200;
  if (scenario === 'realLateAck') response=()=>new Promise(resolve=>setTimeout(()=>resolve(new Response(
    '{"code":0,"data":{"order_id":"fixture-order"}}',{status:200})),300));
  if (scenario === 'realLateSend') beforeAuth=()=>new Promise(resolve=>setTimeout(resolve,250));
  if (scenario === 'realResponseTimeout') {
    contract.responseUntilMs=now+350;
    response=options=>new Promise((_,reject)=>options.signal.addEventListener('abort',()=>reject(Error(marker)),{once:true}));
  }
}
class TestSdk extends OpenApiClient {
  async authSignedRequest(...args) {
    authCalls++; await beforeAuth();
    if (['wrongQuery','extraHeader','wrongBody','wrongRoute','wrongMethod'].includes(scenario)) {
      const url=new URL('https://openapi.gmgn.ai/v1/trade/swap');
      url.searchParams.set('timestamp',String(Math.floor(wall/1000)));
      url.searchParams.set('client_id','a1111111-1111-4111-8111-111111111111');
      const headers={'X-APIKEY':marker,'X-Signature':marker,'User-Agent':'gmgn-cli/test','Content-Type':'application/json'};
      if (scenario === 'wrongQuery') url.searchParams.append('input_amount','1');
      if (scenario === 'extraHeader') headers['X-Extra']=marker;
      if (scenario === 'wrongRoute') url.pathname='/v1/trade/quote';
      return this.doFetch(scenario === 'wrongMethod' ? 'GET' : 'POST','/v1/trade/swap',url.href,headers,
        scenario === 'wrongBody' ? '{}' : payloadJson,marker);
    }
    if (scenario === 'sdkDoubleSend') {await super.authSignedRequest(...args);}
    if (scenario === 'signatureError') throw Error(marker);
    return super.authSignedRequest(...args);
  }
}
const dependencies={OpenApiClient:TestSdk,config:{apiKey:marker,privateKeyPem,host:'https://openapi.gmgn.ai'},
  confirmTrade:(summary,yes)=>{confirmations++; assert.equal(yes,true); confirmTrade(summary,yes);
    if (scenario === 'confirmationError') throw Error(marker);
    if (scenario === 'wallRollback') wall--;
    if (scenario === 'monoRollback') mono--;
  },wallClock:()=>wall,monotonicClock:()=>mono,
  readOwnerGate:()=>{
    gateReads++;
    if (gateReads === 4 && scenario === 'sendWallBoundary') wall=contract.expiresAtMs+1;
    if (gateReads === 4 && scenario === 'sendMonoBoundary') mono=contract.expiresAtMs-now+1;
    return gate === null ? {valid:false,value:'OFF'} : {valid:true,value:gate};
  },
  fetchImpl:async(input,options)=>{
    calls++; beforeFetch(); assert.equal(options.redirect,'error'); assert.equal(options.method,'POST');
    assert.equal(options.body,payloadJson); assert.ok(options.signal instanceof AbortSignal);
    assert.equal(new URL(input).pathname,'/v1/trade/swap'); return response(options);
  }};
if (scenario === 'wrongHost') dependencies.config.host='https://example.invalid';
if (['realLateAck','realLateSend','realResponseTimeout','pythonContract'].includes(scenario)) {
  dependencies.wallClock=Date.now; dependencies.monotonicClock=()=>performance.now();
}
if (scenario === 'missingConfirmation') delete dependencies.confirmTrade;
if (scenario === 'missingConfirmation') {
  assert.throws(()=>createSingleSwapTransport(contract,dependencies),/DEPENDENCY_MISSING/);
} else if (scenario === 'incompatibleBytes') {
  contract.payloadJson=payloadJson.replace('"slippage":1','"slippage":1.0');
  contract.payloadHash=createHash('sha256').update(contract.payloadJson).digest('hex');
  assert.throws(()=>createSingleSwapTransport(contract,dependencies),/PAYLOAD_BYTES_INCOMPATIBLE/);
} else if (scenario === 'invalidPayload' || scenario === 'badHash') {
  if (scenario === 'invalidPayload') {
    contract.payloadJson=JSON.stringify({...JSON.parse(payloadJson),auto_fee:true});
    contract.payloadHash=createHash('sha256').update(contract.payloadJson).digest('hex');
  } else contract.payloadHash='a'.repeat(64);
  assert.throws(()=>createSingleSwapTransport(contract,dependencies),/PAYLOAD_INVALID|CONTRACT_INVALID/);
} else if (['responseWindowShort','responseWindowLong','responseWindowFloat','responseWindowBool',
  'responseWindowUnsafe','responseWindowExtra','responseWindowUndefined'].includes(scenario)) {
  assert.throws(()=>createSingleSwapTransport(contract,dependencies),/CONTRACT_INVALID/);
  assert.equal(confirmations+authCalls+calls,0);
} else {
  const transport=createSingleSwapTransport(contract,dependencies);
  if (scenario === 'mutation') {contract.payloadJson='{}';contract.eventId='MUTATED';contract.expiresAtMs=now+999999;}
  let result;
  if (scenario === 'parallel') {
    const both=await Promise.all([transport.dispatch(),transport.dispatch()]);
    result=both[0]; assert.deepEqual(both[1],{state:'BLOCKED',reason:'CONTRACT_CONSUMED'});
  } else result=await transport.dispatch();
  const success=['success','parallel','mutation','gateOffAfterSend','pythonContract','pythonContractLateAck',
    'lateAck','lateAckGateOff','realLateAck'].includes(scenario);
  const noSend=['off','invalidGate','missingAutomation','expired','lateGate','wallRollback','monoRollback','wrongHost',
    'wrongQuery','extraHeader','wrongBody','wrongRoute','wrongMethod','signatureError','confirmationError',
    'lateSend','realLateSend','sendWallBoundary','sendMonoBoundary'].includes(scenario);
  if (success) {
    assert.equal(result.state,'ACKNOWLEDGED'); assert.equal(result.order_id,expectedOrder);
    assert.equal(result.eventId,supplied?.eventId ?? 'BUY:fixture'); assert.equal(result.tradeId,supplied?.tradeId ?? 'fixture');
    assert.equal(result.requestId,supplied?.requestId ?? 'fixture-request');
    assert.equal(Object.hasOwn(result,'fill'),false);
    if (scenario === 'realLateAck') assert.ok(Date.now() >= contract.expiresAtMs);
  } else {
    assert.equal(result.state,scenario === 'off' || scenario === 'lateGate' ? 'NO_BROADCAST' : noSend ? 'BLOCKED' : 'UNKNOWN');
    assert.equal(Object.hasOwn(result,'order_id'),false);
    if (scenario === 'apiError') assert.equal(result.api_code,429001);
    if (Object.hasOwn(statuses,scenario) && scenario !== 'redirect') assert.equal(result.http_status,statuses[scenario]);
    assert.equal(JSON.stringify(result).includes(marker),false);
  }
  assert.equal(calls,noSend ? 0 : 1);
  if (['off','invalidGate','missingAutomation','expired'].includes(scenario)) {
    assert.equal(authCalls,0); assert.equal(confirmations,0);
  }
  assert.deepEqual(await transport.dispatch(),{state:'BLOCKED',reason:'CONTRACT_CONSUMED'});
  assert.equal(calls,noSend ? 0 : 1);
  if (supplied) console.log('SDK_RESULT '+JSON.stringify(result));
}
assert.throws(()=>parseUniqueJSON('{"__proto__":0,"__proto__":1}'),/RESPONSE_INVALID/);
assert.throws(()=>parseUniqueJSON('\u00a0{"code":0}'),/RESPONSE_INVALID/);
console.log('synthetic SDK boundary passed: '+scenario+'; fake fetches='+calls);
