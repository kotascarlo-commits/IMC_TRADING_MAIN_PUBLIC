from __future__ import annotations

import json
import math
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .store import Store
from .execution_config import load_execution_config


def execution_status():
    """Operational gateway view, separate from CORE Paper statistics.

    Gateway health may persist timeout/kill guards; it never sends an order.
    """
    try:
        cfg = load_execution_config()
        limit = cfg['dashboard_max_response_bytes']
        with urllib.request.urlopen(f"http://127.0.0.1:{cfg['port']}/health",
                                    timeout=cfg['dashboard_gateway_timeout_seconds']) as response:
            body = response.read(limit + 1)
        if len(body) > limit:
            raise ValueError('Gateway response too large')
        data = json.loads(body)
        performance = data.get('performance')
        if data.get('version') != 'imc-execution-ledger' or not isinstance(performance, dict) or performance.get('evidence_class') != 'FINALIZED_CHAIN_RECONCILED' or not performance.get('source'):
            raise ValueError('Unknown execution schema/source')
        for key in ('new_buys_enabled','signals_enabled','reconciliation_required','wallet_access','signing','broadcast'):
            if type(data.get(key)) is not bool:
                raise ValueError('Invalid execution bool')
        if type(data.get('open_positions')) is not int or data['open_positions'] < 0 or type(performance.get('count')) is not int or performance['count'] < 0:
            raise ValueError('Invalid execution count')
        for key in ('trades','events','kill_reasons'):
            if not isinstance(data.get(key),list):
                raise ValueError('Invalid execution collection')
        if performance['unit'] != 'lamports' or performance['count'] > len(data['trades']):
            raise ValueError('Inconsistent execution source/count')
        for key in ('win_rate','profit_factor','top3_positive_profit_share'):
            if performance.get(key) is not None:
                if isinstance(performance[key],bool):
                    raise ValueError('Invalid execution metric type')
                value = float(performance[key])
                if not math.isfinite(value) or value < 0 or (key != 'profit_factor' and value > 1):
                    raise ValueError('Invalid execution metric')
        if any(not isinstance(t,dict) or any(not isinstance(t.get(k),str) for k in ('trade_id','mint','state','remaining_raw')) for t in data['trades']):
            raise ValueError('Invalid execution trade')
        if any(not t['remaining_raw'].isdigit() or len(t['remaining_raw']) > 40 for t in data['trades']):
            raise ValueError('Invalid execution quantity')
        net = performance.get('net_pnl_lamports')
        if net is not None and (not isinstance(net,str) or len(net) > 40 or not net.lstrip('-').isdigit()):
            raise ValueError('Invalid execution PnL')
        if performance['count'] == 0 and any(performance.get(key) is not None for key in ('net_pnl_lamports','win_rate','profit_factor')):
            raise ValueError('Metrics without confirmed trades')
        data['available'] = True
        return data
    except Exception as error:
        return {'available':False, 'execution_mode':'UNAVAILABLE', 'error_class':type(error).__name__}


HTML = """<!doctype html>
<html lang="de"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Mykonos Meme Scout</title><style>
:root{--bg:#07111f;--panel:#0d1d2e;--line:#18344c;--teal:#35e0c1;--text:#e8f2f7;--muted:#88a2b4;--red:#ff6b7a}
*{box-sizing:border-box}body{margin:0;background:linear-gradient(135deg,#07111f,#0b1728);color:var(--text);font:14px system-ui,sans-serif}
main{max-width:1200px;margin:auto;padding:28px}.hero{display:flex;justify-content:space-between;align-items:end}.brand{font-size:28px;font-weight:800}.brand span{color:var(--teal)}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:12px;margin:20px 0}.card,.table-wrap{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:16px}
.value{font-size:24px;font-weight:700;margin-top:6px}.muted{color:var(--muted)}table{width:100%;border-collapse:collapse}th,td{text-align:left;padding:10px;border-bottom:1px solid var(--line)}th{color:var(--muted)}
.pass{color:var(--teal)}.watch{color:var(--red)}h2{font-size:16px;margin:26px 0 10px}.pill{padding:4px 8px;border-radius:20px;background:#102b34;color:var(--teal)}
@media(max-width:650px){.hide-mobile{display:none}main{padding:16px}}
</style></head><body><main><div class="hero"><div><div class="brand">MYKONOS <span>MEME SCOUT</span></div><div class="muted">Paper-Trading · Live-Ausführung gesperrt</div></div><span class="pill" id="status">lädt …</span></div>
<div class="grid"><div class="card"><div class="muted">Paper Cash</div><div class="value" id="cash">–</div></div><div class="card"><div class="muted">Offene Positionen</div><div class="value" id="open">–</div></div><div class="card"><div class="muted">Trades</div><div class="value" id="trades">–</div></div><div class="card"><div class="muted">KI-Cookies</div><div class="value" id="cookies">–</div></div><div class="card"><div class="muted">Kill Switch</div><div class="value" id="kill">–</div></div></div>
<h2>Letzte Bewertungen</h2><div class="table-wrap"><table><thead><tr><th>Zeit</th><th>Coin</th><th>Entscheidung</th><th>Score</th><th class="hide-mobile">Konfidenz</th><th class="hide-mobile">Gründe</th></tr></thead><tbody id="decisions"></tbody></table></div>
<h2>Virtuelle Trades</h2><div class="table-wrap"><table><thead><tr><th>Zeit</th><th>Seite</th><th>USD</th><th class="hide-mobile">Kosten</th><th>Grund</th></tr></thead><tbody id="tradeRows"></tbody></table></div>
<h2>Execution — bestätigte Blockchain-Daten</h2><div class="grid"><div class="card"><div class="muted">Gateway / Modus</div><div id="execMode">UNAVAILABLE</div></div><div class="card"><div class="muted">Echte Positionen</div><div id="execOpen">–</div></div><div class="card"><div class="muted">Risk / Reconciliation</div><div id="execRisk">–</div></div><div class="card"><div class="muted">Execution Kill-Switch</div><div id="execKill">–</div></div><div class="card"><div class="muted">Reconciled Netto-PnL (lamports)</div><div id="execPnl">–</div></div><div class="card"><div class="muted">PF / Trefferquote</div><div id="execPf">–</div></div></div><div class="muted" id="execSource"></div><div class="table-wrap"><table><thead><tr><th>Trade-ID</th><th>Mint</th><th>Status</th><th>Restmenge (raw)</th><th>Shadow-Abweichung (lamports)</th></tr></thead><tbody id="execRows"></tbody></table></div>
<script>
const usd=v=>new Intl.NumberFormat('de-DE',{style:'currency',currency:'USD'}).format(v||0); const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
async function refresh(){try{const r=await fetch('/api/state');const d=await r.json();cash.textContent=usd(d.account.cash_usd);open.textContent=d.positions.filter(p=>p.status==='OPEN').length;trades.textContent=d.trades.length;cookies.textContent=d.learning.cookies;kill.textContent=d.account.kill_reason||'frei';status.textContent='PAPER · SHADOW AI';
decisions.innerHTML=d.decisions.map(x=>`<tr><td>${esc(x.captured_at.slice(11,19))}</td><td>${esc(x.symbol)}</td><td class="${x.decision==='PASS'?'pass':'watch'}">${esc(x.decision)}</td><td>${x.score}</td><td class="hide-mobile">${x.confidence}%</td><td class="hide-mobile">${esc(JSON.parse(x.reasons_json).join(', '))}</td></tr>`).join('');
tradeRows.innerHTML=d.trades.map(x=>`<tr><td>${esc(x.occurred_at.slice(11,19))}</td><td>${esc(x.side)}</td><td>${usd(x.gross_usd)}</td><td class="hide-mobile">${usd(x.cost_usd)}</td><td>${esc(x.reason)}</td></tr>`).join('');}catch(e){status.textContent='OFFLINE'}} refresh();setInterval(refresh,5000);
</script></main></body></html>"""

HTML = HTML.replace('</script>', """
async function refreshExecution(){try{const response=await fetch('/api/execution');const e=await response.json();
execMode.textContent=e.available?e.execution_mode:'UNAVAILABLE';
if(!e.available){execOpen.textContent=execRisk.textContent=execKill.textContent=execPnl.textContent=execPf.textContent='–';execRows.innerHTML='';execSource.textContent='Keine belastbare Gateway-Antwort';return}
execOpen.textContent=e.open_positions;execRisk.textContent=e.reconciliation_required?'Reconciliation erforderlich':(e.new_buys_enabled?'Risk bereit':'BUY gesperrt');execKill.textContent=(e.kill_reasons||[]).join(', ')||'frei';
const p=e.performance;execPnl.textContent=p.net_pnl_lamports??'Keine abgeschlossenen Trades';execPf.textContent=(p.profit_factor??'undefiniert')+' / '+(p.win_rate??'undefiniert');
execSource.textContent=p.source+' · '+p.count+' abgeschlossene Trades · lamports · '+(p.first_trade_at??'–')+' bis '+(p.last_closed_at??'–')+' · DD: nicht verfügbar';
execRows.innerHTML=e.trades.slice(-20).map(t=>`<tr><td>${esc(t.trade_id.slice(0,12))}</td><td>${esc(t.mint)}</td><td>${esc(t.state)}</td><td>${esc(t.remaining_raw)}</td><td>${esc(t.comparison?.pnl_delta_lamports??'nicht verfügbar')}</td></tr>`).join('');
}catch(error){execMode.textContent='UNAVAILABLE';execOpen.textContent=execRisk.textContent=execKill.textContent=execPnl.textContent=execPf.textContent='–';execRows.innerHTML='';execSource.textContent='Gateway-Prüfung fehlgeschlagen'}}
refreshExecution();setInterval(refreshExecution,5000);
</script>""")


def serve(database: Path, port: int = 8080) -> None:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            if self.path == "/api/execution":
                data = execution_status()
                body = json.dumps(data).encode()
                self.send_response(200 if data['available'] else 503)
                self.send_header('Content-Type','application/json')
            elif self.path == "/api/state":
                store = Store(database)
                body = json.dumps(store.dashboard_data()).encode()
                store.close()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
            elif self.path == "/" or self.path.startswith("/?"):
                body = HTML.encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
            else:
                body = b"not found"
                self.send_response(404)
                self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, fmt: str, *args: object) -> None:
            return

    print(f"Dashboard: http://127.0.0.1:{port}")
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
