const fs=require('fs'),path=require('path'),{spawnSync}=require('child_process');
const {Connection,Keypair,VersionedTransaction,PublicKey}=require('@solana/web3.js');
const bs58mod=require('bs58'),bs58=bs58mod.default||bs58mod;
const ROOT=__dirname,CAND=path.join(ROOT,'v480_calibration_candidate.json');
const MASTER=path.join(ROOT,'IMC_MASTER_MODE.json');
const RESULT=path.join(ROOT,'v480_live_calibration_result.json');
const PENDING=path.join(ROOT,'v480_live_pending.json');
const CFG=JSON.parse(fs.readFileSync(path.join(ROOT,'config_v480.json'),'utf8').replace(/^\uFEFF/,''));
const PUB=JSON.parse(fs.readFileSync(path.join(ROOT,'wallet_public.json'),'utf8').replace(/^\uFEFF/,''));
const args=process.argv.slice(2),approved=args.includes('--execute-approved');
const idArg=args.find(x=>/^--trade-id=/.test(x));
function out(x,code=0){console.log(JSON.stringify(x,null,2));process.exit(code)}
function readJson(f){try{return JSON.parse(fs.readFileSync(f,'utf8').replace(/^\uFEFF/,''))}catch{return null}}
function writePending(x){fs.writeFileSync(PENDING,JSON.stringify({...x,updated_at:new Date().toISOString()},null,2))}
const master=readJson(MASTER);
if(!master||master.live_broadcast!==true) out({state:'MASTER_BROADCAST_DISABLED',broadcast:false},2);
if(!fs.existsSync(CAND)) out({state:'NO_EXECUTABLE_V480_CANDIDATE',broadcast:false},3);
const c=JSON.parse(fs.readFileSync(CAND,'utf8').replace(/^\uFEFF/,''));
const priorResult=fs.existsSync(RESULT)?readJson(RESULT):null;
if(priorResult&&Number(priorResult.trade_id)===Number(c.trade_id)&&priorResult.mint===c.mint&&priorResult.confirmed===true&&(!priorResult.prepared_at||priorResult.prepared_at===c.prepared_at))
 out({state:'LIVE_CALIBRATION_ALREADY_CONFIRMED',trade_id:c.trade_id,signature:priorResult.signature,broadcast:false},0);
if(c.version!=='4.8.0'||c.simulation_passed!==true||!c.prepared_tx_b64)
 out({state:'CANDIDATE_NOT_VALIDATED',trade_id:c.trade_id,broadcast:false},4);
if(Number(c.pre_roundtrip_loss)>Number(CFG.max_calibration_roundtrip_loss))
 out({state:'CANDIDATE_ECONOMICS_MOVED',trade_id:c.trade_id,roundtrip_loss:c.pre_roundtrip_loss,broadcast:false},4);
const requested=idArg?Number(idArg.split('=')[1]):null;
if(!approved||requested!==Number(c.trade_id))
 out({state:'LIVE_CALIBRATION_LOCKED',trade_id:c.trade_id,required:'--execute-approved --trade-id='+c.trade_id,broadcast:false},5);
const ageMs=Date.now()-Date.parse(c.prepared_at);
if(!Number.isFinite(ageMs)||ageMs>120000)
 out({state:'CANDIDATE_TOO_OLD',trade_id:c.trade_id,age_ms:ageMs,broadcast:false},6);
const ps=path.join(ROOT,'decrypt_wallet.ps1');
const r=spawnSync('powershell.exe',['-NoProfile','-ExecutionPolicy','Bypass','-File',ps],
 {encoding:'utf8',windowsHide:true});
if(r.status!==0) out({state:'WALLET_DECRYPT_FAILED',broadcast:false},7);
const raw=Buffer.from(r.stdout.trim(),'base64');
const kp=Keypair.fromSecretKey(Uint8Array.from(raw));raw.fill(0);
if(kp.publicKey.toBase58()!==PUB.address) out({state:'WALLET_MISMATCH',broadcast:false},8);
const conn=new Connection(CFG.rpc_url,'confirmed');
async function tokenRaw(mint){
 const rows=await conn.getParsedTokenAccountsByOwner(kp.publicKey,{mint:new PublicKey(mint)},'confirmed');
 let n=0n; for(const x of rows.value)n+=BigInt(x.account.data.parsed.info.tokenAmount.amount);
 return n;
}
(async()=>{
 const priorPending=fs.existsSync(PENDING)?readJson(PENDING):null;
 if(priorPending&&Number(priorPending.trade_id)===Number(c.trade_id)&&priorPending.mint===c.mint&&priorPending.blockhash===c.blockhash_b58&&priorPending.signature&&priorPending.state!=='CONFIRMED'){
  let chain=null;
  try{chain=(await conn.getSignatureStatuses([priorPending.signature],{searchTransactionHistory:true})).value[0]}catch{}
  const currentHeight=await conn.getBlockHeight('confirmed');
  if(chain){
   out({state:'LIVE_RECONCILE_REQUIRED',trade_id:c.trade_id,signature:priorPending.signature,
        chain_status:chain,broadcast:true,reason:'PRIOR_ATTEMPT_SEEN_ON_CHAIN'},13);
  }
  if(currentHeight<=Number(priorPending.last_valid_block_height||0)){
   out({state:'LIVE_RECONCILE_REQUIRED',trade_id:c.trade_id,signature:priorPending.signature,
        blockHeight:currentHeight,lastValidBlockHeight:priorPending.last_valid_block_height,
        broadcast:'UNKNOWN',reason:'PRIOR_ATTEMPT_STILL_VALID'},13);
  }
  out({state:'LIVE_PRIOR_ATTEMPT_EXPIRED_NOT_SEEN',trade_id:c.trade_id,signature:priorPending.signature,
       broadcast:false,reason:'REPREPARE_REQUIRED_DO_NOT_REUSE_OLD_TX'},14);
 }
 const blockHeight=await conn.getBlockHeight('confirmed');
 if(blockHeight>=Number(c.last_valid_block_height))
  out({state:'BLOCKHASH_EXPIRED',trade_id:c.trade_id,blockHeight,lastValidBlockHeight:c.last_valid_block_height,broadcast:false},9);
 const tx=VersionedTransaction.deserialize(Buffer.from(c.prepared_tx_b64,'base64'));
 tx.sign([kp]);
 let sigsim;
 try{ sigsim=await conn.simulateTransaction(tx,{sigVerify:true,commitment:'confirmed'}); }
 catch(e){
  const msg=String(e.message||e);
  const slip=msg.includes('0x1771')||msg.includes('6001');
  out({state:slip?'SIGNED_SIM_SLIPPAGE_MOVED':'SIGNED_SIM_EXCEPTION',trade_id:c.trade_id,error:msg,broadcast:false},10);
 }
 if(sigsim.value.err)
  out({state:'SIGNED_SIM_FAILED',trade_id:c.trade_id,error:sigsim.value.err,broadcast:false},10);
 const beforeSol=await conn.getBalance(kp.publicKey,'confirmed');
 const beforeTok=await tokenRaw(c.mint);
 const signedSignature=bs58.encode(Buffer.from(tx.signatures[0]));
 let signature=signedSignature,conf;
 writePending({state:'SIGNED_READY',trade_id:c.trade_id,mint:c.mint,side:c.side||'BUY',
  signature:signedSignature,blockhash:c.blockhash_b58,last_valid_block_height:Number(c.last_valid_block_height),
  broadcast:false});
 try{
  writePending({state:'BROADCAST_ATTEMPTING',trade_id:c.trade_id,mint:c.mint,side:c.side||'BUY',
   signature:signedSignature,blockhash:c.blockhash_b58,last_valid_block_height:Number(c.last_valid_block_height),
   broadcast:'UNKNOWN'});
  const rpcSignature=await conn.sendRawTransaction(tx.serialize(),{skipPreflight:false,maxRetries:2});
  if(rpcSignature&&rpcSignature!==signedSignature){
   writePending({state:'SIGNATURE_MISMATCH_RECONCILE',trade_id:c.trade_id,mint:c.mint,side:c.side||'BUY',
    signature:signedSignature,rpc_signature:rpcSignature,blockhash:c.blockhash_b58,
    last_valid_block_height:Number(c.last_valid_block_height),broadcast:'UNKNOWN'});
   out({state:'LIVE_RECONCILE_REQUIRED',trade_id:c.trade_id,signature:signedSignature,
        rpc_signature:rpcSignature,broadcast:'UNKNOWN',reason:'RPC_SIGNATURE_MISMATCH'},12);
  }
  signature=rpcSignature||signedSignature;
  writePending({state:'BROADCAST_ACKNOWLEDGED',trade_id:c.trade_id,mint:c.mint,side:c.side||'BUY',
   signature,blockhash:c.blockhash_b58,last_valid_block_height:Number(c.last_valid_block_height),broadcast:true});
  conf=await conn.confirmTransaction({signature,blockhash:c.blockhash_b58,
   lastValidBlockHeight:Number(c.last_valid_block_height)},'confirmed');
 }catch(e){
  let chain=null;
  try{chain=(await conn.getSignatureStatuses([signedSignature],{searchTransactionHistory:true})).value[0]}catch{}
  writePending({state:'RECONCILE_REQUIRED',trade_id:c.trade_id,mint:c.mint,side:c.side||'BUY',
   signature:signedSignature,blockhash:c.blockhash_b58,last_valid_block_height:Number(c.last_valid_block_height),
   broadcast:chain?true:'UNKNOWN',chain_status:chain,error:String(e.message||e)});
  out({state:'LIVE_RECONCILE_REQUIRED',trade_id:c.trade_id,signature:signedSignature,
       broadcast:chain?true:'UNKNOWN',chain_status:chain,error:String(e.message||e)},12);
 }
 if(conf.value.err){
  writePending({state:'LANDED_FAILED',trade_id:c.trade_id,mint:c.mint,side:c.side||'BUY',
   signature,blockhash:c.blockhash_b58,last_valid_block_height:Number(c.last_valid_block_height),
   broadcast:true,error:conf.value.err});
  out({state:'LIVE_CONFIRM_FAILED',trade_id:c.trade_id,signature,error:conf.value.err,broadcast:true},11);
 }
 const afterSol=await conn.getBalance(kp.publicKey,'confirmed');
 const afterTok=await tokenRaw(c.mint);
 const side=c.side||'BUY';
 const result={state:side==='SELL'?'V480_LIVE_EXIT_CONFIRMED':'V480_LIVE_ENTRY_CONFIRMED',trade_id:c.trade_id,mint:c.mint,side,signature,prepared_at:c.prepared_at,blockhash:c.blockhash_b58,
  prepared_age_ms:ageMs,expected_input_lamports:c.input_lamports,expected_input_token_raw:c.input_token_raw,
  expected_buy_out_raw:c.expected_buy_out_raw,expected_sell_out_lamports:c.expected_sell_out_lamports,
  expected_token_delta_raw:c.expected_token_delta_raw,actual_token_delta_raw:(afterTok-beforeTok).toString(),
  expected_fee_lamports:c.buy_fee_lamports,expected_rent_locked_lamports:c.rent_locked_lamports,
  expected_wallet_delta_lamports:c.expected_wallet_delta_lamports,
  actual_sol_delta_lamports:afterSol-beforeSol,simulation_units:c.simulation_units,
  signed_sim_units:sigsim.value.unitsConsumed,broadcast:true,confirmed:true,recorded_at:new Date().toISOString()};
 writePending({state:'CONFIRMED',trade_id:c.trade_id,mint:c.mint,side,signature,
  blockhash:c.blockhash_b58,last_valid_block_height:Number(c.last_valid_block_height),broadcast:true,confirmed:true});
 fs.writeFileSync(RESULT,JSON.stringify(result,null,2)); out(result,0);
})().catch(e=>{
 const p=fs.existsSync(PENDING)?readJson(PENDING):null;
 const uncertain=!!(p&&Number(p.trade_id)===Number(c.trade_id)&&['BROADCAST_ATTEMPTING','BROADCAST_ACKNOWLEDGED','RECONCILE_REQUIRED','SIGNATURE_MISMATCH_RECONCILE'].includes(p.state));
 out({state:uncertain?'LIVE_RECONCILE_REQUIRED':'LIVE_CALIBRATION_EXCEPTION',trade_id:c.trade_id,
      signature:p&&p.signature,error:String(e.message||e),broadcast:uncertain?'UNKNOWN':false},12);
});
