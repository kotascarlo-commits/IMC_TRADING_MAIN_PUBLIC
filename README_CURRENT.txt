IMC TRADING MAIN
================

This is the single current IMC Trading project folder.

CORE
- Current decision/data engine.
- Active database: CORE\data\imc-main.db
- Python package keeps its historical internal module name for compatibility.

RUNTIME
- Current v4.8 calibrated execution simulation.
- Watcher, learner, status databases and executor gateway.
- Live broadcast remains OFF until performance targets are met.

WALLET
- Wallet status/control helpers.
- Current wallet address is configured.

DOCS
- Double SIM-vs-REAL validation milestone and current notes.

LAUNCHERS
- START_IMC.ps1 / STOP_IMC.ps1 / STATUS_IMC.ps1

ROOT SHORTCUTS
- START_IMC.bat
- STOP_IMC.bat
- STATUS_IMC.bat

Validated milestone:
- 2 complete real BUY->SELL calibration runs
- SIM/REAL execution path validated
- Next phase: optimize calibrated simulation to PF >= 2, hit rate >= 70%, DD <= 15%, Top-3 <= 25%
