# Stufenplan bis Live-Trading

Acht Stufen vom heutigen Stand (alles simuliert, Authority 0%) bis zu
echtem, unbeaufsichtigtem Live-Trading. Siehe [[04_performance_truth]]
für die Zahlen, auf die sich dieser Plan stützt.

## Stufe 0 — Ausgangslage: sauber abgesichert ✅

Live-Ausführung ist an mehreren unabhängigen Stellen hart deaktiviert:
Config wirft `ValueError` bei `live_trading_enabled=True`,
Authority-Flags überall 0%, Broadcast überall `false`.

## Stufe 1 — Strategie auf die eigenen Zielwerte bringen ❌ größter Blocker

Reine Research-Arbeit, kein Infrastrukturthema. SAFE100 ist mit PF 1.13
die beste Variante, aber noch deutlich unter dem Ziel von ≥2.0. Scoring-
Gewichte, Entry-/Exit-Regeln und ggf. neue Signal-Features überarbeiten,
bevor über eine Kapitalerhöhung nachgedacht wird.

## Stufe 2 — Ausführungspipeline zuverlässig machen ⚠ teilweise

Im v4.9-Twin kommen nur 23% der Signale überhaupt zu einer geschlossenen
Position. Siehe [[03_apis_and_services]]: dedizierter RPC-Endpoint +
Jupiter-API-Key sind die zwei konkreten nächsten Schritte.

## Stufe 3 — Kapitalplan festlegen ❌

Aktuelles Wallet: 0.0166 SOL (≈ $1.95) — strukturell zu wenig selbst für
die kleinste konfigurierte Positionsgröße. Vor jeder Aufstockung: bewusste
Entscheidung über Kapitalhöhe und Risikobudget, plus ein nachvollziehbarer
Weg für Ein-/Auszahlungen ins Hot-Wallet.

## Stufe 4 — Signierung & Broadcast Ende-zu-Ende ⚠ Grundlage vorhanden

Zwei echte Buy→Sell-Testtrades (24.09.2026) belegen, dass der
Build/Sign/Broadcast-Pfad funktioniert und exakt mit der Simulation
übereinstimmt. Offene Entscheidung: Privy fertig verdrahten oder bewusst
bei der lokalen DPAPI-Wallet bleiben (siehe [[03_apis_and_services]]) —
nicht beides halbfertig parallel pflegen.

## Stufe 5 — Stufenweisen Rollout formalisieren ⚠ Baustein existiert

`session_mode: THREE_TRADE_TEST`, `max_session_trades: 3`,
`max_open_positions: 1` sind bereits im v4.9-Gateway vorhanden. Zu einem
festen Ablauf ausbauen: z. B. 3 Trades → manuelle Review → 10 Trades →
Review → offener Betrieb.

## Stufe 6 — Monitoring & Alerting für echtes Geld ❌

Aktuell muss jede Statusabfrage manuell erfolgen. Aktive Benachrichtigung
bei jedem Fill, jedem Kill-Switch-Trigger und bei ausbleibendem Heartbeat
einrichten — siehe [[05_operations]] für den aktuellen (manuellen) Stand.

## Stufe 7 — Betriebs-Runbook ✅ Grundlage angelegt

Siehe [[05_operations]] — wird bei jedem neuen Störfall-Typ ergänzt.
