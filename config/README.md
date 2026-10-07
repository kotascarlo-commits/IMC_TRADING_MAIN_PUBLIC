# Config-Übersicht

Dieser Ordner ist der vorgesehene zentrale Ort für **neue** Konfiguration.

**Die bestehenden Config-Dateien wurden bewusst nicht hierher verschoben.**
Sie sind in 18 verschiedene aktive Dateien eingebrannt (Pfad-Strings in
`.py`, `.js`, `.mjs`, `.ps1`) — darunter der bereits real validierte
Signier-/Broadcast-Pfad (`gateway_v480.py`, `signal_gateway_v490.py`,
`PRIVY_PREP/*`). Ein Massen-Umzug mit Anpassung aller 18 Referenzen wäre
genau in dem Teil des Systems riskant, der am wertvollsten erwiesen ist
(zweifach reale SIM↔REAL-Validierung). Siehe
`../DOCS/decisions/0001-cleanup-baseline.md` für die Begründung.

## Wo die bestehenden Configs tatsächlich liegen

| Datei | Ort | Zweck |
|---|---|---|
| `imc-advisor.json` | `config/` | Arbeits-Levels Sol Low/Medium/High; danger-full-access/never mit Reviewer user; Astra High über Read-only-Host-IPC |
| `owner-broadcast-gate.json` | `config/` | Einzige vorgesehene Owner-Autorisierung, Standard OFF; ungültig/fehlend bleibt OFF. Der vorhandene passive Pfad ist noch kein ausführungfähiger Live-Adapter. |
| `imc-execution.json` | `config/` | Ledger-/Quote-/Risk-/Reconciliation-/Dashboard-Grenzen; ledger_lock_timeout_seconds steuert Init-/SQLite-Lockwartezeit (5s, maximal 30s); Live-Authority/Signing/Broadcast bleiben 0/false |
| `config_v480.json` | `RUNTIME/EXECUTOR_GATEWAY/` | Build-Parity-Kalibrierung (v4.8) |
| `config_v490_live.json` | `RUNTIME/EXECUTOR_GATEWAY/LIVE_V490/` | Live-Signal-Gateway (v4.9), Session-Limits |
| `privy_config.template.json` | `RUNTIME/EXECUTOR_GATEWAY/PRIVY_PREP/` | Vorlage für Privy-Wallet-Setup |
| `BASELINE_15_CONFIG.json`, `BASELINE_200_CONFIG.json` | `DOCS/BASELINE_200/` | Referenz-Parametersätze für Replay-Tests |
| Python-`Config`-Dataclass | `CORE/mykonos_meme_scout/config.py` | Schema + Validierung für den CORE-Hauptmotor; lädt optional via `MYKONOS_CONFIG`-Env-Var oder `Config.load(path)` |

Vollständige Erklärung jedes Feldes: [[../DOCS/02_configuration]].

## Regel ab jetzt

Neue, noch nicht fest verdrahtete Konfiguration gehört hierher, nicht in
neue `vNNN`-Dateien neben dem Code. Siehe [[../AGENTS.md]] / [[../CLAUDE.md]].
