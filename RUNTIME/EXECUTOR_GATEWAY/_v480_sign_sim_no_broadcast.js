const fs=require('fs'),path=require('path'),{spawnSync}=require('child_process');
const {Connection,Keypair,VersionedTransaction}=require('@solana/web3.js');
const clean=p=>fs.readFileSync(p,'utf8').replace(/^\uFEFF/,'');
const cfg=JSON.parse(clean(path.join(__dirname,'config_v480.json')));
const pub=JSON.parse(clean(path.join(__dirname,'wallet_public.json')));
const prep=JSON.parse(clean(path.join(__dirname,'v480_calibration_candidate.json')));
const r=spawnSync('powershell.exe',['-NoProfile','-ExecutionPolicy','Bypass','-File',path.join(__dirname,'decrypt_wallet.ps1')],{encoding:'utf8',windowsHide:true});
if(r.status!==0) throw new Error('WALLET_DECRYPT_FAILED');
const raw=Buffer.from(r.stdout.trim(),'base64'),kp=Keypair.fromSecretKey(Uint8Array.from(raw));raw.fill(0);
if(kp.publicKey.toBase58()!==pub.address) throw new Error('WALLET_MISMATCH');
(async()=>{
 const tx=VersionedTransaction.deserialize(Buffer.from(prep.prepared_tx_b64,'base64'));
 tx.sign([kp]);
 const c=new Connection(cfg.rpc_url,'confirmed');
 const sim=await c.simulateTransaction(tx,{sigVerify:true,commitment:'confirmed'});
 console.log(JSON.stringify({
   state:sim.value.err?'SIGNED_SIM_FAILED':'SIGNED_SIM_OK_READY_TO_SEND',
   side:prep.side||'BUY',trade_id:prep.trade_id,mint:prep.mint,
   wallet:pub.address,simulationErr:sim.value.err,
   unitsConsumed:sim.value.unitsConsumed,broadcast:false
 },null,2));
 if(sim.value.err) process.exit(2);
})().catch(e=>{console.error(JSON.stringify({state:'SIGNED_SIM_EXCEPTION',error:String(e.message||e),broadcast:false}));process.exit(1)});