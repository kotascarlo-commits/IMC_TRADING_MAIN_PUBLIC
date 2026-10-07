# IMC Trading — Überblick

> Second-Brain-Vault für IMC Trading. Diese Dateien sind normales Markdown
> mit `[[wiki-links]]` — öffne den `DOCS/`-Ordner direkt als Obsidian-Vault,
> oder lies sie in jedem Editor/GitHub. Agenten (Codex/ChatGPT, Claude Code)
> lesen dieselben Dateien über [[../AGENTS.md]] / [[../CLAUDE.md]].

## Was IMC ist

IMC ist ein selbst gebautes **Research- und Paper-Trading-System für neu
gelistete Solana-Memecoins**. Es scannt fortlaufend frisch entstandene
Liquiditätspools, bewertet jeden Token in den ersten 15 Minuten nach dem
Launch anhand von Sicherheits- und Momentum-Kriterien und simuliert darauf
basierend Käufe und Verkäufe nach festen Risikoregeln.

Mehr Details zur Funktionsweise: [[01_architecture]].

## Die wichtigste Regel beim Lesen von Ergebnissen

IMC führt **nicht nur eine** Simulation, sondern mehrere parallele
"Wahrheitsebenen" gleichzeitig — ein Hauptkonto (CORE), eine
Forschungs-Schattendatenbank mit vier konkurrierenden Parametervarianten
(CALIB/SAFE30/SAFE50/SAFE100), einen Ausführungs-Zwilling, der echte
Solana-Transaktionen baut, und synthetische Replay-Tests. Diese Ebenen
liefern unterschiedliche Zahlen und dürfen nicht vermischt werden.
Siehe [[04_performance_truth]] für die aktuelle, verifizierte Übersicht.

## Aktuelles Ziel

Die Strategie soll in der Simulation auf folgende Werte gebracht werden,
**bevor** echtes Kapital automatisiert eingesetzt wird:

| Kriterium | Ziel |
|---|---|
| Profit-Faktor | ≥ 2.0 |
| Trefferquote | ≥ 70% |
| Max. Drawdown | ≤ 15% |
| Top-3-Gewinnanteil | ≤ 25% |

Eine Variante (SAFE100) erfüllt davon bereits zwei von vier Kriterien —
siehe [[04_performance_truth]]. Der Stufenplan bis zu echtem Live-Trading
steht in [[06_roadmap]].

## Live-Status

Live-Ausführung ist an mehreren unabhängigen Stellen hart deaktiviert
(Config-Assertion im Code, Authority-Flags überall 0%, Broadcast überall
`false`). Details: [[05_operations]].

## Navigation

- [[01_architecture]] — Komponenten, Datenflüsse, DB-Schema
- [[02_configuration]] — wo welche Einstellung lebt
- [[03_apis_and_services]] — angebundene APIs, Status, Alternativen
- [[04_performance_truth]] — die vier Performance-Ebenen
- [[05_operations]] — Start/Stop/Status, Runbook für Störfälle
- [[06_roadmap]] — Stufenplan bis Live-Trading
- [[glossary]] — SAFE100, CALIB, PF2, Governor & Co. erklärt
- `decisions/` — Architecture Decision Records, ein File pro wichtiger Entscheidung
