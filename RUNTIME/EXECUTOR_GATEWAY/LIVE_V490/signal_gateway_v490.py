import json, sqlite3, threading
from pathlib import Path
from datetime import datetime, timezone, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE=Path(__file__).resolve().parent
RUNTIME=HERE.parents[1]
DB=RUNTIME/"real-forward-v490-live-twin.db"
STATE=HERE/"live_signal_state_v490.json"
STATUS=HERE/"live_signal_gateway_v490_status.json"
JOURNAL=HERE/"live_execution_journal_v490.jsonl"
CFG=json.loads((HERE/"config_v490_live.json").read_text(encoding="utf-8-sig"))
LOCK=threading.RLock()
HOST="127.0.0.1"; PORT=49090

def now():
    return datetime.now(timezone.utc)

def iso():
    return now().isoformat()

def conn():
    c=sqlite3.connect(DB,timeout=5)
    c.row_factory=sqlite3.Row
    c.execute("pragma busy_timeout=5000")
    return c

def max_trade_id():
    if not DB.exists(): return 0
    c=conn()
    try: return int(c.execute("select coalesce(max(id),0) from trades").fetchone()[0] or 0)
    except Exception: return 0
    finally: c.close()

def blank_state():
    return {
        "version":"4.9.0-live",
        "created_at":iso(),
        "session_active":True,
        "new_buys_enabled":True,
        "baseline_trade_id":max_trade_id(),
        "buy_filled":{},
        "buy_rejected":[],
        "buy_skipped_stale":[],
        "sell_filled":{},
        "sell_deferred_until":{},
        "pending":None,
        "last_ack":None
    }

def unsold_count(s):
    return sum(1 for mint in s.get("buy_filled",{}) if mint not in s.get("sell_filled",{}))

def load_state():
    if not STATE.exists():
        s=blank_state(); save_state(s); return s
    s=json.loads(STATE.read_text(encoding="utf-8-sig"))
    for k,v in blank_state().items():
        s.setdefault(k,v)
    return s

def save_state(s):
    tmp=STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(s,indent=2),encoding="utf-8")
    tmp.replace(STATE)

def append_journal(obj):
    with JOURNAL.open("a",encoding="utf-8") as f:
        f.write(json.dumps(obj,separators=(",",":"))+"\n")

def status_obj(s):
    return {
        "version":"4.9.0-live",
        "updated":iso(),
        "session_active":bool(s.get("session_active")),
        "new_buys_enabled":bool(s.get("new_buys_enabled")),
        "baseline_trade_id":int(s.get("baseline_trade_id",0)),
        "open_live_positions":unsold_count(s),
        "buy_filled":len(s.get("buy_filled",{})),
        "sell_filled":len(s.get("sell_filled",{})),
        "completed_roundtrips":len(s.get("sell_filled",{})),
        "max_session_trades":int(CFG.get("max_session_trades",999999)),
        "session_complete":(len(s.get("sell_filled",{}))>=int(CFG.get("max_session_trades",999999)) and unsold_count(s)==0),
        "pending":s.get("pending"),
        "db_exists":DB.exists(),
        "db_max_trade_id":max_trade_id()
    }

def write_status(s):
    STATUS.write_text(json.dumps(status_obj(s),indent=2),encoding="utf-8")

def parse_ts(v):
    return datetime.fromisoformat(str(v).replace("Z","+00:00"))

def sell_event(s):
    if not DB.exists(): return None
    c=conn()
    try:
        for mint,rec in s.get("buy_filled",{}).items():
            if mint in s.get("sell_filled",{}): continue
            until=s.get("sell_deferred_until",{}).get(mint)
            if until:
                try:
                    if now()<parse_ts(until): continue
                except Exception: pass
            row=c.execute("""select id,address,status,exit_at,exit_reason
                             from trades where address=? order by id desc limit 1""",(mint,)).fetchone()
            if not row or str(row["status"])!="CLOSED": continue
            return {
                "event_id":f"SELL:{row['id']}:{mint}",
                "side":"SELL",
                "trade_id":int(row["id"]),
                "mint":mint,
                "exit_reason":row["exit_reason"],
                "exit_at":row["exit_at"],
                "created_at":iso()
            }
    finally:
        c.close()
    return None

def buy_event(s):
    if len(s.get("buy_filled",{}))>=int(CFG.get("max_session_trades",999999)):
        s["new_buys_enabled"]=False
        save_state(s)
        return None
    if not s.get("new_buys_enabled") or unsold_count(s)>=int(CFG["max_open_positions"]):
        return None
    if not DB.exists(): return None
    seen=set(s.get("buy_filled",{})) | set(s.get("buy_rejected",[])) | set(s.get("buy_skipped_stale",[]))
    c=conn()
    try:
        rows=c.execute("""select id,address,signal_at,entry_at,status,input_lamports,buy_impact,pre_sell_impact,pre_rt_loss
                          from trades where id>? and status='OPEN' order by id""",
                       (int(s.get("baseline_trade_id",0)),)).fetchall()
        for row in rows:
            mint=str(row["address"])
            if mint in seen: continue
            try:
                age=(now()-parse_ts(row["entry_at"])).total_seconds()
            except Exception:
                age=999
            if age>float(CFG["max_buy_age_seconds"]):
                s["buy_skipped_stale"].append(mint); save_state(s); continue
            return {
                "event_id":f"BUY:{row['id']}:{mint}",
                "side":"BUY",
                "trade_id":int(row["id"]),
                "mint":mint,
                "input_lamports":int(row["input_lamports"] or 0),
                "signal_at":row["signal_at"],
                "entry_at":row["entry_at"],
                "sim_buy_impact":row["buy_impact"],
                "sim_sell_impact":row["pre_sell_impact"],
                "sim_roundtrip_loss":row["pre_rt_loss"],
                "created_at":iso()
            }
    finally:
        c.close()
    return None

def next_event():
    with LOCK:
        s=load_state()
        if s.get("pending"):
            write_status(s); return s["pending"]
        ev=sell_event(s)
        if ev is None: ev=buy_event(s)
        if ev:
            s["pending"]=ev; save_state(s)
        write_status(s)
        return ev

def ack(data):
    with LOCK:
        s=load_state(); ev=s.get("pending")
        if not ev or data.get("event_id")!=ev.get("event_id"):
            return False,"event_mismatch"
        rec={"time":iso(),"event":ev,"ack":data}
        append_journal(rec)
        mint=str(ev.get("mint") or "")
        st=str(data.get("status") or "")
        if ev.get("side")=="BUY":
            if st=="FILLED":
                s["buy_filled"][mint]={
                    "trade_id":ev["trade_id"],"signature":data.get("signature"),
                    "actual_output_raw":data.get("actual_output_raw"),"filled_at":iso()
                }
            else:
                if mint not in s["buy_rejected"]: s["buy_rejected"].append(mint)
        else:
            if st=="FILLED":
                s["sell_filled"][mint]={
                    "trade_id":ev["trade_id"],"signature":data.get("signature"),
                    "actual_output_raw":data.get("actual_output_raw"),"filled_at":iso()
                }
                s["sell_deferred_until"].pop(mint,None)
                if len(s["sell_filled"])>=int(CFG.get("max_session_trades",999999)) and unsold_count(s)==0:
                    s["new_buys_enabled"]=False
                    s["session_active"]=False
            elif st=="DEFERRED":
                retry=data.get("retry_after") or (now()+timedelta(seconds=int(CFG["sell_retry_seconds"]))).isoformat()
                s["sell_deferred_until"][mint]=retry
        s["last_ack"]=rec
        s["pending"]=None
        save_state(s); write_status(s)
        return True,None

def control(action):
    with LOCK:
        s=load_state()
        if action=="stop":
            s["new_buys_enabled"]=False
            s["session_active"]=False
        elif action=="pause_buys":
            s["new_buys_enabled"]=False
        elif action=="resume_buys":
            s["new_buys_enabled"]=True
            s["session_active"]=True
        else:
            return False
        save_state(s); write_status(s); return True

class Handler(BaseHTTPRequestHandler):
    def sendj(self,obj,code=200):
        raw=json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type","application/json")
        self.send_header("Content-Length",str(len(raw)))
        self.end_headers(); self.wfile.write(raw)

    def do_GET(self):
        if self.path=="/health":
            with LOCK:
                s=load_state(); write_status(s); self.sendj(status_obj(s))
        elif self.path=="/next":
            self.sendj({"signal":next_event()})
        else:
            self.sendj({"error":"not_found"},404)

    def do_POST(self):
        n=int(self.headers.get("Content-Length","0"))
        try: data=json.loads(self.rfile.read(n) or b"{}")
        except Exception: return self.sendj({"ok":False,"error":"bad_json"},400)
        if self.path=="/ack":
            ok,err=ack(data)
            return self.sendj({"ok":ok,"error":err},200 if ok else 409)
        if self.path=="/control":
            ok=control(str(data.get("action") or ""))
            return self.sendj({"ok":ok},200 if ok else 400)
        self.sendj({"error":"not_found"},404)

    def log_message(self,fmt,*args):
        return

def main():
    with LOCK:
        s=load_state(); write_status(s)
    print(f"IMC LIVE signal gateway {HOST}:{PORT} DB={DB}")
    ThreadingHTTPServer((HOST,PORT),Handler).serve_forever()

if __name__=="__main__":
    main()
