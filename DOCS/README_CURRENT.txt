IMC TRADING - CURRENT STRUCTURE
===============================
(aktualisiert 01.10.2026 - siehe DOCS/ fuer die vollstaendige Doku)

CURRENT MILESTONE
- v4.8 execution path validated twice with real BUY -> SELL tests.
- Simulation-to-real execution parity confirmed for fees, compute, wallet delta and token delta.
- Live trading remains disabled.
- SAFE100 is the only breadth-watcher variant with a positive forward
  profit factor so far (1.13 over 119 closed trades) - still below the
  PF >= 2.0 target. See DOCS/04_performance_truth.md.

TOP LEVEL (aktueller Stand, ersetzt die alte IMC_DOCS/IMC_ARCHIVE-Struktur
weiter unten, die es in diesem Projekt nicht mehr gibt)
- CORE\            Entscheidungsmotor "mykonos_meme_scout" (Python), Paper-Ledger.
- RUNTIME\         v4.8/v4.9 Execution-Twin, Breadth-Watcher, Health-Watchdog.
- RUNTIME\EXECUTOR_GATEWAY\  Node.js-Dienst: Jupiter-Build, Signierung, GMGN-Bridge.
- RESEARCH\        Forward-only Strategie-Experimente (VR12, DG120, Sweeps).
- WALLET\          Balance-/Aktivitaets-Check (reine Lese-RPC-Aufrufe).
- LAUNCHERS\       Start/Stop/Status-Skripte je Lane.
- config\          Zentrale Konfigurationsdateien (siehe DOCS/02_configuration.md).
- docs\            Second-Brain-Vault - Architektur, APIs, Performance, Roadmap, Glossar.

HINWEIS ZUR FRueHEREN STRUKTUR
Diese Datei verwies frueher auf "IMC_DOCS" und "IMC_ARCHIVE" mit sieben
Unterordnern - diese Struktur existiert im aktuellen Projekt nicht mehr.
Seit 01.10.2026 ist das Projekt zusaetzlich unter Git versioniert; alte
Dateistaende sind ueber die Git-Historie statt ueber Archiv-Ordner
abrufbar.

DO NOT USE DISABLED/ARCHIVED SCRIPTS AS CURRENT EXECUTION CODE.
