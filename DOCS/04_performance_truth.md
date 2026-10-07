# Die vier Performance-Ebenen

Jede Ebene beantwortet eine andere Frage. Sie nie isoliert zitieren —
immer zusammen lesen, sonst wird z. B. "PF 99" aus fünf Gewinnern
leicht als Gesamtergebnis missverstanden.

Letzte Vollverifikation: 01.10.2026, direkt gegen die Live-Datenbanken
gerechnet (nicht geschätzt).

| Ebene | Quelle | Stichprobe | Trefferquote | PF | Netto | Einordnung |
|---|---|---|---|---|---|---|
| CORE Paper | `CORE/data/imc-main.db` → `positions` | 82 Trades | 29.3% | 0.54 | −$45.85 | Hauptkonto, per Drawdown-Kill gesperrt seit 14.09.2026 |
| **SAFE100 Forward-Shadow** | `RUNTIME/watch-breadth-v425-shadow.db` → `picks` | 119 Trades | **79.0%** | **1.13** | **+$275.34** | Größte reale Stichprobe, einzige der 4 Varianten mit positivem PF |
| Jupiter-Execution-Twin v4.9 | `RUNTIME/real-forward-v490-live-twin.db` | 5 Trades | 100% | n. a.* | +16.6% Ø | *Sentinel ohne Verlust — Stichprobe zu klein für eine PF-Aussage; Top-3-Anteil 69.66% verletzt die 25%-Regel |
| 200-Trade Replay/Holdout | `IMC_PARITY_200_TEST/*.json` | synthetisch | 88–97% | 2.7–45 | — | Resampling-Test, andere Evidenzklasse — kein unabhängiger Forward-Beweis |

## Die vier Breadth-Watcher-Varianten im Detail

Von insgesamt 922 abgeschlossenen Forward-Trades über alle vier
Varianten ist **SAFE100 die einzige mit positivem Profit-Faktor**:

| Variante | Closed | Trefferquote | PF | Netto | Top-3-Anteil |
|---|---|---|---|---|---|
| CALIB | 519 | 31.79% | 0.287 | −$13,826.27 | 17.95% |
| SAFE30 | 153 | 65.36% | 0.715 | −$1,069.83 | 8.77% |
| SAFE50 | 131 | 74.05% | 0.948 | −$149.96 | 8.62% |
| SAFE100 | 119 | 78.99% | 1.127 | +$275.34 | 9.30% |

## Ziel vs. Ist

| Kriterium | Ziel | CORE | SAFE100 |
|---|---|---|---|
| Profit-Faktor | ≥ 2.0 | 0.54 ❌ | 1.13 ❌ |
| Trefferquote | ≥ 70% | 29.3% ❌ | 79.0% ✅ |
| Top-3-Gewinnanteil | ≤ 25% | n. a. | 9.3% ✅ |
| Max. Drawdown | ≤ 15% | 5.15% ✅ | n. a. |

**Lesart:** SAFE100 erfüllt bereits zwei von vier Kriterien. Der
Profit-Faktor ist der einzige noch offene, aber auch der wichtigste
Blocker für Live-Trading — siehe [[06_roadmap]], Stufe 1.

## Cross-Check-Hinweis

Diese Zahlen wurden am 01.10.2026 gegen ein unabhängig erstelltes
zweites Audit desselben Projekts geprüft — jede stichprobenhaft
nachgerechnete Behauptung (SAFE100-Statistik, PASS/WATCH-Verteilung,
Scan-Intervall, GMGN-Fehlermeldung) stimmte exakt. Details:
`decisions/0001-cleanup-baseline.md`.

## Wie man diese Tabelle aktuell hält

Nach jedem neuen Forward-Test-Batch kurz neu gegen die Datenbanken
rechnen (siehe Abfragen in `decisions/0001-cleanup-baseline.md` als
Vorlage) und diese Datei aktualisieren — nicht eine neue Datei danaben
anlegen.
