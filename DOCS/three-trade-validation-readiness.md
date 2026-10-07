# Kontrollierte technische 3-Trade-Abnahme

Stand: 05.10.2026. Status: **READY_FOR_3_TRADE_VALIDATION**.
Keine Echtgeld-Trades gestartet. Astra High final: **OK**.

Das Ziel dieser Phase ist `READY_FOR_3_TRADE_VALIDATION`. Es ist keine
Strategie-Freigabe oder Erlaubnis zum Signieren/Broadcasten. Die bestehende
100+-Trade-Abnahme mit PF, Trefferquote und Drawdown bleibt separat.

## Ein kanonischer Ausführungspfad

Der vorhandene Gateway benutzt weiterhin `execution_state.Ledger`,
`RiskEngine`, `SingleSendAdapter`, `PositionManager` und denselben
Receipt-/Reconciliation-Pfad. Jupiter ergänzt den vorhandenen Adapter:

Candidate → Quote/Route/Risk → dauerhafter Claim → unsigned Transaction Build
→ erneutes Risk → lokal gebundene Signatur → Send-Autorisierung → finale
Route/Wallet/Blockheight/Gate-Prüfung → einmaliger Send → ACK → finalisiertes
Receipt → Wallet-/Token-Deltas → POSITION_OPEN → eigener SELL-Claim/Build/Send
→ finalisiertes SELL-Receipt → vollständige Reconciliation → CLOSED.

Trade, Request, Claim, Route, Message-Hash und Receipt-Signatur sind dauerhaft
gebunden. Der Claim wird vor dem Build atomar persistiert. Eine Signatur wird
vor möglichem Netzwerk-Send persistiert. ACK ist kein Fill. UNKNOWN reserviert
die Verpflichtung und kann niemals automatisch erneut gesendet werden.
SELL verwendet ausschließlich die verifizierte Restmenge desselben Trades.

Der technische Modus handelt ausschließlich das feste Paar SOL/USDC über
verifizierte einzelne Whirlpool- oder unterstützte Legacy-Raydium-CLMM-Routen.
Unbekannte Instruction-Varianten, Token2022-Swaps, Mehrfachrouten, dynamische
Raydium-Gebühren oder nicht unterstützte Tick-Bitmap-Erweiterungen blockieren.
Das ist keine allgemeine Memecoin- oder Strategie-Abnahme.

Quote, Input, Minimum-Output, Slippage, Price Impact, Route Plan, Mint- und
Vault-Identitäten, Wallet-ATAs, Accounts/Privileges, Tick-Reihenfolge/PDAs,
Pool-Konfiguration/Gebühren, Compute-Limit, Priority-Fee, Blockhash und gesamte
Transaction Message werden geprüft. Pool-Liquidität und aktuelle Pool-Spot-
Abweichung bilden die geschlossene Risikoprüfung; es gibt keinen unabhängigen
externen Preisoracle. Zwei unsigned Simulationen bestimmen das CU-Limit und
prüfen Kosten/Rent/Wallet-Deltas. Eine spätere signierte Transaktion muss exakt
dieselbe Message verwenden. Unmittelbar vor dem ersten signierten HTTP-Byte
werden Quote-Frist, Owner und unveränderte Konfiguration auch nach TLS erneut
geprüft. Keine Retries, kein Fee-Fallback und kein Blockhash-Austausch.

Ein kurzlebiger READ-Worker pro Dispatch hält die vorhandene Verbindung.
Die originale Quote-Frist wird nicht erneuert. IPC hat begrenzte Frames,
Sequenzen, Call-Anzahl und harte Eltern-Cancellation. Unbestätigtes Cleanup
blockiert weitere Ausführung. Er ist kein zusätzlicher dauerhafter Broker.

## Budget, Recovery und Inventar

Der kanonische Ledger friert den Modus mit höchstens drei BUY-Versuchen,
drei vollständig reconcilierten CLOSED-Trades und einer Position ein.
Ein Session-Reset füllt diesen Zähler nicht auf. Trade 2/3 wartet jeweils auf
den vollständig abgeschlossenen Vorgänger. Der dritte Abschluss schreibt den
Auto-Stop atomar. UNKNOWN, Receipt-/Ledger-/Wallet-/Route-Mismatch, Risk-Failure,
Recovery-Failure, Kill-Switch oder Staleness brechen den aktivierten Modus ab.

Die vorhandene lokale Wallet wurde ohne bestehende Ausführung atomar und
auditgebunden in denselben Ledger aufgenommen. Historische unsent Kandidaten
bleiben archiviert; alte Equity-Werte und Wallet-Bindungen sind nicht die neue
Baseline. Fremdes Token-Inventar wird vollständig eingefroren und bei jeder
Reconciliation geprüft. Es ist keine Executor-Position und wird nicht verkauft.

Wallet-Reads akzeptieren nur komplette SPL- und Token2022-Inventare und native
Balance exakt desselben finalisierten Slots. Token2022 wird vollständig über
`getProgramAccounts` mit Owner-Filter und `withContext=true` gelesen; es gibt
keinen festen DataSize-Filter, der Extensions auslassen würde. Die native
Balance darf nach validierten gleichen Inventaren einmal auf derselben
Verbindung am gemeinsamen Floor nachgelesen werden. Nur exakte Slotgleichheit
ist akzeptabel. Neuere Balance verwirft die ganze Runde; ungültige Daten oder
Werte unter dem angeforderten Floor blockieren. Höchstens drei ganze Runden,
ein Nachlesebudget, unveränderte Gesamtfrist und native Prozess-Cancellation.

Die Tests decken Crash vor/nach Claim, nach Build, vor Send, UNKNOWN Send,
nach ACK/Fill, bei offener Position, beim Exit, nach SELL und vor CLOSED-
Persistierung samt Restart ab. Failed-Chain-Receipts buchen belegte Gebühren
einmal, schaffen keine Position und lösen keinen erneuten Send aus.
Ein vor Aktivierung entstandener Read-Abort ist nur bei null Ausführungen,
allen Gates OFF, unveränderter Baseline und nativer Maintenance-Ownership
auditierbar reparierbar. Danach bleibt eine zweite unabhängige Reconciliation
zwingend. Aktive Validation-Aborts werden dabei niemals gelöscht.

## Runtime-Evidence

- `RUNTIME/three-trade-preparation.json`: PREPARED_ONLY, endgültige lokale
  Wallet, null Versuche/Abschlüsse/Positionen, `started=false`, Gates OFF.
- `RUNTIME/maintenance-state.json`: IDLE nach nativer Integrity-/Safety-/
  Cleanup-Prüfung. Abgebrochene Maintenance bleibt bis zur belegten nativen
  Owner-/Generations-Recovery gesperrt; PID-Reuse ist keine Freigabe.
- `RUNTIME/three-trade-route-observation.json`: tatsächlicher Mainnet-Quote,
  Route, unsigned Build, zwei Simulationen, finale Revalidation und TLS;
  05.10.2026 19:14 UTC: 1,813 s, Quote-Alter 1,794 s bei unverändertem
  2-s-Limit, zwölf begrenzte READs, null Signaturen und null Sends.
- `RUNTIME/three-trade-shadow-acceptance.json`: derselbe Produktionspfad mit
  isoliertem Kontentransport/disposablen Keys, drei CLOSED, sechs eindeutige
  isolierte Send-Calls, Restart-Zähler drei, Auto-Stop und null Positionen.
  Diese synthetische Evidence belegt keine echten Fills oder Performance.
- Nativer tatsächlicher Start mit genau einem Supervisor und CORE/WATCHER/
  GATEWAY/BRIDGE/HEALTH: richtige Wallet/Contract-Bindung, ReadMonitor
  VERIFIED_NATIVE_ONLY, null Positionen. Nativer Stop hat Dienste,
  Descendants und geschlossenen Gateway-Port verifiziert.

Öffentliche Mainnet-RPCs können 429, Minimum-Slot-Lag oder Slot-Drift liefern.
Dies wird nicht durch zusammengeführte unterschiedliche Banks oder Ledger-
Werte versteckt. Vor Aktivierung bleibt die Vorbereitung gesperrt, bis ein
vollständiger frischer Snapshot besteht; während der Validation wird abgebrochen.
Der öffentliche Leseversuch ist keine Verfügbarkeitsgarantie für drei Trades.

## Tests und Abschluss

Die vollständige relevante Suite umfasst Execution, Jupiter, Paper-Forward,
Maintenance, Launcher und Runtime-Supervisor. Jeder Lauf nutzt ein isoliertes
`.local-tools`-Verzeichnis; keine parallel betriebenen Simulationen/Watcher.
Endgültiger Gesamtlauf: **1002 PASS, 0 FAIL, 0 ERROR, 0 SKIP** in 96,51 s.
JUnit: `.local-tools/three-trade-final-tests.xml`; öffentliche Zusammenfassung
in `RUNTIME/three-trade-readiness.json`. Die Zweier-Instanz-Prüfung umfasst
zusätzlich deterministische SENT-/ACK-Rennen: Ein verspäteter Risk-Failure
der unterlegenen Instanz kann den gültigen Claim nicht abbrechen. Echter
READY-Risk-Failure bleibt haltend. Astra High hat alle acht Readiness-Bereiche,
die aktuellen Gates und den kanonischen Ledger unabhängig lesend geprüft:
final **OK**, keine weitere konkrete blockierende Lücke. Astra hat keine
Tests ausgeführt; der Gesamtlauf und die Runtime-Evidence stammen vom Hauptagenten.

`config/imc-execution.json` und `config/owner-broadcast-gate.json` sind
unverändert: Signing/Broadcast/Live/Wallet-Access/Authority OFF bzw. null,
Owner OFF. `config/imc-three-trade-validation.json` beschreibt PREPARED_ONLY;
der persistierte kanonische Vertrag ist verbindlich. Die drei realen Trades
werden in diesem Auftrag nicht aktiviert. Auch technische Readiness hebt diese
Grenze nicht auf; eine spätere Owner-Live-Entscheidung bleibt separat.

## Primärquellen des geschlossenen Route-Decoders

- [Jupiter Build API](https://developers.jup.ag/docs/api-reference/swap/build)
- [Finalisierte Transaktionsdaten](https://solana.com/docs/rpc/http/gettransaction)
- [Owner-Inventar mit Kontext](https://solana.com/docs/rpc/http/getprogramaccounts)
- [Native Balance und Mindestslot](https://solana.com/docs/rpc/http/getbalance)
- [Raydium CLMM Swap, ed1eb415](https://github.com/raydium-io/raydium-clmm/blob/ed1eb41519d5355755f7df52b43fa9610938b60b/programs/amm/src/instructions/swap.rs)
- [Raydium Pool/Observation, gleicher Commit](https://github.com/raydium-io/raydium-clmm/tree/ed1eb41519d5355755f7df52b43fa9610938b60b/programs/amm/src/states)
- [Whirlpool Swap, f4b99e79](https://github.com/orca-so/whirlpools/blob/f4b99e79e7140f3917e4ce81a2e8ad06ccdf8ce4/programs/whirlpool/src/instructions/swap.rs)

Raydium-Observation wird durch den gespeicherten Pool-Key, Instruction-Account
und program-owned Observation-Pool-ID gebunden. Historisch korrekt angelegte
Observation-Keypairs sind erlaubt; ein allgemeiner PDA-Zwang wäre falsch.
Andere Pool-/Config-/Tick-/Bitmap-PDA-Prüfungen bleiben bestehen. Unbekannter
deployed Layout/IDL-Stand bleibt fail closed.
