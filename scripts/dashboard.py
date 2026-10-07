"""Mini-Dashboard fuer IMC CORE - Start, Stop, Status. Sonst nichts.

Absichtlich ohne Grafiken, ohne JavaScript, ohne externe Pakete - reines
Python-Stdlib (http.server) + eine Handvoll HTML. Laeuft nur auf
127.0.0.1, ist von aussen nicht erreichbar.

Prozesszustand stammt aus der gemeinsamen exakten IMC-Prozesszuordnung.
Snapshot-Frische ist eine getrennte Dateninformation. Unvollstaendige
Prozesssicht ergibt UNKNOWN und blockiert Start/Stop.

Start:  python scripts/dashboard.py
Dann im Browser: http://127.0.0.1:8787
"""
from __future__ import annotations

import sqlite3
import json
import hmac
import html
import secrets
import subprocess
import sys
import threading
import time
from urllib.parse import parse_qs
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from mykonos_meme_scout.execution_config import load_execution_config

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "CORE" / "data" / "imc-main.db"
START_SCRIPT = ROOT / "CORE" / "START_CORE_WORKER.ps1"
PORT = 8787
RUNNING_IF_YOUNGER_THAN_SECONDS = 10
COMMON = ROOT / "LAUNCHERS/IMC_PROCESS_COMMON.ps1"
CSRF_TOKEN = secrets.token_urlsafe(32)
ACTION_LOCK = threading.Lock()
START_PENDING = False
DASH_CONFIG = load_execution_config()

def process_command(command):
    common = str(COMMON).replace("'", "''")
    return subprocess.run(['powershell.exe', '-NoProfile', '-Command',
                           "$ErrorActionPreference='Stop'; . '" + common + "'; " + command],
                          stdin=subprocess.DEVNULL, capture_output=True, text=True,
                          timeout=DASH_CONFIG['process_query_timeout_seconds'])


def process_snapshot():
    result = process_command('Get-IMCProcessSnapshot | ConvertTo-Json -Depth 5 -Compress')
    if result.returncode:
        raise RuntimeError('Prozesssicht UNKNOWN: CIM-Pruefung fehlgeschlagen')
    snapshot = json.loads(result.stdout)
    if not isinstance(snapshot,dict) or not isinstance(snapshot.get('UnknownInterpreterPids'),list):
        raise RuntimeError('Prozesssicht UNKNOWN: ungueltige Antwort')
    if snapshot.get('UnknownInterpreterPids'):
        raise RuntimeError('Prozesssicht UNKNOWN: nicht zugeordnete Interpreter')
    if not isinstance(snapshot.get('Processes'), list):
        raise RuntimeError('Prozesssicht UNKNOWN: ungueltige Antwort')
    if any(not isinstance(p,dict) or not isinstance(p.get('Role'),str) or type(p.get('ProcessId')) is not int or p['ProcessId'] <= 0 for p in snapshot['Processes']):
        raise RuntimeError('Prozesssicht UNKNOWN: ungueltige Prozessidentitaet')
    return snapshot['Processes']


def snapshot_detail() -> str:
    if not DB.exists():
        return "Datenbank nicht gefunden"
    try:
        con = sqlite3.connect(DB.resolve().as_uri() + '?mode=ro', uri=True, timeout=2.0)
        row = con.execute("SELECT captured_at FROM snapshots ORDER BY id DESC LIMIT 1").fetchone()
        con.close()
    except Exception as exc:
        return "Datenbank nicht lesbar (" + type(exc).__name__ + ")"
    if not row:
        return "noch keine Snapshots"
    try:
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(row[0])).total_seconds()
        return f"letzter Snapshot vor {age:.0f}s" if age >= 0 else 'Snapshot-Zeit ungueltig'
    except (ValueError, TypeError):
        return 'Snapshot-Zeit ungueltig'


def core_status() -> tuple[bool | None, str]:
    data = snapshot_detail()
    try:
        processes = process_snapshot()
        workers = [p for p in processes if p['Role'] == 'Core']
        wrappers = [p for p in processes if p['Role'] == 'CoreWrapper']
        if len(workers) > 1 or len(wrappers) > 1:
            return None, 'Mehrfachprozesse; ' + data
        detail = 'Worker PID ' + str(workers[0]['ProcessId']) if workers else ('Wrapper wartet auf Worker' if wrappers else 'kein CORE-Prozess beobachtet')
        return bool(workers), detail + '; ' + data
    except (RuntimeError, ValueError, TypeError, KeyError, subprocess.SubprocessError):
        return None, 'Prozesssicht UNKNOWN; ' + data


def start_core() -> None:
    global START_PENDING
    with ACTION_LOCK:
        processes = process_snapshot()  # fresh immediately before start
        if any(p['Role'] in {'Core', 'CoreWrapper'} for p in processes):
            START_PENDING = False
            return
        if START_PENDING:
            raise RuntimeError('Start bereits angefordert; Prozessbestaetigung fehlt')
        START_PENDING = True
        args = ['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(START_SCRIPT)]
        try:
            subprocess.Popen(args, creationflags=subprocess.CREATE_NO_WINDOW)
        except Exception:
            START_PENDING = False
            raise
        for _ in range(DASH_CONFIG['dashboard_start_poll_attempts']):
            if any(p['Role'] in {'Core','CoreWrapper'} for p in process_snapshot()):
                START_PENDING = False
                return
            time.sleep(DASH_CONFIG['dashboard_start_poll_seconds'])
        raise RuntimeError('Start nicht bestaetigt; keine erneute Startanforderung')


def stop_core() -> None:
    global START_PENDING
    with ACTION_LOCK:
        result = process_command("Stop-IMCRoles -Roles @('CoreWrapper','Core')")
        if result.returncode:
            raise RuntimeError('Stop nicht bestaetigt; Prozesspruefung oder Stop fehlgeschlagen')
        START_PENDING = False


PAGE = """<!doctype html>
<meta charset="utf-8">
<meta http-equiv="refresh" content="3">
<title>IMC Dashboard</title>
<style>
  body {{ font-family: system-ui, sans-serif; background: #111; color: #eee; padding: 32px; }}
  .dot {{ display:inline-block; width:14px; height:14px; border-radius:50%; margin-right:10px; }}
  .on {{ background: #2ecc71; }}
  .off {{ background: #e74c3c; }}
  h1 {{ font-size: 20px; }}
  .sub {{ color: #999; font-size: 13px; margin-top: 4px; }}
  form {{ display:inline; }}
  button {{
    font-size: 16px; padding: 12px 28px; margin: 16px 8px 0 0; border: none;
    border-radius: 8px; cursor: pointer; color: #fff;
  }}
  .start {{ background: #2ecc71; }}
  .stop {{ background: #e74c3c; }}
  button:disabled {{ background: #444; color: #888; cursor: not-allowed; }}
</style>
<h1><span class="dot {dotclass}"></span>CORE ist {statuswort}</h1>
<div class="sub">{detail} &middot; Seite aktualisiert sich alle 3s</div>
<form method="post" action="/start"><input type="hidden" name="csrf" value="{csrf}"><button class="start" {start_disabled}>Start</button></form>
<form method="post" action="/stop"><input type="hidden" name="csrf" value="{csrf}"><button class="stop" {stop_disabled}>Stop</button></form>
<div class="sub" style="margin-top:24px">Start/Stop wirken nur auf CORE (den Scanner). Andere Prozesse (Gateway, Bridge) bleiben unberuehrt.</div>
"""


class Handler(BaseHTTPRequestHandler):
    def setup(self):
        super().setup()
        self.connection.settimeout(DASH_CONFIG['dashboard_control_timeout_seconds'])
    def _render(self) -> bytes:
        running, detail = core_status()
        return PAGE.format(
            dotclass="on" if running else "off",
            statuswort="UNKNOWN" if running is None else "AN" if running else "AUS",
            detail=html.escape(detail),
            start_disabled="" if running is False else "disabled",
            stop_disabled="disabled" if running is None else "",
            csrf=CSRF_TOKEN,
        ).encode("utf-8")

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(self._render())

    def do_POST(self):
        try:
            size = int(self.headers.get('Content-Length','0'))
            if not 0 < size <= DASH_CONFIG['dashboard_control_body_bytes'] or self.headers.get('Transfer-Encoding'):
                raise ValueError()
            data = parse_qs(self.rfile.read(size).decode())
            supplied = data.get('csrf',[''])[0]
            if not hmac.compare_digest(supplied.encode('utf-8'), CSRF_TOKEN.encode('utf-8')):
                self.send_error(403, 'CSRF token required')
                return
        except (ValueError, UnicodeError):
            self.send_error(400, 'Invalid form')
            return
        try:
            if self.path == '/start':
                start_core()
            elif self.path == '/stop':
                stop_core()
            else:
                self.send_error(404)
                return
        except (RuntimeError, OSError, ValueError, subprocess.SubprocessError):
            self.send_error(503, 'Action not confirmed; inspect process status')
            return
        self.send_response(303)
        self.send_header("Location", "/")
        self.end_headers()

    def log_message(self, format, *args):  # ruhiger Server, keine Konsolen-Flut
        pass


def main() -> int:
    server = HTTPServer(("127.0.0.1", PORT), Handler)
    print(f"IMC Dashboard laeuft auf http://127.0.0.1:{PORT}  (Strg+C zum Beenden)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
