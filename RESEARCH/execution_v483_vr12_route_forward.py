import json,sqlite3,time,urllib.parse,urllib.request,urllib.error,base64,struct,math,socket,threading,queue,http.client,io,sys
from pathlib import Path
from datetime import datetime,timezone,timedelta
from solders.pubkey import Pubkey
from solders.hash import Hash
from solders.instruction import Instruction,AccountMeta
from solders.address_lookup_table_account import AddressLookupTableAccount
from solders.message import MessageV0
from solders.transaction import VersionedTransaction
from solders.signature import Signature
from solders.compute_budget import set_compute_unit_limit
ROOT=Path.home()/"Desktop"/"IMC_TRADING_MAIN"/"RESEARCH"
RUNTIME_ROOT=Path.home()/"Desktop"/"IMC_TRADING_MAIN"/"RUNTIME"
sys.path.insert(0,str(RUNTIME_ROOT))
from imc_resilience import reserve as shared_reserve, backoff as shared_backoff
WATCH=ROOT/"watch-safe100-vr12-balanced.db"
DB=ROOT/"execution-v483-vr12-route-forward.db"
STATUS=ROOT/"execution_v483_vr12_route_forward_status.json"
STATE=ROOT/"execution_v483_vr12_route_forward_state.json"
BASELINE_TARGET=15
BASELINE_COMPLETE=ROOT/"EXECUTION_V483_VR12_COMPLETE.json"
CALIB_CANDIDATE=ROOT/"v483_vr12_candidate.json"
VIRTUAL_START_CASH=10_000_000
SOL="So11111111111111111111111111111111111111112"
WALLET="58Wqgfw5EcV8pkLXDJ2ipvcKkMyEeTEpPB3RiEtFjPdj"
LITE="https://lite-api.jup.ag/swap/v1/quote"
BUILD="https://api.jup.ag/swap/v2/build"
RPC="https://api.mainnet-beta.solana.com"
JUP_KEY_PATH=Path.home()/"Desktop"/"IMC_TRADING_MAIN"/"RUNTIME"/"EXECUTOR_GATEWAY"/"jupiter_api_key.txt"
LIVE_VARIANT="SAFE100_VR12"; POS=.10; MAX_OPEN=3; MAX_DD=.15; DD_BUFFER=.005
PRE_MAX_SIDE=.04; PRE_MAX_RT=.06; SELL_MAX_IMPACT=.06
TARGET_ENTRY_SEC=1.0; FRESH_SEC=2.0; POLL=.05; MARK_INTERVAL=2.2; MAX_HOLD=180; PRE_DELAY=.05
BASE_FEE=5000; BUY_CU_EST=120000; SELL_CU_EST=100000; CLOSE_FEE_EST=0; OPERATING_RESERVE=50000
STATUS_INTERVAL=.5; BAL_TTL=30.0; BAL_CACHE=None; BAL_CACHE_AT=0.0; RENT_CACHE=None
EQ_SAMPLE_INTERVAL=2.0; LAST_EQ_SAMPLE=0.0
HTTP_TIMEOUT=1.5
BUILD_PRIORITY="medium"; LAST_BUILD=0.0
BUILD_BLOCK_UNTIL=0.0
BUILD_BACKOFF=2.2
ATA_PROGRAM=Pubkey.from_string("ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL"); MINT_PROGRAM_CACHE={}
SIGNAL_UDP=("127.0.0.1",49226); DIRECT_Q=queue.SimpleQueue(); SIGNAL_WAKE=threading.Event()
DIRECT_STATS={"rx":0,"errors":0,"last_bus_ms":None,"avg_bus_ms":None,"last_at":None}
LITE_MIN_GAP=1.05 if JUP_KEY_PATH.exists() else 2.2
LAST_LITE=0.0
LITE_BLOCK_UNTIL=0.0
LITE_BACKOFF=2.2
LITE_LOCK=threading.Lock()
def now():return datetime.now(timezone.utc)
def iso():return now().isoformat()
def dt(s):return datetime.fromisoformat(str(s).replace("Z","+00:00"))
def signal_listener():
 try:
  s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM);s.bind(SIGNAL_UDP);s.settimeout(1.0)
 except OSError:return
 while True:
  try:
   raw,_=s.recvfrom(8192);d=json.loads(raw.decode("utf-8"));d["v480_received_at"]=iso()
   if d.get("variant")==LIVE_VARIANT and d.get("address") and d.get("decision_at"):
    DIRECT_STATS["rx"]+=1;DIRECT_STATS["last_at"]=d["v480_received_at"]
    if d.get("emitted_at"):
     ms=max(0.0,(dt(d["v480_received_at"])-dt(d["emitted_at"])).total_seconds()*1000)
     DIRECT_STATS["last_bus_ms"]=round(ms,3);n=DIRECT_STATS["rx"];old=DIRECT_STATS["avg_bus_ms"]
     DIRECT_STATS["avg_bus_ms"]=round(ms if old is None else old+(ms-old)/n,3)
    DIRECT_Q.put(d);SIGNAL_WAKE.set()
  except socket.timeout:continue
  except Exception:DIRECT_STATS["errors"]+=1;continue
def req_json(url,data=None):
 h={"User-Agent":"IMC-v483-fresh-connect","Connection":"close"}
 if data is not None:h["Content-Type"]="application/json"
 if "api.jup.ag" in url and JUP_KEY_PATH.exists():
  k=JUP_KEY_PATH.read_text(encoding="utf-8").strip()
  if k:h["x-api-key"]=k
 u=urllib.parse.urlsplit(url);host=u.hostname;path=u.path+("?" + u.query if u.query else "")
 last=None
 for attempt in range(2):
  conn=http.client.HTTPSConnection(host,timeout=HTTP_TIMEOUT)
  try:
   conn.request("POST" if data is not None else "GET",path,body=data,headers=h)
   r=conn.getresponse();raw=r.read()
   if r.status>=400:
    raise urllib.error.HTTPError(url,r.status,r.reason,r.headers,io.BytesIO(raw))
   return json.loads(raw.decode("utf-8"))
  except urllib.error.HTTPError:
   raise
  except (http.client.RemoteDisconnected,ConnectionResetError,BrokenPipeError,TimeoutError,socket.timeout,OSError) as e:
   last=e
   if attempt==0:
    time.sleep(.12);continue
   raise
  finally:
   try:conn.close()
   except Exception:pass
 if last:raise last
 raise RuntimeError("HTTP_UNKNOWN_FAILURE")
def lite_quote(i,o,amount,wait=True):
 global LAST_LITE,LITE_BLOCK_UNTIL,LITE_BACKOFF
 with LITE_LOCK:
  tnow=time.monotonic()
  gap=max(LITE_MIN_GAP-(tnow-LAST_LITE) if LAST_LITE else 0.0,LITE_BLOCK_UNTIL-tnow)
  if gap>0:
   if (not wait) or gap>.65:raise RuntimeError(f"JUPITER_LITE_RATE_BUDGET {gap:.3f}s")
   time.sleep(gap)
  shared_gap=shared_reserve("jupiter_lite",LITE_MIN_GAP,wait=wait,max_wait=.65)
  if shared_gap>0:raise RuntimeError(f"JUPITER_LITE_SHARED_BUDGET {shared_gap:.3f}s")
  u=LITE+"?"+urllib.parse.urlencode({"inputMint":i,"outputMint":o,"amount":str(int(amount)),
                                    "slippageBps":"100","instructionVersion":"V2"})
  try:
   z=req_json(u)
  except urllib.error.HTTPError as e:
   LAST_LITE=time.monotonic()
   if e.code==429:
    LITE_BLOCK_UNTIL=LAST_LITE+LITE_BACKOFF
    shared_backoff("jupiter_lite",LITE_BACKOFF,"http_429")
    LITE_BACKOFF=min(30.0,LITE_BACKOFF*2.0)
    raise RuntimeError(f"JUPITER_LITE_RATE_LIMIT backoff={LITE_BACKOFF:.1f}s")
   raise
  LAST_LITE=time.monotonic();LITE_BLOCK_UNTIL=0.0;LITE_BACKOFF=2.2
  if not z.get("outAmount"):raise RuntimeError(z.get("error") or "LITE_NO_ROUTE")
  return z
def lite_roundtrip(mint,amount):
 b=lite_quote(SOL,mint,amount);s=lite_quote(mint,SOL,int(b["outAmount"]))
 bi=abs(float(b.get("priceImpactPct") or 0));si=abs(float(s.get("priceImpactPct") or 0))
 back=int(s["outAmount"]);loss=max(0.0,1-back/max(1,int(amount)))
 return b,s,bi,si,back,loss
def fast_build(side,mint,amount):
 global LAST_BUILD,BUILD_BLOCK_UNTIL,BUILD_BACKOFF
 min_gap=1.05 if JUP_KEY_PATH.exists() else 2.2
 tnow=time.monotonic()
 gap=max(min_gap-(tnow-LAST_BUILD) if LAST_BUILD else 0.0,BUILD_BLOCK_UNTIL-tnow)
 if gap>0:raise RuntimeError(f"JUPITER_BUILD_RATE_BUDGET {gap:.3f}s")
 shared_gap=shared_reserve("jupiter_build",min_gap,wait=False,max_wait=0)
 if shared_gap>0:raise RuntimeError(f"JUPITER_BUILD_SHARED_BUDGET {shared_gap:.3f}s")
 i,o=(SOL,mint) if str(side).upper()=="BUY" else (mint,SOL)
 u=BUILD+"?"+urllib.parse.urlencode({"inputMint":i,"outputMint":o,"amount":str(int(amount)),
  "taker":WALLET,"slippageBps":"rtse","mode":"fast","computeUnitPricePercentile":BUILD_PRIORITY})
 try:
  z=req_json(u);LAST_BUILD=time.monotonic();BUILD_BLOCK_UNTIL=0.0;BUILD_BACKOFF=2.2
 except urllib.error.HTTPError as e:
  LAST_BUILD=time.monotonic()
  if e.code==429:
   BUILD_BLOCK_UNTIL=LAST_BUILD+BUILD_BACKOFF
   shared_backoff("jupiter_build",BUILD_BACKOFF,"http_429")
   BUILD_BACKOFF=min(30.0,BUILD_BACKOFF*2.0)
   raise RuntimeError(f"JUPITER_BUILD_RATE_LIMIT backoff={BUILD_BACKOFF:.1f}s")
  raise
 except Exception:
  LAST_BUILD=time.monotonic()
  raise
 if not z.get("outAmount") or not z.get("swapInstruction"):raise RuntimeError(z.get("error") or "BUILD_NO_ROUTE")
 return z
def build_fee(z,cu_est):
 micro=0
 for ins in z.get("computeBudgetInstructions") or []:
  try:
   raw=base64.b64decode(ins.get("data") or "")
   if len(raw)>=9 and raw[0]==3:micro=max(micro,struct.unpack("<Q",raw[1:9])[0])
  except:pass
 return int(BASE_FEE+math.ceil(float(cu_est)*micro/1_000_000)),int(micro)
def build_instruction(x):
 return Instruction(Pubkey.from_string(x["programId"]),base64.b64decode(x.get("data") or ""),
  [AccountMeta(Pubkey.from_string(a["pubkey"]),bool(a["isSigner"]),bool(a["isWritable"])) for a in x.get("accounts") or []])
def compile_build(z,cu_limit=None,extras=None):
 xs=[]
 for x in z.get("computeBudgetInstructions") or []:
  raw=base64.b64decode(x.get("data") or "")
  if cu_limit and raw and raw[0]==2:continue
  xs.append(build_instruction(x))
 if cu_limit:xs.insert(0,set_compute_unit_limit(int(cu_limit)))
 xs += [build_instruction(x) for x in z.get("setupInstructions") or []]
 if z.get("swapInstruction"):xs.append(build_instruction(z["swapInstruction"]))
 if z.get("cleanupInstruction"):xs.append(build_instruction(z["cleanupInstruction"]))
 xs += [build_instruction(x) for x in z.get("otherInstructions") or []]
 if extras:xs += list(extras)
 if z.get("tipInstruction"):xs.append(build_instruction(z["tipInstruction"]))
 luts=[AddressLookupTableAccount(Pubkey.from_string(k),[Pubkey.from_string(a) for a in v])
       for k,v in (z.get("addressesByLookupTableAddress") or {}).items()]
 h=Hash.from_bytes(bytes(z["blockhashWithMetadata"]["blockhash"]))
 msg=MessageV0.try_compile(Pubkey.from_string(WALLET),xs,luts,h)
 return VersionedTransaction.populate(msg,[Signature.default()])
def mint_token_program(mint):
 if mint in MINT_PROGRAM_CACHE:return MINT_PROGRAM_CACHE[mint]
 z=rpc("getAccountInfo",[mint,{"encoding":"base64","commitment":"confirmed"}]).get("value")
 if not z:raise RuntimeError("MINT_ACCOUNT_MISSING")
 p=Pubkey.from_string(z["owner"]);MINT_PROGRAM_CACHE[mint]=p;return p
def ata_for_mint(mint):
 owner=Pubkey.from_string(WALLET);m=Pubkey.from_string(mint);tp=mint_token_program(mint)
 return str(Pubkey.find_program_address([bytes(owner),bytes(tp),bytes(m)],ATA_PROGRAM)[0])
def has_token_account(mint):
 try:
  return bool(rpc("getTokenAccountsByOwner",[WALLET,{"mint":mint},{"encoding":"jsonParsed","commitment":"confirmed"}])["value"])
 except:return False
def token_account_rent(mint):
 try:
  owner=str(mint_token_program(mint))
  size=170 if owner=="TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb" else 165
  return int(rpc("getMinimumBalanceForRentExemption",[size]))
 except:return 1_513_840
def peak_entry_required(mint,amount):
 wsol_rent=rent_lamports()
 out_rent=0 if has_token_account(mint) else token_account_rent(mint)
 return int(amount)+int(wsol_rent)+int(out_rent)+BASE_FEE
def close_ata_instruction(mint):
 ata=Pubkey.from_string(ata_for_mint(mint));owner=Pubkey.from_string(WALLET);tp=mint_token_program(mint)
 return Instruction(tp,bytes([9]),[
  AccountMeta(ata,False,True),AccountMeta(owner,False,True),AccountMeta(owner,True,False)])
def token_balance_raw(mint):
 try:
  rows=rpc("getTokenAccountsByOwner",[WALLET,{"mint":mint},{"encoding":"jsonParsed","commitment":"confirmed"}])["value"]
  return sum(int(x["account"]["data"]["parsed"]["info"]["tokenAmount"]["amount"]) for x in rows)
 except:return 0
def simulate_tx(tx,capture_wallet=False,capture_mint=None):
 raw=base64.b64encode(bytes(tx)).decode()
 opt={"encoding":"base64","sigVerify":False,"replaceRecentBlockhash":True,"commitment":"confirmed"}
 addresses=[]
 if capture_wallet:addresses.append(WALLET)
 if capture_mint:addresses.append(ata_for_mint(capture_mint))
 if addresses:opt["accounts"]={"encoding":"base64","addresses":addresses}
 r=rpc("simulateTransaction",[raw,opt]);v=r.get("value") or {}
 post=None;token_post=None;accs=v.get("accounts") or [];idx=0
 if capture_wallet:
  if len(accs)>idx and accs[idx]:post=int(accs[idx]["lamports"])
  idx+=1
 if capture_mint and len(accs)>idx and accs[idx]:
  d=accs[idx].get("data")
  if isinstance(d,list):d=d[0]
  rawacc=base64.b64decode(d or "")
  if len(rawacc)>=72:token_post=int.from_bytes(rawacc[64:72],"little")
 return {"err":v.get("err"),"fee":int(v.get("fee") or 0),"units":int(v.get("unitsConsumed") or 0),
         "wallet_post":post,"token_post_raw":token_post,"logs":list(v.get("logs") or [])}
def _sim_error(label,res):
 err=json.dumps(res.get("err"),separators=(",",":"))
 logs=res.get("logs") or []
 reason=next((x for x in reversed(logs) if "insufficient lamports" in x.lower()),None)
 if reason is None:
  reason=next((x for x in reversed(logs) if "failed:" in x.lower()),None)
 return label+":"+err+("|"+reason if reason else "")
def simulate_build(z,capture_wallet=False,capture_mint=None):
 token_pre=token_balance_raw(capture_mint) if capture_mint else None
 first=simulate_tx(compile_build(z),capture_wallet,capture_mint)
 if first["err"] is not None:raise RuntimeError(_sim_error("BUILD_SIM_PRE",first))
 limit=min(1_400_000,max(50_000,int(math.ceil(max(1,first["units"])*1.20))))
 final_tx=compile_build(z,limit)
 final=simulate_tx(final_tx,capture_wallet,capture_mint)
 if final["err"] is not None:raise RuntimeError(_sim_error("BUILD_SIM_FINAL",final))
 final["cu_limit"]=limit;final["first_units"]=first["units"]
 final["token_pre_raw"]=token_pre
 if capture_mint and final.get("token_post_raw") is not None:
  final["token_delta_raw"]=int(final["token_post_raw"])-int(token_pre or 0)
 final["prepared_tx_b64"]=base64.b64encode(bytes(final_tx)).decode()
 final["blockhash_b58"]=str(final_tx.message.recent_blockhash)
 final["last_valid_block_height"]=int((z.get("blockhashWithMetadata") or {}).get("lastValidBlockHeight") or 0)
 return final
def rpc(method,params):
 body=json.dumps({"jsonrpc":"2.0","id":1,"method":method,"params":params}).encode()
 last=None
 for attempt in range(3):
  shared_gap=shared_reserve("solana_rpc",.12,wait=True,max_wait=.8)
  if shared_gap>0:
   raise RuntimeError(f"SOLANA_RPC_SHARED_BUDGET {shared_gap:.3f}s")
  try:
   z=req_json(RPC,body)
   if z.get("error"):
    raise RuntimeError(f"SOLANA_RPC_ERROR {z['error']}")
   return z["result"]
  except urllib.error.HTTPError as e:
   last=e
   if e.code not in (408,429,500,502,503,504) or attempt>=2:raise
   delay=min(5.0,(1.0 if e.code==429 else .25)*(attempt+1))
   shared_backoff("solana_rpc",delay,f"http_{e.code}")
   time.sleep(delay)
  except (RuntimeError,TimeoutError,ConnectionResetError,http.client.RemoteDisconnected) as e:
   last=e
   if attempt>=2:raise
   delay=.2*(attempt+1)
   shared_backoff("solana_rpc",delay,type(e).__name__)
   time.sleep(delay)
 if last:raise last
 raise RuntimeError("SOLANA_RPC_UNKNOWN_FAILURE")
def balance_lamports(force=False):
 global BAL_CACHE,BAL_CACHE_AT
 if not force and BAL_CACHE is not None and time.monotonic()-BAL_CACHE_AT<BAL_TTL:return BAL_CACHE
 try:
  BAL_CACHE=int(rpc("getBalance",[WALLET,{"commitment":"confirmed"}])["value"]);BAL_CACHE_AT=time.monotonic();return BAL_CACHE
 except:return BAL_CACHE
def rent_lamports():
 global RENT_CACHE
 if RENT_CACHE is not None:return RENT_CACHE
 try:RENT_CACHE=int(rpc("getMinimumBalanceForRentExemption",[165]))
 except:RENT_CACHE=1_513_840
 return RENT_CACHE
def mint_rent(mint):
 try:
  rows=rpc("getTokenAccountsByOwner",[WALLET,{"mint":mint},{"encoding":"jsonParsed","commitment":"confirmed"}])["value"]
  return 0 if rows else rent_lamports()
 except:return rent_lamports()
def preflight_entry(mint,amount):
 checks=[]
 for k in range(2):
  x=lite_roundtrip(mint,amount);checks.append(x)
  if k==0:time.sleep(PRE_DELAY)
 return checks
def init():
 c=sqlite3.connect(DB,timeout=5);c.execute('pragma journal_mode=WAL');c.execute('pragma busy_timeout=5000')
 c.execute("""create table if not exists trades(
 id integer primary key,address text unique,variant text,signal_at text,signal_age_ms integer,entry_at text,status text,
 input_lamports integer,buy_out_raw text,buy_impact real,pre_sell_impact real,pre_rt_loss real,
 ultra_buy_out_raw text,ultra_buy_impact real,ultra_buy_router text,ultra_entry_sell_back integer,ultra_entry_sell_impact real,
 rent_locked integer,buy_fee integer,sell_fee integer,close_fee integer,current_sell_back integer,peak_sell_back integer,
 peak_return_pct real,last_quote_at text,quote_errors integer default 0,exit_at text,exit_reason text,exit_sell_back integer,
 exit_impact real,net_return_pct real,last_error text)""")
 c.execute("""create table if not exists observations(
 id integer primary key autoincrement,trade_id integer,address text,stage text,captured_at text,payload_json text)""")
 c.execute("""create table if not exists ledger(
 id integer primary key autoincrement,trade_id integer,event text unique,captured_at text,delta_lamports integer,note text)""")
 c.execute("create table if not exists meta(key text primary key,value text)")
 c.execute("""create table if not exists equity_history(
  id integer primary key autoincrement,captured_at text,equity_lamports integer,cash_lamports integer,
  open_count integer,trapped_count integer)""")
 cols={x[1] for x in c.execute("pragma table_info(trades)").fetchall()}
 for name,typ in [("entry_tx_b64","text"),("entry_tx_blockhash","text"),("entry_last_valid_block_height","integer"),("entry_path_ms","real")]:
  if name not in cols:c.execute(f"alter table trades add column {name} {typ}")
 if not c.execute("select 1 from meta where key='start_cash'").fetchone():
  bal=VIRTUAL_START_CASH
  c.execute("insert into meta(key,value) values('start_cash',?)",(str(bal),))
  c.execute("insert into meta(key,value) values('high_water',?)",(str(bal),))
 c.commit();c.close()
 if not STATE.exists():
  STATE.write_text(json.dumps({"version":"4.8.3-route-forward","started_at":iso(),"authority":"0%","live_execution":False,"virtual_start_cash_lamports":VIRTUAL_START_CASH},indent=2))
def meta_get(c,key,default=0):
 r=c.execute("select value from meta where key=?",(key,)).fetchone()
 try:return float(r[0]) if r else float(default)
 except:return float(default)
def meta_set(c,key,value):
 c.execute("insert into meta(key,value) values(?,?) on conflict(key) do update set value=excluded.value",(key,str(value)))
def shadow_cash(c):
 return int(meta_get(c,"start_cash",0)+sum(int(x[0]) for x in c.execute("select delta_lamports from ledger").fetchall()))
def shadow_equity(c):
 cash=shadow_cash(c)
 marks=sum(int(x[0] or 0) for x in c.execute(
  "select current_sell_back from trades where status in ('OPEN','TRAPPED')").fetchall())
 locked_rent=sum(int(x[0] or 0) for x in c.execute(
  "select rent_locked from trades where coalesce(rent_locked,0)>0").fetchall())
 return cash+marks+locked_rent
def open_count(c):
 return c.execute("select count(*) from trades where status in ('OPEN','TRAPPED')").fetchone()[0]
def planned_amount(c):
 if open_count(c)>=MAX_OPEN:return 0
 eq=shadow_equity(c);cash=shadow_cash(c);high=max(meta_get(c,"high_water",eq),eq);meta_set(c,"high_water",high)
 risk=max(0,int(eq-high*(1-MAX_DD+DD_BUFFER)));rent=rent_lamports()
 cashcap=max(0,cash-rent-OPERATING_RESERVE)
 return max(0,min(int(eq*POS),risk,cashcap))
def candidates(start):
 cut=max(dt(start),now()-timedelta(seconds=FRESH_SEC)).isoformat();rows=[]
 while True:
  try:
   d=DIRECT_Q.get_nowait()
   if d.get("variant")==LIVE_VARIANT and dt(d["decision_at"])>=dt(cut):
    meta={"transport":"UDP_DIRECT",**d}
    try:
     if d.get("source_emitted_at") and d.get("source_received_at"):
      meta["source_to_watcher_bus_ms"]=round((dt(d["source_received_at"])-dt(d["source_emitted_at"])).total_seconds()*1000,3)
     if d.get("emitted_at") and d.get("v480_received_at"):
      meta["watcher_to_v480_bus_ms"]=round((dt(d["v480_received_at"])-dt(d["emitted_at"])).total_seconds()*1000,3)
     if d.get("source_received_at") and d.get("watch_seen_at"):
      meta["watcher_processing_ms"]=round((dt(d["watch_seen_at"])-dt(d["source_received_at"])).total_seconds()*1000,3)
     if d.get("source_emitted_at"):
      meta["decision_to_source_emit_ms"]=round((dt(d["source_emitted_at"])-dt(d["decision_at"])).total_seconds()*1000,3)
    except Exception:pass
    rows.append((d["address"],d["decision_at"],d["variant"],meta))
  except queue.Empty:break
 if rows:
  best={}
  for row in rows:best[row[0]]=row
  return sorted(best.values(),key=lambda x:x[1])
 c=sqlite3.connect(WATCH)
 dbrows=c.execute("""select address,decision_at,variant from picks
                   where accepted=1 and variant=? and decision_at>=? order by decision_at""",
                (LIVE_VARIANT,cut)).fetchall()
 c.close()
 for a,t,v in dbrows:rows.append((a,t,v,{"transport":"DB_FALLBACK"}))
 best={}
 for row in rows:
  a,t,v,meta=row
  if a not in best or meta.get("transport")=="UDP_DIRECT":best[a]=row
 return sorted(best.values(),key=lambda x:x[1])
def obs(c,tid,address,stage,payload):
 c.execute("insert into observations(trade_id,address,stage,captured_at,payload_json) values(?,?,?,?,?)",
           (tid,address,stage,iso(),json.dumps(payload,separators=(",",":"))))
def open_new(c,start):
 for a,t,v,transport in candidates(start):
  old=c.execute("select status from trades where address=?",(a,)).fetchone()
  if old:
   if old[0] in ("RETRY_BUILD","RETRY_RPC","RETRY_QUOTE"):c.execute("delete from trades where address=?",(a,))
   else:continue
  planned=planned_amount(c)
  if planned<=0:return
  age_ms=int((now()-dt(t)).total_seconds()*1000)
  if age_ms>FRESH_SEC*1000:
   cur=c.execute("insert into trades(address,variant,signal_at,signal_age_ms,status,last_error) values(?,?,?,?,?,?)",
             (a,v,t,age_ms,"REJECTED_STALE","FAST_PATH_AGE"))
   obs(c,cur.lastrowid,a,"LATENCY_REJECT_STALE",{"signal_to_detect_ms":age_ms,**transport});continue
  available=shadow_cash(c);required=peak_entry_required(a,planned)
  if available is not None and available<required:
   msg=f"PEAK_CAPITAL available={available} required={required} shortfall={required-available}"
   cur=c.execute("insert into trades(address,variant,signal_at,signal_age_ms,status,input_lamports,last_error) values(?,?,?,?,?,?,?)",
             (a,v,t,age_ms,"REJECTED_CAPITAL_SETUP",planned,msg))
   obs(c,cur.lastrowid,a,"CAPITAL_PREFLIGHT",{"available_lamports":available,"required_peak_lamports":required,
       "shortfall_lamports":required-available,"position_lamports":planned,**transport});continue
  started=time.perf_counter();detect_age_ms=age_ms;stage="BUILD_BUY";t_build=None;t_exit=None;t_sim=None
  try:
   b=fast_build("BUY",a,planned);t_build=time.perf_counter();stage="EXIT_CHECK";buy_out=int(b["outAmount"]);build_buy_out=buy_out;bi=abs(float(b.get("priceImpactPct") or 0))
   sq=lite_quote(a,SOL,buy_out);t_exit=time.perf_counter();stage="ROUTE_GATE";back=int(sq["outAmount"]);si=abs(float(sq.get("priceImpactPct") or 0))
   loss=max(0.0,1-back/max(1,planned))
   if bi>PRE_MAX_SIDE or si>PRE_MAX_SIDE or loss>PRE_MAX_RT:
    final_age=int((now()-dt(t)).total_seconds()*1000)
    cur=c.execute("""insert into trades(address,variant,signal_at,signal_age_ms,status,input_lamports,buy_impact,
                 pre_sell_impact,pre_rt_loss,last_error) values(?,?,?,?,?,?,?,?,?,?)""",
              (a,v,t,final_age,"REJECTED_BUILD_ROUTE",planned,bi,si,loss,
               f"BUILD_GATE buy={bi:.6f} sell={si:.6f} rt={loss:.6f}"))
    obs(c,cur.lastrowid,a,"LATENCY_REJECT_ROUTE",{"signal_to_detect_ms":detect_age_ms,
        "build_ms":round((t_build-started)*1000,1),"exit_check_ms":round((t_exit-t_build)*1000,1),
        "path_ms":round((t_exit-started)*1000,1),"signal_to_final_ms":final_age,**transport});continue
   stage="ROUTE_FORWARD_BUILD";age_ms=int((now()-dt(t)).total_seconds()*1000)
   if age_ms>FRESH_SEC*1000:
    cur=c.execute("insert into trades(address,variant,signal_at,signal_age_ms,status,input_lamports,last_error) values(?,?,?,?,?,?,?)",
              (a,v,t,age_ms,"REJECTED_STALE",planned,"FAST_PATH_AFTER_ROUTE_BUILD"))
    obs(c,cur.lastrowid,a,"LATENCY_REJECT_STALE_AFTER_ROUTE",{"signal_to_detect_ms":detect_age_ms,
        "build_ms":round((t_build-started)*1000,1),"exit_check_ms":round((t_exit-t_build)*1000,1),
        "signal_to_final_ms":age_ms,**transport});continue
   buyfee,bmicro=build_fee(b,BUY_CU_EST)
   rent=0 if has_token_account(a) else token_account_rent(a)
   sellfee=buyfee;net0=(back-planned-buyfee-sellfee-CLOSE_FEE_EST)/max(1,planned)*100
   cur=c.execute("""insert into trades(address,variant,signal_at,signal_age_ms,entry_at,status,input_lamports,buy_out_raw,
                    buy_impact,pre_sell_impact,pre_rt_loss,ultra_buy_out_raw,ultra_buy_impact,ultra_buy_router,
                    ultra_entry_sell_back,ultra_entry_sell_impact,rent_locked,buy_fee,sell_fee,close_fee,
                    current_sell_back,peak_sell_back,peak_return_pct,last_quote_at)
                    values(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                 (a,v,t,age_ms,iso(),"OPEN",planned,str(buy_out),bi,si,loss,str(buy_out),bi,"swap_v2_build_fast",
                  back,si,rent,buyfee,sellfee,CLOSE_FEE_EST,back,back,net0,iso()))
   tid=cur.lastrowid
   path_ms=round((time.perf_counter()-started)*1000,1)
   c.execute("update trades set entry_path_ms=? where id=?",(path_ms,tid))
   obs(c,tid,a,"ROUTE_ENTRY_BUILD",{"signal_age_ms":age_ms,"signal_to_detect_ms":detect_age_ms,"path_ms":path_ms,
       "build_ms":round((t_build-started)*1000,1),"exit_check_ms":round((t_exit-t_build)*1000,1),
       "build_buy_out":build_buy_out,"buy_impact":bi,"sell_back":back,"sell_impact":si,"rt_loss":loss,
       "buy_fee_est":buyfee,"priority_micro_lamports":bmicro,"rent_exact_model":rent,
       "priority":BUILD_PRIORITY,"setup_count":len(b.get("setupInstructions") or []),
       "virtual_start_cash_lamports":VIRTUAL_START_CASH,"local_tx_simulation":False,**transport})
   CALIB_CANDIDATE.write_text(json.dumps({
       "version":"4.8.3-route-forward","trade_id":tid,"mint":a,"signal_at":t,"prepared_at":iso(),
       "input_lamports":planned,"expected_buy_out_raw":str(buy_out),
       "buy_impact":bi,"pre_sell_impact":si,"pre_roundtrip_loss":loss,
       "buy_fee_lamports_est":buyfee,"rent_locked_lamports":rent,
       "jupiter_build_passed":True,"local_tx_simulation":False,"broadcast":False
   },indent=2),encoding="utf-8")
   c.execute("insert into ledger(trade_id,event,captured_at,delta_lamports,note) values(?,?,?,?,?)",
             (tid,f"{tid}:ENTRY",iso(),-(planned+rent+buyfee),f"position={planned};rent={rent};fee={buyfee};path=swap_v2_build_fast"))
  except Exception as e:
   msg=f"{type(e).__name__}: {e}";st="REJECTED_BUILD"
   low=msg.lower()
   if "insufficient lamports" in low:st="REJECTED_CAPITAL_SETUP"
   elif "jupiter_lite_rate" in low:st="RETRY_QUOTE"
   elif "remote disconnected" in low or "remotedisconnected" in low or "connectionreseterror" in low or "brokenpipeerror" in low:st="RETRY_BUILD"
   elif "timeouterror" in low or "timed out" in low:st="RETRY_BUILD"
   elif "jupiter_build_rate" in low:st="RETRY_BUILD"
   elif "solana_rpc" in low or "429" in low or "simulatetransaction" in low:st="RETRY_RPC"
   elif "lite" in low or "no_route" in low:st="RETRY_QUOTE"
   final_age=int((now()-dt(t)).total_seconds()*1000)
   cur=c.execute("insert into trades(address,variant,signal_at,signal_age_ms,status,input_lamports,last_error) values(?,?,?,?,?,?,?)",
             (a,v,t,final_age,st,planned,msg))
   timing={"stage":stage,"signal_to_detect_ms":detect_age_ms,"signal_to_final_ms":final_age,
           "path_ms":round((time.perf_counter()-started)*1000,1)}
   if t_build is not None:timing["build_ms"]=round((t_build-started)*1000,1)
   if t_exit is not None:timing["exit_check_ms"]=round((t_exit-t_build)*1000,1)
   timing.update(transport)
   obs(c,cur.lastrowid,a,"LATENCY_BUILD_ERROR",timing)
 c.commit()
def manage(c):
 rows=c.execute("""select id,address,entry_at,input_lamports,ultra_buy_out_raw,peak_sell_back,
                   rent_locked,buy_fee,sell_fee,close_fee,status,quote_errors,last_quote_at,exit_reason
                   from trades where status in ('OPEN','TRAPPED')""").fetchall()
 for i,a,et,inp,tok,peak,rent,buyfee,sellfee,closefee,st,qerr,lastq,pending_reason in rows:
  age=(now()-dt(et)).total_seconds()
  if lastq and (now()-dt(lastq)).total_seconds()<MARK_INTERVAL:continue
  try:
   q=lite_quote(a,SOL,int(tok),wait=False);back=int(q["outAmount"]);impact=abs(float(q.get("priceImpactPct") or 0))
   fee_est=int(sellfee or buyfee or BASE_FEE)
   net=(back-inp-int(buyfee or 0)-fee_est-int(closefee or 0))/max(1,inp)*100
   peak=max(int(peak or 0),back)
   peakret=(peak-inp-int(buyfee or 0)-fee_est-int(closefee or 0))/max(1,inp)*100
   obs(c,i,a,"LITE_MARK",{"age_seconds":age,"sell_back":back,"sell_impact":impact,
       "sell_fee_est":fee_est,"net_return_pct":net})
   reason=pending_reason if st=="TRAPPED" else None
   if not reason and age<=90 and net<=-7:reason="FAST_EXEC_STOP"
   elif not reason and net<=-12:reason="HARD_EXEC_STOP"
   elif not reason and peakret>=12 and peakret-net>=5:reason="TRAILING_EXEC_PROFIT"
   elif not reason and age>=MAX_HOLD:reason="MAX_HOLD_3M"
   if not reason:
    c.execute("""update trades set status='OPEN',current_sell_back=?,peak_sell_back=?,peak_return_pct=?,
                 last_quote_at=?,quote_errors=0,last_error=null where id=?""",(back,peak,peakret,iso(),i))
    continue
   try:s=fast_build("SELL",a,int(tok))
   except Exception as be:
    msg=f"{type(be).__name__}: {be}"
    c.execute("""update trades set status='TRAPPED',current_sell_back=?,peak_sell_back=?,peak_return_pct=?,
                 last_quote_at=?,exit_reason=?,last_error=? where id=?""",(back,peak,peakret,iso(),reason,msg,i))
    obs(c,i,a,"EXIT_BUILD_WAIT",{"reason":reason,"error":msg,"mark_back":back,"mark_impact":impact});continue
   xback=int(s["outAmount"]);ximpact=abs(float(s.get("priceImpactPct") or 0))
   xfee,smicro=build_fee(s,SELL_CU_EST)
   cleanup_fee=BASE_FEE if int(rent or 0)>0 else 0
   reclaim=int(rent or 0) if cleanup_fee else 0
   xnet=(xback-inp-int(buyfee or 0)-xfee-cleanup_fee)/max(1,inp)*100
   if ximpact>SELL_MAX_IMPACT:
    c.execute("""update trades set status='TRAPPED',current_sell_back=?,peak_sell_back=?,peak_return_pct=?,
                 last_quote_at=?,quote_errors=0,sell_fee=?,exit_reason=?,last_error=? where id=?""",
              (xback,max(peak,xback),peakret,iso(),xfee,reason,f"SELL_LIQUIDITY_TRAP {ximpact:.6f}",i))
    obs(c,i,a,"EXIT_BUILD_TRAP",{"reason":reason,"sell_back":xback,"sell_impact":ximpact,"sell_fee_est":xfee});continue
   if reclaim:
    close_ata_instruction(a)
   c.execute("""update trades set status='CLOSED',current_sell_back=?,peak_sell_back=?,peak_return_pct=?,last_quote_at=?,
                quote_errors=0,sell_fee=?,close_fee=?,rent_locked=0,exit_at=?,exit_reason=?,exit_sell_back=?,exit_impact=?,net_return_pct=?,last_error=null
                where id=?""",(xback,max(peak,xback),peakret,iso(),xfee,cleanup_fee,iso(),reason,xback,ximpact,xnet,i))
   credit=xback-xfee+reclaim-cleanup_fee
   c.execute("insert or ignore into ledger(trade_id,event,captured_at,delta_lamports,note) values(?,?,?,?,?)",
             (i,f"{i}:EXIT",iso(),credit,f"sell={xback};rent_reclaimed={reclaim};sellfee={xfee};closefee={cleanup_fee};reason={reason};path=swap_v2_build_fast"))
   obs(c,i,a,"EXIT_BUILD",{"reason":reason,"sell_back":xback,"sell_impact":ximpact,
       "sell_fee_est":xfee,"sell_micro_lamports":smicro,"ata_close_mode":"SEPARATE_AFTER_SELL",
       "rent_reclaimed":reclaim,"close_fee_est":cleanup_fee,"net_return_pct":xnet})
  except Exception as e:
   msg=f"{type(e).__name__}: {e}"
   if "JUPITER_LITE_RATE_BUDGET" in msg or "JUPITER_LITE_RATE_LIMIT" in msg:
    obs(c,i,a,"LITE_MARK_DEFERRED",{"age_seconds":age,"error":msg,"quote_errors":int(qerr or 0)})
    return
   qn=int(qerr or 0)+1
   route_like=("LITE" in msg or "NO_ROUTE" in msg or "404" in msg)
   trapped=(route_like and qn>=3) or qn>=6
   obs(c,i,a,"LITE_MARK_ERROR",{"age_seconds":age,"error":msg,"quote_errors":qn,"mark_zeroed":bool(trapped)})
   if trapped:
    reason=pending_reason or ("QUOTE_ROUTE_UNAVAILABLE" if route_like else "QUOTE_FEED_UNAVAILABLE")
    c.execute("""update trades set status='TRAPPED',current_sell_back=0,quote_errors=?,last_error=?,
                 last_quote_at=?,exit_reason=? where id=?""",(qn,msg,iso(),reason,i))
   else:
    c.execute("update trades set quote_errors=?,last_error=?,last_quote_at=? where id=?",(qn,msg,iso(),i))
 c.commit()
def metrics(c):
 rows=c.execute("""select id,net_return_pct,input_lamports,exit_sell_back,buy_fee,sell_fee,close_fee
                   from trades where status='CLOSED' order by id""").fetchall()
 vals=[float(x[1]) for x in rows if x[1] is not None]
 pnls=[int(x[3] or 0)-int(x[2] or 0)-int(x[4] or 0)-int(x[5] or 0)-int(x[6] or 0) for x in rows]
 w=[x for x in pnls if x>0];l=[x for x in pnls if x<0];gp=sum(w);gl=-sum(l)
 start=max(1,int(meta_get(c,"start_cash",1)));eq=start;peak=eq;dd=0.0
 for p in pnls:
  eq+=p;peak=max(peak,eq);dd=max(dd,(peak-eq)/max(1,peak)*100)
 hist=[int(x[0]) for x in c.execute("select equity_lamports from equity_history order by id").fetchall()]
 if hist:
  hpeak=max(1,hist[0]);hdd=0.0
  for h in hist:
   hpeak=max(hpeak,h);hdd=max(hdd,(hpeak-h)/max(1,hpeak)*100)
  dd=max(dd,hdd)
 top=sum(sorted(w,reverse=True)[:3])
 return {"closed":len(pnls),"wins":len(w),"losses":len(l),
         "hit_rate_pct":round(100*len(w)/len(pnls),2) if pnls else 0,
         "pf_net":round(gp/gl,3) if gl else (99.0 if gp else None),
         "avg_return_pct":round(sum(vals)/len(vals),3) if vals else 0,
         "max_drawdown_pct":round(dd,3),
         "top3_profit_share_pct":round(100*top/gp,2) if gp else 0,
         "realized_pnl_lamports":sum(pnls)}
def status(c):
 global LAST_EQ_SAMPLE
 eq=shadow_equity(c);cash=shadow_cash(c);high=max(meta_get(c,"high_water",eq),eq);meta_set(c,"high_water",high)
 dd=(high-eq)/high if high>0 else 0
 counts={s:c.execute("select count(*) from trades where status=?",(s,)).fetchone()[0]
         for s in ["OPEN","TRAPPED","CLOSED","REJECTED_STALE","REJECTED_PREFLIGHT","REJECTED_BUILD_ROUTE",
                   "REJECTED_BUILD","REJECTED_CAPITAL_SETUP","RETRY_BUILD","RETRY_RPC","RETRY_QUOTE"]}
 ages=c.execute("select signal_age_ms from trades where entry_at is not null").fetchall()
 avg_age=round(sum(int(x[0]) for x in ages)/len(ages),1) if ages else None
 if time.monotonic()-LAST_EQ_SAMPLE>=EQ_SAMPLE_INTERVAL:
  c.execute("insert into equity_history(captured_at,equity_lamports,cash_lamports,open_count,trapped_count) values(?,?,?,?,?)",
            (iso(),int(eq),int(cash),int(counts["OPEN"]),int(counts["TRAPPED"])))
  c.commit();LAST_EQ_SAMPLE=time.monotonic()
 obj={"version":"4.8.3-route-forward","mode":"ROUTE_FORWARD_VIRTUAL_0_01_SOL_NO_SIGNING","authority":"0%","live_execution":False,
      "updated":iso(),"variant":LIVE_VARIANT,"execution_path":"JUPITER_V2_BUILD_FAST_ROUTE_FORWARD","price_source":"LITE_MARK_BUILD_EXEC",
      "communication":"UDP_DIRECT_PLUS_DB_FALLBACK","direct_bus":dict(DIRECT_STATS),
      "build_priority":BUILD_PRIORITY,"jupiter_api_key_present":JUP_KEY_PATH.exists(),
      "entry_target_seconds":TARGET_ENTRY_SEC,"signal_max_age_seconds":FRESH_SEC,"mark_interval_seconds":MARK_INTERVAL,
      "pre_max_side_impact":PRE_MAX_SIDE,"pre_max_roundtrip_loss":PRE_MAX_RT,
      "sell_max_impact":SELL_MAX_IMPACT,"wallet_reference_lamports":balance_lamports(),"virtual_start_cash_lamports":VIRTUAL_START_CASH,
      "shadow_cash_lamports":cash,"shadow_equity_lamports":eq,"equity_high_water_lamports":int(high),
      "equity_drawdown_pct":round(dd*100,3),"position_fraction":POS,"max_drawdown_fraction":MAX_DD,
      "avg_entry_signal_age_ms":avg_age,"counts":counts,"next_position_lamports":planned_amount(c),
      "baseline_target_closed":BASELINE_TARGET,"baseline_progress_pct":round(100*counts["CLOSED"]/BASELINE_TARGET,2),
      "metrics":metrics(c),"last_error":None}
 STATUS.write_text(json.dumps(obj,indent=2),encoding="utf-8")
def main():
 init();start=json.loads(STATE.read_text(encoding="utf-8-sig"))["started_at"];c=sqlite3.connect(DB,timeout=5);c.execute("pragma busy_timeout=5000");last_status=0.0
 threading.Thread(target=signal_listener,daemon=True).start()
 while True:
  SIGNAL_WAKE.clear()
  try:
   open_new(c,start);manage(c)
   if time.monotonic()-last_status>=STATUS_INTERVAL:
    status(c);last_status=time.monotonic()
    m=metrics(c)
    if m["closed"]>=BASELINE_TARGET:
     BASELINE_COMPLETE.write_text(json.dumps({"version":"4.8.3-route-forward","state":"ROUTE_FORWARD_15_COMPLETE","completed_at":iso(),"target_closed":BASELINE_TARGET,"metrics":m,"authority":"0%","live_execution":False},indent=2),encoding="utf-8")
     return
  except Exception as e:
   STATUS.write_text(json.dumps({"version":"4.8.0-build-parity","mode":"BUILD_ONLY_NO_SIGNING",
     "authority":"0%","live_execution":False,"updated":iso(),"last_error":f"{type(e).__name__}: {e}"},indent=2))
  SIGNAL_WAKE.wait(POLL)
if __name__=="__main__":main()
