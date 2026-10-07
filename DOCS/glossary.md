# Glossar

- **CALIB / SAFE30 / SAFE50 / SAFE100** — vier Parameter-Varianten im
  Breadth-Watcher (`RUNTIME/watch_breadth_v425_shadow.py`), die
  unabhängig voneinander dieselben Token nach unterschiedlich strengen
  Liquiditäts-/Buy-Ratio-/Momentum-Schwellen bewerten. SAFE100 ist aktuell
  die einzige mit positivem Forward-Profit-Faktor. Siehe [[04_performance_truth]].
- **PF (Profit-Faktor)** — Bruttogewinn geteilt durch Bruttoverlust über
  alle abgeschlossenen Trades. PF < 1 heißt: insgesamt Verlust. PF ≥ 2.0
  ist das eigene Zielkriterium.
- **Top-3-Gewinnanteil** — welcher Anteil des Gesamtgewinns auf die drei
  besten Einzeltrades entfällt. Hoch = Ergebnis hängt an wenigen
  Ausreißern statt an einer robusten Strategie. Ziel: ≤ 25%.
- **Kill-Switch / `kill_reason`** — automatische Handelssperre im
  `account_state` des Paper-Kontos, ausgelöst bei zu großem Tages-,
  Wochen- oder Drawdown-Verlust (`daily_loss_limit`, `weekly_loss_limit`,
  `drawdown_limit`). Bleibt aktiv, bis explizit zurückgesetzt.
- **Authority / `authority_pct`** — wie viel Entscheidungsgewalt ein
  Modell oder eine Lane über echtes Kapital hat. Steht überall auf 0%.
- **Execution-Twin (v4.8/v4.9)** — Pfad, der echte Solana-Transaktionen
  baut (Jupiter Quote → Build → lokale Simulation), um zu prüfen, ob die
  Paper-Simulation realistisch ist. Kann bei Bedarf winzige echte
  Beträge signieren, aber Broadcast ist deaktiviert.
- **GMGN-Bridge** — optionale externe Signalweiterleitung im Copy-Trading-
  Stil. Endet aktuell bei `READY_FOR_GMGN_EXECUTOR` — die eigentliche
  Ausführung ist nicht angeschlossen.
- **brain_v1 / SequenceBrain / Governor** — eigenständiges, technisch
  aufwendiges RNN-Subsystem mit mehreren "Spezialisten" und einem
  Champion/Challenger-Gate. Aktuell **nicht verdrahtet** (siehe
  [[01_architecture]]).
- **PASS / WATCH** — Entscheidung des CORE-Scorings. PASS = Score und
  alle Hard-Gates erfüllt, Entry grundsätzlich möglich. WATCH = beobachtet,
  aber (noch) nicht qualifiziert.
- **fail-closed** — Sicherheitsprinzip der GoPlus-Integration: fehlen
  Sicherheitsdaten zu einem Token, wird das als Risiko behandelt statt
  ignoriert.
