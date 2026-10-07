import sqlite3,json,time,math,threading,socket,queue
from pathlib import Path
from datetime import datetime,timezone,timedelta
from tail_exit_guard_v41 import exit_signal
ROOT=Path.home()/"Desktop"/"IMC_TRADING_MAIN"/"RESEARCH"
MAIN=Path.home()/"Desktop"/"IMC_TRADING_MAIN"/"CORE"/"data"/"imc-main.db"
DB=ROOT/"watch-safe100-vr12-balanced.db"; STATUS=ROOT/"watch_safe100_vr12_balanced_status.json"; STATE=ROOT/"safe100_vr12_balanced_state.json"
N=100.;AX=.01;POOL=.003;NET=.001;SOLUSD=104.62;MAXD=90;MINSELL=1000.;FRESH_SCAN_SEC=180
DECISION_UDP=("127.0.0.1",49225); V480_UDP=("127.0.0.1",49226)
BUS_STATS={'rx':0,'rx_errors':0,'tx_errors':0,'last_ms':None,'avg_ms':None,'last_at':None}
V480_SEND=socket.socket(socket.AF_INET,socket.SOCK_DGRAM)
RULES={
 'SAFE100_VR12':{'liq':100000,'br':.75,'lo':20,'hi':500,'age':30,'vr':.12},
 'SAFE100_VR10_SHADOW':{'liq':100000,'br':.75,'lo':20,'hi':500,'age':30,'vr':.10},
 'SAFE100_VR08_SHADOW':{'liq':100000,'br':.75,'lo':20,'hi':500,'age':30,'vr':.08}}
def now():return datetime.now(timezone.utc)
def dt(s):return datetime.fromisoformat(str(s).replace('Z','+00:00'))
def pay(r):
 try:return json.loads(r['payload_json'] or '{}')
 except:return {}
def slip(liq):return min(.05,max(.0035,.0035+50/max(1.,liq)))
def init():
 c=sqlite3.connect(DB,timeout=5);c.execute('pragma journal_mode=WAL');c.execute('pragma busy_timeout=5000');c.execute('create table if not exists picks(address text,variant text,decision_at text,accepted integer,reason text,entry_at text,entry_market real,entry_fill real,entry_liq real,status text,exit_at text,exit_reason text,net_pnl real,primary key(address,variant))');c.execute('create table if not exists meta(key text primary key,value text)')
 cols={x[1] for x in c.execute('pragma table_info(picks)').fetchall()}
 if 'watch_seen_at' not in cols:c.execute('alter table picks add column watch_seen_at text')
 c.commit();c.close()
 if not STATE.exists():STATE.write_text(json.dumps({'version':'VR12-research','started_at':now().isoformat(),'authority':'0%','forward_only':True,'send_to_v480':False},indent=2),encoding='utf-8')
def first(c,a,t):return c.execute('select * from snapshots where address=? and captured_at>=? order by captured_at limit 1',(a,t)).fetchone()
def features(r):
 p=pay(r);b=float(p.get('buys_5m') or 0);s=float(p.get('sells_5m') or 0);L=float(p.get('liquidity_usd') or 0);V=float(p.get('volume_5m_usd') or 0);cr=float(p.get('pair_created_at_ms') or 0);age=max(0,(dt(r['captured_at']).timestamp()*1000-cr)/60000) if cr>0 else 999
 return {'liq':L,'br':b/max(1,b+s),'chg':float(p.get('price_change_5m_pct') or 0),'age':age,'vr':V/max(1,L)}
def accept(x,r):
 if x['liq']<r['liq']:return 0,'LIQ'
 if x['br']<r['br']:return 0,'FLOW'
 if not(r['lo']<=x['chg']<=r['hi']):return 0,'MOM'
 if x['age']>r['age']:return 0,'AGE'
 if x['vr']>r['vr']:return 0,'VOL_LIQ'
 return 1,'ACCEPT'
def decision_listener(fast):
 try:
  s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM);s.bind(DECISION_UDP);s.settimeout(1.0)
 except OSError:return
 while True:
  try:
   raw,_=s.recvfrom(8192);d=json.loads(raw.decode('utf-8'));d['bus_received_at']=now().isoformat()
   if d.get('decision')=='WATCH' and d.get('address') and d.get('captured_at'):
    BUS_STATS['rx']+=1;BUS_STATS['last_at']=d['bus_received_at']
    if d.get('emitted_at'):
     ms=max(0.0,(dt(d['bus_received_at'])-dt(d['emitted_at'])).total_seconds()*1000)
     BUS_STATS['last_ms']=round(ms,3);n=BUS_STATS['rx'];old=BUS_STATS['avg_ms']
     BUS_STATS['avg_ms']=round(ms if old is None else old+(ms-old)/n,3)
    fast['udp'].put(d);fast['wake'].set()
  except socket.timeout:continue
  except Exception:BUS_STATS["rx_errors"]+=1;continue
def push_v480(address,decision_at,variant,seen_at,source_emitted_at=None,source_received_at=None):
 try:
  payload=json.dumps({'address':address,'decision_at':decision_at,'variant':variant,
                      'watch_seen_at':seen_at,'source_emitted_at':source_emitted_at,
                      'source_received_at':source_received_at,'emitted_at':now().isoformat()},separators=(',',':')).encode('utf-8')
  V480_SEND.sendto(payload,V480_UDP)
 except OSError:BUS_STATS["tx_errors"]+=1
def scan(src,dst,fast):
 while True:
  try:
   d=fast['udp'].get_nowait()
   fast['pending'][f"udp:{d['address']}:{d['captured_at']}"]=d
  except queue.Empty:break
 rows=src.execute("select * from decisions where id>? and decision='WATCH' order by id limit 2000",(fast['last_id'],)).fetchall()
 max_db_id=fast['last_id']
 for d in rows:
  max_db_id=max(max_db_id,int(d['id']))
  fast['pending'][int(d['id'])]=dict(d)
 done=[];to_push=[]
 for k,d in list(fast['pending'].items()):
  age=(now()-dt(d['captured_at'])).total_seconds()
  if age>MAXD:
   done.append(k);continue
  e=first(src,d['address'],d['captured_at'])
  if not e:continue
  if (dt(e['captured_at'])-dt(d['captured_at'])).total_seconds()>MAXD:
   done.append(k);continue
  x=features(e);m=float(e['price_usd'] or 0)
  if m<=0:
   done.append(k);continue
  for v,r in RULES.items():
   old=dst.execute('select accepted,status from picks where address=? and variant=?',(d['address'],v)).fetchone()
   if old and int(old[0] or 0)==1:continue
   ac,reason=accept(x,r);ef=m*(1+slip(x['liq'])) if ac else None;seen=now().isoformat()
   vals=(d['captured_at'],ac,reason,e['captured_at'] if ac else None,m if ac else None,ef,x['liq'] if ac else None,'OPEN' if ac else 'REJECT',seen,d['address'],v)
   if old:
    dst.execute('update picks set decision_at=?,accepted=?,reason=?,entry_at=?,entry_market=?,entry_fill=?,entry_liq=?,status=?,watch_seen_at=?,exit_at=null,exit_reason=null,net_pnl=null where address=? and variant=?',vals)
   else:
    dst.execute('insert into picks(address,variant,decision_at,accepted,reason,entry_at,entry_market,entry_fill,entry_liq,status,watch_seen_at) values(?,?,?,?,?,?,?,?,?,?,?)',(d['address'],v,d['captured_at'],ac,reason,e['captured_at'] if ac else None,m if ac else None,ef,x['liq'] if ac else None,'OPEN' if ac else 'REJECT',seen))
   if v=='SAFE100_VR12' and ac:to_push.append((d['address'],d['captured_at'],v,seen,d.get('emitted_at'),d.get('bus_received_at')))
  done.append(k)
 if max_db_id>fast['last_id']:
  dst.execute("insert into meta(key,value) values('last_decision_id',?) on conflict(key) do update set value=excluded.value",(str(max_db_id),))
 dst.commit()
 fast['last_id']=max_db_id
 for k in done:fast['pending'].pop(k,None)
 for args in to_push:push_v480(*args)
def settle(src,dst):
 for row in dst.execute("select address,variant,entry_at,entry_market,entry_fill,entry_liq from picks where status='OPEN'").fetchall():
  a,v,et,em,ef,eliq=row;deadline=(dt(et)+timedelta(minutes=10)).isoformat();rs=src.execute('select * from snapshots where address=? and captured_at>? and captured_at<=? order by captured_at',(a,et,deadline)).fetchall();chosen=None;reason=None
  for r in rs:
   p=pay(r);age=(dt(r['captured_at'])-dt(et)).total_seconds();xl=float(p.get('liquidity_usd') or 0);xm=float(r['price_usd'] or 0)
   if xl<MINSELL:chosen=r;reason='UNSELLABLE_DRAIN';break
   sg=exit_signal(em,xm,eliq,xl,float(p.get('buys_5m') or 0),float(p.get('sells_5m') or 0),age,1.0)
   if sg['exit']:chosen=r;reason=sg['reason'];break
   if age>=180:chosen=r;reason='MAX_HOLD_3M';break
  if not chosen:
   if (now()-dt(et)).total_seconds()>600:
    dst.execute("update picks set status='EXPIRED_NO_DATA',exit_at=?,exit_reason=? where address=? and variant=?",(now().isoformat(),"NO_FOLLOWUP_DATA",a,v))
   continue
  p=pay(chosen);xl=float(p.get('liquidity_usd') or 0);xm=float(chosen['price_usd'] or 0)
  if reason=='UNSELLABLE_DRAIN':net=-N-N*AX-N*POOL-NET*SOLUSD
  else:
   xf=xm*(1-slip(xl));qty=N/ef;gross=qty*xf-N;net=gross-(N+qty*xf)*(AX+POOL)-2*NET*SOLUSD
  dst.execute("update picks set status='CLOSED',exit_at=?,exit_reason=?,net_pnl=? where address=? and variant=?",(chosen['captured_at'],reason,net,a,v))
 dst.commit()
def metr(vals):
 w=[x for x in vals if x>0];l=[x for x in vals if x<0];gp=sum(w);gl=-sum(l);top=sum(sorted(w,reverse=True)[:3])
 return {'n':len(vals),'wins':len(w),'losses':len(l),'hit_rate_pct':round(100*len(w)/len(vals),2) if vals else 0,'pf_net':round(gp/gl,3) if gl else (99.0 if w else None),'net_pnl_usd':round(sum(vals),4),'top3_profit_share_pct':round(100*top/gp,2) if gp else 100}
def status(dst):
 out={}
 for v in RULES:
  vals=[float(x[0]) for x in dst.execute("select net_pnl from picks where variant=? and status='CLOSED'",(v,)).fetchall()]
  out[v]={'seen':dst.execute('select count(*) from picks where variant=?',(v,)).fetchone()[0],'accepted':dst.execute('select count(*) from picks where variant=? and accepted=1',(v,)).fetchone()[0],'result':metr(vals)}
 obj={'version':'VR12-research','state':'RUNNING_BALANCED_RESEARCH','updated':now().isoformat(),'authority':'0%','forward_only':True,'source_lane':'WATCH','communication':'UDP_DIRECT_PLUS_DB_FALLBACK','direct_bus':dict(BUS_STATS),'variants':out,'last_error':None}
 STATUS.write_text(json.dumps(obj,indent=2),encoding='utf-8')
def housekeep():
 src=sqlite3.connect(f'file:{MAIN.as_posix()}?mode=ro',uri=True);src.row_factory=sqlite3.Row;dst=sqlite3.connect(DB,timeout=5);dst.execute('pragma busy_timeout=5000')
 while True:
  try:settle(src,dst);status(dst)
  except Exception as e:STATUS.write_text(json.dumps({'version':'4.2.5-shadow-watch','state':'RUNNING_BREADTH_RESEARCH','updated':now().isoformat(),'authority':'0%','last_error':f'HOUSEKEEP {type(e).__name__}: {e}'},indent=2),encoding='utf-8')
  time.sleep(20)
def main():
 init();src=sqlite3.connect(f'file:{MAIN.as_posix()}?mode=ro',uri=True);src.row_factory=sqlite3.Row;dst=sqlite3.connect(DB,timeout=5);dst.execute('pragma busy_timeout=5000')
 saved=dst.execute("select value from meta where key='last_decision_id'").fetchone()
 current=int(src.execute("select coalesce(max(id),0) from decisions").fetchone()[0] or 0)
 last_id=int(saved[0]) if saved else current
 if not saved:
  dst.execute("insert into meta(key,value) values('last_decision_id',?)",(str(last_id),));dst.commit()
 fast={'last_id':last_id,'pending':{},
       'udp':queue.SimpleQueue(),'wake':threading.Event()}
 threading.Thread(target=housekeep,daemon=True).start()
 threading.Thread(target=decision_listener,args=(fast,),daemon=True).start()
 while True:
  fast['wake'].clear()
  try:scan(src,dst,fast)
  except Exception as e:STATUS.write_text(json.dumps({'version':'4.2.5-shadow-watch','state':'RUNNING_BREADTH_RESEARCH','updated':now().isoformat(),'authority':'0%','last_error':f'FASTSCAN {type(e).__name__}: {e}'},indent=2),encoding='utf-8')
  fast['wake'].wait(.25)
if __name__=='__main__':main()
