# Execution-Härtung — verifizierter Arbeitsstand

**Aktuelle Fortsetzung:** Siehe den aktuellen Abschnitt in
[RC1-Prüfung](rc1-readiness.md). Die nachfolgenden Sandbox-/CIM-Befunde sind
historisch. Inzwischen funktioniert nativer Git-Zugriff, ein eigener passiver
Supervisor läuft und echte GMGN-/RPC-Reads sind teilweise bestätigt. Das
System bleibt SIGNAL_ONLY; die vollständige Live-Kette ist nicht abgenommen.

Stand: 02.10.2026. Ausgangscommit `9828923`. Die Gesamtaufgabe und der
Clean Reset sind noch nicht abgeschlossen. Live-Autorität, Signing und
Broadcast bleiben bei 0/false. Keine produktiven IMC-Prozesse wurden während
dieser Arbeit gestartet oder gestoppt; keine produktive Datenbank wurde
bereinigt. Tests nutzen isolierte Dateien und kurzlebige HTTP-Fixtures auf
zufälligen Ports.

## Bestehende Architektur und Claude-Arbeit

Der kanonische Einstieg bleibt `START_IMC.bat` →
`LAUNCHERS/START_IMC_SIGNAL_ONLY.ps1`. CORE Discovery/Security/Scoring,
Breadth/SAFE100, Gateway und GMGN-Outbox werden weiterverwendet. Die
vorhandene `.venv`, Dependency-Pins, Prozesszuordnung, Health-Prüfung,
CORE-Kill-Sperre und Log-Rotation bleiben Grundlagen der Arbeit.

```text
CORE → Breadth/SAFE100 → bestehender Pure-Signal-Gateway
                          ↕ execution-ledger.db
                          → bestehende GMGN-Bridge → passive Outbox

Interner, isoliert getesteter Execution-Vertrag:
Candidate → Approval → Quote/Risk → atomarer Claim → Trusted Order Binding
→ ACK → GMGN-Orderbericht + finalized RPC-Receipt → tatsächliche Menge
→ Exit → SELL derselben Menge → Orderbericht + RPC-Receipt
→ Executor-/Token-/Native-Balance-Reconciliation → CLOSED → Statistik
```

Die zweite Kette ist **kein Nachweis einer angeschlossenen Live-Ausführung**.
`POST /claim` bleibt gesperrt. Die Bridge ruft keinen Swap auf und die
Outbox enthält weiterhin `execution=false`. Ein ausführender GMGN-Adapter
mit belastbaren echten Antwortfixtures und geklärtem Crash-/Idempotenzvertrag
ist noch erforderlich. Es gibt keine neue parallele IMC-Installation.

Jupiter-/Privy-/lokale Wallet- und Execution-Twin-Pfade sind getrennte,
optionale historische Wege. Sie wurden nicht aktiviert oder entfernt.
Die vorhandenen Start-/Stop-Launcher schließen widersprechende Betriebsmodi
über die gemeinsame Prozesszuordnung aus; eine tatsächliche Laufzeitprüfung
ist in dieser Sandbox wegen gesperrtem CIM-Zugriff nicht vollständig möglich.

## Inventur und Cleanup

`execution-audit.json` erfasst Repository-Dateien, statische Referenzen,
Code-Hashes und reine DB/WAL/SHM-Metadaten. ACTIVE bedeutet im vorgesehenen
Pfad verdrahtet, nicht aktuell laufend. REMOVE_CANDIDATE bedeutet lediglich
weiter zu prüfen. Fehlende Literal-/Importreferenzen beweisen keine tote
Komponente; externe Aufrufe und dynamische Imports bleiben mögliche Nutzer.
Identische lokale Exit-Guards haben unterschiedliche Importkonsumenten.

Keine physischen Dateien wurden gelöscht. Neue Ledger-/Fill-/Fortschritts-
und Journal-Artefakte bleiben durch `.gitignore` lokal. Historische Trade-
und Performance-Daten sowie ADR-geschütztes `brain_v1` bleiben erhalten.
Der automatische `reset_session` im kanonischen Start ist entfernt.
Weitere Datenkomprimierung und die Vereinheitlichung optionaler Supervisoren
bleiben offen; die bestehende Cleanup-Sperre wird nicht umgangen.

## Zentrale Execution-Wahrheit

`RUNTIME/EXECUTOR_GATEWAY/GMGN_BRIDGE/execution-ledger.db` ist die einzige
Wahrheit für den neuen Execution-Vertrag, getrennt vom CORE-Paper-Ledger
und der SAFE100-Shadow-Quelle. Tabellen:

| Tabelle | Bedeutung |
|---|---|
| `trades` | Candidate-Quelle, Trade-ID, Status, bestätigte BUY-/SELL-/Restmenge, Wallet-Deltas, Fees, Shadow-Bezug, realer Netto-PnL |
| `events` | BUY/SELL-Intent, Quote, Claim, bekannte Order-ID, aggregierte Fill-Mengen |
| `feedback` | Idempotente Rückmeldungen; gleiche ID mit anderem Inhalt ist ein Konflikt |
| `receipts` | Global eindeutige Signatur mit unabhängig gelesenem Blockchain-Beleg |
| `audit` | Zustandswechsel, Gründe, Fehler, Sperren und Reconciliation-Beobachtungen |
| `meta` | Session, Baseline, Freigabestatus, kumulative Sperrgründe und Balancebasis |

`BEGIN IMMEDIATE` und `synchronous=FULL` verbinden Mengen, Status, Receipt
und Dedupe in einer Transaktion. Ein Crash nach Commit vor HTTP-Antwort
darf dieselbe Buchung nicht erneut durchführen. JSON-Dateien sind Projektionen;
ein Fehler beim Schreiben einer Projektion nimmt einen Ledger-Commit nicht zurück.

Trade-IDs werden am SAFE100-Handoff aus Quelle, Variante, Mint und Decision-Zeit
deterministisch gebildet. Unterschiedliche Mints mit gleichem Zeitstempel sind
verschiedene Trades. Ein Exit muss dieselbe Candidate-Generation betreffen.
Die Zuordnung beginnt derzeit hier; eine durchgehende ID bereits ab früher
Discovery ist noch nicht in alle CORE-/Watcher-Datensätze integriert.

## State, Fill und Restart

Der normale Vertrag verwendet CANDIDATE, APPROVED, BUY_SENT, BUY_ACK,
BUY_FILLED, POSITION_OPEN, SELL_SENT, SELL_ACK, SELL_FILLED und CLOSED.
READY bezeichnet einen passiven Outbox-Intent, keine gesendete Order.
Claims werden vor externem I/O exklusiv reserviert; gesendete oder unbekannte
Aufträge werden nicht blind erneut geclaimt.

Ein ACK bucht keine Position. Die Order-ID muss zuvor durch einen
vertrauenswürdigen Executor-/Recovery-Aufruf gebunden worden sein; HTTP-ACKs
dürfen diese Bindung nicht selbst herstellen. FILLED erfordert den passenden
GMGN-Orderbericht und eine erfolgreiche finalized Solana-Transaktion mit
passendem Wallet/Signer, Tokenpaar und Mengen. Mengen aus Fill-JSON werden
nicht als Blockchain-Wahrheit übernommen.

Raw-Mengen bleiben Integer/Dezimalstrings. SELL verwendet die verbleibende
bestätigte positionsbezogene Menge, keinen Wallet-Prozentsatz. Teilfills
erhalten die tatsächliche Restposition und blockieren neue Trades, bis der
Auftrag nachweislich vollständig abgeschlossen und reconciled ist. Der Ledger
unterstützt mehrere bestätigte Teilreceipts und eine nachträgliche Terminalmeldung
derselben Signatur auch nach Timeout/Restart. Der reale GMGN-Leseadapter kennt
bislang nur den dokumentierten einzelnen terminalen Hash; eine GMGN-Order mit
mehreren Transaktionen ist damit **noch nicht live verifiziert** und bleibt bei
nicht vollständig rekonstruierbaren Mengen gesperrt.

Nach Neustart wird der persistierte Zustand übernommen. Unklare Sends,
ungeprüfte Altpositionen und beschädigte Ledger-Daten geben keine neuen BUYs
frei. Alte JSON-Positionen werden nicht blind migriert oder überschrieben.
Stop/Pause löscht keine Verpflichtung; verspätete bestätigte Fills bleiben
verarbeitbar. Veraltetes Fehlerfeedback setzt einen abgeschlossenen Auftrag
nicht zurück. Dauerhafte Konflikte können nicht durch API-Fehler, Resume oder
eine gewöhnliche Reconciliation verschwinden.

## Reconciliation und Risk

`POST /reconcile` nimmt ausschließlich `{}` an. Der Adapter liest selbst:
GMGN-Walletbindung, vollständig paginierte Holdings inklusive Airdrops und
abnormaler Positionen, bekannte Orders, beide Solana-Tokenprogramme und
Native-Balance. Die RPC-Inventuren müssen denselben finalized Bank-Slot haben.
Uneinheitliche Slots, unvollständige Seiten, unbekannte Bestände oder zu langsame
Lesevorgänge blockieren statt eine vollständige Momentaufnahme vorzutäuschen.

Bekannte GMGN-Orders mit verpassten Rückmeldungen werden über Orderbericht
und RPC-Receipt nachgebucht. Unbekannte Orders ohne belastbare Bindung werden
nicht geraten oder erneut gesendet. Local/Executor/Chain-Mengen und Native-
Balance müssen übereinstimmen. Erst danach ist ein vollständig verkaufter Trade
CLOSED. Eine geteilte oder extern veränderte Wallet führt zu einer Balance-
Sperre; der Vertrag setzt eine nachvollziehbare, isolierte Trading-Wallet voraus.

Die Bridge stößt für Execution-Verpflichtungen periodische Reconciliation an,
auch nach einem Stop. Neue Trade-Intents im rein passiven Signal-only-Modus
bleiben ohne Walletabfrage möglich und sind ausdrücklich
`risk_status=PENDING_EXECUTION_CHECKS`. Daraus folgt keine Echtgeldfreigabe.

Guards prüfen Signal-/Quote-/Reconciliation-Alter, Kapitalgröße/Reserve,
Liquidität, Slippage, Price Impact, Preisabweichung und Session-Limits.
Die Quote muss Event und Tokenpaar entsprechen. Ein absoluter realisierter
Session-Verlust wird aus geschlossenen Roundtrips berechnet; investiertes
BUY-Kapital ist kein realisierter Verlust. Ein fortlaufender Equity-/Mark-to-
Market-Drawdown-Guard und eine vollständig belegte Quote-/Impact-Quelle bleiben
vor dem Live-Piloten erforderlich.

## Reality Check, Statistik und Dashboard

Realer Netto-PnL ist die Summe der tatsächlichen Native-Wallet-Deltas eines
geschlossenen, reconciled Roundtrips, in lamports. Darin enthaltene Fees
werden nicht erneut abgezogen. Die Quelle lautet Ledger `trades/receipts/audit`.
Das Gateway liefert Anzahl, Zeitraum, Einheit, Netto-PnL, Trefferquote und PF
getrennt vom CORE-Paper-Konto. PF ohne Verluste und Trefferquote ohne Trades
sind undefiniert. Drawdown wird ohne belastbare Equity-Historie nicht erfunden.

Ein separat belegter Shadow-Datensatz kann unter derselben Trade-ID verglichen
werden. PnL-Abweichungen werden erst nach realem Abschluss berechnet. Automatischer
vollständiger 1:1-Shadow-Lauf mit Entry/Exit, Latenzen, realem Price Impact und
Simulation-Kalibrierung ist noch nicht integriert. Die feste SAFE100-Shadow-
Notional darf nicht einfach als positionsgleicher Live-Simulator gelten.

Das bestehende CORE-Dashboard zeigt Execution-Modus, echte Restpositionen,
Reconciliation, Sperrgründe und belegte Statistik getrennt von Paper-Daten.
Gateway-Ausfall ergibt UNAVAILABLE. Die Gateway-Health-Abfrage ist eine
operative Ansicht: Sie kann fällige Timeout-Sperren im Ledger persistieren,
führt aber keine Order aus. Der separate CORE-Steuerungsbildschirm nutzt
exakte Prozessrollen, zeigt Datenfrische gesondert und schützt Start/Stop
mit Sitzungstoken. Fehlende Prozesssicht blockiert Aktionen.

## Verifikation und Astra

Die Tests decken die 27 angeforderten Szenarien ab, einschließlich normalem
Roundtrip, Rejects, Timeout/API-Ausfall, verspäteten und doppelten Fills,
Doppelsignalen, Neustart, Balance-/Executor-Widerspruch, Freshness-/Quote-
Guards, Kill-/Emergency-Zuständen, beschädigtem State und Teilmengen.
Zusätzlich: Parallel-Claim, atomarer Rollback, Projektionfehler, kumulative
Sperren, veraltetes Feedback, vertrauenswürdige Orderbindung, Pagination,
Float-Präzision, Snapshot-Slots und Dashboard-CSRF/UNKNOWN.

Drei vollständige BUY→SELL-Fixtures beweisen ausschließlich den lokalen
funktionalen Vertrag; sie sind keine Micro-Live-Trades oder Gewinn-Evidenz.
Finaler Gesamtlauf: **139 Tests bestanden**, keine fehlgeschlagenen oder
übersprungenen Tests. Aufruf mit der gemeinsamen `.venv`, deaktiviertem
pytest-Cache und einem neuen UUID-Tempverzeichnis unter `.local-tools/`:

```powershell
$testRun = '.local-tools/execution-tests-' + [guid]::NewGuid().ToString('N')
& .venv/Scripts/python.exe -m pytest -q -p no:cacheprovider --basetemp=$testRun
```

Zusätzliche Tests prüfen die Ablehnung leerer/beschädigter bestehender Ledger
ohne Reinitialisierung, Pflichtmetadaten/Spalten, parallele Initialisierung,
gemeinsame Configvalidierung und Unicode-Tokenvergleich. Vorhandene Ledger
werden unter derselben Schreibtransaktion auf ihren Schema-/Constraint-
Vertrag geprüft; unbekannte Topologie wird nicht still repariert.

### Nachbefund: paralleles Anlegen des Ledgers

Ein späterer Gesamttest reproduzierte `incomplete_ledger_schema` bei zwei
gleichzeitigen Konstruktoren: Der zweite konnte die vom ersten gerade
angelegte leere DB sehen und die SQLite-Transaktion zuerst erhalten.
`execution_lock.py` serialisiert jetzt Existenzaufnahme, Schema-Prüfung und
vollständigen Commit mit einem OS-Lock auf dem kanonischen Sidecar
`<ledger>.init.lock`. Windows verwendet `msvcrt.locking`, POSIX `flock`;
nichtblockierende Versuche enden spätestens an der monotonen Deadline.
Konfiguration: `ledger_lock_timeout_seconds=5`, validiert auf `(0, 30]`,
auch für SQLite-Lockwartezeiten. Ein Timeout verhindert die Initialisierung.

Der Sidecar bleibt bestehen und ist für `.db`-Ledger ignoriert. Er ist kein
Ready-Nachweis; seine Existenz bedeutet keinen aktiven Lock. Nie während
einer möglichen Initialisierung löschen: Eine neue Datei könnte einen
zweiten unabhängigen Lock erzeugen. OS-Locks werden bei Handle-/Prozessende
freigegeben; bei Schemafehler bleibt die beschädigte DB unverändert und wird
weiterhin abgewiesen. Alle Teilnehmer müssen denselben Lockvertrag nutzen;
fremde DB-Schreiber sind damit nicht autorisiert oder abgesichert.

Zwölf zusätzliche kleine Regressionen prüfen das deterministische
Thread-Rennen, Prozesskonkurrenz, normales/abruptes Prozessende, Timeout,
Rollback, unerwartete OS-Fehler und ungültige Konfiguration. Der komplette
Testlauf mit den 18 Arbeitslauncher-Regressionen ist **183 PASS, 0 FAIL,
0 SKIP (21.66s)**. Es wurden keine produktiven DBs, Orders oder IMC-Dienste
verwendet. Quellen des Lockvertrags:
[Python msvcrt](https://docs.python.org/3.12/library/msvcrt.html),
[Python fcntl](https://docs.python.org/3.12/library/fcntl.html).

Astra High wurde ausschließlich lesend eingesetzt. Sein CHANGE-Befund führte
unter anderem zu kumulativen Sperren, unabhängiger Order→Signatur-Prüfung,
monotonen Terminalzuständen, Recovery derselben Teilreceipt-Signatur nach
Timeout, Decimal-Decoding und geschützten Dashboard-Aktionen.
Abschließendes Astra-Urteil: **OK für den abgegrenzten Signal-only-
Zwischenstand**, keine weiteren notwendigen Codekorrekturen innerhalb
dieses Umfangs. Dies ist ausdrücklich keine Live-Freigabe oder Abnahme
des gesamten Masterprompts.

`imc-ask-advisor -RiskReview` scheiterte in dieser Sandbox am Codex-Home-
Zugriff; der ausdrücklich gewünschte Astra-High-Subagent übernahm die Prüfung.

## Konkrete verbleibende Zugriffsgrenze

Nach der Nutzerbestätigung „lokal eingerichtet“ wurde `gmgn-cli portfolio info
--raw` erneut lesend ausgeführt. Die CLI meldet weiterhin einen fehlenden
API-Key. Die ergänzende Pfadprüfung zeigt PermissionError auf
`%USERPROFILE%\.config\gmgn\.env`, während Node das korrekte Benutzerhome
erkennt. Damit ist **die Sandbox-Sicht blockiert**, nicht bewiesen, dass die
lokale Zugangseinrichtung fehlt oder falsch ist. Keine Credentials wurden
ausgegeben, kopiert oder im Repository gespeichert.

Nach der ergänzenden Nutzerbestätigung zum Read-only-Broker wurde der vorhandene
Shim mit den festen Argumenten `portfolio info --raw` aufgerufen. Ergebnis:
`broker_unavailable`, Exit 1. In dieser Sitzung ist `IMC_GMGN_BROKER_DIR` nicht
gesetzt; im vorgesehenen `%TEMP%/imc-gmgn-broker-*`-Pfad ist kein Broker sichtbar.
Damit wurde noch keine authentifizierte GMGN-Antwort empfangen. Der konkrete
IPC-Pfad ist angefragt. Ein beliebiger TEMP-Ordner wird nicht übernommen.

Astra High hat diese neue Transportentscheidung vor weiterer Nutzung geprüft:
ein eindeutig zugeordneter Broker erlaubt den engen Portfolio-Lesecheck, aber
die allgemeine Broker-Implementierung benötigt noch eine engere Argumentprüfung,
validierte Request-IDs und atomare Antwortdateien. Diese vom Nutzer bereitgestellten
Tools wurden hier nicht verändert oder als allgemein sicher freigegeben.

Auch der erneute thematisch begrenzte `git add` scheitert an `Permission denied`
für `.git/index.lock`. Es sind keine Dateien staged und keine Commits dieser
Execution-Härtung erstellt. Vorbestehende Launcher-/Advisor-Änderungen bleiben
unverändert und würden nicht in die vorgesehenen Execution-Commits aufgenommen.

Nächster notwendiger Nachweis ist ein lesender GMGN-Check über den eindeutig
zugeordneten, erreichbaren Broker. Danach echte
Order-/Quote-/Holdings-Fixtures prüfen, den ausführenden Adapter samt
Crash-/Idempotenzvertrag schließen und 1:1-Shadow/Equity-Guards integrieren.
Reale Pilottrades benötigen weiterhin ausdrückliche Freigabe des Owners.
