// Preload used only by the native, source-pinned READ broker. The installed
// CLI initializes its own provider configuration; the parent never reads it.
import {dirname, isAbsolute, join} from 'node:path';
import {pathToFileURL} from 'node:url';
import {fileURLToPath} from 'node:url';
import {realpathSync} from 'node:fs';
import {registerHooks, isBuiltin} from 'node:module';
import {parseQuoteArgs, quoteFetchGuard, safeFailure} from './GMGN-SignedQuoteProbe.mjs';

export function restrictModuleGeneration(paths) {
  if(!Array.isArray(paths)||!paths.length||paths.length>2048||
     paths.some(p=>typeof p!=='string'||!isAbsolute(p)))
    throw Error('GMGN_READ_SOURCE_POLICY_INVALID');
  const canonical=p=>realpathSync.native(p).toLowerCase();
  const approved=new Set(paths.map(canonical));
  const check=url=>{
    if(typeof url!=='string')throw Error('GMGN_READ_MODULE_BLOCKED');
    if(isBuiltin(url))return;
    if(!url.startsWith('file:'))throw Error('GMGN_READ_MODULE_BLOCKED');
    const parsed=new URL(url);
    if(parsed.search||parsed.hash||!approved.has(canonical(fileURLToPath(parsed))))
      throw Error('GMGN_READ_MODULE_BLOCKED');
  };
  // Synchronous hooks also cover require/createRequire. Resolve rejects added
  // shadow packages before their code runs; load checks the final URL again.
  registerHooks({
    resolve(specifier,context,nextResolve){
      const result=nextResolve(specifier,context);check(result.url);return result;
    },
    load(url,context,nextLoad){check(url);return nextLoad(url,context);}
  });
}

export function enforceReadControls() {
  delete process.env.GMGN_DEBUG;
  process.env.GMGN_RATE_LIMIT_AUTO_RETRY_MAX_WAIT_MS='0';
}

export function oneReadFetch(fetchImpl) {
  let attempts=0;
  return async (input,options={})=>{
    enforceReadControls();
    if(typeof input!=='string')throw Error('GMGN_READ_ROUTE_BLOCKED');
    const url=new URL(input);
    if(url.origin!=='https://openapi.gmgn.ai'||url.username||url.password||url.hash||
       !['GET','POST'].includes(options.method))throw Error('GMGN_READ_ROUTE_BLOCKED');
    // The host has already validated the complete CLI command. Independently
    // exclude every transaction, order mutation and withdrawal route here.
    const readRoute=/^\/v1\/(?:token\/(?:info|security|pool_info)|market\/(?:token_top_holders|token_top_traders|token_kline|rank|token_signal|hot_searches|search)|user\/(?:info|wallet_holdings|wallet_activity|wallet_stats|wallet_profits|wallet_token_balance|follow_tokens|follow_token_groups|kol|smartmoney|created_tokens)|trade\/(?:quote|query_order|gas_price|strategy\/orders)|trenches)$/;
    if(!readRoute.test(url.pathname)||options.method==='POST'&&
       !['/v1/trenches','/v1/market/token_signal','/v1/market/hot_searches','/v1/user/wallet_profits'].includes(url.pathname))throw Error('GMGN_READ_ROUTE_BLOCKED');
    if(attempts++)throw Error('GMGN_READ_RETRY_BLOCKED');
    const response=await fetchImpl(input,{...options,redirect:'error'});
    if(response.status===429)process.stderr.write('GMGN_READ_RATE_OBSERVED\n');
    if(response.redirected||response.status>=300&&response.status<400){
      response.body?.cancel().catch(()=>{});throw Error('GMGN_READ_REDIRECT_BLOCKED');
    }
    return response;
  };
}

async function initializeCli() {
  const entry=process.argv[1];
  if(!entry||!isAbsolute(entry)||! /[/\\]node_modules[/\\]gmgn-cli[/\\]dist[/\\]index\.js$/.test(entry))
    throw Error('GMGN_READ_CLI_ENTRY_BLOCKED');
  const args=process.argv.slice(2);
  let paths;
  try{paths=JSON.parse(process.env.IMC_GMGN_PUBLIC_GENERATION);}
  catch{throw Error('GMGN_READ_SOURCE_POLICY_INVALID');}
  delete process.env.IMC_GMGN_PUBLIC_GENERATION;
  restrictModuleGeneration(paths);
  const fetchImpl=globalThis.fetch;
  if(args[0]==='order'&&args[1]==='quote'){
    const quoteGuard=quoteFetchGuard(async(input,options)=>{
      enforceReadControls();return fetchImpl(input,options);
    },parseQuoteArgs(args),{onRateHeaders:()=>process.stderr.write('GMGN_QUOTE_RATE_OBSERVED\n')});
    globalThis.fetch=async(input,options)=>{
      try{return await quoteGuard(input,options);}
      catch(error){
        process.stderr.write(safeFailure(error,Date.now(),quoteGuard.evidence)+'\n');
        throw Error('GMGN_READ_QUOTE_FAILED');
      }
    };
  }else globalThis.fetch=oneReadFetch(fetchImpl);
  // Module initialization loads dotenv before controls are sealed. Import is
  // inside the existing broker's READ child; no getConfig call or key export.
  await import(pathToFileURL(join(dirname(entry),'config.js')));
  enforceReadControls();
}

// A --import preload is distinct from ordinary test/module imports. The
// native factory supplies this exact source-pinned module as its sole preload.
if(process.execArgv.includes('--import')&&process.execArgv.some((arg,index)=>
  index>0&&process.execArgv[index-1]==='--import'&&(arg.startsWith('file:')?new URL(arg).href:pathToFileURL(arg).href)===import.meta.url)){
  await initializeCli();
}
