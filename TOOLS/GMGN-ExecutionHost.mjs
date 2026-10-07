// Production entry to the existing single-swap boundary; no CLI trade fallback.
import {readFileSync} from 'node:fs';
import {join, resolve} from 'node:path';
import {pathToFileURL} from 'node:url';
import {createInterface} from 'node:readline';
import {performance} from 'node:perf_hooks';
import {createSingleSwapTransport, parseUniqueJSON} from './GMGN-SingleSwapTransport.mjs';

export function readGate(path) {
  try {
    const body=readFileSync(path);
    if (body.length > 1024) throw Error();
    const gate=parseUniqueJSON(body.toString('utf8').replace(/^\uFEFF/,''));
    if (Object.keys(gate).sort().join(',') !== 'OWNER_BROADCAST_GATE,schema_version' ||
        gate.schema_version !== 1 || !['OFF','ON'].includes(gate.OWNER_BROADCAST_GATE)) throw Error();
    return {valid:true,value:gate.OWNER_BROADCAST_GATE};
  } catch { return {valid:false,value:'OFF'}; }
}

export async function runExecutionHost({gatePath, loadSDK}) {
  const lines=createInterface({input:process.stdin,crlfDelay:Infinity});
  const iterator=lines[Symbol.asyncIterator]();
  const first=await iterator.next();
  if (first.done || Buffer.byteLength(first.value) > 65536) throw Error('HOST_INPUT_INVALID');
  const contract=parseUniqueJSON(first.value);
  const payload=parseUniqueJSON(contract.payloadJson);
  const sol='So11111111111111111111111111111111111111112';
  const isSell=payload.output_token === sol && payload.input_token !== sol;
  let closeOnlyGranted=false;
  const startWall=Date.now(), startMono=performance.now();
  const deadline=startMono+Math.max(0,contract.expiresAtMs-startWall);
  let lastWall=startWall,lastMono=startMono;
  function fresh(beforeConfig=false) {
    const gate=readGate(gatePath), wall=Date.now(), mono=performance.now();
    if (!gate.valid || (gate.value !== 'ON' && !(gate.value === 'OFF' && isSell &&
        (closeOnlyGranted || beforeConfig))) || wall < lastWall || mono < lastMono ||
        wall < contract.issuedAtMs || wall >= contract.expiresAtMs || mono >= deadline)
      throw Error('HOST_PREFLIGHT_BLOCKED');
    lastWall=wall;lastMono=mono;
  }
  async function verify(phase) {
    fresh(phase === 'CONFIG');
    process.stdout.write(phase+'\n');
    const reply=await iterator.next();
    if (reply.done || !['ALLOW','ALLOW_EXIT'].includes(reply.value) ||
        reply.value === 'ALLOW_EXIT' && !isSell) throw Error('HOST_PREFLIGHT_BLOCKED');
    closeOnlyGranted=reply.value === 'ALLOW_EXIT';
    fresh();
  }
  try {
    const gate=readGate(gatePath);
    if (!gate.valid || gate.value !== 'ON' && !isSell)
      return {state:'NO_BROADCAST',reason:'OWNER_GATE_OFF_OR_INVALID'};
    await verify('CONFIG'); // Before the SDK config module can load any credentials.
    const dependencies=await loadSDK();
    fresh();
    // Opt-in follows Owner ON or the authenticated exit of an owner-opened trade.
    delete process.env.GMGN_DEBUG;
    process.env.GMGN_ALLOW_AUTOMATED_TRADES='1';
    process.env.GMGN_RATE_LIMIT_AUTO_RETRY_MAX_WAIT_MS='0';
    const fetchImpl=dependencies.fetchImpl;
    const transport=createSingleSwapTransport(contract,{...dependencies,
      readOwnerGate:()=>readGate(gatePath),automationEnv:process.env,
      readCloseOnlyAuthority:grant=>closeOnlyGranted && isSell &&
        ['eventId','requestId','tradeId','payloadHash'].every(key=>grant[key] === contract[key]) &&
        grant.wallet === payload.from_address && grant.mint === payload.input_token &&
        grant.amount === payload.input_amount,
      fetchImpl:async(input,options)=>{
        await verify('SEND');
        // No await between the final gate/deadline check and starting fetch.
        fresh();
        const pending=fetchImpl(input,options);
        process.stdout.write('STARTED\n');
        return pending;
      }});
    return await transport.dispatch();
  } finally { lines.close(); }
}

async function main() {
  const [gatePath,dist]=process.argv.slice(2);
  if (!gatePath || !dist || process.argv.length !== 4) throw Error('HOST_ARGUMENT_INVALID');
  const realFetch=globalThis.fetch;
  globalThis.fetch=async()=>{throw Error('HOST_UNGUARDED_FETCH');};
  const result=await runExecutionHost({gatePath,loadSDK:async()=>{
    // SDK diagnostics must never escape to our structured stdout/stderr.
    console.log=console.warn=console.error=()=>{};
    const {OpenApiClient}=await import(pathToFileURL(join(dist,'client/OpenApiClient.js')));
    const {confirmTrade}=await import(pathToFileURL(join(dist,'confirm.js')));
    const {getConfig}=await import(pathToFileURL(join(dist,'config.js')));
    return {OpenApiClient,confirmTrade,config:getConfig(true),fetchImpl:realFetch};
  }});
  process.stdout.write(JSON.stringify(result)+'\n');
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  main().catch(()=>{process.stdout.write('{"state":"UNKNOWN","reason":"HOST_FAILURE"}\n');
    process.exitCode=1;});
}
