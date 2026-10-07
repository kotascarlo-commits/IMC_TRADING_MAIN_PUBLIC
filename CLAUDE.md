# CLAUDE.md

Diese Datei gilt speziell für Claude Code in diesem Repo. Die
inhaltlichen Regeln sind identisch mit [`AGENTS.md`](AGENTS.md) — beide
existieren, weil ChatGPT/Codex und Claude Code unterschiedliche
Standard-Dateinamen für Agenten-Instruktionen lesen. **Bei einem
Widerspruch zwischen den beiden Dateien: `AGENTS.md` aktualisieren und
diese Datei synchron halten, nicht zwei unterschiedliche Regelsätze
pflegen.**

Siehe [`AGENTS.md`](AGENTS.md) für die vollständigen Arbeitsregeln
(Live-Trading-Flags, keine `.db`-Commits, keine `vNNN`-Dateien, erst
fragen vor dem Löschen) und [`DOCS/00_overview.md`](DOCS/00_overview.md)
als Einstiegspunkt in die Projektdokumentation.

## Claude-Code-spezifisch

- Dieses Projekt enthält eine ~30-GB-SQLite-Datenbank
  (`CORE/data/imc-main.db`). Lies sie nur per `sqlite3`-Verbindung im
  Read-Only-Modus (`file:...?mode=ro`) mit kurzem `timeout` — niemals
  die Datei selbst kopieren oder vollständig einlesen.
- Mehrere Prozesse (Python-Worker, Node-Gateway) können parallel laufen
  und dieselben Dateien/DBs halten. Vor destruktiven Dateioperationen im
  laufenden Betrieb: `tasklist` prüfen.
- Tests: `pytest` vom Repo-Root (nutzt `tests/conftest.py`, das `CORE/`
  auf `sys.path` legt — kein vorheriger `pip install` nötig). Falls
  `pytest` mit einem `PermissionError` auf `AppData/Local/Temp` fehlschlägt
  (sandboxspezifisch), einmalig mit `--basetemp=.pytest_tmp` starten.
