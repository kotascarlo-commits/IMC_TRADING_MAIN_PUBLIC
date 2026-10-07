import json, sqlite3
from pathlib import Path
from datetime import datetime, timezone, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer

ROOT=Path(__file__).resolve().parent
CFG=json.loads((ROOT/"config_v480.json").read_text(encoding="utf-8"))
NS=str(CFG.get("state_namespace","")).strip()
STATE=ROOT/(f"state_{NS}.json" if NS else "state.json")
JOURNAL=ROOT/(f"journal_{NS}.jsonl" if NS else "journal.jsonl")
FEEDBACK=ROOT/(f"execution_feedback_{NS}.jsonl" if NS else "execution_feedback.jsonl")
DB=Path(CFG["source_db"])

def now():
    return datetime.now(timezone.utc).isoformat()

def save_state(s):
    tmp=STATE.with_suffix(f".{__import__('os').getpid()}.tmp")
    tmp.write_text(json.dumps(s,indent=2),encoding="utf-8")
    tmp.replace(STATE)

def filled_from_journal(side):
    out=[]
    if not JOURNAL.exists(): return out
    try:
        for line in JOURNAL.read_text(encoding="utf-8").splitlines():
            if not line.strip(): continue
            rec=json.loads(line); ev=rec.get("event") or {}; ack=rec.get("ack") or {}
            if ev.get("side")==side and str(ack.get("status",""))=="FILLED":
                tid=int(ev.get("trade_id"));
                if tid not in out: out.append(tid)
    except Exception:
        pass
    return out

def filled_mints_from_journal(side):
    out=[]
    if not JOURNAL.exists(): return out
    try:
        for line in JOURNAL.read_text(encoding="utf-8").splitlines():
            if not line.strip(): continue
            rec=json.loads(line); ev=rec.get("event") or {}; ack=rec.get("ack") or {}
            mint=str(ev.get("mint") or "")
            if mint and ev.get("side")==side and str(ack.get("status",""))=="FILLED" and mint not in out:
                out.append(mint)
    except Exception:
        pass
    return out

def sell_defer_active(s,tid):
    raw=(s.get("sell_deferred_until") or {}).get(str(tid))
    if not raw: return False
    try:return datetime.now(timezone.utc)<datetime.fromisoformat(str(raw).replace("Z","+00:00"))
    except Exception:return False

def load_state():
    if STATE.exists():
        s=json.loads(STATE.read_text(encoding="utf-8"))
        s.setdefault("buy_emitted",[])
        s["buy_filled"]=sorted(set(s.get("buy_filled",[])) | set(filled_from_journal("BUY")))
        s.setdefault("buy_rejected",[])
        s.setdefault("sell_emitted",[])
        s["sell_filled"]=sorted(set(s.get("sell_filled",[])) | set(filled_from_journal("SELL")))
        s["buy_filled_mints"]=sorted(set(s.get("buy_filled_mints",[])) | set(filled_mints_from_journal("BUY")))
        s["sell_filled_mints"]=sorted(set(s.get("sell_filled_mints",[])) | set(filled_mints_from_journal("SELL")))
        s.setdefault("buy_emitted_mints",[])
        s.setdefault("sell_emitted_mints",[])
        s.setdefault("sell_deferred_until",{})
        s.setdefault("stale_buy_skipped",[])
        s.setdefault("stale_buy_skipped_mints",[])
        s["source_integrity_warning"]=s.get("source_integrity_warning") or s.get("source_integrity_error")
        s["source_integrity_error"]=None
        s.setdefault("pending",None)
        return s
    c=sqlite3.connect(DB)
    baseline=c.execute("select coalesce(max(id),0) from trades").fetchone()[0]
    c.close()
    s={"baseline":baseline,"buy_emitted":[],"buy_filled":[],"buy_rejected":[],"sell_emitted":[],
       "sell_filled":[],"buy_filled_mints":[],"sell_filled_mints":[],"buy_emitted_mints":[],
       "sell_emitted_mints":[],"sell_deferred_until":{},"stale_buy_skipped":[],"stale_buy_skipped_mints":[],
       "source_integrity_warning":None,"source_integrity_error":None,"pending":None}
    save_state(s)
    return s

def append_jsonl(path,obj):
    with path.open("a",encoding="utf-8") as f:
        f.write(json.dumps(obj,separators=(",",":"))+"\n")
def buy_is_fresh(entry_at,signal_at=None):
    try:
        limit=float(CFG.get("max_buy_entry_age_seconds",10))
        now_utc=datetime.now(timezone.utc)
        entry_ts=datetime.fromisoformat(str(entry_at).replace("Z","+00:00")).astimezone(timezone.utc)
        if (now_utc-entry_ts).total_seconds()>limit:
            return False
        if signal_at:
            signal_ts=datetime.fromisoformat(str(signal_at).replace("Z","+00:00")).astimezone(timezone.utc)
            if (now_utc-signal_ts).total_seconds()>limit:
                return False
        return True
    except Exception:
        return False

def next_signal():
    s=load_state()
    if s.get("pending"):
        return s["pending"]
    c=sqlite3.connect(DB)
    c.row_factory=sqlite3.Row
    max_id=int(c.execute("select coalesce(max(id),0) from trades").fetchone()[0] or 0)
    if max_id<int(s.get("baseline",0)):
        s["source_integrity_warning"]=f"SOURCE_DB_ID_RESET old_baseline={s.get('baseline')} new_max={max_id}; mint-key protection active"
        s["baseline"]=0
    rows=c.execute("""select id,address,variant,status,signal_at,entry_at,exit_at,exit_reason
                      from trades where id>? order by id""",(s["baseline"],)).fetchall()
    c.close()
    for r in rows:
        tid=int(r["id"]); mint=str(r["address"])
        if r["status"]=="OPEN" and mint not in s["buy_emitted_mints"] and mint not in s["buy_filled_mints"] and mint not in s["stale_buy_skipped_mints"]:
            if str(r["variant"])!=str(CFG.get("live_variant","SAFE100")):
                continue
            if not buy_is_fresh(r["entry_at"],r["signal_at"]):
                s["stale_buy_skipped"].append(tid)
                s["stale_buy_skipped_mints"].append(mint)
                save_state(s)
                continue
            ev={"event_id":f"BUY:{mint}","side":"BUY","trade_id":tid,"trade_key":mint,
                "mint":mint,"position_fraction":CFG["position_fraction"],
                "variant":r["variant"],"signal_at":r["signal_at"],"created_at":now()}
            s["buy_emitted"].append(tid); s["buy_emitted_mints"].append(mint)
            s["pending"]=ev
            save_state(s)
            return ev
        if r["status"] in ("READY_EXIT","CLOSED") and mint in s["buy_filled_mints"] and mint not in s["sell_filled_mints"] and not sell_defer_active(s,mint):
            ev={"event_id":f"SELL:{mint}","side":"SELL","trade_id":tid,"trade_key":mint,
                "mint":mint,"exit_reason":r["exit_reason"],"created_at":now()}
            if tid not in s["sell_emitted"]: s["sell_emitted"].append(tid)
            if mint not in s["sell_emitted_mints"]: s["sell_emitted_mints"].append(mint)
            s["pending"]=ev
            save_state(s)
            return ev
    return None
class Handler(BaseHTTPRequestHandler):
    def send_json(self,obj,code=200):
        raw=json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type","application/json")
        self.send_header("Content-Length",str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        if self.path=="/health":
            s=load_state()
            self.send_json({
                "ok":not bool(s.get("source_integrity_error")),
                "time":now(),
                "mode":CFG["mode"],
                "live_variant":CFG.get("live_variant","SAFE100"),
                "broadcast_provider":CFG.get("broadcast_provider","UNKNOWN"),
                "routing_provider":CFG.get("routing_provider",CFG.get("broadcast_provider","UNKNOWN")),
                "position_fraction":CFG["position_fraction"],
                "max_price_impact_fraction":CFG.get("max_price_impact_fraction"),
                "max_buy_entry_age_seconds":CFG.get("max_buy_entry_age_seconds"),
                "source_integrity_error":s.get("source_integrity_error"),
                "source_integrity_warning":s.get("source_integrity_warning")
            })
        elif self.path=="/next":
            self.send_json({"signal":next_signal()})
        else:
            self.send_json({"error":"not_found"},404)

    def do_POST(self):
        if self.path!="/ack":
            return self.send_json({"error":"not_found"},404)
        n=int(self.headers.get("Content-Length","0"))
        data=json.loads(self.rfile.read(n) or b"{}")
        s=load_state()
        pending=s.get("pending")
        if not pending or data.get("event_id")!=pending.get("event_id"):
            return self.send_json({"ok":False,"error":"event_mismatch"},409)
        rec={"time":now(),"event":pending,"ack":data}
        append_jsonl(JOURNAL,rec)
        append_jsonl(FEEDBACK,rec)
        tid=int(pending["trade_id"]); mint=str(pending.get("mint") or ""); key=str(pending.get("trade_key") or mint or tid)
        if pending.get("side")=="BUY":
            if str(data.get("status",""))=="FILLED":
                if tid not in s["buy_filled"]: s["buy_filled"].append(tid)
                if mint and mint not in s["buy_filled_mints"]: s["buy_filled_mints"].append(mint)
            else:
                if tid not in s["buy_rejected"]: s["buy_rejected"].append(tid)
        elif pending.get("side")=="SELL":
            if str(data.get("status",""))=="FILLED":
                if tid not in s["sell_filled"]: s["sell_filled"].append(tid)
                if mint and mint not in s["sell_filled_mints"]: s["sell_filled_mints"].append(mint)
                s["sell_deferred_until"].pop(key,None)
            elif str(data.get("status",""))=="DEFERRED":
                retry=data.get("retry_after") or (datetime.now(timezone.utc)+timedelta(seconds=30)).isoformat()
                s["sell_deferred_until"][key]=retry
        s["pending"]=None
        s["baseline"]=max(int(s["baseline"]),tid-1)
        save_state(s)
        self.send_json({"ok":True})

    def log_message(self,fmt,*args):
        return

if __name__=="__main__":
    host=CFG["host"]
    port=int(CFG["port"])
    print(f"IMC Executor Gateway {host}:{port}")
    HTTPServer((host,port),Handler).serve_forever()
