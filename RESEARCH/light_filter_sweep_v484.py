import sqlite3,json
from pathlib import Path
ROOT=Path.home()/"Desktop"/"IMC_TRADING_MAIN"
CORE=ROOT/"CORE"/"data"/"imc-main.db"
BASE=ROOT/"RUNTIME"/"watch-breadth-v425-shadow.db"
OUT=ROOT/"RESEARCH"/"LIGHT_FILTER_SWEEP_V484.json"
LIQS=[100000,110000,120000,125000,130000,140000,150000]
VRS=[0.08,0.10,0.12]
START_CAPITAL=1000.0
def payload(s):
    try:return json.loads(s or "{}")
    except:return {}
def metrics(rows,stress=0.0):
    vals=[p-stress for _,_,_,p in rows]
    wins=[x for x in vals if x>0]; losses=[x for x in vals if x<0]
    gp=sum(wins); gl=-sum(losses); top=sum(sorted(wins,reverse=True)[:3])
    eq=peak=START_CAPITAL; dd=0.0
    for x in vals:
        eq+=x; peak=max(peak,eq); dd=max(dd,(peak-eq)/max(1,peak)*100)
    return {"n":len(vals),"wins":len(wins),"losses":len(losses),
            "hit_pct":round(100*len(wins)/len(vals),2) if vals else 0,
            "pf":round(gp/gl,3) if gl else (99.0 if gp else None),
            "dd_pct":round(dd,2),"top3_pct":round(100*top/gp,2) if gp else 0,
            "net":round(sum(vals),2)}
b=sqlite3.connect(BASE); b.row_factory=sqlite3.Row
c=sqlite3.connect(f"file:{CORE.as_posix()}?mode=ro",uri=True); c.row_factory=sqlite3.Row
base=b.execute("select address,decision_at,entry_at,net_pnl from picks where variant='SAFE100' and status='CLOSED' order by decision_at").fetchall()
rows=[]
missing=0
for r in base:
    s=c.execute("select payload_json from snapshots where address=? and captured_at>=? order by captured_at limit 1",(r['address'],r['decision_at'])).fetchone()
    if not s:
        missing+=1; continue
    p=payload(s['payload_json']); L=float(p.get('liquidity_usd') or 0); V=float(p.get('volume_5m_usd') or 0)
    rows.append((L,V/max(1,L),r['decision_at'],float(r['net_pnl'])))
results=[]
for liq in LIQS:
    for vr in VRS:
        chosen=[x for x in rows if x[0]>=liq and x[1]<=vr]
        results.append({"liq_min":liq,"vr_max":vr,"base":metrics(chosen,0.0),"stress_plus_6pct":metrics(chosen,6.0)})
def passes(m):
    return m['n']>=30 and m['hit_pct']>=70 and (m['pf'] or 0)>=2 and m['dd_pct']<=15 and m['top3_pct']<=25
for x in results:
    x['base_pass']=passes(x['base']); x['stress_pass']=passes(x['stress_plus_6pct'])
ranked=sorted(results,key=lambda x:(x['stress_pass'],x['stress_plus_6pct']['pf'] or 0,-x['stress_plus_6pct']['dd_pct'],x['stress_plus_6pct']['n']),reverse=True)
obj={"version":"4.8.4-light-sweep","source_closed_safe100":len(base),"usable":len(rows),"missing":missing,
     "method":"stricter subset of already-closed SAFE100; no new market calls; +6 stress modeled as extra $6 per $100 trade",
     "results":ranked}
OUT.write_text(json.dumps(obj,indent=2),encoding='utf-8')
print(json.dumps({"source":len(base),"usable":len(rows),"top":ranked[:8]},indent=2))
b.close(); c.close()
