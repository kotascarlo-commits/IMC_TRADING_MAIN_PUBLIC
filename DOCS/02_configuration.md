# Konfiguration

Siehe [[../config/README]] für die Begründung, warum die bestehenden
Dateien an ihrem heutigen Ort bleiben statt in `config/` verschoben zu
werden.

## CORE (Python, `mykonos_meme_scout.Config`)

Interpreter und Abhängigkeiten: eine lokale Python-3.12-Umgebung `.venv`,
mit exakten Paketversionen in `requirements.txt`. Einrichtung und
Launcher-Preflight stehen in [[05_operations]]. Diese Umstellung ändert
keine Scan-, Strategie- oder Execution-Parameter.

Schema und Validierung in `CORE/mykonos_meme_scout/config.py`. Wichtige
Defaults (vollständige Liste im Code, hier nur die für den aktuellen
Stand relevanten):

| Feld | Default | Bemerkung |
|---|---|---|
| `live_trading_enabled` | `False` | `__post_init__` wirft `ValueError`, falls `True` gesetzt wird — kann nicht versehentlich aktiviert werden |
| `scan_interval_seconds` | `15` | **Tatsächlich genutzt wird 0.5s** — siehe unten |
| `minimum_score` | `72` | Mindest-Score für eine PASS-Entscheidung |
| `maximum_entry_age_minutes` | `15` | First-15-Minutes-Fenster |
| `hard_stop_pct` | `12.0` | Stop-Loss |
| `account_drawdown_kill_pct` | `5.0` | Kill-Switch-Schwelle vom Hochwasserstand |
| `daily_loss_kill_pct` | `1.0` | Kill-Switch-Schwelle, greift in der Praxis meist vor `account_drawdown_kill_pct` |

### Das tatsächliche Scan-Intervall

`CORE/START_CORE_WORKER.ps1` startet den Worker mit `--interval 0.5` —
nicht mit dem Config-Default von 15 Sekunden. Das ist die Hauptursache
für das Datenbankwachstum (siehe `decisions/0001-cleanup-baseline.md`
und [[04_performance_truth]]). Wer das ändern will, passt den
`--interval`-Parameter in `START_CORE_WORKER.ps1` an, nicht den
Config-Default (sonst driften Launcher und Doku wieder auseinander).

## Execution-Twin / Gateway (JSON, siehe `config/README.md` für Fundorte)

- `config_v480.json` — `position_fraction`, `calibration_input_lamports`,
  `max_calibration_roundtrip_loss`, `broadcast_default` (immer `false`)
- `config_v490_live.json` — `max_open_positions`, `max_session_trades`,
  `max_buy_age_seconds`, `broadcast_enabled` (immer `false`),
  `require_local_live_confirm` (immer `true`)

## Breadth-Watcher-Varianten

Hartcodiert als Dict in `RUNTIME/watch_breadth_v425_shadow.py`:

```python
'CALIB':  {'liq':10000, 'br':.45, 'lo':5,  'hi':500, 'age':15, 'vr':5.0}
'SAFE30': {'liq':30000, 'br':.50, 'lo':10, 'hi':100, 'age':3,  'vr':1.0}
'SAFE50': {'liq':50000, 'br':.70, 'lo':10, 'hi':500, 'age':5,  'vr':.5}
'SAFE100':{'liq':100000,'br':.75, 'lo':20, 'hi':500, 'age':30, 'vr':5.0}
```

`liq` = Mindestliquidität USD, `br` = Mindest-Buy-Ratio, `lo`/`hi` =
Momentum-Band in %, `age` = max. Alter in Minuten, `vr` = max.
Volumen/Liquidität-Verhältnis. **Diese Werte nicht per Script zur
Laufzeit patchen** (das frühere `TEMP_LOOSE_ONE_SIGNAL.ps1`-Muster wurde
deshalb entfernt, siehe `decisions/0001-cleanup-baseline.md`) — stattdessen
hier im Dict direkt ändern und committen.

## Fehlende/optionale Schlüssel

- `JUPITER_API_KEY` (Env-Var) — aktuell nicht gesetzt. Siehe
  [[03_apis_and_services]] für die Auswirkung auf die Latenz.

## IMC-Advisor

`config/imc-advisor.json` legt die Arbeits- und Advisor-Levels fest:

- `model`: `gpt-6-astra`
- `reasoning_effort`: `low` für normale Code-Reviews
- `risk_reasoning_effort`: `medium` für `-RiskReview`
- `max_response_words`: `160` als Antwortziel, ohne wesentliche Risiken wegzulassen
- `work.model`: `gpt-6.1-sol`
- `work.routine`: `low`, `work.standard`: `medium`, `work.complex`: `high`

`TOOLS/Get-IMC-AdvisorConfig.ps1` lädt und validiert die Einstellungen;
die Launcher verwenden sie auch für ihre Anzeigen und Rollenbeschreibung.
Eine fehlende oder ungültige Config bricht den Aufruf ab. Es gibt keine
stille Hochstufung und kein XHigh/Max im normalen Betrieb. Der Advisor
arbeitet weiterhin ausschließlich lesend. Die Review-Pflicht und ihre
Routineausnahmen stehen in [[../AGENTS]].

Aufrufe:

- `imc-ask-advisor "Aufgabe + Ansatz + relevante Quellen"` — Astra Low
- `imc-ask-advisor -RiskReview "Risikoentscheidung + Ansatz + Quellen"` — Astra Medium
- `TOOLS/Start-IMC-Advisor.ps1 -RiskReview` — interaktiver Advisor mit Medium
- `TOOLS/Start-IMC-Work.ps1` — neue Sol-Sitzung mit Medium
- `TOOLS/Start-IMC-Work.ps1 -Level routine` bzw. `-Level complex` — Low bzw. High

Eine laufende Sitzung wird durch Config-/Launcheränderungen nicht umgestellt.
Einsparungen hängen von Aufgabe, Kontext und Kontingent ab; eine feste
Arbeitsdauer ist nicht garantiert.
