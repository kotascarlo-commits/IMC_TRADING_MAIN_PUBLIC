# Architektur

Siehe auch [[00_overview]] für den Kontext.

## Datenfluss (kanonischer Signal-only-Pfad)

```text
Pool-Discovery (GeckoTerminal, DexScreener)
        ↓
Security-Check (GoPlus, fail-closed)
        ↓
CORE-Scoring (CORE/mykonos_meme_scout/engine.py, scoring.py)  →  PASS / WATCH
        ↓
Breadth-Watcher (RUNTIME/watch_breadth_v425_shadow.py)  →  CALIB / SAFE30 / SAFE50 / SAFE100
        ↓ (SAFE100-Signal)
SAFE100 picks (watch-breadth-v425-shadow.db)
        ↓
Pure-Signal-Gateway (GMGN_BRIDGE/imc_pure_signal_gateway.py)
  127.0.0.1:49090 — /health /next /ack /control /reconcile /shadow
  ↔ execution-ledger.db: Trade-ID, State, Orders, Receipts, Audit
        ↓
GMGN-Signal-Bridge (RUNTIME/EXECUTOR_GATEWAY/GMGN_BRIDGE/)
  schreibt "execution_state": "READY_FOR_GMGN_EXECUTOR" — Kette endet hier
        ↓  ⚠ nicht geschlossen
Echtgeld-Ausführung gesperrt (Authority 0%, broadcast_enabled: false)
```

Parallel dazu läuft ein Online-Learning-Pfad (Snapshots → Outcomes →
Neural-Adaptive-Modell → Champion/Challenger-Vergleich), der das
Scoring kontinuierlich weiterentwickelt, aber nie automatisch
Ausführungsrechte erhält.

Der Execution-Twin v480/v490 und dessen eigener Signal-Gateway sind optionale
separate Betriebsmodi. Sie gehören nicht zum kanonischen Signal-only-Start.
Diese Verdrahtung belegt keinen aktuellen Prozessstatus.

Der bestehende Pure-Gateway verwendet jetzt einen transaktionalen Execution-
Ledger. ACK allein ist kein Fill. Reale Positionen benötigen einen passend
gebundenen GMGN-Orderbericht und unabhängig gelesene finalized RPC-Daten;
CLOSED benötigt zusätzlich Executor-/Token-/Native-Balance-Reconciliation.
`/claim` bleibt gesperrt, die Bridge schreibt weiterhin `execution=false`.
Der vollständige verifizierte Arbeitsstand und offene Live-/Shadow-/Cleanup-
Punkte stehen in [[execution-hardening]]; Dateieinstufungen in
`execution-audit.json`.

## Komponenten (Top-Level-Ordner)

| Ordner | Rolle |
|---|---|
| `CORE/` | Haupt-Entscheidungsmotor, Python-Paket `mykonos_meme_scout`. Scoring, Lernschleife, Paper-Ledger (SQLite, `CORE/data/imc-main.db`). |
| `CORE/mykonos_meme_scout/brain_v1/` | Eigenständiges RNN-/Governor-Subsystem (13 Dateien). **Nicht verdrahtet** — siehe `decisions/0001-cleanup-baseline.md`. |
| `RUNTIME/` | v4.8/v4.9 Execution-Twin, Breadth-Watcher (4 Varianten), System-Health-Watchdog, Resilience-Layer. |
| `RUNTIME/EXECUTOR_GATEWAY/` | Node.js/TypeScript-Dienst: baut und signiert echte Solana-Transaktionen (Jupiter v2), inkl. GMGN-Bridge und Privy-Setup. |
| `RESEARCH/` | Forward-only Strategie-Experimente (VR12, DG120, Filter-Sweeps), eigene DBs, kein Rückkanal zur Live-Ausführung. |
| `WALLET/` | Minimaler Balance-/Aktivitäts-Check (reine Lese-RPC-Aufrufe). |
| `LAUNCHERS/` | PowerShell Start/Stop/Status je Lane. |
| `config/` | Zentrale Konfigurationsreferenz — siehe [[02_configuration]]. |
| `DOCS/` | Dieser Vault. |
| `tests/` | pytest-Suite für Config/Scoring/Risk-Kill. |

## Zentrale Datenbanken

| Datei | Inhalt | Größenordnung |
|---|---|---|
| `CORE/data/imc-main.db` | Paper-Ledger des CORE-Hauptkontos: `snapshots`, `decisions`, `positions`, `trades`, `learning_predictions`, `learning_outcomes`, `account_state` | ~30 GB, Millionen Zeilen — siehe [[02_configuration]] zur Scan-Frequenz und `decisions/` zur Retention-Entscheidung |
| `RUNTIME/watch-breadth-v425-shadow.db` | `picks`-Tabelle: Forward-Ergebnisse der 4 Breadth-Varianten (CALIB/SAFE30/SAFE50/SAFE100) mit `net_pnl`, `status`, `exit_reason` | ~191.000 Picks, 922 davon abgeschlossen |
| `RUNTIME/real-forward-v490-live-twin.db`, `real-forward-v480-build-parity.db` | Execution-Twin-Trades, Equity-Historie | klein |

## Warum `brain_v1` separat dokumentiert ist

Eine projektweite Codesuche findet keine einzige Referenz auf
`brain_v1` außerhalb seines eigenen Ordners — `engine.py` und `cli.py`,
der Code, der tatsächlich die Paper-Trades macht, importieren nichts
davon. Die dort beschriebenen Governor-/Sicherheitsmechanismen (RNN mit
7 Spezialisten, Champion/Challenger-Gates) sind technisch real,
aber aktuell folgenlos, weil niemand sie aufruft. Entscheidung dazu:
`decisions/0001-cleanup-baseline.md`.
