# APIs & angebundene Dienste

| Dienst | Zweck | Status |
|---|---|---|
| GeckoTerminal API | Discovery frisch entstandener Solana-Pools (10s-Throttle, Cache-Fallback bei 429) | aktiv |
| DEX Screener API | Primärer Markt-/Preis-Feed, Token-Profile & Boosts, Watch-Refresh offener Positionen | aktiv, primär |
| GoPlus Security API | Token-Sicherheit: Mint-/Freeze-Authority, Honeypot, Holder-Konzentration — fail-closed bei fehlenden Daten | aktiv |
| Jupiter Swap v2 (api.jup.ag) | Quote, Build & Execute für echte Swaps auf Solana | konfiguriert, Broadcast aus, **kein API-Key** |
| Solana Mainnet-RPC | Balance-Abfragen, Signaturen, Transaktionsbau | aktiv, öffentlicher RPC (ratenlimitiert) |
| Privy | Embedded-Wallet-Signierung für spätere Live-Nutzung | vorbereitet, nicht verbunden |
| GMGN CLI | Externe Signalweiterleitung / Portfolio-Abfrage | optional, Pure-Signal-Modus, Kette endet bei `READY_FOR_GMGN_EXECUTOR` |

## Alternativen-Bewertung (Empfehlungen, keine Umsetzung — brauchen Accounts/Keys)

### Solana RPC
**Ist:** öffentlicher `api.mainnet-beta.solana.com`, geteilt mit der ganzen
Welt, keine Priorität, strenge Rate-Limits.
**Alternativen:**
- **Helius** — Solana-spezialisiert, kostenloses Test-Tier, integrierter
  Jito-Bundle-Support. Erster Kandidat.
- **QuickNode** — breiter Multi-Chain-Anbieter, solide Solana-Performance.
- **Triton/RPCFast, Chainstack** — weitere dedizierte Optionen.

**Warum wichtig:** Das durchschnittliche Signalalter im v4.9-Twin lag bei
~4,77s (verifiziert aus `v480_build_parity_status.json`), weit über dem
eigenen 1-4s-Zielfenster. Ein dedizierter RPC-Endpoint ist ein Teil der
Lösung, der zweite ist der fehlende Jupiter-Key (siehe unten).

### Jupiter
**Ist:** kein `JUPITER_API_KEY` gesetzt → Lite-Tier mit ~2,2s
Mindestabstand zwischen Requests. Das ist eine direkte Ursache des
Latenzproblems oben.
**Empfehlung:** API-Key beantragen — größter Einzelhebel gegen die
Latenz, bevor an RPC-Anbietern optimiert wird.

### GMGN CLI vs. direkte Ausführung
**Aktueller Code-/Zugriffsbefund 02.10.2026:** Die Bridge schreibt weiterhin
`"execution": false` / `READY_FOR_GMGN_EXECUTOR`; ein echter Swap wird nicht
aufgerufen. `gmgn-cli` 1.6.6 ist lokal installiert. Der neue Ledger-/Fill-/
Reconciliation-Vertrag ist isoliert getestet, die reale Antwortprüfung ist
noch offen. Der lokale Read-only-Broker ist inzwischen eindeutig zugeordnet:
Portfolio- und Holdings-Reads funktionieren über das feste Repository-Shim.
Der Broker prüft Eltern-/Prozessidentität und beschränkt erlaubte Kommandos;
GMGN-Schreibkommandos bleiben gesperrt. Echte Quote-Reads liefern bisher
HTTP/API 429. Die Ursache ist nicht bewiesen; eine persistente gemeinsame
Cooldown-Koordination verhindert weitere Requests vor der Provider-Frist.
Ein realer finalisierter RPC-Snapshot stimmt mit GMGN-Balance und leerem
Tokeninventar überein. Keine Credentials wurden gelesen, kopiert oder ausgegeben.
Details: [[execution-hardening]].
**Zwei echte Optionen, bewusst zu entscheiden:**
1. GMGN-Kette fertig schließen (Order → Fill → ACK → Recovery implementieren
   und mit GMGN-Account testen), **oder**
2. GMGN ganz weglassen und direkt über das bereits vorbereitete
   Executor-Gateway ausführen (Jupiter Build + eigene Wallet-Signierung) —
   das ist technisch schon bewiesen (zwei reale Testtrades, siehe
   [[04_performance_truth]]) und vermeidet die offene Lücke komplett,
   statt sie zu schließen.

### Privy vs. lokale DPAPI-Wallet
**Ist:** Privy vorbereitet (Setup-Skripte in `PRIVY_PREP/`), aber nicht
aktiv verbunden. Die lokale DPAPI-Wallet funktioniert bereits
nachweislich.
**Empfehlung:** Vorerst bei der lokalen Wallet bleiben. Privy erst
aktivieren, wenn ein konkreter Grund besteht (Mehrgeräte- oder
Team-Zugriff) — sonst unnötige Komplexität für keinen Zusatznutzen.

### GoPlus / DexScreener / GeckoTerminal
Funktionieren nachweislich, keine Probleme gefunden. Unverändert lassen.
