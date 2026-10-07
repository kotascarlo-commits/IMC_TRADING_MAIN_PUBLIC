# Betrieb

## Start / Stop / Status

### Gemeinsame Python-Umgebung

Die Trading-Launcher verwenden ausschließlich `.venv/Scripts/python.exe`.
`LAUNCHERS/IMC_PYTHON_COMMON.ps1` prüft vor Config-Schreibvorgängen und
Prozessaktionen Python 3.12 sowie die Imports CORE, NumPy und solders.
Fehlt die Umgebung oder ein Import, bricht der Launcher ab. Es gibt keinen
Fallback auf `py.exe` oder ein globales Python. Die geprüfte Installation
verwendet Python 3.12.10; direkte und transitive Pakete sind in
`requirements.txt` festgeschrieben.

Einrichtung aus dem Repo-Verzeichnis mit einem installierten Python 3.12:

```powershell
& '<Pfad zu Python 3.12>\python.exe' -m venv .venv
& .venv/Scripts/python.exe -m pip install -r requirements.txt
& .venv/Scripts/python.exe -m pip install --no-deps --no-build-isolation -e ./CORE
& .venv/Scripts/python.exe -m pip check
```

Die Umgebung bleibt lokal und wird nicht eingecheckt. Die Umstellung wurde
mit Import-/CLI-Prüfungen und Launcher-Fixtures getestet; ein produktiver
Start-/Stop-Durchlauf wurde dabei nicht ausgeführt. Die separaten optionalen
Launcher behalten ihre bisherigen Betriebsmodi.

### IMC-Arbeitschat

In einer frischen PowerShell `IMC` eingeben. Der Befehl öffnet den
konfigurierten Codex-Chat im Terminal mit Sol Medium und den Advisor-Regeln
aus `AGENTS.md` und `config/imc-advisor.json`; er startet keinen Trading-Dienst.
`IMC -Level routine` bzw. `IMC -Level complex` startet Sol Low bzw. High.
`IMC -Check` prüft Config und CLI-Installation ohne Modellstart.

`IMC -ShowLaunchConfig -Level complex` zeigt ausschließlich die geplanten
Startwerte, auch ohne CLI-/Broker-Verbindung. Es startet keine Prozesse und
verändert keinen Git-Index. Der Arbeitslauncher verwendet die zentralen
Werte `work.sandbox_mode=danger-full-access`, `work.approval_policy=never`
und `work.approvals_reviewer=user`. Alle drei werden vor dem Start geprüft
und ausdrücklich an Codex übergeben. Astra bleibt read-only.
Der GMGN-Read-only-Shim steht auch bei fehlendem oder ausgefallenem Broker
vor der echten CLI im PATH; ohne erreichbaren Broker wird der Aufruf
abgewiesen. Eine fehlende Shim-Datei verhindert den Arbeitsstart.

Git-Schreiboperationen erfolgen mit nativen Git-Kommandos innerhalb der
autonomen Entwicklungsfreigabe. Der Launcher startet keinen Git-Schreibbroker;
auch `-BrokerCheck` verändert den Index nicht und meldet Git-Schreibzugriff
als NOT_TESTED. Vorbestehende Git-Broker-Dateien wurden erhalten.
Die Startanzeige beschreibt Konfiguration, keinen erfolgreichen Schreibtest.

Launcherwerte gelten erst beim nächsten Sitzungsstart. Die aktuelle Sitzung
hat nativen Git-Schreibzugriff durch Commit `e7f0a9d` belegt. Vor Integration
den Staging-Stand lesen, gezielte Pfade/Hunks auswählen und den resultierenden
Diff prüfen. Keine Locks blind löschen, keine ACLs manipulieren und keinen
Host-Broker zur Umgehung verwenden. Die Entwicklungsfreigabe autorisiert
keinen Echtgeld-Broadcast.

Einmalige Einrichtung: `TOOLS/Install-IMC-Command.ps1` installiert den
versionierten Wrapper `TOOLS/IMC.cmd` in den Benutzer-npm-Ordner und ergänzt
ihn bei Bedarf im Benutzer-PATH. Bestehende PATH-Einträge bleiben erhalten;
PowerShell-Profile werden nicht geändert. Falls ein bereits geöffnetes
Terminal die PATH-Änderung nicht übernimmt, auch die Terminal-App neu öffnen.

Kanonischer Hauptpfad seit dem Clean Reset am 02.10.2026:

- `START_IMC.bat` / `LAUNCHERS/START_IMC.ps1` — bestehender Signal-only-Modus:
  CORE, Breadth/SAFE100, Pure-Signal-Gateway und GMGN-Signal-Bridge.
- `STOP_IMC.bat` / `LAUNCHERS/STOP_IMC.ps1` — beendet ausschließlich exakt
  zugeordnete IMC-Dienste; Restart-Wrapper zuerst, danach Kinder. Rolle, PID
  und Erstellungszeit werden vor dem Stop erneut geprüft. Stopfehler oder
  unbekannte Prozesse führen zum Abbruch statt zu einer Erfolgsmeldung.
- `STATUS_IMC.bat` / `LAUNCHERS/STATUS_IMC.ps1` — echte Prozesse mit Rollen
  und PIDs; fehlende Zugriffsrechte ergeben UNKNOWN. Gespeicherte JSONs
  werden nur als historische Statusdateien mit Zeitstempel aufgeführt.
- `START_IMC_SIGNAL_ONLY.ps1` / `STOP_IMC_SIGNAL_ONLY.ps1` bleiben kompatible
  Einstiege in denselben Standardmodus beziehungsweise kanonischen Stop.

Die gemeinsame Prozesszuordnung liegt in `LAUNCHERS/IMC_PROCESS_COMMON.ps1`.
Sie wertet die tatsächliche Skript-/Modulposition in der Windows-Kommandozeile
aus. Fremde gleichnamige Skripte, Codex-Prompts und Inline-Kommandos zählen
nicht als IMC-Dienste. Während der Umstellung wurden keine Trading-Dienste
gestartet oder gestoppt; die Zuordnung ist mit synthetischen Fällen geprüft.

Optionale Pfade bleiben separat erhalten:

- `START_IMC_MASTER.ps1` — setzt zusätzlich `v490.broadcast_enabled=false`,
  `require_local_live_confirm=true`, `v480.broadcast_default=false` beim Start
- `SHOW_IMC_MASTER_STATUS.ps1`, `SHOW_IMC_SIGNALS.ps1` — Statusanzeige

**Wichtig:** Ein `"state": "RUNNING"` in einer Status-JSON bedeutet nur
"letzter gespeicherter Heartbeat", nicht "Prozess läuft garantiert
noch". Bei Zweifel echten Prozess prüfen (`tasklist` nach `python.exe`/
`node.exe`), nicht nur die JSON-Datei lesen — siehe
`decisions/0001-cleanup-baseline.md` für ein konkretes Beispiel, bei dem
das zu einer Fehleinschätzung geführt hätte.

## Health-Quelle

### Execution-Ledger und geschützte Rückmeldungen

Der Signal-only-Start setzt keine Execution-Session mehr zurück. Vorhandene
Positionen, Claims, Sperren und Receipts werden übernommen. Legacy-JSONs mit
BUYs oder Pending werden nicht automatisch überschrieben oder als bestätigt
importiert. Konfiguration: `config/imc-execution.json`, gemeinsam validiert
durch `CORE/mykonos_meme_scout/execution_config.py`.

GET `/health` liefert die operative Ledger-Ansicht und kann fällige Timeout-
Sperren persistieren; GET `/next` liefert ausschließlich passive Intents.
POST `/ack`, `/control`, `/shadow`, `/reconcile` und Bridge-Fehlermeldungen
brauchen `Authorization: Bearer <IMC_EXECUTION_TOKEN>` aus lokaler Umgebung.
Keine Tokens oder GMGN-Credentials ins Repo schreiben. `/reconcile` akzeptiert
nur `{}` und liest die Beobachtungen selbst; Caller-Balances werden abgewiesen.
`/claim` bleibt gesperrt. `import_gmgn_fill.py --fill <Datei>` leitet vollständige
Feedback-Identitäten weiter; der Gateway prüft Fills unabhängig.

Der separate CORE-Steuerungsbildschirm schützt Formulare mit Sitzungstoken,
verwendet die gemeinsame Prozesszuordnung und blockiert bei UNKNOWN.
Das CORE-Paper-Dashboard zeigt Execution-Daten getrennt über `/api/execution`.
Gateway-Ausfall wird als UNAVAILABLE angezeigt. Details, Prüfbelege und
verbleibende Zugangshürde: [[execution-hardening]].

`RUNTIME/imc_system_health.py` ist die gemeinsame Health-Quelle. Einmalige
lesende Prüfung: `.venv/Scripts/python.exe RUNTIME/imc_system_health.py
--once`. Dieser Modus schreibt weder Status noch Lock und startet keine
Dienste. Der bisherige kontinuierliche Aufruf bleibt separat verfügbar;
er schreibt die lokale, ignorierte `IMC_SYSTEM_HEALTH.json`.

`config/imc-health.json` definiert Erwartungen, optionale Komponenten und
Schwellen. Der konfigurierte `PURE_SIGNAL_ONLY`-Modus erwartet CORE-Worker,
Watcher, Pure-Gateway und Bridge. CORE-Wrapper allein ersetzt keinen Worker.
Optionale Research-/Execution-Komponenten werden geprüft, wenn tatsächlich
beobachtet; Konflikte mit dem gespeicherten Modus und doppelte Prozesse
werden gemeldet. Andere Modi sind bis zur belegten Zuordnung unbekannt und
ergeben DEGRADED. Der Modus ist eine Erwartung, kein Prozessnachweis.

Die echte CIM-Aufnahme verwendet die gemeinsame Prozesszuordnung. Bei
Timeout oder fehlenden Kommandozeilen ist die Sicht UNKNOWN/teilweise.
Relative Skriptpfade oder unbekannte Argumentformen können unerkannt bleiben.
Datenfrische und Prozesspräsenz werden getrennt geprüft. Statusdateien sind
historische Informationen; Gateway-/Bridge-Dateien haben keinen periodischen
Heartbeat und werden deshalb nicht allein wegen ihres Alters beanstandet.
SQLite-Probes und Rate-Budget-Inspektion sind read-only einschließlich WAL.
Die Health-Ausgabe garantiert weder Broadcast noch dessen Ausschluss:
`authority_guard=UNKNOWN`, `live_execution_any=null`.

## Runbook: Was tun bei …

| Störfall | Vorgehen |
|---|---|
| **RPC antwortet nicht / 429** | `imc_resilience.py` bremst automatisch über globale SQLite-Buckets; prüfen, ob `RUNTIME/IMC_SYSTEM_HEALTH.json` Fehler zeigt. Bei Dauerproblem: dedizierten RPC-Anbieter erwägen (siehe [[03_apis_and_services]]). |
| **Kill-Switch ausgelöst (`kill_reason` gesetzt)** | Nicht einfach zurücksetzen, ohne den Grund zu verstehen — `account_state` in `imc-main.db` prüfen (`day_start_equity`, `high_water_equity`, aktuelles `cash_usd`). Reset ist eine bewusste Entscheidung, kein Routine-Fix. |
| **Wallet fast leer** | `WALLET/wallet_status.py` ausführen (reine Lese-RPC, ungefährlich). Vor jeder Aufstockung Kapitalplan aus [[06_roadmap]] (Stufe 3) durchgehen. |
| **Prozess abgestürzt** | CORE hat einen Restart-Loop; optionale Runtime-/Research-Watchdogs starten ihre Kinder erneut. Das gilt nicht pauschal für alle Dienste des Signal-only-Modus. Zuerst echten Prozessbestand über STATUS prüfen, dann den passenden Launcher verwenden. |
| **Jupiter-Route schlägt dauerhaft fehl** | Prüfen, ob `JUPITER_API_KEY` gesetzt ist; ohne Key greift das enge Lite-Rate-Limit (siehe [[03_apis_and_services]]). |
| **GMGN-Bridge-Fehler (`ConnectionResetError`)** | Bridge hat eingebautes Retry; bei Dauerproblem Grundsatzfrage aus [[03_apis_and_services]] ("GMGN fertig schließen oder weglassen") klären, statt Symptome zu patchen. |

## Was NICHT tun

- Keine Parameter per Script zur Laufzeit in eine `.py`-Datei
  hineinpatchen (ehemaliges `TEMP_LOOSE_ONE_SIGNAL.ps1`-Muster) — Werte
  liegen jetzt in [[02_configuration]] dokumentiert, dort ändern.
- Keine neue `vNNN`-Datei neben einer bestehenden anlegen — eine neue
  Iteration ist ein Git-Commit/Tag, keine neue Datei. Siehe [[../AGENTS.md]].
- Keine `.db`/`.db-wal`/`.db-shm`-Datei committen (per `.gitignore`
  bereits ausgeschlossen).
