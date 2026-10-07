# Clean Reset — Arbeitsstand

## Lokale Wallet / Jupiter, 5. Oktober 2026

100-CLOSED-Quote-Paper-Vertrag, frischer kanonischer Feed, DPAPI-Public-Key-
Verifikation, finalisiertes Inventar und unsigned Mainnet-Simulation aufgebaut.
Keine GMGN-Cohort geändert, kein neuer Forward-Trade erzeugt, keine Signatur.
Bei 0,016557069 SOL blockieren Kapital und Roundtrip-Kosten den Start.
Echter lokaler Sendepfad, vollständige Kosten-/Route-Prüfung und finale Recovery
sind noch nicht abgenommen; keine OWNER-LIVE-Readiness oder 100%-Abnahme.
[Nachweis und Bedienung](jupiter-forward-readiness.md).

## Unabhängige Quote-Diagnose vom 5. Oktober 2026, 16:17 UTC

Ein isolierter Jupiter-v2-Zugang ist jetzt implementiert und mit genau einer
keyless GET-Leseprobe geprüft: SOL → USDC, 4 Routenschritte, 0,390 s.
Keine Walletadresse, Transaktion oder Signatur. Der GMGN-429-Befund und die
unbekannte Kontoursache bleiben bestehen; ein Walletwechsel innerhalb
derselben GMGN-Anbindung löst das API-Limit nicht. Der neue Diagnoseweg wird
nicht in GMGN-Executor, Ledger, Health oder Readiness eingespeist.

Lösungsweg und Verwendung: [GMGN-API-Lösung](gmgn-api-solution.md).
Zielbild ist ein unabhängig abgenommener Quote-/Wallet-/RPC-Pfad. Die
produktive Providerumstellung bleibt offen; MARKET_DATA weiterhin UNKNOWN,
keine neue Micro-Live-Readiness. Alle drei Schutzkonfigurationen unverändert.
Tests: **142 PASS / 0 FAIL / 0 SKIP**, 33,24 s nach Behebung der Test-Temp-
Zugriffsprobleme durch ein eigenes lokales Basetemp. Astra High vor Umsetzung
und nach neuer echter Quote-Evidence: **OK** für den isolierten Umfang.

## Quote-429-Ursachenprüfung vom 5. Oktober 2026, 15:40 UTC

`READY_FOR_FIRST_MICRO_LIVE_TEST` bleibt **BLOCKED**. Neu geschlossen ist die
Diagnoselücke: Der Quote-Guard filterte bereits drei bekannte Provider-Tags,
doch die authentifizierte Fehler-Evidence verlor diese Information. `api_error`
wird jetzt als geschlossenes Enum bis zum Runtime-Reader und Probe-Launcher
weitergereicht. Unbekannte Texte, Message, Upgrade-URL/-Message, Headerdump,
Stack und Credentials werden weiterhin nicht ausgegeben. Lokale Cooldown-
Ablehnungen republizieren keinen historischen Provider-Tag. Alte Fehler-
Envelopes ohne das neue Feld werden abgewiesen. Nach Astra-RISK wurde der
IPC-Vertrag für Ready, Request und Response einheitlich auf **Version 3**
angehoben: alte Broker werden vor dem Anlegen eines Requests abgewiesen,
alte Requests vor Node-Start. Eine neue reguläre Broker-Generation verwendet
den zusammenhängenden aktuellen Vertrag. Kein Health-
PASS und keine neue Execution-Autorität entstehen daraus. Der unbelegte
Kommentar über unabhängige Portfolio-/Quote-Buckets wurde korrigiert.

**Echte Evidence:** genau ein regulärer `-ProbeOnly`, ohne Signed-Schalter
und ProbeWallet, nach abgelaufener gemeinsamer Sperre. Portfolio-Read bestand;
nach fünf Sekunden lieferte SOL → USDC über 10.000.000 Lamports am
**15:40:20 UTC** HTTP/API **429**, **`RATE_LIMIT_EXCEEDED`**, Reset aus
**Header und Body**. Berechnete Sperrfrist 33 s, persistierte Untergrenze
**2026-10-05T15:40:53.4415792Z**. Quota-Limit/-Remaining bleiben UNKNOWN.
Keine weitere Provideranfrage oder Retry-Schleife. Der Guard erlaubt einen
Fetch, pinnt GET `https://openapi.gmgn.ai/v1/trade/quote` und prüft acht
gebundene Queryfelder; SDK 1.6.6 verwendet API-Key-Auth (`X-APIKEY`), kein
Signing. Schlüsselwerte wurden nicht gelesen oder ausgegeben. Das Request-
Schema entspricht der öffentlichen SDK-Implementierung; die erfolgreiche
Verarbeitung eines Quote-Schemas bleibt mangels Erfolg **nicht verifiziert**.

Die aktuelle [offizielle GMGN-Dokumentation](https://github.com/GMGNAI/gmgn-skills/blob/main/skills/gmgn-swap/SKILL.md#rate-limit-handling)
nennt Free-Rate/Kapazität 5/5 und Quote-Gewicht 10. **Unter diesen Regeln**
kann ein Free-Bucket auch nach vollständiger Auffüllung keinen Quote bedienen.
Der Tarif dieses Kontos, tatsächliche Kapazität, Quote-Entitlement und
anderweitige Nutzung desselben Kontos sind **nicht verifiziert**. Der bekannte
[öffentliche Fremdbericht](https://github.com/GMGNAI/gmgn-skills/issues/224)
enthält abweichende ältere Portalwerte; er ist keine Evidence dieses Kontos.
Ein Tarifwechsel wird deshalb weder als gesicherte Lösung behauptet noch
automatisch durchgeführt. Belegt ist die externe Ratelimit-Ablehnung,
keine neue Auth-/Schema-Fehlermeldung. Ihre konkrete Kontoursache kann lokal
ohne kontobezogene Provider-Evidence nicht bestimmt oder korrigiert werden.

Der reguläre Launcher prüfte HMAC-/Native-/Transport-Bindungen vor dem Read.
Die zusätzliche passive Beobachtung scheiterte an einem Regex für den
gequoteten BrokerDir-Schalter: separat gehaltene Broker-Native-Observation
ist für diesen Versuch **nicht verifiziert**. Kein neuer API-Aufruf nur zur
Wiederholung dieser Beobachtung. Die anschließend passiv geprüfte Ready-
Envelope und sechs SDK-/Guard-Transporthashes stimmen; sechs Laufquellen
und alle drei Schutzkonfigurationen blieben unverändert. Nach der Probe
0 Trading-Rollen, 0 UNKNOWN-Interpreter und 0 Listener auf 49090. Ein
bestehender Arbeits-Read-Broker ist weiterhin vorhanden; diese alte IPC-
Generation wird durch v3 sicher abgewiesen und vor späterer Nutzung regulär
neu gestartet werden müssen. Kein laufender
Forward-/Trading-Prozess wird behauptet. Ledger um 15:42:45 UTC: 0 CLOSED,
0 offene Positionen, 0 Send-Marker, 0 unsichere Events.

Tests vor Änderung: **112 PASS**. Danach fokussiert **118 PASS** und separat
**3 PASS** für die gesamte Quote-Transport-/Cooldown-Enum-Kette. Zwei neue
Fixture-Erwartungen waren zunächst falsch: Die bestehende lokale Sperre
behält BANNED/BUSINESS als Fehlerklasse. Erwartungen anhand des Codes
korrigiert, keine Produktlogik gelockert. Abschließende relevante Gesamtsuite:
**784 PASS / 0 FAIL / 0 SKIP**, 107,66 s. Alle neuen Fälle darin enthalten.
Astra High verlangte zuerst CHANGE für den fehlenden Tarifbeweis und die
verlorene Enum-Diagnose; beides wurde entsprechend berücksichtigt.
Der anschließende Astra-Review fand kein neues Leak/Race/Fail-open im Enum-
Diff, verlangte aber eindeutige Schema-Versionierung. Diese ist mit IPC v3
umgesetzt; fokussiert danach **80 PASS**. Ein weiterer relevanter Gesamtlauf
ergab zunächst **784 PASS / 1 FAIL**: Die neue v2-Protokoll-Fixture wiederholte
eine v1-Datei-ID und wurde vom bestehenden Create-once-Schutz abgewiesen.
Jede Schema-Prüfung nutzt jetzt ihre eigene ID; der betroffene Protokolltest
erneut **PASS**. Die Produktlogik wurde nicht geändert. Abschließender
Gesamtlauf: **785 PASS / 0 FAIL / 0 SKIP**, 107,77 s. Native Kontrolle danach
um 15:51:45 UTC: 0 Rollen/UNKNOWN/Listener; drei Schutzkonfigurationen
unverändert. Der abschließende **Astra High: OK** schließt das konkrete
Schema-RISK und bestätigt den sicheren **BLOCKED**-Abschluss. Kein neuer
CHANGE-Bedarf im geprüften Schema-Diff. Die Tests gelten für den dokumentierten
Arbeitsbaum einschließlich vorbestehender Protocol-Hunks, nicht als isolierter
Index-/Commit-Test. Aktueller gewichteter Planfortschritt **20 %**: Broker-
Paket 15 plus abgeschlossene Tests/Review/Dokumentation 5; keine technische
Readiness-Zertifizierung. Die übrigen Pakete bleiben offen.
Der echte Providerbefund stammt vom vorangehenden v2-Diagnosestand; nach der
Versionierung wurde keine neue Provideranfrage durchgeführt. Die Hashes
dieser beiden Stände bleiben getrennt. Kein Advisor-OK ersetzt Marktdaten.

MARKET_DATA bleibt UNKNOWN; Route, Liquidity, Impact, Drift und Fees haben
weiterhin keinen erfolgreichen produktiven Quote-Nachweis. Lifecycle,
produktive Exit-/Recovery-Vollkette, 100 qualifizierende CLOSED-Trades und
Micro-Live-Parität bleiben offen. PM ist STOPPED. PF, Trefferquote und Max DD
sind für die geforderte Kohorte nicht berechenbar. Owner-/Broadcast-/Live-/
Signing-/Wallet-Freigaben bleiben OFF. Kein Echtgeld bewegt.

**Genau eine notwendige Owner-Aktion:** Beim GMGN-Support den Quote-Zugang
des bestehenden Kontos prüfen und korrigieren lassen: Endpoint
`GET /v1/trade/quote`, Zeitpunkt 2026-10-05 15:40:20 UTC, HTTP/API 429,
`RATE_LIMIT_EXCEEDED` trotz abgelaufener Sperre und Einzelrequest; tatsächliche
Bucket-Kapazität/Quote-Gewicht und Quote-Entitlement bestätigen lassen.
Keinen Schlüssel an Codex senden. Danach ein regulärer Read-Nachweis mit
aktueller Broker-Generation; erst bei authentischem Erfolg die verbleibende
Market-Evidence und Readiness-Phasen bearbeiten. Keine Echtgeldfreigabe.

Belege in `.local-tools/micro-live-readiness-20261005/`: `429-probe-output.txt`,
`429-cooldown-before.json`, `429-source-hashes.json`,
`429-broker-generation-partial.json`, `429-safety-before.json`,
`429-safety-after.json`, `429-process-audit.json`, `429-ledger-final.json`,
`429-focused-tests.txt`, `429-enum-boundary-tests.txt`,
`429-enum-boundary-fixed-tests.txt`, `429-relevant-tests.txt`,
`429-astra-final-review.txt`, `429-final-source-hashes.json`,
`429-schema3-focused-tests.txt`, `429-schema3-relevant-tests.txt`,
`429-schema3-protocol-fixed-tests.txt`, `429-schema3-final-tests.txt`,
`429-astra-schema3-final-review.txt`, `429-safety-final.json`,
`429-ledger-closeout.json` (15:52:37 UTC erneut alle vier Bestandszähler 0),
`429-rpc-diagnostic.txt` (vier Abbruchfälle PASS, WinError 10054 aus der
unverändert protokollierenden lokalen RPC-Abbruch-Fixture, keine Unterdrückung).
Vorbestehende Protocol-/Storage-Änderungen
werden nicht als Bestandteil dieses Arbeitspakets committed.

## Micro-Live-Readiness vom 5. Oktober 2026, 14:39 UTC — externer Quote-Blocker

Neuer Auftrag: `READY_FOR_FIRST_MICRO_LIVE_TEST`, vollständig vorbereiten,
ohne Echtgeld-SEND. Dieser Zustand ist **nicht erreicht**. Keine Geldfreigabe
wurde verändert. Der vorherige ARMED-Arbeitsplan mit 77 % ist historisch;
für den erweiterten Auftrag gilt ein neuer Plan mit acht gewichteten Paketen:
Broker/Cooldown 15, Market-Evidence 20, Lifecycle 15, Recovery 10, Health 10,
Forward 15, Micro-/Live-Parität 10, abschließende Tests/Review/Dokumentation 5.
Nur vollständig nachgewiesene Pakete erhalten 100 %; offene Pakete bleiben 0.

**Reguläre aktuelle Broker-Generation nachgewiesen:** Der unveränderte
`RUN_IMC_SIGNAL_ONLY.ps1 -ProbeOnly` startete einen eigenen Broker in
`EXIST_QUOTE`, ohne Signed-Schalter und ohne ProbeWallet. Der Parent prüfte
HMAC-/Native-/Quellbindungen vor dem Read; die passive Beobachtung hielt beide
Originalprozesse und bestätigte Generation und Quote-Quellhash
`c5551db3aedf2daaaf37dadf4e6b4c1baaf699a2404c45fb76c0b2034c74bd70`.
Der bestehende Arbeitsbroker wurde nicht umgebunden oder umgangen. Die
reguläre Konstruktion verwendet den aktuellen Code; die Probe-Generation
wurde anschließend beendet, kein dauerhaft neuer Broker blieb zurück.

Der aktuelle Arbeitsbaum einschließlich der vorbestehenden Protocol-Hunks ist
in `probe-source-hashes.json` festgehalten, Protocol-SHA256
`9c3707ca8267b641ea0b5fe36bc3749aa7003e23f0d6827b4ab56b29430d387c`.
Commit-IDs allein werden nicht als vollständiger Ausführungsstand behauptet.
Diese Quellen und alle drei Schutzkonfigurationen waren nach der Probe
unverändert. Die Probe endete nach 13,18 s; Parent und Broker sind nativ
beendet. Danach 0 Trading-Rollen, 0 UNKNOWN-Interpreter, 0 Listener auf 49090.

**Authentischer Provider-Befund:** Portfolio-Read bestand; nach dem im Launcher
vorgesehenen Abstand genau eine Quote SOL → USDC über 10.000.000 Lamports.
Ergebnis: `GMGN_RATE_LIMITED`, origin `provider_response`, HTTP/API **429**,
berechnete Sperrfrist **33 s**, Quota-Hints UNKNOWN. Die Quotegröße ist keine
festgelegte oder freigegebene Echtgeld-Testpositionsgröße. Persistierte
Retry-Untergrenze: **2026-10-05T14:40:15.5807670Z**. Sie garantiert keine
Providerfreigabe; es wurde kein automatischer weiterer Versuch gestartet.
Die vorherige Sperrfrist war vor dem Versuch nachweislich abgelaufen.

Die Ursache hinter dem wiederholten Provider-429 ist **nicht verifiziert**:
keine belastbare Aussage über Plan, Quota, Berechtigung oder Providerdefekt.
Ein erfolgreiches Quote-Schema ist ebenfalls nicht verifiziert. Die aktuelle
Quell-/Broker-Bindung als lokale Fehlerursache ist mit der Probe ausgeschlossen;
die externe Ablehnung lässt sich durch lokale Marktfeld-Ergänzungen nicht
schließen. Kein Auth-Fallback, keine parallelen Reads und keine Retry-Schleife.

MARKET_DATA bleibt UNKNOWN. Route, Impact, routengebundene Liquidität, Drift,
Fees, Quote-/Route-Alter und Token-/Pool-Evidence sind nicht vollständig
produktiv nachgewiesen. Keine synthetischen Ersatzwerte, kein Health-PASS.
Es wurde kein Forward-Lauf gestartet. Der echte Ledger um 14:42:34 UTC hat
0 CLOSED, 0 offene Positionen, 0 Send-Marker und 0 unsichere Events. PF,
Trefferquote und Drawdown für qualifizierende CLOSED-Trades sind nicht
berechenbar. Das `FINALIZED_CHAIN_RECONCILED`-Gate bleibt verbindlich; ein
Quote-Paper-CLOSED wird nicht als bestätigter On-chain-CLOSED umklassifiziert.

Produktive Exit-Vollkette und Recovery-Nachweis bleiben offen. Die vorhandenen
kontrollierten Tests beweisen Blockierung/Idempotenz, keine realen Fills oder
100 qualifizierenden Trades. PM war zuvor bei 0 Positionen 12/12 PASS; aktuell
ist die Trading-Runtime gestoppt, ein neuer PM-PASS wird nicht behauptet.
Der exakt einmalige Micro-Roundtrip mit absolutem Exposure-Cap, Route-Max-Age
und passender Autoritätsfreigabe ist noch nicht abgenommen. `max_session_trades=3`
wurde nicht als Ein-Trade-Schutz ausgegeben und nicht vorschnell geändert.

Frischer relevanter Gesamtlauf: **775 PASS / 0 FAIL / 0 SKIP**, 132,83 s.
Der zusätzlich sichtbare serverseitige WinError 10054 wurde in der lokalen
HTTP/1.1-RPC-Abbruch-Fixture eingegrenzt: vier Fälle erneut PASS, 2,48 s;
originale Fehlerausgabe und Assertions bleiben erhalten. Die Fixture prüft
begrenzten Read-Abbruch und beendete Worker. Kein ungeklärter Test-FAIL.

Astra-Host war beendet: dieselbe quellgeprüfte bestehende Task wurde regulär
neu gestartet und an ihre Originalinstanz gebunden. 16 abgelaufene Request-
Dateien wurden reversibel archiviert. Das ist kein dauerhafter Replay-Schutz.
Astra-High-RiskReview bestätigte den begrenzten Probe-Ansatz und verlangte
Native-Cleanup, tatsächliche Quellhashes sowie UNKNOWN bei 429; diese Punkte
sind in den genannten Belegen erfüllt. Der abschließende Astra-High-Gesamtreview
ergab **OK für den abgesicherten BLOCKED-Abschluss**, keine Micro-Live-
Readiness und keine vollständige Sicherheitsfreigabe. Im geprüften Pfad ist
kein konkreter neuer Sicherheitsfehler, Race, Bypass oder Fail-open belegt.
Tests/Review/Dokumentation sind abgeschlossen; damit **20 % im neuen
gewichteten Auftrag** (Broker 15 + Abschlussprüfung 5). Alle anderen Pakete
bleiben offen. Antwort: `astra-final-review.txt` im Evidenzverzeichnis.
Abschließende native Kontrolle um 14:46:00 UTC: erneut 0 Rollen/UNKNOWN/
Listener, drei unveränderte Schutzkonfigurationen (`safety-final.json`).

Belege: `.local-tools/micro-live-readiness-20261005/` mit
`broker-generation.json`, `probe-cleanup.json`, `probe-output.txt`,
`probe-source-hashes.json`, `safety-before.json`, `safety-after-probe.json`,
`ledger-final.json`, `relevant-tests.txt`, `rpc-close-diagnostic.txt` und
`astra-phase1-risk.txt`, `astra-final-review.txt` und `safety-final.json`.
Nächste Owner-Aktion ist die Klärung der Quote-API-
Sperre beim GMGN-Provider, keine Echtgeldfreigabe. Nach wiederhergestelltem
Quotezugriff sind die verbleibenden Readiness-Phasen weiterhin abzuarbeiten.

## Fortsetzung vom 5. Oktober 2026, 12:09 UTC — HTTP-Retry-After korrigiert

Der Quote-Guard wertete bisher nur `x-ratelimit-reset` und `reset_at` aus,
nicht den standardmäßigen HTTP-Header `Retry-After`. Die früher berichteten
31/33 Sekunden waren **berechnete Sperrfristen aus Reset-Evidence**, kein
Nachweis eines ausgelesenen `Retry-After`-Headers. Der Guard berücksichtigt nun
Delta-Sekunden ab Headerempfang und strikt geprüfte IMF-fixdate-Daten. Gültige
Fristen werden per Maximum zusammengeführt; mehrdeutige/ungültige oder zu große
Angaben bleiben UNKNOWN. Die bestehende Broker-Sicherheitssekunde, persistente
Cooldown-Sperre, Body-UNKNOWN-Sicherung und Ein-Fetch-Grenze bleiben erhalten.
Grenzfall: `Retry-After: 86400` kann durch Aufrundung und die bestehende
Sicherheitssekunde UNKNOWN werden; die Frist wird nicht auf 86400 gekürzt.

Eine einzige neue echte Quote nach nachweislich abgelaufenem Cooldown ergab
am 12:08:54 UTC erneut **HTTP/API 429**, berechnete Sperrfrist 32 Sekunden,
`reset_source=header_and_body`, keine Quota-Hints. Der notwendige Portfolio-Read
bestand. Kein zweiter Quoteversuch, kein Gateway-Dispatch, kein Runtime-Start,
kein SEND. Die Abfrage erfolgte vor der Quelländerung über den bestehenden
authentifizierten Read-only-Broker. Quote-/Route-/Impact-/Liquiditäts-/Drift-
Evidence bleiben **nicht verifiziert**, die konkrete Provider-Ursache ebenso.

Der Runtime-Code setzt weiterhin alle drei Markt-Risikowerte auf `None`;
`execution_probes()` hat keinen produktiven MARKET_DATA-PASS-Schreiber. Die
offizielle Quote-Response beschreibt sechs Basisfelder ohne Routing-/Impact-
Schema; Token-/Pool-Typen des installierten SDK sind `Promise<unknown>`.
Quellen: `imc_pure_signal_gateway.py:quote_event/execution_probes`,
`GMGN-ReadProtocol.ps1:ConvertTo-GmgnSafeOutput`,
[GMGN CLI-Referenz](https://raw.githubusercontent.com/GMGNAI/gmgn-skills/main/docs/cli-usage.md).
Keine spekulative Schemafreigabe, keine ersatzweise Nullwerte und keine
Lockerung von Health-Gates. **MARKET_DATA bleibt UNKNOWN; Forward BLOCKED.**

Der bestehende Work-Broker ist an den vorherigen Quote-Quellhash gebunden;
`Assert-GmgnTransportUnchanged` weist nach der Korrektur ordnungsgemäß
`GMGN_TRANSPORT_CHANGED` nach. Die neue Fassung benötigt die reguläre neue
Broker-Generation vor produktiver Nutzung. Kein HMAC-/Hash-/Parent-Bypass,
kein zusätzlicher Broker und kein zusätzlicher echter API-Aufruf zum Testen.

Gezielte isolierte Tests: **59 PASS / 0 FAIL / 0 SKIP**, 26,94 Sekunden,
einschließlich persistenter HTTP-only-Cooldowns und genau eines Fake-Fetches.
Relevanter Gesamtlauf: **775 PASS / 0 FAIL / 0 SKIP**, 96,36 Sekunden;
`relevant-tests.txt` unter `.local-tools/market-readiness-20261005/`. Getestet
wurde der Arbeitsbaum mit den unveränderten vorbestehenden Protocol-Hunks und
Storage-Dateien, kein isolierter Commit. Astra bestätigt die sichere
Integrierbarkeit der Korrektur, meldet aber RISK für den Gesamtstand:
Provider-/Market-/Performance-Evidence fehlt; die echte Quote prüft den Patch
nicht. Zusätzlich verlangt Astra einen Offline-Nachweis gegen den tatsächlich
zu integrierenden Protocol-Stand. Dieser Nachweis bestand mit **30 PASS /
0 FAIL / 0 SKIP**, 26,61 Sekunden, in isolierten Quote-Fixtures mit
`git show HEAD:TOOLS/GMGN-ReadProtocol.ps1`. Der im Protokoll festgehaltene
Protocol-SHA256 ist `01a5ddcf197305a8af8965796fc7cffdefcff9e8756b8ea99cf3f00ee4a82a4d`.
Keine Projektdatei wurde dafür zurückgesetzt, kein zusätzlicher echter
API-Aufruf ausgelöst. Dies belegt die Quote-Korrektur mit committed Protocol;
der 775er Gesamtlauf bleibt ausdrücklich ein Arbeitsbaum-Test.
Die abschließende Astra-High-Prüfung von Commit `6746626` und dem neuen
Offline-Beleg ergab **OK für den abgesicherten BLOCKED-Abschluss**: keine
weitere zwingende interne Korrektur im geprüften Umfang. Dies ist keine
Market-/ARMED-/Forward-Freigabe. Antwort: `astra-integration-review.txt` im
Evidenzverzeichnis. Die vorherigen RISK-Einwände wurden damit berücksichtigt.
Nativer Read-only-Beleg um 12:13:21 UTC: 0 Rollen, 0 UNKNOWN-Interpreter,
0 Listener auf 49090, drei unveränderte Schutzkonfigurationshashes. Der echte
Ledger um 12:13:38 UTC enthält 0 CLOSED, 0 Positionen und 0 Send-Marker.
Belege: `safety-final.json`, `ledger-final.json` im selben Verzeichnis.

Der Astra-High-RiskReview meldete RISK wegen der möglichen Dispatch-Probe:
`dispatch_next()` führt nach erfolgreicher Quote in `execute()` weiter.
Dieser Pfad wurde nicht verwendet; die Probe nutzte direkt `GMGNReader.read`.
Der Parseransatz wurde mit strikter Kalenderprüfung und Maximumregel umgesetzt.
Keine eigenen Tests/Prozesse/Änderungen durch Astra.

Auch nach einem reparierten Marktvertrag qualifizieren die Quote-Paper-
Ergebnisse nicht für das unveränderte `FINALIZED_CHAIN_RECONCILED`-Gate
(`execution_policy.py:quality_gates`). Der vorhandene Paper-Vertrag verlangt
200 Intents, nicht 100 qualifizierende echte CLOSED-Trades. Ein Forward-Lauf
wurde nicht gestartet; Zielzahl und Evidence-Klasse wurden nicht umgedeutet.

## Fortsetzung vom 5. Oktober 2026, 11:33 UTC — PM-BUSY behoben

Der eigene PM-Thread ist jetzt der einzige Tick-Schreiber im gestarteten
Gateway. `next_event()` konsumiert dessen Health statt einen konkurrierenden
Tick auszuführen. Der Heartbeat wartet höchstens 0,5 s auf den Lock und kopiert
erst unter Lock; bei Timeout bleibt UNKNOWN ohne ungeschützte Statekopie.
Threadende, STOP, Tickfehler und die ursprüngliche 2-s-Deadline bleiben Sperren.

Die gemessene zweite Ursache waren vollständige Audit-Scans pro Trade in
`validate_links()`. Ein nicht eindeutiger Index auf `audit(action,trade_id,id)`
erhält sämtliche Daten und Prüfungen. Vorhandene Indexdefinitionen werden
auf Tabelle, Spalten, Nicht-Eindeutigkeit und Nicht-Partialität geprüft.
Read-only-Profil am echten Ledger: SCAN und 171–203 ms vorher; SEARCH mit dem
Index und 0–15 ms in der Messauflösung danach. Keine Daten gelöscht.

Die originale monotone PM-Deadline wird jetzt zwingend bis Risk-Entscheidung
und Permit übernommen. Fehlend/malformed/abgelaufen blockiert BUY und SELL;
eine beim abschließenden Audit abgelaufene Freigabe wird zurückgenommen.

**Echter Runtime-Nachweis:** 12/12 PM-PASS mit fortschreitenden eigenen
Loop-Zeitstempeln, 37–197 ms Health-Latenz; zuletzt viermal gleichzeitig
CORE/MONITORING/Watchdog PASS. Alle Geldflags false. Diese zwölf Samples haben
**0 offene Positionen**; ein produktiver Positionslast-Nachweis wird nicht
behauptet. Danach kanonischer STOP, native 0 Rollen/UNKNOWN/Listener und drei
unveränderte Schutzkonfigurationshashes um 11:33:24 UTC.

Passende Tests: **159 PASS** (PM, Risk, Adapter, Runtime-Transport und
Prozess-Lifecycle). Astra High prüfte den Block mit **OK**: im belegten Umfang
geschlossen, kein neuer blockierender Fehler. Astra bestätigte die native
Aufzeichnung/SQL-Profile/STOP-Datei, führte keine eigenen Tests aus und konnte
die 159 PASS mangels gespeichertem Vollprotokoll nicht unabhängig bestätigen.
Die abschließenden relevanten Gesamttests folgen mit gespeichertem Protokoll.

Abschließender relevanter Lauf nach den Fixturekorrekturen und zusätzlicher
Positionslast-Prüfung: **772 PASS / 0 FAIL / 0 SKIP in 173,73 s**;
`relevant-tests-final.txt` im selben Evidenzverzeichnis. Der erste Gesamtlauf
hatte 769 PASS / 2 FAIL: Der neue PM-Cap verdeckte den bestehenden Quote-
Rundungsgrenzfall; eine 429-Fixture las während einer Windows-Dateisperre.
Die isolierte Dispatch-Fixture trennt die Quotegrenze nun von ihrem PM-Cap;
die Produktion bleibt bei 2 s. Die File-Fixture behält beim Sharing-Retry ihr
ursprüngliches 3-s-Budget; JSON-Fehler werden nicht übergangen. Die anschließende
Positions-/Dispatch-Prüfung bestand mit 115 PASS.

Zusätzlich belegt die eigene PM-Loop mit einer kontrollierten offenen Position
Bewertung und dynamischen Trailing-Exit-Intent aus bestätigter Restmenge 1000.
Dies ist ein Test mit kontrollierter Receipt-/Quote-Evidence, kein produktiver
Fill und kein SEND. Der reale Ledger hat weiterhin 0 CLOSED, 0 offene
Positionen, 0 ausstehende SEND/ACK/PARTIAL/UNKNOWN und 0 SEND-Marker.
Der vollständige produktive CLOSE_ONLY → FLAT_SAFE → STOP-Nachweis bleibt offen;
ein programmatischer FLAT_SAFE-Zustand ist im geprüften Runtime-Code nicht
belegt. Bestehende kontrollierte CLOSE_ONLY-/Lifecycle-/Recovery-Tests sind
im relevanten Gesamtlauf enthalten. UNKNOWN ohne Order-ID bleibt gesperrt,
ohne Doppel-Send oder unsicheres Retry.

Alle Tests beziehen sich auf den Arbeitsbaum einschließlich vorbestehender
uncommitteter `GMGN-ReadProtocol.ps1`-Änderungen, nicht auf einen isolierten
Commit. Nach dem Gesamtlauf: nativer STOP-Beleg `stop-final.json` vom
11:46:16 UTC, 0 Rollen/UNKNOWN/Listener und unveränderte Schutzkonfigurationen.

Belege unter `.local-tools/armed-no-broadcast-20261005-pm/`:
`runtime-pm-indexed.json`, `sql-profile-before.json`, `sql-profile-after.json`,
`stop-pm.json`, `astra-pm-review.txt`.

**Noch kein ARMED:** Eine einzelne neue echte Quote um 11:33:51 UTC liefert
weiterhin Provider-HTTP/API 429 mit Retry-After 33 s; Portfolio-Read PASS.
Price Impact, Liquidität und Preisabweichung sind nicht vollständig belegt.
Die Broker-Allowlist sperrt zusätzlich `token info`/`token pool`; dies wird
vor einer Erweiterung separat mit Astra geprüft. Reale Exit-/Recovery- und
Performance-Evidence bleiben offen. Der unten folgende Checkpoint ist
historisch; seine PM-BUSY-Sperre ist durch diesen Abschnitt ersetzt.

Der separate Market-RiskReview ergab CHANGE: Installierten CLI-GET-/Auth-Code
vor Discovery prüfen; nur bekannte Feldpfade, begrenzte Tiefe/Größe und
verifizierte Schema-/Einheiten-/Frische-/Routenbindung zulassen. Der Code wurde
geprüft: `token info` und `pool` sind GET mit normaler API-Auth; die installierten
Response-Typen sind `Promise<unknown>`. Die offizielle Quote-Dokumentation
beschreibt sechs Basisfelder ohne die drei Risikowerte. Eine generische
Zahlen-/Adressredaktion oder Pool-Liquidität ist kein routenbezogener Impact-
Nachweis. Deshalb wurde kein spekulativer Broker-/Auth-/Market-Vertrag
produktiv eingeschaltet; fehlende Evidence bleibt blockierend. Es wurde kein
100-Trades-Lauf gestartet. Der abschließende Astra-High-Gesamtreview ergab
**OK für den gesicherten BLOCKED-Stand**: kein neuer blockierender
Sicherheitsfehler in `392a5f6` samt den drei Testkorrekturen. 772 PASS im
gespeicherten Protokoll bestätigt; kein eigener Testlauf durch Astra.
Antwort: `astra-final-review.txt` im Evidenzverzeichnis. Kein ARMED, keine
Echtgeldfreigabe und keine Behauptung über einen isolierten Commit-Testlauf.

## Gesicherter Stand vom 5 Oktober 2026 — ARMED bleibt gesperrt

Der bestehende Quote-/Health-Pfad wurde gezielt gehärtet; **ARMED_NO_BROADCAST
ist nicht erreicht**. Keine Freigabe, kein Signing und kein Echtgeld-SEND.
Dieser Abschnitt ersetzt für die Fortsetzung den älteren Checkpoint vom 4. Oktober.

### Nachgewiesene Änderungen

- `GMGNReader` übernimmt ausschließlich das streng typisierte, redaktierte
  Fehlerformat des authentifizierten Brokers. Provider-Retry-After wird im
  bestehenden Dispatch nicht verkürzt; UNKNOWN bleibt gesperrt. Eine lokale
  Cooldown-Antwort zählt dort nicht als weiterer Providerfehler.
- Der bestehende ReadMonitor bindet CORE/MONITORING/Watchdog an seinen
  ursprünglichen Wall-/Monotonic-Lease und frische native Prozessgenerationen.
  Die vorhandene native `.venv`-Wrapper-/Worker-/Console-Bindung wird im
  Health-Snapshot erhalten und gegen den Supervisor sowie den Health-Report
  geprüft. Alte Quellen bekommen durch einen neuen Heartbeat kein neues Alter.
- Der vorhandene `/health`-Endpunkt zeigt diese eigenen Execution-Probes.
  MARKET_DATA bleibt ohne vollständige produktive Quote-Risikowerte UNKNOWN.
- Der kanonische STOP veröffentlicht STOPPED erst nach nativer Abwesenheit
  aller erkannten Rollen/UNKNOWN-Interpreter und erfolgreicher Prüfung des
  geschlossenen Gateway-Ports. Frühere Statusbytes bleiben unverändert unter
  `.local-tools/status-history/` erhalten. Chain-Zustand wird dabei ausdrücklich
  nicht aus Prozessabwesenheit abgeleitet.
- Die 429-Fixtures verwenden zwingende lokale Fake-Imports. Die vorbestehende
  Entfernung von NODE_OPTIONS/NODE_PATH im Produktionspfad bleibt erhalten.
  Fehlender Fake-Import bricht vor SDK/FETCH ab; UNKNOWN-Cooldown wird auch beim
  isolierten Restart geprüft.
- Eine neue Regression bestätigt, dass UNKNOWN ohne Order-ID nach Neustart
  auch durch eine leere Wallet-Beobachtung nicht aufgelöst oder erneut gesendet
  wird. Eine sichere reale GMGN-Request-ID-Recovery wird damit nicht behauptet.

### Tests und tatsächliche Runtime-Evidence

Abschließender relevanter Python-Lauf nach der ergänzten PM-Diagnose:
**749 PASS / 0 FAIL / 0 SKIP in 104,19 s**.
Umfang: sämtliche `test_execution*.py`, SingleSwap-Transport, Bridge-Heartbeat,
Paper, Health, GMGN-Read-Evidence/-Broker und Signed-Quote-/429-Tests.
Zusätzlich: 5 STOP-Projektionsfälle, 49 Prozessidentitätsprüfungen und
21 native Redirector-/Console-/Read-Identitätsprüfungen bestanden.
Keine Behauptung über die vollständige Repository-/Storage-Testsuite.
Der Lauf prüfte den tatsächlichen Arbeitsbaum einschließlich der bereits
vorbestehenden, nicht mitintegrierten Änderungen in
`TOOLS/GMGN-ReadProtocol.ps1`; er ist kein isolierter Commit-Nachweis.

Die letzte positive echte Runtime-Beobachtung vom **05.10.2026,
10:35:42 UTC** zeigt CORE=PASS, MONITORING=PASS, Watchdog=true,
RECONCILIATION=PASS, frische Equity, keine ausstehende Reconciliation,
0 offene Ledger-Positionen, 0 CLOSED-Trades und broadcast=false.
MARKET_DATA und POSITION_MANAGER waren UNKNOWN. Eine zusätzliche native
Beobachtung vom 10:45–10:46 UTC mit rein diagnostischem Heartbeat-Grund
meldet POSITION_MANAGER=UNKNOWN mit `reason=busy`. Der gespeicherte PASS
ist deshalb weiterhin kein gültiger eigener Health-Nachweis. Die Diagnose
verändert weder PASS-Bedingungen noch Deadline, Locks oder Gates.
Nach dem letzten Lauf kanonischer STOP: 0 erkannte Rollen,
0 UNKNOWN-Interpreter, Gateway-Port geschlossen und Status STOPPED.
Die abschließende native Prüfung nach allen Tests vom **10:49:09 UTC**
bestätigt erneut 0 Rollen, 0 UNKNOWN und 0 Listener; Beleg
`final-native-stop.json`. Der lokale Read-only-Advisor-Host läuft weiterhin
als bestehende Aufgabe `IMC-AdvisorReadOnly`; dies ist kein Trading-Stack.
Die drei geprüften Owner-/Execution-/Legacy-Broadcast-Konfigurationen sind
gegen die vorherigen SHA256-Werte unverändert; alle Echtgeldfreigaben OFF.
Diese Beobachtung ist nach STOP kein aktueller Readiness-/Frischebeleg.

Lokale Belege: `.local-tools/armed-no-broadcast-20261005/relevant-tests-final.txt`,
`runtime-health-redirectors.json`, `runtime-position-manager.json`,
`runtime-pm-reasons.json`, `safety-config-before.json` und
`astra-final-risk.txt`. Die ersten breiten 429-Tests fanden 24 Fixturefehler,
weil der vorbestehende NODE_OPTIONS-Schutz die implizite Fake-Injektion
entfernte; der oben genannte finale Lauf enthält die explizite Isolation.

### Verbleibende echte Sperren

1. Aktueller echter Portfolio-Read erfolgreich; Quote-Probe weiter
   GMGN_RATE_LIMITED, HTTP/API 429, Retry-After 33 s. Quota-/Plan-Ursache
   nicht nachgewiesen. Kein wiederholter Quote-Probe und kein Provider-Fallback.
2. Price Impact, Liquidität und Preisabweichung fehlen im produktiven
   Quote-Vertrag; diese UNKNOWN-Werte blockieren weiterhin BUY und SELL.
   Das dokumentierte OpenAPI-Quote-Schema enthält nur Token, Amount und
   Slippage, siehe [GMGN CLI-Dokumentation](https://github.com/GMGNAI/gmgn-skills/blob/main/docs/cli-usage.md#order-quote).
3. Der aktive Execution-Ledger enthält weiterhin **0** qualifizierende CLOSED.
   Der unveränderte BUY-Risk-Vertrag verlangt mindestens 100 reconciled CLOSED
   sowie PF >=2, Trefferquote >=70 %, Drawdown <=15 % und Top-3-Anteil <=25 %.
   Kontrollierte Tests/Paperdaten dürfen diese reale Evidence nicht ersetzen.
4. Positionsbewertung/dynamischer Exit und der vollständige produktive Pfad
   CLOSE_ONLY → FLAT_SAFE → STOP mit einer realen offenen Position sind nicht
   vollständig nachgewiesen. Der leere Runtime-Pfad und die kontrollierten
   Lifecycle-/CLOSE_ONLY-/Crash-Tests sind belegt.
5. POSITION_MANAGER bleibt im echten Heartbeat UNKNOWN (`busy`). Die
   Runtime-Ursache ist damit eingegrenzt, aber kein produktiver PASS bewiesen.
6. UNKNOWN ohne Order-ID bleibt fail-closed; kein sicherer realer GMGN-
   Request-ID-Lookup nachgewiesen. Weder blindes Retry noch erzwungenes CLOSED.

Der erste Astra-High-Gesamtreview ergab **RISK**: sicher blockierter
Zwischenstand, kein ARMED; im geprüften Umfang kein neuer konkreter
Sicherheitsfehler. Die PM-Diagnose wurde separat vor Umsetzung mit **OK**
geprüft. Der abschließende Review aller 17 eigenen Dateien nach den Tests
ergab erneut **RISK**, ohne neuen konkreten Sicherheitsfehler oder zwingenden
Code-CHANGE. Astra bestätigte 749 PASS im Protokoll sowie 14 echte PM-BUSY-
Samples und betrachtete den dokumentierten STOP; keine eigene Testausführung
oder unabhängige aktuelle Prozessprüfung. Antwort:
`.local-tools/armed-no-broadcast-20261005/astra-final-pm-risk.txt`.
Keine ARMED- oder Live-Freigabe aus bestandenen Teilverträgen ableiten.

## Gesicherter Stand vom 4 Oktober 2026

Codebasis dieses Checkpoints: `cf930a6`. Der kontrollierte Execution-Lifecycle
und seine Anbindung an den bestehenden Runtime-Poll sind integriert und
getestet. Die produktive Gesamtfreigabe ist noch nicht erreicht. Der zuletzt
geprüfte Stack ist **STOPPED**, das Owner-Gate bleibt **OFF**.
Die weiter unten folgenden Arbeitsstände sind historische Befunde; bei
Widersprüchen gilt für die Fortsetzung dieser Checkpoint.

### Integrierte Änderungen

| Commit | Nachweislich integrierter Umfang |
|---|---|
| `7356610` | Bestehender SingleSendAdapter mit geschütztem SDK-Host verbunden; Risk und Gate vor tatsächlichem SEND erneut geprüft. |
| `aa9a8bf` | Fünf echte Prozessabbrüche und Wiederaufnahme aus demselben persistenten Ledger kontrolliert getestet. |
| `1e76722` | Verifizierte CLOSE_ONLY-SELLs bei Gate OFF unter bestehender Autorisierung und Sicherheitsprüfungen erhalten. |
| `cf930a6` | Bestehender Bridge-Poll ruft den kontrollierten Execution-Pfad auf; aktuelle Order-Antwort normalisiert; beobachteten Position-Manager-BUSY-Konflikt ohne BUY-Fallthrough behandelt. |

Der kontrollierte Ablauf verbindet Candidate, Risk unmittelbar vor SEND,
persistent gespeicherte Trade-/Event-/Request-ID und Payload-Hash, genau einen
BUY-SEND, Order-ID/ACK, unabhängige Fill-Evidence und Reconciliation,
POSITION_OPEN, persistenten Exit-Intent, SELL aus bestätigter Restmenge,
genau einen SELL-SEND, unabhängige SELL-Fill-Evidence und
Wallet/Reconciliation bis CLOSED.

ACK bleibt ausdrücklich kein Fill. UNKNOWN führt weder zu blindem Retry noch
zu einer konkurrierenden neuen Order. Fehlende Quote-/Wallet-/Risk-Evidence
blockiert BUY. Es wurde kein zweiter Execution-Stack und kein automatischer
Transport-Fallback eingeführt.

Die fünf Prozessgrenzen sind BUY-ACK vor Fill, persistierter BUY-Fill vor
Reconciliation, POSITION_OPEN mit Exit-Intent, SELL-ACK nach SEND und
persistierter SELL-Fill vor CLOSED. Die Tests beenden einen separaten Prozess
mit `os._exit(91)` und verwenden nach Neustart denselben Ledger sowie eine
unabhängige kontrollierte Venue. Das belegt Prozess-Recovery, keinen
Windows-Neustart oder Stromausfall und keine reale GMGN-Request-ID-Recovery.

### Tests und unabhängige Prüfung

Letzter abgeschlossener Lauf auf der genannten Codebasis: **586 PASS / 0 FAIL
in 67,01 Sekunden**. Umfang: Execution-Regression, SingleSwap-Transport und
Bridge-Heartbeat. Dies war keine vollständige Repository-Testsuite.

```powershell
$testFiles = @(rg --files tests -g 'test_execution*.py')
$testFiles += @('tests/test_single_swap_transport.py', 'tests/test_bridge_heartbeat.py')
& .venv\Scripts\python.exe -m pytest @testFiles -q --basetemp .local-tools/pytest-runtime-completed-final-20261004a
```

Die abgeschlossenen sicherheitsrelevanten Blöcke wurden von Astra High
unabhängig read-only geprüft und nach Berücksichtigung der Befunde mit **OK**
bewertet. Das ist keine finale Abnahme des gesamten IMC-Systems. Astra hat
die Tests nicht selbst ausgeführt. Letzte lokale Review-Evidence:
`.local-tools/runtime-dispatch-final-risk-20261004.txt` und
`.local-tools/runtime-position-busy-final-risk-20261004.txt`.
Die Dateien unter `.local-tools` sind lokale, nicht versionierte Belege.

### Tatsächlich beobachteter Runtime Stand

Ein begrenzter kanonischer START/Poll/STOP vom 04.10.2026,
08:51:45 bis 08:52:28 UTC, hat reale GMGN-Portfolio-/Holdings-Reads und eine
erfolgreiche leere Wallet/RPC-Reconciliation geliefert. Die Bridge erreichte
`NO_READY_EVENT` ohne letzten Fehler. Im Beobachtungsfenster bestand kein
geeigneter frischer SAFE100-Candidate; ein echter BUY wurde nicht ausgeführt.

Die gestarteten Rollen waren SignalSupervisor, Core, Watcher,
PureSignalGateway, Bridge und Health. Während des Laufs gab es eine noch
nicht abschließend zugeordnete Interpreter-Beobachtung; daraus wird kein
vollständiges Health-PASS abgeleitet. STOP wurde anschließend bestätigt.
Die letzte native Prozessaufnahme zeigte keine IMC-Rollen und keine
unbekannten Interpreter. Lokaler Beleg:
`.local-tools/runtime-dispatch-startstop-20261004.json`.

Letzter Ledger-Befund: 19 REJECTED-Events, keine offene Position und keine
chain-reconcilierten CLOSED-Trades; Kill-Grund leer und keine ausstehende
Reconciliation. Letzte erfolgreiche Reconciliation:
`2026-10-04T08:52:14.975648+00:00`. Dies ist eine historische Zeitpunktaufnahme
und nach STOP kein Frischebeleg. Gespeicherte RUNNING-Statusdateien sind
kein Nachweis eines noch laufenden Prozesses.

Die produktiven Freigaben wurden nicht verändert:

```text
OWNER_BROADCAST_GATE = OFF
execution_authority_pct = 0
broadcast_enabled = false
live_trading_enabled = false
signing = false
wallet_access = false
```

Kein Echtgeld-Broadcast und keine Geldbewegung. Der kontrollierte
SDK-Lifecycle verwendet isolierte Testdaten und Dummy-Schlüssel; er ist kein
produktiver Signing-/Wallet-Freigabebeleg.

### Reale Blocker und direkter nächster Schritt

1. Der letzte reale GMGN-Quote-Probe liefert weiterhin HTTP/API **429**,
   `GMGN_RATE_LIMITED`, Retry-After 31 Sekunden. Provider-Ursache und
   Quota-/Plan-Zuordnung sind nicht bewiesen. Identische Probe-Schleifen
   liefern derzeit keinen neuen Erkenntnisgewinn.
2. Dem produktiven Quote-Pfad fehlen belegte frische Werte für Price Impact,
   Liquidität und Preisabweichung. Diese bleiben unbekannt und blockieren
   BUY; es werden keine Nullwerte oder PASS-Ergebnisse erfunden.
3. CORE/MARKET_DATA/MONITORING sind im Execution-Health-Vertrag noch UNKNOWN,
   der Watchdog-Nachweis fehlt. Prozessanwesenheit allein genügt nicht.
4. Produktive Bewertung offener Positionen, dynamischer Exit und der gesamte
   Runtime-Ablauf Gate OFF → CLOSE_ONLY → FLAT_SAFE → STOP sind noch nicht
   vollständig belegt. Die kontrollierte CLOSE_ONLY-SELL-Kette ist belegt.
5. UNKNOWN ohne bekannte Order-ID bleibt fail-closed. Für GMGN ist keine
   sichere Request-ID-Recovery-API nachgewiesen; der kontrollierte
   Venue-Request-Journal ist keine reale Provider-Funktion.
6. Reale Performance-/Rollout-Gates, vollständige Projektabnahme und finaler
   Astra-Gesamtreview sind offen. Kontrollierte/simulierte Ergebnisse dürfen
   weder reale PnL noch ausreichende Produktions-Performance ersetzen.

**Nächster direkter Arbeitsblock:** vorhandenen produktiven Quote-/Market-
Evidence-Pfad prüfen und vervollständigen, soweit reale Provider-Daten dies
erlauben; danach die notwendigen Health-Verträge anbinden und denselben
Runtime-Pfad ohne Broadcast bis unmittelbar vor SEND belegen.
Bestehende Execution-Komponenten weiterverwenden. Keine Live-Freigaben ändern.

### Erhaltene Änderungen und bewusst zurückgestellte Arbeit

Diese bereits vorhandenen Working-Tree-Dateien wurden nicht in die vier
Execution-Commits aufgenommen und müssen bei der Fortsetzung erhalten bleiben:

- `TOOLS/GMGN-ReadProtocol.ps1` — bestehende Änderung.
- `TOOLS/ImcStorageThreadLifetime.cs` — untracked.
- `tests/storage_thread_lifetime_fixture.ps1` — untracked.
- `tests/test_storage_thread_lifetime.py` — untracked.

Der ursprüngliche gehaltene Maintenance-Owner wurde nach unabhängiger
RiskReview und nativer Identitäts-/Handle-Prüfung kontrolliert abgeschlossen;
Maintenance steht IDLE. Es wurden keine Datenbanken veröffentlicht,
verschoben oder gelöscht. Die große CORE-Datenbank von zuletzt rund 40,8 GB
und die vorhandenen Arbeits-/Rohkopien wurden erhalten.

Weitere Read-/Storage-/Legacy-/Native-Lifetime-Sonderfälle, zusätzliche
Fixture-Permutationen, Dashboard-Politur und Architekturverschönerung bleiben
zurückgestellt, solange kein konkret nachgewiesener Produktionsblocker sie
erfordert. Das System ist noch nicht ARMED_NO_BROADCAST; der Owner-Gate-Wechsel
ist noch nicht der einzige verbleibende Schritt.

## Historische Arbeitsstände vor diesem Checkpoint

Stand: 02.10.2026. Aktuelle Fortsetzung ab `de7f999`; native Integration:
`e7f0a9d`, `833749b`, `d9e9d4c`, `972cdbb`.
Der Clean Reset und die Live-Abnahme sind **noch nicht abgeschlossen**.

Aktuell: 304 Gesamttests PASS, 0 FAIL; eigener passiver Supervisor läuft.
GMGN-Portfolio/Holdings und ein kohärenter finalisierter Wallet-Snapshot sind
verifiziert. Erste Reconciliation/Equity-Baseline bestanden. Quote-Reads liefern
bisher 429, Ursache nicht bewiesen. Der echte Schreibtransport fehlt noch.
Die früheren Sandbox-/Git-/Broker-Zugangsblocker unten sind historische Befunde
und durch diese Fortsetzung überholt. Details: [[rc1-readiness]].

## Historischer Ausgangsstand vor dieser Fortsetzung

Ausgangscommit: `c395c9d`; ursprüngliche Git-Baseline: `1d607b7`.

Aktueller Execution-Arbeitsstand nach dem Folgeauftrag: siehe
[[execution-hardening]] und `execution-audit.json`. Der vorhandene
Signal-only-Pfad ist um einen transaktionalen Execution-Vertrag mit
unabhängigen Fill-/Reconciliation-Lesern und geschütztem Monitoring ergänzt.
**139 Gesamttests bestanden; Astra High: OK für diesen Signal-only-
Zwischenstand.** Live-Freigaben bleiben 0/false. Reale GMGN-Antwortprüfung
ist weiter offen: der direkte Credential-Zugriff ist gesperrt; der vom Nutzer
bereitgestellte Read-only-Broker ist in dieser Sitzung noch nicht zugeordnet
(`IMC_GMGN_BROKER_DIR` leer, Shim meldet `broker_unavailable`). Der Git-Commit
scheitert weiterhin an fehlendem Schreibzugriff auf `.git/index.lock`;
ausführender Adapter, vollständiger 1:1-Shadow, Equity-Guards und die
verbleibenden Clean-Reset-Arbeiten sind weiterhin offen.

## Phase 0 — Ausgangspunkt und Schutzgrenzen

AGENTS.md, IMC_CODEX_MASTERPLAN.md und config/imc-advisor.json vollständig
gelesen. Keine Launcher ausgeführt, keine Trading-Prozesse gestartet oder
gestoppt, keine Strategie- oder Authority-/Broadcast-Parameter geändert.

Die echte Prozessprüfung über `Get-CimInstance Win32_Process` außerhalb der
Sandbox zeigte keine IMC-Python-Dienste und keine Trading-Node-Gateways.
Node PID 3320 war der Sol-Codex-Launcher; die übrigen Node-/node_repl-Prozesse
gehörten zur Codex-Computer-Use-Runtime. Während der Advisor-Prüfung kamen
vorübergehend Advisor-Codex-Prozesse hinzu. Nach deren Ende wurde die Zuordnung
erneut geprüft. `Get-NetTCPConnection -State Listen` zeigte keine IMC-Listener
auf 49090 oder 8787; `Get-NetUDPEndpoint` keine Belegung von 48125, 48126,
49225 oder 49425. Dies ist eine Zeitpunktaufnahme, keine Betriebsgarantie.

Vor Beginn bestand bereits eine Änderung an
`CORE/data/neural-adaptive-state-v0.4.3.json`. Sie bleibt unangetastet und
wird nicht in den Arbeitscommit aufgenommen. Datenbanken und WAL/SHM sind
nicht Git-versioniert. Die Inventur erfasst Metadaten, keine Secrets und
keine vollständigen Runtime-Zustände.

### Pipeline und Komponenten

ACTIVE bedeutet hier: im vorgesehenen Hauptpfad verdrahtet, **nicht** aktuell
laufend. OPTIONAL ist ein gesondert startbarer, belegter Pfad; LEGACY ein
historischer oder nicht integrierter Pfad. DEAD wird nur für geprüfte
überflüssige Artefakte verwendet, nicht allein wegen eines Versionsnamens.

| Komponente | Einstufung | Nachweis / Abhängigkeit |
|---|---|---|
| Discovery → Security → Scoring / Paper-Ledger | ACTIVE | `CORE/mykonos_meme_scout/cli.py`, `engine.py`, `providers.py`, `scoring.py`, `store.py`; Start über `CORE/START_CORE_WORKER.ps1` |
| Breadth / SAFE100 | ACTIVE | `RUNTIME/watch_breadth_v425_shadow.py`; Decision-Bus UDP 48125, CORE-SQLite; importiert lokalen `tail_exit_guard_v41.py` |
| Pure-Signal-Gateway | ACTIVE | `START_IMC.bat` → `LAUNCHERS/START_IMC_SIGNAL_ONLY.ps1` → `GMGN_BRIDGE/imc_pure_signal_gateway.py`; Port 49090 |
| GMGN-Signal-Bridge | ACTIVE | derselbe Launcher → `gmgn_signal_bridge.py`; `/next` → Outbox; liest GMGN-Portfolio über CLI |
| Tatsächlicher GMGN-Executor | nicht geschlossen | Bridge erzeugt `execution=False`, `READY_FOR_GMGN_EXECUTOR`; daraus folgt kein ausgeführter Trade |
| Execution-Twin v480 | OPTIONAL | `WATCHDOG_V460_TRUTH_TWIN.ps1` startet `real_market_execution_v480_build_parity.py`; SAFE100 über UDP 48126 / DB-Fallback; historischer Build-/Simulationsnachweis bleibt erhalten |
| v490 Twin / Signal-Gateway / Supervisor | OPTIONAL | `START_IMC_TRUTH_STACK.ps1` → `START_IMC_MASTER.ps1` → `START_IMC_AUTONOMOUS_PREP.ps1`; eigener Gateway ebenfalls Port 49090 |
| Learning-Settler | OPTIONAL | `WATCHDOG_V460_TRUTH_TWIN.ps1` startet `learning_settler_v460.py`; historische Snapshots/Predictions/Outcomes sind Lernabhängigkeiten |
| VR12 / DG120 Research | OPTIONAL | `WATCHDOG_RESEARCH_V482.ps1` startet beide Watcher und `execution_v483_vr12_route_forward.py` |
| System Health | OPTIONAL | `RUNTIME/imc_system_health.py` ist separat aufrufbar; nach Launcher-Vereinheitlichung kein Bestandteil des Signal-only-Starts |
| `brain_v1/` | LEGACY, geschützt | eigenständiger CLI-Pfad, keine Verdrahtung mit Hauptmotor; ADR 0001 verlangt gesonderte Owner-Entscheidung |
| CORE-Dashboard | OPTIONAL | `cli.py` importiert `CORE/mykonos_meme_scout/dashboard.py`; read-only Anzeige |
| `scripts/dashboard.py` | OPTIONAL | separate CORE-Start/Stop-Oberfläche, Port 8787; nicht bloß identische Kopie des CORE-Dashboards |
| zwei `tail_exit_guard_v41.py` | OPTIONAL, beide benötigt | identischer SHA256, aber lokale Imports aus RUNTIME bzw. RESEARCH; keine Kopie ohne Importmigration entfernen |
| alte v481/v482 State-/Status-Artefakte | DEAD-Kandidaten | siehe konkrete Phase-1-Liste; Freigabe und Advisor-Bewertung vor Entfernung |

Der derzeit gespeicherte Mastermodus ist `PURE_SIGNAL_ONLY` mit
`live_broadcast=false`, `signing=false`, `wallet_access=false`.
`config_v480.json` enthält `broadcast_default=false`,
`config_v490_live.json` enthält `broadcast_enabled=false`. Diese Lesebefunde
belegen keine aktuell ausgeführte Pipeline; dafür gilt die Prozessaufnahme.

### Ursprüngliche Widersprüche und Bearbeitungsstand

- `START_IMC.bat` startet Signal-only, `LAUNCHERS/START_IMC.ps1` hingegen
  CORE plus Runtime-/Research-Watchdogs und Health. Namen sind nicht
  gleichbedeutend. Das Root-Startziel ist die Grundlage der Hauptpfadkarte.
  **Behoben:** Root-Start und `LAUNCHERS/START_IMC.ps1` führen jetzt über
  denselben bestehenden Signal-only-Einstieg.
- Pure-Signal-Gateway und v490-Gateway beanspruchen beide Port 49090.
  Ein zukünftiger Supervisor muss Modi gegenseitig ausschließen.
- `STOP_IMC.ps1` trifft pauschal jede passende Projekt-Kommandozeile,
  einschließlich Arbeitswerkzeugen. Diesen Stopper nicht als Prüfwerkzeug
  ausführen; spätere PID-Verwaltung muss auf konkrete Dienste begrenzt sein.
  **Behoben:** gemeinsame Skript-/Modulzuordnung mit PID-/Erstellungszeit-
  Prüfung; zuerst verifizierter Wrapper-Stop, danach erneute Kinderaufnahme.
- `START_IMC_AUTONOMOUS_PREP.ps1` kann Configs schreiben, Wallet-Verifikation
  und Netzwerkzugriffe ausführen sowie bestehende Zustände und eine DB
  verschieben. Für die Inventur ausschließlich statisch gelesen.
- Die bisherige Betriebsdoku verneint automatischen Neustart, während
  CORE-Loop und Watchdogs im Code Neustartlogik enthalten. Die neuen
  Betriebsunterlagen müssen sich am tatsächlichen Modus orientieren.
  **Behoben:** Betriebsrunbook unterscheidet CORE-Loop, optionale Watchdogs
  und Dienste ohne pauschale Restart-Garantie.
- Vorhandene Launcher erwarten `py -3.12`; in dieser Arbeitsumgebung wurde
  `py` nicht gefunden, `python` meldet 3.14.7. Eine einheitliche `.venv`
  besteht noch nicht. Keine Interpreterumstellung in Phase 0.

### Verifikation / Advisor

`python -m pytest -q -p no:cacheprovider --basetemp=.reset_test_tmp`:
**16 passed**. Vorher Tests gelesen: sie schreiben nur isolierte Testdateien
und Testdatenbanken, starten keine IMC-Dienste und nutzen keine produktive DB.
Der neue Test-Tempordner wird ignoriert und nicht gelöscht.

Astra RiskReview (Medium) bewertete die Inventur als vertretbar, meldete aber
RISK wegen unvollständiger Node-Zuordnung innerhalb seiner Sandbox.
Die ergänzende freigegebene CIM-/Portprüfung des Hauptagenten klärte diese
Zuordnung. Weitere Empfehlungen umgesetzt: nur statische Launcherprüfung,
keine DEAD-Einstufung anhand von Namen/Offline-Status, brain_v1 und
Config-Pfade schützen, vorbestehenden Modellzustand nicht committen.

## Phase 1 — Inventur und konkrete Freigabeliste

`clean-reset-inventory.json` erfasst alle 229 am Ausgangscommit getrackten
Dateien sowie 26 zusätzliche lokale Dateien außerhalb Dependency-/Cache-
Ordnern. Enthalten sind Dateigrößen, Code-Hashes, Versions-/Backupnamen und
Referenzfundstellen. Alle erfassten Dateien konnten gelesen werden.
Literal- und Python-Import-Suche ist ein Indiz, keine vollständige Analyse
dynamischer Imports. DB-Dateien sind nur als Metadaten enthalten.

Keine neuen Codeversionen oder Dateikopien erzeugt. Bestehende aktive
Versionsnamen bleiben erhalten, bis eine geprüfte Pfadmigration möglich ist.

### Freigegeben und entfernt am 02.10.2026

Alle acht Dateien sind über `git show 1d607b7:<Pfad>` wiederherstellbar.
Die Referenzsuche in lesbaren getrackten Dateien fand keine Konsumenten.
Die ursprünglichen v481/v482-Produzenten sind im aktuellen Checkout nicht
vorhanden; der Research-Watchdog referenziert nur den v483-Execution-Pfad.
Die zugehörigen Datenbanken und Forschungsergebnisse werden nicht gelöscht.

| Pfad | Grund |
|---|---|
| `RESEARCH/light_filter_sweep_v484.err.txt` | leere Prozessausgabe |
| `RESEARCH/light_filter_sweep_v484.out.txt` | JSON-Ausgabe ist die ersten acht Resultate des erhaltenen `LIGHT_FILTER_SWEEP_V484.json`; Quellen-/usable-Zähler ebenfalls identisch |
| `RESEARCH/execution_v481_timeout_research_state.json` | verwaister historischer Runtime-Zustand |
| `RESEARCH/execution_v481_timeout_research_status.json` | verwaister historischer Runtime-Status |
| `RESEARCH/execution_v482_dg120_route_forward_state.json` | verwaister historischer Runtime-Zustand |
| `RESEARCH/execution_v482_dg120_route_forward_status.json` | verwaister historischer Runtime-Status |
| `RESEARCH/execution_v482_vr12_route_forward_state.json` | verwaister historischer Runtime-Zustand |
| `RESEARCH/execution_v482_vr12_route_forward_status.json` | verwaister historischer Runtime-Status |

Der Owner hat die konkrete Acht-Dateien-Liste mit „Geht los“ freigegeben.
Alle acht Dateien wurden nach erneuter Pfad- und Git-Statusprüfung entfernt.
Die anschließende isolierte pytest-Prüfung lief mit **16 passed**.
Damit ist dieser Bereinigungsblock abgeschlossen, nicht die gesamte Phase 1.
Weitere Runtime-Artefakte erst nach Zuordnung ihrer
Konsumenten und gegebenenfalls Migration aus Git. Keine pauschale Entfernung
von Research-Lanes, Modellen, Journals, Logs oder DBs.

Astra hat die konkrete Liste in einer gesonderten RiskReview (Medium)
mit **OK nach Owner-Freigabe** bewertet. Der Hauptagent hat alle acht
Dateien zusätzlich bytegenau gegen `git show 1d607b7:<Pfad>` verglichen.
Der historische v482-VR12-Status enthält `TRAPPED=1`; er ist ein
Simulationszustand, kein aktueller Live-Nachweis. Sein Ursprungsinhalt bleibt
über Git und die dazugehörige unveränderte Forschungsdatenbank erhalten.
Der Advisor bestätigte die v483-Unabhängigkeit und den Top-8-Vergleich;
die repoweite Referenzsuche und freigegebene Prozessprüfung stammen vom
Hauptagenten. Die konkrete Freigabe lag vor der Entfernung vor.

### Kanonische Betriebswege

START/STOP/STATUS in Root und LAUNCHERS sind vereinheitlicht; Signal-only
bleibt der bestehende Standard. Optionalen Truth-/Research-Pfaden wurden
keine Dateien entzogen. Astra RiskReview meldete CHANGE und verlangte,
auch START auf die sichere gemeinsame Zuordnung umzustellen, tatsächliche
Skriptpositionen statt beliebiger Argumente zu prüfen, PID-Wiederverwendung
abzuwehren und Stopfehler/Restprozesse sichtbar zu machen. Dies ist umgesetzt.

Verifikation: **27 pytest-Tests passed**, darunter **38 synthetische
Prozess-Identitätsfälle** und Syntaxprüfung von sechs PowerShell-Dateien.
Die echte lesende Statusprüfung zeigte Core, CoreWrapper, Watcher,
PureSignalGateway und Bridge STOPPED; gespeicherte JSONs wurden separat
als historische Dateien angezeigt. Kein START-/STOP-End-to-End-Test am
Trading-System wurde ausgeführt; kein Trading-Dienst wurde gestartet/gestoppt.
Neue LIVE-/Broadcast-/Authority-Aktivierung fand nicht statt.

Noch offen in Phase 1/4: übrigen Runtime-State aus Git trennen, einheitlicher
Interpreter, mode-aware Health und durchgängige Supervisor-/PID-Verwaltung
gegen parallele Starter. Der Health-Watchdog wurde nicht als aktuelle
Prozesswahrheit ausgegeben und ist noch nicht mode-aware vereinheitlicht.

### Laufzeitdateien aus dem Git-Index getrennt

Commit `9c84d23` nimmt genau 13 durch Writer belegte Runtime-Dateien aus
dem Index. **Alle 13 lokalen Dateien bleiben bytegleich vorhanden**;
Existenz und SHA256 wurden unmittelbar vor/nach `git rm --cached` geprüft.
Das umfasst ausdrücklich die vorbestehende Owner-Änderung an
`CORE/data/neural-adaptive-state-v0.4.3.json`. Sie wurde weder zurückgesetzt
noch als neuer Inhalt committed. `.gitignore` enthält genau diese 13
an der Repo-Wurzel verankerten Einträge; Modelle, Configs und Baselines sind
weiter versioniert. Astra RiskReview bestätigte die Auswahl mit OK.
Die Liste steht vollständig in diesem Commit und in `.gitignore`.

Andere Checkouts müssen ihre lokalen Runtime-Inhalte vor Übernahme dieses
Index-Löschcommits erhalten. Die Git-Historie enthält ältere Versionen;
die lokale uncommittete Owner-Änderung bleibt nur lokal vorhanden.

## Phase 2–6 — noch offen, Vorbefunde für die Datenarbeit

### Reproduzierbare read-only Inventur

`scripts/db_audit.py` liefert JSON mit Quelle, Schema/Triggern, Zeilenzahlen,
Zeitbereichen, optionalen dbstat-Objektgrößen und UTC-Stunden-/Tageszählungen.
Eine Read-Transaction hält die SQL-Ergebnisse konsistent; Dateimetadaten sind
ausdrücklich Außenmessungen. `mode=ro` berücksichtigt WAL-Inhalte, verhindert
Nutzdatenänderungen und kann SQLite-SHM-Buchführung beeinflussen.

Astra RiskReview meldete CHANGE: Gesamtbudget, Lock-Timeout, Fetch-Grenzen,
WAL-/SHM-Einschränkungen und Snapshot-/Fehlerprüfungen erforderlich. Diese
Punkte wurden umgesetzt. Der erste große Lauf überschritt das kooperative
Budget und wurde gezielt als eigener Audit-Prozess beendet. Er lieferte
keine verwendbaren Zahlen; kein Trading-Prozess wurde gestoppt.

Eine neue, eng begrenzte Astra-Prüfung zum Timeout-Befund meldete RISK und
verlangte vor Wiederholung einen externen Watchdog. Der CLI startet deshalb
einen eigenen read-only Kindprozess, den `subprocess.run` nach Gesamtbudget
plus Lock-/Shutdown-Zugabe beendet und abwartet. Ein erzwungener Abbruch
liefert `complete=false`, Fehler und keine teilweise exakten Zähler.
`COUNT(1)` ersetzt den SQLite-Fast-Count; die Ursache des ursprünglichen
langen Laufs ist damit nicht rückwirkend bewiesen.

Tests nach diesen Änderungen: **26 passed**. Neue Fälle prüfen WAL,
fehlendes SHM, parallelen Schreiber, Locks, Einzel- und Mehrzeilen-Fetch,
verspätete Ergebnisse sowie die unvollständige CLI-Abbruchmeldung.

Beispiel: `python scripts/db_audit.py --pages --growth --query-seconds 15
--total-seconds 90`. `complete/errors` immer zusammen mit Ergebnissen lesen.
Exit 2 bedeutet unvollständiges Audit. dbstat misst zugewiesene Bytes, keine
automatisch freigebbaren Bytes. Das Werkzeug nimmt keine Datenbereinigung vor.

`clean-reset-db-audit.json`: 90-Sekunden-Audit mit Wachstum; **unvollständig**.
`dbstat` ist in diesem Python-/SQLite-Build nicht verfügbar; das wurde durch
`PRAGMA compile_options` bestätigt. Zusätzlich liefen einige große Abfragen
ins Zeitlimit. Die Größenanteile der einzelnen Tabellen sind deshalb noch
offen, auch wenn die Gesamtdateigröße und einzelne Zähler ermittelt wurden.

`clean-reset-db-counts.json`: separate konsistente Zählung ohne Wachstum und
dbstat, **complete=true, keine Fehler**, 96,752 Sekunden. Quelle für jede
Zeilenzahl: `CORE/data/imc-main.db` → die angegebene Tabelle.

| Tabelle | Exakte Zeilen |
|---|---:|
| snapshots | 12.247.905 |
| decisions | 12.246.427 |
| learning_predictions | 16.162.190 |
| learning_outcomes | 6.050.634 |
| cookie_ledger | 6.050.634 |
| outcomes | 22.854 |
| positions | 82 |
| trades | 187 |
| model_registry | 62 |
| account_state | 1 |
| schema_meta | 3 |

Wachstumszählungen im unvollständigen ersten Audit sind Ereignisse pro
UTC-Stunde/Tag, keine gemessenen Bytes pro Stunde und keine Projektion einer
Einsparquote. Vollständige Tabellenzuordnung/KEEP-COMPACT-DELETE und eine
laufende Compaction-Policy bleiben Aufgaben der Phase 2.

### Stichprobe zur Redundanz (nicht auf die ganze DB hochrechnen)

Quelle: `CORE/data/imc-main.db` → `snapshots`, neueste 10.000 Zeilen nach ID
(12.237.906 bis 12.247.905). Entfernt man für den Vergleich ausschließlich
`captured_at` aus dem JSON-Payload, verbleiben **363 unterschiedliche
Payloads**. Roh-Payloads dieser Stichprobe umfassen 8.324.587 Bytes; die
einmaligen normalisierten Payloads 290.748 Bytes. Zeitpunkte und Zeilenindizes
sind darin nicht enthalten. Das ist ein Ansatzpunkt für eine verlustfreie
Ereignis-/Payload-Trennung, kein Nachweis einer globalen Einsparquote und
keine Freigabe zur Entfernung von Snapshots.

Geschützte Konsumenten: `training_data._snapshot_features` (bis zu 240
historische Snapshots), `machine_intelligence.training_rows` (exakte Quell-
und Terminalpayloads), `store.label_and_reward_learning_predictions`
(Horizonverlauf/Rug-Liquidität), `store.exit_shadow_summary` (Preisverlauf
geschlossener Positionen), Breadth-/Research-Watcher (Decision-Cursor und
Exit-Verläufe). `outcomes` labelt Decisions; `learning_outcomes` labelt
Predictions. Diese Tabellen dürfen nicht miteinander verwechselt werden.

`CORE/data/imc-main.db` misst **37.475.799.040 Bytes** (37,476 GB dezimal;
Dateimetadaten am 02.10.2026); WAL zusätzlich 11.906.832 Bytes. Das ist
eine Dateigröße, keine Aussage über lernrelevante Tabellenanteile.

Das bestehende `scripts/db_retention.py --execute` wird nicht ausgeführt:
Es kürzt Tabellen unabhängig nach Zeit, während `training_data.py`
Predictions/Outcomes verbindet und historische Snapshots für Features nutzt.
`store.py` hat außerdem Append-only-Trigger für learning_outcomes. Diese
Abhängigkeiten müssen zuerst in einem lernrelevanten Ereignisdatensatz
abgesichert werden. Ein einfaches Altersfenster oder VACUUM allein erfüllt
den Masterplan nicht.

Nächste Datenarbeit: read-only Schema-/Tabellen-/Wachstumsanalyse,
Konsumentenkarte, KEEP/COMPACT/DELETE-Konzept, Astra RiskReview; danach
bewerteter Dry-run und Wiederherstellungsweg vor konkreter Datenfreigabe.
Retention/Compaction muss auch neue Schreibvorgänge begrenzen und mit den
Lernpfaden getestet werden. Strategietuning und Live-Aktivierung bleiben aus.

### Phase 4 — gemeinsame Python-Umgebung (Teilabschluss)

Vor Umsetzung: Astra RiskReview meldete CHANGE und verlangte einen
absoluten Interpreter sowie Versions-/Importprüfung vor sämtlichen
Config- und Prozessaktionen, auch vor Restart-Loops. Das ist in
`LAUNCHERS/IMC_PYTHON_COMMON.ps1` und den Python-startenden Launchern
umgesetzt. Kein globaler PATH wurde verändert; keine Dienste gestartet.

Lokale `.venv`: Python 3.12.10, CORE editable aus `./CORE`, NumPy, solders
und Testwerkzeuge. `requirements.txt` pinnt direkte, transitive und
Build-Abhängigkeiten. `pip check` ohne Fehler, CORE `--help` erfolgreich,
**28 Tests bestanden**. Die neuen Fixtures prüfen fehlende Umgebung,
gescheiterten Preflight und Syntax aller 13 geänderten Launcherdateien.
Testlauf: `.venv/Scripts/python.exe -m pytest -q --basetemp
.local-tools/test-env -p no:cacheprovider`; das frühere temporäre Verzeichnis
verweigerte den Zugriff, deshalb wurde ein isoliertes schreibbares
Verzeichnis verwendet. Keine produktive Datenbank ist eine Testfixture.

Noch offen: mode-aware Health, vollständige Supervisor-Vereinheitlichung
der optionalen Pfade und die dauerhafte Datenkomprimierung. Dieser
Teilabschluss ist kein Abschluss des Clean Reset.

### Sperre des unsicheren Alters-Cleanup

Neuer Codebefund, vor Änderung mit Astra RiskReview geprüft (**OK**):
`scripts/db_retention.py` committete DELETEs auf Snapshots und Decisions
einzeln, bevor Append-only-Trigger auf Lerntabellen den Lauf abbrechen
konnten. Fehlende Snapshots können außerdem in `_snapshot_features` durch
Defaults verdeckt werden. Das Skript wurde nicht zur Löschung ausgeführt.

`--execute` wird jetzt mit Exit 2 vor Audit-Import und DB-Zugriff abgewiesen.
DELETE-/VACUUM-Pfade sind entfernt. Der kompatible lesende Einstieg nutzt
den extern überwachten CLI von `db_audit.py`; `--days` bleibt syntaktisch
akzeptiert, hat ausdrücklich keine Wirkung. stdout enthält Audit-JSON,
der Kompatibilitätshinweis geht nach stderr. Der Audit-Fehlerstatus wird
weitergegeben. Das alte Textformat und die Altersauswahl entfallen bewusst.
Die Sperre repariert keine möglichen Verluste aus früheren Läufen und
ersetzt noch keine automatische Retention oder Compaction.

### Phase 4 — Health nach Betriebsmodus

Astra RiskReview meldete **CHANGE**: gespeicherter Modus kann veraltet sein,
Wrapper ist kein Worker, teilweise CIM-Sicht muss sichtbar bleiben, sämtliche
DB-/Rate-Probes müssen read-only sein, keine Broadcast-Garantie aus Health.
Diese Punkte sind umgesetzt. Erwartungen/Schwellen in `config/imc-health.json`,
die vorhandene gemeinsame Prozesszuordnung liefert echte Rollen/PIDs.
Nur erwartete oder tatsächlich beobachtete Komponenten liefern Warnungen;
Konflikte/Mehrfachprozesse bleiben Alarme. Andere Betriebsmodi sind noch
nicht als gesund bewertbar. Keine Launcher oder Authority-Flags verändert.

`--once` schreibt weder Lock noch Status. Echte lesende Prüfung am
02.10.2026 10:31 UTC: keine erkannten IMC-Dienste, CRITICAL wegen nicht
beobachteter erwarteter Rollen und alter CORE-Daten. Kein Research-Alarm nur
wegen ausgeschalteter optionaler Lanes. Keine Prozesse gestartet/gestoppt.
Fixtures prüfen Moduskonflikte, Wrapper/Worker, Duplikate, Sichtlücken,
Konfigurationsfehler, ungültige/zukünftige Zeiten, WAL-Lesen, fehlende
Tabellen und Nicht-Erzeugen fehlender DBs. **43 Tests bestanden**.

### Speicher-Audit: ausdrücklich unvollständig

`clean-reset-db-storage.json` verwendet SQLite-CLI 3.53.4 read-only mit
180 Sekunden pro Objekt und 900 Sekunden Gesamtbudget. main/WAL-Dateigröße
und Änderungszeit blieben unverändert. Ein eigener SQLite-Kindprozess wird
bei Zeitüberschreitung beendet; keine IMC-Dienste betroffen. Jede Objektmessung
hat eine eigene Lesetransaktion; die Metadatenprüfung ist kein gemeinsamer
SQL-Snapshot und kein kryptographischer Inhaltsnachweis.

Quelle `CORE/data/imc-main.db`: vier erfolgreich gemessene explizite Indizes
für Snapshots, Decisions und Predictions belegen zusammen 7.038.410.752 Bytes.
`cookie_ledger` belegt 1.381.122.048 Bytes, `learning_outcomes` 343.887.872
Bytes. Dies sind zugewiesene Bytes, kein freigabefähiges Löschpotenzial.
Die großen Tabellen Snapshots, Decisions und learning_predictions sowie
mehrere Autoindizes fehlen wegen Zeitlimits; deshalb `complete=false`.
Keinen Tabellenanteil oder globale Einsparung aus dieser Teilmessung ableiten.

### Phase 2/3 — Trainingsquellen mit WAL-Provenienz

Vor Umsetzung Astra RiskReview (**CHANGE**) zur neu gefundenen
`immutable=1`-/Hash-Lücke im `training-dataset`-Pfad. Der Leser verwendet
jetzt `mode=ro`, query-only und eine tatsächlich fixierte BEGIN-Lesetransaktion.
Hauptdatei und WAL werden vor und nach sämtlichen Quell-/Feature-Abfragen
vollständig gehasht; Dateiidentität, Existenz, Größe und Zeiten werden auch
um die Hashdurchläufe geprüft. Abweichungen brechen vor Dataset-Ausgabe ab.
Fingerprint Version 2 enthält eindeutig beide Hashes bzw. WAL-Abwesenheit;
Records und Manifest verwenden denselben Fingerprint. Das Manifest behält
`sha256` als Hauptdatei-Hash und ergänzt `wal_sha256`.

SHM kann durch SQLite angelegt/verändert werden. Diese Prüfungen ersetzen
keinen exklusiven Schreiber-Lock und erkennen nicht jede rückgängig gemachte
Zwischenmutation; produktive Exporte verlangen eine ruhende Quelle. Zwei
vollständige Hashläufe können auf der großen Datenbank lange dauern.

**50 Tests bestanden**: WAL-Features, im WAL liegende Simulationsmarker,
Mutation bei Feature-Lesen, Dateiidentitätswechsel, neu auftauchendes WAL,
abweichender Inhalt trotz gleicher Metadaten und Schreibsperre. Keine
produktive Datenbank exportiert oder geändert. Der Export bleibt balanced-only
und auf `max_rows_per_source` begrenzt; er ist kein vollständiges Lernarchiv
und begründet keine Rohdatenlöschung. Die anschließende Fortsetzung vereinheitlicht
die Quellen-/WAL-Provenienz auch für `simulation.py` und
`temporal_intelligence.py`: siehe [[research-source-integrity]]. Astra High
RiskReview: **OK**; **153 Gesamttests bestanden**. Dies ist weiterhin keine
Freigabe zur Rohdatenbereinigung und kein Abschluss des Clean Reset.

### Windows-Leseschutz fuer das naechste Offline-Audit

`scripts/windows_read_guard.py` haelt native Main-/WAL-Dateihandles mit
FILE_SHARE_READ und validiert Datei-ID und kanonischen Native-Pfad. Das
verhindert gewoehnliche neue Schreib-/Delete-Opens; bestehende Mappings und
SHM-Buchfuehrung sind davon nicht abgedeckt. Kein ACL-Eingriff.

Der exakt angegebene SQLite-CLI liest mit query-only, begrenztem Cache und
einem gemeinsamen kooperativen Deadline-Budget. Ausgabe ist auf 1 MiB
begrenzt. Init-Datei und Binary bleiben bis zum nativen Child-Ende geschuetzt.
Unbestaetigtes Child-/Thread-Ende oder CloseHandle-Fehler halten die Guards
im Controllerprozess; dies ist keine persistente Neustartsperre. Der
aufrufende Controller muss Cleanup vor jedem Runtime-Neustart bestaetigen.

Zwoelf echte Windows-Fixturetests pruefen WAL-Lesen, Schreibsperre nach
Reader-Ende, Timeout, Output-Limit, Init-Rennen, Handle-Fehler und Thread-
Konstruktor-/Start-/Join-Fehler. **780 Gesamttests PASS, 0 FAIL** am 03.10.2026.
Astra High: **OK fuer diesen begrenzten Utility-Diff**. Die produktive
Datenbank wurde damit noch nicht gestoppt, geaendert oder komprimiert;
vollstaendige Objektmessung und dauerhafte Wachstumsbegrenzung bleiben offen.

### Bestaetigter Cleanup vor der produktiven Offline-Messung

`TOOLS/Invoke-IMC-OfflineAudit.ps1` ist der Wartungscontroller fuer genau
eine begrenzte lesende Messung. Der Aufrufer bindet den erwarteten Supervisor
an PID und native UTC-Geburtszeit. Der Controller prueft Gate OFF, passive
Schema-2-Execution ohne Positionen/unsichere Orders/Receipts, haelt native
Prozessidentitaeten, stoppt nur diesen Supervisor und uebernimmt danach den
Runtime-Mutex. Main/WAL und der gepinnte CLI bleiben gegen neue normale
Schreib-/Delete-Opens geschuetzt. Kein Checkpoint, VACUUM, Delete oder Backup.

Der bestehende Runtime-Job-Helper registriert Startressourcen vor Erstellung;
unbestaetigtes Assign-/Resume-/Close-/Member-Capture bleibt erhalten. Native
Job-Member werden auf genau diesen Job geprueft und gehaltene Handles bis
zum Exit abgewartet. Eine Member-Stichaufnahme beweist nicht alle historischen
Transienten; der Auditcaller bindet daher den Worker und verlangt zusaetzlich
dessen eigene Native-CLI-/Thread-/ReadFiles-Abschlussbelege. Accounting-Null
allein ersetzt kein Prozesssignal. Die urspruengliche Ursache des sporadischen
Orphan-Fixturefehlers ist nicht nachtraeglich bewiesen.

`scripts/db_offline_probe.py` speichert Metadaten vor dem grossen Scan,
begrenzt Ausgabe/Zeit und nennt jede separate SQL-Transaktion. Der FINAL-
Vertrag prueft aktuelle Main-/WAL-Metadaten, eindeutige Query-Envelopes,
native Endbelege und exakte Typen. Saubere Zeitlimits bleiben unvollstaendig;
SQL-/I/O-/Outputfehler sind keine erfolgreichen Zeitlimit-Messungen. RETAINED
haelt bei unbestaetigtem Cleanup Controller/Sperren am Leben. Logfehler nach
tatsaechlichem Release behaupten keine weiter gehaltene Schutzsperre.

Verifikation vor produktiver Nutzung am 03.10.2026: **825 Gesamttests PASS,
0 FAIL in 189,07 s**; anschliessend **8 Controller-Fixturetests PASS** nach
einer rein testinternen Marker-Synchronisierung. Echte isolierte Faelle pruefen
auch optimierte Python-Umgebung, fehlerhaftes Logging, Partial-Release,
Member-Erfassungsfehler und gleichzeitige Eltern-/Kind-Endnachweise. Die
produktive Datenbank ist bis zu diesem Dokumentstand weiterhin unveraendert.
Astra High: **OK im begrenzten geprueften Controller-/Utility-Scope**, nach
den dokumentierten Korrekturen. Vor dem echten STOP folgt die frische native
Safety-/Prozesspruefung; diese Abnahme ist keine globale Live-Freigabe.

### Produktionsgetreue Offline-Vorpruefung

Der erste reale Controlleraufruf brach vor STOP ab; der passive Supervisor
lief weiter. Ursache der anschliessenden Read-only-Diagnose: gespeicherte
Schema-2-Trades besitzen `execution_state`, keine Legacy-Spalte `state`.
Die alte isolierte Fixture bildete denselben falschen Dokumentvertrag ab.

Safety liest jetzt ausschliesslich direkt read-only, validiert echte
Schema-2-Dokumente ueber statische Validatoren ohne Ledger-Konstruktor und
prueft passive Lifecycle-States, Nullmengen, Trade-/Event-Identitaeten und
Bindings. Persistierte Legacy-States, doppelte JSON-Felder, Sende-/Order-
Evidenz und zusaetzliche READY-Events bleiben gesperrt. Ein READY-Event muss
das aktive Event eines READY-Trades sein. Diagnosefelder enthalten feste
Stages und ausschliesslich ordinal exakt erlaubte interne Fehlercodes;
unbekannte Exceptions bleiben generisch. Native Identitaetsanforderungen
und Schutz-/Retain-/Release-Regeln wurden nicht gelockert.

Fixtures entstehen nun aus echten isolierten Ledger-Kandidaten, Events und
deren gespeichertem Schema. Verifikation am 03.10.2026: **54 gezielte Tests
PASS**, danach **852 Gesamttests PASS, 0 FAIL in 205,88 s**. Nach einer rein
testinternen WHERE-Korrektur bestand der gezielte Zusatz-READY-Test erneut
mit nachgewiesener PK-/Dokumentkonsistenz. Die produktive Read-only-Safety
ergab Gate OFF, Schema 2, 0 Positionen, 0 unsichere Events, 0 Receipts.
Die produktiven DB-Schreiber wurden bis zu diesem Dokumentstand nicht
gestoppt; die Datenbank wurde nicht geaendert. Astra High: **OK fuer die
Korrektur im begrenzten Code-/Fixture-Scope**. Vor operativem STOP werden
die frischen nativen Preconditions und der konkrete Wartungsscope getrennt
geprueft; diese Codeabnahme erteilt keine globale Live-Freigabe.

Der zweite reale Aufruf bestand Safety, brach jedoch erneut vor STOP im
Identitaetspfad ab. Derselbe lesende Vorpfad bestand anschliessend; eine
konkrete Ursache ist dadurch nicht bewiesen. Die ergaenzte Diagnose speichert
nur freigegebene Typnamen und feste Datei-IDs mit validierter Zeilennummer.
**35 Controller-Fixtures PASS**; Astra High **OK fuer den Diagnose-Diff**
und separat **OK fuer den beschriebenen kontrollierten Wartungsablauf**.

### Unterbrochenes Execution-Journal und dauerhafte Wartungssperre

Der naechste Aufruf stoppte den eigenen passiven Supervisor und bestaetigte
die Abwesenheit der Runtime. Vor jeglicher Main-DB-Dateiguard oder Audit-Job
blieb er in RETAINED: Execution-SQLite meldet **776,
SQLITE_READONLY_ROLLBACK**. Die Ledger-Datei umfasst 4.538.368 Bytes, ihr
Rollback-Journal 8.720 Bytes; WAL/SHM fehlen. Das Journal hat einen gueltigen
Header mit zwei Records, 4.096-Byte-Seiten und keinen Super-Journal-Trailer.
Dies beweist Recovery-Bedarf, nicht rueckwirkend jede Ursache frueherer
Vor-STOP-Abbrueche. Original und Journal wurden noch nicht recovered.

`IMC_MAINTENANCE.ps1` und `maintenance_state.py` verbinden einen globalen
Windows-Mutex mit einem dauerhaft publizierten ACTIVE-Zustand. Fehlende,
defekte oder unbestaetigte Zustandsdaten blockieren START und direkten RUN.
START gibt seine kurze Pruefung vor dem asynchronen RUN frei; RUN prueft
erneut und haelt die Fence durch seinen eigenen Start. Runtime und Audit
erhalten ebenfalls globale Mutexnamen. Abandonment persistiert Recovery-
Bedarf. IDLE verlangt dieselbe Nonce/Owneridentitaet sowie bestaetigte
Integrity, Safety und native Freigabe. Dies ist eine automatische
Wartungssperre und veraendert OWNER_BROADCAST_GATE nicht.

Die gemeinsame passive Schema-2-Pruefung liegt jetzt in `offline_ledger.py`.
Recovery ist auf Fehler 776 nach kontrolliertem Writer-Stopp beschraenkt:
native DB-/Journal-Guards, bytegleiches RAW-Paar und getrenntes WORK-Paar,
SQLite-Rollback nur auf WORK, Integrity/Safety/logischer und physischer
Digest, erneute Originalpruefung, erst danach normale SQLite-Recovery des
Originals und vollstaendiger Vergleich. RAW bleibt dabei nativ geschuetzt.
Kein Ledger-Konstruktor, keine Anwendungs-DML/DDL, kein manuelles Journal-
Loeschen und kein Main-DB-Checkpoint. Guardschluss vor RW ist gegenueber
nichtkooperierenden Schreibern keine universelle Ausschlusssperre; jede
Abweichung blockiert den Wiederanlauf.

Der Controller bindet den echten Recovery-Worker an seinen nativen Job und
bestaetigt ihn vor dessen Datenarbeit per ACK. Native Endbelege und ein
erneuter Originalvergleich gehen der Main-DB-Messung voraus. Isolierte
Tests pruefen echte Hot-Journals, erhaltenes RAW, Super-Journal-Abweisung,
Gate ON, andere SQLite-Fehler, Hashabweichungen, fehlendes ACK, Absturz nach
Original-Rollback vor Verifikation, blockierte START/RUN und native
Abandonment-/Handover-Pfade. **105 gezielte Tests PASS**; nach weiteren
Handoff-/Guard-Verstaerkungen **31 Recovery-/Maintenance-Tests PASS**.
Die erste Gesamtsuite ergab 882 PASS / 1 FAIL: START hatte den vollen
Interpreter-Importpreflight vor den konfliktgeprueften Startzweig gezogen.
Die reine Pfadpruefung ist nun getrennt; die urspruengliche AST-Invariante
bleibt erhalten. 54 gezielte Tests bestanden nach Korrektur. Astra CHANGE
forderte zudem eigene Handover-Fehlertests und nachgewiesenen nativen
Dateihandle-Schluss im einmaligen Incident-Operator. Dieser liegt einmalig
in `TOOLS/Repair-IMC-RetainedAudit.ps1` und verwendet die kanonischen
Safety-/Recovery-/Close-Funktionen. Fuenf isolierte Kontrollflusstests
bestanden: Erfolg, falsche Identitaet, fehlende Legacy-Uebernahme,
Dateischlussfehler und Prozessabsturz nach Altcontroller-Exit. Abhaengigkeiten
sind synthetisch; weder ein universeller Writer-Ausschluss noch ein
Produktions-Handover wird damit als ausgefuehrt dargestellt.
Erneute Gesamtsuite: **888 PASS, 0 FAIL in 179,59 s**, Exitcode 0.
Astra High **OK fuer den beschriebenen Recovery-/Handover-Ablauf nach
Gesamtsuite-PASS**; native Prozess-/Mutexoperationen der Operator-Fixture
sind ausdrücklich Doubles. Dies ist keine globale Live-Abnahme.
Original-Recovery folgt erst jetzt. Der alte
Controller bleibt derzeit lebend in RETAINED; ein gepruefter einmaliger
Handover uebernimmt ihn unter neuer dauerhafter/globaler Sperre.

Am 03.10.2026 wurde dieser Ablauf nach Code-Commit `29679f5` real ausgefuehrt:
Incident-Record `retained-repair-d0f81a326da34741b3163db06f739dcb` FINAL,
Cleanup/Restart bestaetigt, RAW-Paar gesichert, WORK und Original mit
Integrity/Safety sowie identischem logischem/physischem Digest verifiziert.
Der gehaltene alte Controller 18720 endete; Legacy-Mutexe wurden unter
ACTIVE uebernommen. Wartungszustand anschliessend IDLE. Frische passive
Safety: Gate OFF, Schema 2, Positionen/unsichere Events/Receipts jeweils 0.
Nach separatem canonical START lief Supervisor 4164 mit fuenf Workern,
0 Restarts, PASSIVE_NO_TRANSPORT, Broadcast/live_ready false. Read-Monitor
VERIFIED und VERIFIED_NATIVE_ONLY, Position Manager PASS mit 0 Positionen.
Dies belegt Ledger-Recovery und passiven Wiederanlauf; die Main-DB-Messung
und die gesamte ARMED_NO_BROADCAST-Abnahme stehen weiterhin aus.

Der erste echte quiesced Main-DB-Audit (Nonce
`3858fefe059248aca1a191b9fe3f6098`) endete FINAL mit bestaetigtem Cleanup
und Restart-Erlaubnis. Main/WAL-Metadaten unveraendert, kein Fatalfehler.
Fuenf Abfragegruppen bestanden in zusammen 0,405 s; die globale Allocation
erreichte ihr individuelles Limit nach 144,0 s (0 Bytes ausgegeben).
Gesamtdauer 144,437 s. Daher **keine vollstaendige Allocation-Messung**.
Metadata: 9.879.172 Seiten a 4.096 Bytes, Freelist 0. Die letzten 10.000
Snapshoteintraege zeigen 449 verschiedene Vergleichspayloads ohne
captured_at; dies ist kein globaler oder byteverlustfreier Sparnachweis.
Nach frischer Safety/IDLE/Abwesenheit folgte separat canonical START;
Supervisor 15968, fuenf Worker ohne Restarts, passive Flags false,
Read-Monitor VERIFIED. Keine Main-DB-DML/DDL/Checkpoint/Vacuum/Loeschung.

Astra High OK fuer eine begrenzte Budgetanpassung: Maximum 900 Sekunden,
Default 180, Allocation 865, kooperatives Parent-Wartebudget 902 Sekunden.
Native Cleanup kann zusaetzliche Zeit beanspruchen; unbekanntes Cleanup
haelt den Schutz unbegrenzt. Budgettyp/Endlichkeit/Grenzen werden vor
Child-Erzeugung und im Finalreport geprueft; der Controller vergleicht
das gemeldete mit seinem angeforderten Budget. Eine kuerzere gemeinsame
Guard-Deadline bleibt maßgeblich. Codeabnahme/Tests vor erneutem STOP.

**82 gezielte Tests PASS in 34,91 s**, danach **904 Gesamttests PASS,
0 FAIL in 197,77 s**, Exitcode 0. Astra High Nachreview **OK fuer diesen
begrenzten Budget-Diff und beschriebenen Audit nach Gesamtsuite-PASS**.
Der erneute produktive Leseaudit folgt auf diesem geprueften Stand;
865 Sekunden sind kein Nachweis fuer eine ausreichende Scanlaufzeit.

Auch der zweite quiesced Global-Audit (Nonce
`95fae1f194754a1e86e078a3a70c51bf`) erreichte das individuelle Allocation-
Limit: 864,0 s, keine erfassten Allocation-Bytes; insgesamt 864,437 s,
fuenf Abfragegruppen vollstaendig, Main/WAL-Metadaten unveraendert,
FINAL/Cleanup/Restart bestaetigt. Nach erneuter Safety/IDLE/Abwesenheit
folgte separat canonical START, Supervisor 10912 passiv. Ein voruebergehender
RPC-32016-Zustand blieb UNKNOWN; der Read-Monitor wurde spaeter ohne
Flag-/Risk-Lockerung wieder VERIFIED. Keine weitere Budgeterhoehung.

Astra CHANGE verlangte objektweise Ergebnisse und eine korrigierte
Seitenbilanz. SQLite DBSTAT zaehlt Btree-/Overflow-Seiten, aber weder
Freelist-, Pointermap- noch Lockseiten; die bisherige Summengleichung war
fuer grosse/Auto-vacuum-Dateien unvollstaendig. Referenzen:
https://www.sqlite.org/dbstat.html und https://www.sqlite.org/fileformat.html.
Der neue Reportvertrag v2 misst jedes Inventarobjekt mit exaktem
SQL-Name-Literal getrennt, sichert vollstaendige Mitglieder sofort und
laesst Timeout-/Budgetobjekte explizit ungemessen. 120 Sekunden je Objekt
innerhalb derselben 900-Sekunden-Deadline; die Reihenfolge ist eine
Heuristik, kein Groessenbeweis. Auch Teilreports werden voll validiert.

Reservebyte und Header-Metadaten stammen per SELECT aus sqlite_dbpage im
selben schemafixierten Pager-/WAL-Snapshot, nicht aus einem angenommenen
Main-Header. Astra OK fuer diese Metadatenloesung; Standard-Readonly-CLI,
query_only, keine unsafe-testing-Option und keine DBPAGE-Writes.
https://www.sqlite.org/dbpage.html und
https://raw.githubusercontent.com/sqlite/sqlite/master/src/dbpage.c.
Lock-/Pointermap-/Freelistbytes sind getrennt bilanziert; Restseiten sind
ungemessen, niemals eine erfundene Tabellengroesse. Reportlimit 128 KiB.
59 gezielte Probe-Tests bestanden vor ergaenztem nativen SQL-Namensfall;
Integration/Gesamtsuite/Nachreview stehen vor dem naechsten Original-Audit.

Nach dem ergaenzten nativen Namensfall: **115 gezielte Tests PASS in
78,20 s**, **937 Gesamttests PASS, 0 FAIL in 247,94 s**, Exitcode 0.
Astra High Nachreview **OK fuer den exakt vier Dateien umfassenden
Mess-Diff und den beschriebenen Audit nach Full-PASS/frischen Preconditions**.
Kein aeusserer Abbruchtimeout fuer den Schutzcontroller. Der objektweise
Original-Audit folgt auf diesem geprueften Stand; PARTIAL bleibt PARTIAL.

Der parallel gepruefte verlustfreie Dictionary-Entwurf erhielt Astra CHANGE:
Store-INDEXED-BY-Pfad und weitere Reader benoetigen schemaabhaengige
Kompatibilitaet, referenzierte Dictionaries Append-only-Schutz, Source-
Freeze muss Kopie und Vollvergleich umfassen, versteckte rowid etwa von
model_registry muessen erhalten bleiben. Keine Originalmigration/Replace
und keine Growth-/Retention-Loesung wurden daraus als fertig abgeleitet.

`storage_codec.py` ist ein isolierter, noch ungenutzter Helper fuer diesen
Entwurf: vollstaendige eindeutige JSON-Validierung vor lexikalischem
captured_at-Split, Originalstring-/UTF-8-Identitaet, vollstaendiger Fallback
bei Unsicherheit, typ-/laengen-/zeilengebundene Fingerprints einschliesslich
bereitgestellter hidden rowids. Kein DB-/Netzwerk-/Prozesszugriff. Digest-
Treffer sind allein kein Gleichheitsbeweis; Originalteile muessen spaeter
verifiziert werden. **19 isolierte Tests PASS in 0,09 s; Astra High OK nur
fuer den Codec-Scope**. Schema, Reader-Integration, Migration und Retention
sind damit nicht implementiert oder abgenommen.

Objektweiser Original-Audit `36c9e6c0cadd4ca48d03612a8c546082`:
FINAL nach 704,735 s, Cleanup/Restart erlaubt, Main/WAL unveraendert.
18.100.166.656 B Btree-Seiten sind objektweise vollstaendig gemessen;
22.520.827.904 B bleiben ungemessen. Decisions und Snapshots erreichten
ihr individuelles Zeitlimit; die Gesamtmessung bleibt ausdruecklich PARTIAL.
Logical 40.620.998.656 B, Freelist/Pointermap jeweils 0 B, Lockpage 4096 B.
Nach frischer IDLE-/Gate-OFF-/Ledger-0-/Runtime-Abwesenheitspruefung und
nachgewiesenem Controller-Ende separat canonical START: Supervisor 18596,
fuenf Worker mit 0 Restarts, PASSIVE_NO_TRANSPORT, Broadcast false,
live_ready false. Kein ARMED_NO_BROADCAST oder Live-Readiness behauptet.

Dictionary-Foundation und Store-/Reader-Anschluss bleiben Fixture-Stufe;
keine Originalmigration oder Dateiersetzung. Astra CHANGE reproduzierte
Legacy-Indexnamen-Fehler und REPLACE-Umgehung der Append-only-Trigger.
Exklusive Formatobjekte und INSERT-Konflikttrigger beheben diese Befunde,
einschliesslich PK-/UNIQUE-Konflikten bei recursive_triggers/FK OFF.
38 Foundation-Tests PASS. Formatpruefung vor Store-DDL, voller verbleibender
Store-Schemavertrag, drei Write-Delegationen mit bestehenden Commit-Grenzen,
validierter privater INDEXED-BY-Pfad und gemeinsame Reader-Inventur.
88 gezielte Tests PASS in 2,09 s: reale Legacy-/Compact-Store-Aufrufe,
Rohdaten/IDs/Reihenfolge/Limits, commit=False, WATCH nach Commit, historische
und Training-WAL-Reads, Brain-Quellinventur und unveraenderte Domain-Sperren.
Der WAL-Reader-Test haelt wie die bestehenden Provenienztests einen idle
Writer offen; kein Beweis fuer writerlose WAL-Dateineuanlage daraus.
Full-PASS des abschliessenden unveraenderten Diffs/Nachreview folgen.

Abschluss Foundation/Integration: **999 Gesamttests PASS, 0 FAIL in
244,88 s**, Astra High scoped **OK** nach Behebung des zusaetzlichen
Brain-Domain-Befunds. Ergaenzte Empfehlung umgesetzt: Brain-Fixture umfasst
140 Sekunden und produziert vor dem Simulationsmarker nichtleere Records;
danach weiterhin leer und als SIMULATION ausgeschlossen, 5 Integrationstests
erneut PASS. Das ist keine Abnahme der noch folgenden Kopie/Umstellung.
Ein kurzer read-only Schema-Abgleich des laufenden Originals bestaetigt den
vollstaendigen Legacy-Vertrag: exakt 11 logische Tabellen, keine Abweichung.
Der passive Original-Store nutzt weiterhin den Legacy-Schreibpfad.

Separater Copy-Builder `storage_copy.py`: ausschliesslich neue WORK-Datei,
exakter Legacy-Schemavertrag, source query_only/Snapshot, alle 11 logischen
Tabellen einschliesslich hidden rowids, Originaltypen und Rohstrings/BLOBs.
Getrennte vollstaendige Source-/Destination-Fingerprints mit Counts/Bounds,
Source-/Target-Integrity, Target-FKs und semantische Orphan-/Cookie-Family-
Counts; bestehende Orphans bleiben erhalten und werden gemeldet. Keine
Store-Konstruktor-Aufrufe, stillen Reparaturen, RAW-Sicherung, Freeze oder
Dateiersetzung in dieser Bibliothek; Publication ausdruecklich gesperrt.
FAILED WORK wird erhalten. Astra CHANGE zur LIKE-Unterstrich-Inventurluecke
durch NOT GLOB sqlite_* plus befuelltem sqliteX_evidence-Fall behoben.
Weitere Deadline-CHANGE-Punkte behoben: SOURCE-Progress-Handler und
Busy-Timeout vor erster Quellabfrage, Cleanup auch vor Zielanlage,
abschliessendes check nach Callback. 22 Copy-Faelle/65 gezielte Tests PASS;
**1021 Gesamttests PASS, 0 FAIL in 249,20 s; Astra scoped OK**.
Die Quelle wird durch den Builder nicht beschreibbar geoeffnet; physische
Unveraendertheit/RAW-Backup/Writer-Freeze beduerfen weiterhin des Controllers.

Noch unintegrierter Copy-Worker: drei strikte nonce/PID-ACKs vor RAW,
nach RAW und vor Freigabe der verifizierten WORK-Datei. Parent muss native
Job-Mitgliedschaft/Guards und Abschlussvertrag selbst nachweisen.
11 Worker-Primitive-Faelle bestanden, zusammen 33 Copy-/Worker-Tests in
7,58 s, einschliesslich realer Read-Guards fuer WAL vorhanden/fehlend.
Bei fehlendem Input-WAL entsteht ausschliesslich im RAW-Verzeichnis eine
leere, vor SQLite geschuetzte Hilfsdatei; Source-WAL bleibt fehlend.
Diese Tests starten weder Produktionscontroller noch IMC-Runtime und
liefern keine operative Copy-/Replace-Freigabe. Worker-Nachreview laeuft.

Copy-Controller-Anschluss im bestehenden Offline-Controller: explizit
`-Operation Copy` mit exakt 13 Selbstargumenten/7200 s gemeinsamer Deadline;
Default Audit bleibt 11 Argumente/900 s. Gleiche globale Wartungssperre,
ACTIVEmarker, Native-Lifetime-/Stop-Kette, Ledger-Recovery und Sourceguards.
Parent beobachtet/haelt echten Native Job-Member vor ACK, uebernimmt RAW
und WORK jeweils vor getrenntem ACK innerhalb der Worker-Guards und
bestaetigt Native-Ende. Zweiter beobachteter Read-only Job liest unabhaengig
Source-/RAW-/Target-Hashes und alle Zeilenbeweise/Rowids/Referenzen erneut.
Kein Source Replace. Missing Proof fuehrt zur bestehenden RETAINED-Kette.

Windows-Fixturebefunde: ACK sichtbar vor Streamclose und voruebergehende
Report-Rename-Sperre. ACKs daher temp/Flush/Close/atomischer Move;
Reportreader erlaubt READWRITE/DELETE. Private Copy-Reportgenerationen
werden ausschliesslich bei Win32 5/32, maximal 1 s/Restdeadline und nur
nach Bytegleichheit der bereits fsync-gesicherten TEMP-Datei erneut
veroeffentlicht. Kein DB-Copy-/Order-Send-Retry. Fehlerausgaben beschraenken
sich auf bekannte Klassen/Quellstellen/Win32-Code, keine Exceptiontexte.

Astra CHANGE zum abschliessenden PowerShell-Typcoercion-Vertrag behoben:
exakte case-sensitive Header-/Ergebnisfelder, Schema/PID echte Integer,
Erfolgsflags echte Booleans, kein Code-/Missing-Feld. 18 gezielte
Vertragsfaelle plus 2 echte native Parent-/Job-/Handoff-/Verifier-Fixtures
PASS, Source/WAL mit und ohne WAL byteidentisch. 93 gezielte Tests PASS
in 44,92 s. Astra scoped **OK** fuer Controller-/Worker-/Contract-Diff;
keine operative Originalfreigabe. Erste Suite **1061 PASS, 0 FAIL in
270,38 s** ohne die zuletzt hinzugefuegten 18 Vertragsfaelle; abschliessende
Suite auf unveraendertem Code laeuft. Original weiter Supervisor 18596,
Native Birth 2026-10-03T03:29:11.3000225Z, SIGNAL_ONLY, Broadcast false,
Read VERIFIED/Equity VERIFIED_NATIVE_ONLY. Keine Originalkopie/Umstellung.

Abschliessender unveraenderter Copy-Integrationsstand: **1079 Gesamttests
PASS, 0 FAIL in 267,50 s**, einschliesslich aller 18 neuen strikten
Vertragsfaelle. Astra High scoped OK vor dieser Gesamtsuite beruecksichtigt.
Der operative Copy-Aufruf bedarf jetzt eigener RiskReview/frischer
Native-Birth-/Runtime-/Gate-/Ledger-/IDLE-Preconditions. Er ersetzt die
Originaldatei ausdruecklich nicht; anschliessender Replace bleibt getrennt.

Operativer Copy-Versuch 2026-10-03, Nonce
14fce188ea2946deadd61bfe660a6cbf: RAW bytegesichert und alle Tabellen in
WORK kopiert, aber die 7200-s-Frist endete in der Target-Zeilenpruefung.
COPY_PROOF_FAILED/TimeoutError; keine Copy-Abnahme/Originalersetzung.
Original 40.876.032.000 B, RAW-WAL 441.614.592 B und ungepruefte WORK
21.015.281.664 B bleiben erhalten. Runtime kontrolliert offline; Controller
10528 haelt weiterhin ACTIVE, sechs Read-Guards und drei globale Mutexes.
Gate OFF. Die veraltete Runtime-Statusdatei ist kein Running-Nachweis.

Nach Astra scoped OK nur den leeren OriginalJob ueber sein vorhandenes
Objekt bereinigt: Native Primary-Handle signalisiert, Exit 2, Active 0.
Geladene und frisch aus kanonischem Quellcode kompilierte Jobimplementierung
stimmen inklusive Layout/Marshalling/IL/qualifizierter Member ueberein.
Negative Fixtures unterscheiden Pack, MarshalAs und deklarierenden Typ.
NtQueryMutant bestaetigt bei drei Mutexes OwnerPID 10528 und
OwnerThread=CallerThread. CleanupConfirmed true; fsync-INTENT/DONE unter
.local-tools/retained-job-d4813a1dfd6147e38e3d39df5aa612ff. Nachpruefung
bestaetigt gehaltene Guards/Mutexes. Kein Restart-/Copy-Erfolgsnachweis.

Noch unintegrierte Publication-Bausteine: relatives NT-Rename mit gehaltenen
Datei-/Verzeichnisidentitaeten und NTFS-Sentinels, kein Overwrite; unklarer
Nativeabschluss haelt Buffer/IOSB/Handles und blockiert Close/Retry.
Geordnete fsync-INTENT/DONE-Hashkette mit unabhaengigem Projektroot und
typed generationgebundenen Pfad-/Identitaetsbeweisen. 68 isolierte Tests
PASS, darunter echter Workerabbruch mit ueberlappendem Parentguard.
Astra begrenztes Komponentenreview ohne weiteren Blocker; keine operative
Original-Publication-Freigabe.

Schema-Subset-Luecke durch exakte Final-Gleichheit geschlossen. Separate
Offlinepruefung kontrolliert Dictionary-Digests/Typen, unbenutzte Eintraege,
private/public Kardinalitaet und Referenzen. Builder und unabhaengiger
Verifier pruefen selbst. 175 gezielte Tests PASS in 9,94 s; Astra scoped OK.
Zusaetzliche Verifier-/SQL-Deadline-Regressionsfaelle und voller Vergleich
der alten ACTIVE-Generation sind inzwischen implementiert; gezielter Lauf
laeuft. Kein neuer Gesamtsuiten-/ARMED-/Quality-Gates-PASS.
Naechster Schritt: Guard-Uebergabe mit Generationvergleich und vollstaendige
RO-Abnahme der vorhandenen WORK. Kein blindes Copy-Resume.

Recovery-Handoff 2026-10-03: Nach Astra scoped OK (90793) und 268 gezielten
PASS / vier wegen gehaltenen globalen Produktionsmutexes ausgesetzten Tests
wurde der absolute kanonische CopyTimeout-Aufruf ausgefuehrt. Neue Nonce
43979952e12c43c9966ff97698abeb7b, Controller 13292, native Birth
2026-10-03T07:13:23.2169372Z. Original-/RAW-Hashes stimmen mit dem alten
Copy-Versuch ueberein; WORK-SHA256
c6b26c7baff46603e73352313a90d10dc610dc93555eea941a38609db99640a7.
Bewachter Native-Abschluss von 10528, ueberlappende Datei-/Mutexguards und
vollstaendiger ACTIVE-Generationsvergleich sind im frischen fsync-Handoff
belegt. ACTIVE jetzt 13292; Gate OFF, keine Position/Uncertain/Receipt.
Die neue RO-Abnahme laeuft bei SOURCE_INTEGRITY. Noch kein Copy-PASS,
keine Originalpublikation und kein Runtime-Neustart. Erfolgszustand dieses
Operators waere nur VERIFIED_HELD, nicht IDLE oder ARMED.

Isolierter Publication-Move-Baustein: feste vier INTENT/fsync/Rename/native
Nachweis/DONE/fsync-Schritte; weder VERIFY_INSTALLED noch RELEASE im Worker.
Nach Astra CHANGE werden WORK-Sidecars, Source-Journal und wiedererschienene
archivierte Sourcepfade in jedem kritischen Zeitfenster geprueft. 150 native
Komponenten-/Journal-/Guardtests PASS in 40,11 s mit und ohne erhaltenes
WAL/SHM-Paar. Astra bestaetigt den scoped Sidecar-Fix; Parentintegration bleibt
offen: gemeinsame Sentinel-Native-ID, expliziter Attach statt Exists-Fallback
und kompatibles READWRITE/noDELETE-Sharing fuer Leaf-Verzeichnisse. Noch
keine Produktionspublikation, kein ARMED oder globales finales Advisor-PASS.

Publication-Komponenten integriert in 2d63347: 162 gezielte Tests PASS in
23,11 s, Astra scoped OK fuer Attach-/Parent-/Move-/Journal-Bausteine.
Parentdateien haben READ-Zugriff/shareREADDELETE; Worker hat gesonderte
DELETE-Handles. Expliziter Sentinel-Attach prueft die vollstaendige native
Generation; Default bleibt CREATE_NEW. Starke Rueckuebernahme prueft echte
Handles/Pfade/IDs, schwache Dateihandles werden erst danach geschlossen,
Verzeichnis-/Sentinel-Pins bleiben gehalten. Kein PS-Publish integriert.

Breite Zwischenregression: 1290 PASS, 3 FAIL, 4 wegen globaler Mutexes
deselected, 276,06 s. Zwei weitere Tests verwenden globale Produktionsmutexes
und scheitern derzeit erwartbar an IMC_MAINTENANCE_BUSY. Dritter Befund:
initialer Copy-Verifier im ReportProof-Fehlerfixture meldete einmal
COPY_VALIDATION_FAILED, bevor die absichtliche Fault-Injektion aktiv war.
Einzelrepeat PASS und Read-only-Replay des erhaltenen Temp-Reports PASS
klaeren dessen Ursache nicht. Keine volle gruene Suite behauptet. Diagnose
von ACK-Lese-/Report-Fehlern und Produktionsfreigabe sind weiter offen.

Cold-ACK-/Diagnoseutilities in b05d451 integriert, bewusst noch nicht in
laufende Worker verdrahtet. Erzwungene native Sharesperre: CRT-Reader verliert
Win32-Code (errno 13/winerror null), native READ-Operation liefert 32. Dies
beweist nicht die alte Flake-Ursache. Native Reader nutzt gepruefte READ-only
Handleprimitiven ohne Directory-/Sentinelwrites, retryt ausschliesslich native
Open/Read 5/32 mit einmaliger kooperativer Ergebnisgrenze und priorisiert
unbestaetigtes Close vor jedem Retry. Diagnose gibt keine Messages/Locals aus.
Astra scoped OK; 79 gezielte Tests PASS einschliesslich 28 neuer Faelle.

PS-ParentGuard-Library: kein Controller/CLI/Gate-/Restartzugriff. Vollstaendige
DriveRoot-Zugangskette, x64-ABI vor Open, zuerst Directory-shareREAD, dann
frische 0B-Sentinels, ueberlappender Leaf-shareREADWRITE/noDELETE-Upgrade.
READ-only-Dateihandles teilen READDELETE, Strong-Handoff prueft alle bekannten
Zielketten vor erstem Strong-Open und alle Native-IDs vor erstem Weak-Close.
Jeder oeffentliche Prueffehler verriegelt die Gruppe; unklarer Close bleibt
retained und verhindert erneute Native-Verwendung. Full128-JSON ist unsigned.
Astra scoped OK nach CHANGE-Fixes. 20 native PS-Fixtures plus andere
Publication-/ACK-Komponenten: 210 PASS in 35,80 s. Darunter echte Junctions
oberhalb/am/unter CallerRoot, erfolgreicher ungeschuetzter Reparse-Kontrollfall
und Sentinel-Nichtleerfehler 145, partielle Strong-Uebernahme, Native-Close
mit nachfolgender injizierter Exception, konkurrierendes Hash/Close.
PS5-Propertysyntax verschluckt Getterexceptions: Fixture verwendet echte
Getterinvokation; spaeterer Controller muss Getterabfragen explizit mit
AssertAll/Faultstatus pruefen. Kein pauschales Finally-Close bei Publishfehler.

Produktions-Recovery: erfolgreiche RAW-Integritaet, physische Dictionaries,
alle elf logischen Source-/Target-Zeilenbeweise und Referenzen. Aktuell
TARGET_INTEGRITY; noch keine FINAL-Abnahme/Originalpublikation. Gate OFF,
ACTIVE/13292 bleibt gehalten, Runtime offline.

Parent-Rebind in b7caeed integriert: vollstaendige phasenspezifische
Zuordnung wird nativ vor jeder Pfadmutation geprueft, keine Handles geoeffnet
oder geschlossen. 27 native Tests und Astra scoped OK. Zusaetzliche reine
Scopegruppe haelt Journal-/IPC-Pins unabhaengig von SourceClose; kein Dummy-DB.
Cold supervisorlokales ACK-Exchange bindet opaque Receipts an exakt publizierte
Bytes und verhindert Replay, Bool/Integer-Verwechslung sowie Callbackreentranz.
Sichtbare ACK-Datei allein bedeutet keine Konsumberechtigung. Keine native
Crossprocess-Authority oder Produktionsintegration behauptet. Astra scoped OK;
254 gezielte Tests PASS in 47,11 s, danach drei verschaerfte Scopefaelle PASS.

Erste Produktionspruefung FINAL/complete/cleanup_confirmed fuer PID10992.
Controller13292 hat den separaten unabhaengigen Verifier PID16704 gestartet,
Native-ACK vorhanden. Dieser ist noch RUNNING, keine Originalpublikation.
Publication-Entscheidung nach Astra: ein langlebiger Parent-eigener
Journalwriter fuer alle zwoelf Events, getrennter Renameworker, getrennte
Source-/Journal-Scopegruppen. Writertransfer wird dadurch vermieden.

Cold nativer Crossprocess-Kanal: C# local-only Named Pipe mit FIRST_INSTANCE,
ein Peer, OVERLAPPED, keine vererbbaren Authorityhandles. Gegenpeer nativ ueber
gehaltenen Processhandle/PID/FILETIME gebunden; keine Reconnects. LE-UInt32-
Framing vor Allocation begrenzt. Pending/Timeout haelt Nativebuffer, OVERLAPPED,
Event und Handles, niemals Send-Retry. Close/Fault-Abschluss C# via CAS, Python
via gesonderter Zustandssperre. Pythonbuffer liegen in nativer Heapallocation
ohne GC-Finalizer. Astras Close-Race-/Deadline-/PeerExit-Befunde behoben.
30 native Kanalfaelle PASS, kombiniert 284 gezielte Tests PASS in 100,13 s;
Astra scoped OK. Keine Produktions-IPC-/Mutexauthority oder Publication
behauptet; Parentthread-/Mutex-/Markerintegration ist der naechste Schritt.

Gemeinsame absolute Deadline umgesetzt: Native-QPC-Endtick plus unabhaengig
gepruefte OS-Frequenz, kein Reset beim Worker-/Kanalstart. Ein Tick konservative
Expiry-Grenze; erste Messung bleibt Baseline, jeder Ruecksprung faultet.
Pruefungen direkt vor Server-/Client-Native-Open und vor weiteren I/O-/Cleanup-
Schritten. Astra scoped OK nach Initialisierungsfix. 17 Deadlinefaelle PASS,
einschliesslich deterministischer Regression/Expiry nach Client-Bind und vor
Native-Open; echte PS-/PS- und PS-/Python-Endtickpaare. Gesamtsuite gezielt:
305 PASS in 116,70 s. Produktions-Authority zwingt diese absolute Deadline.

Cold ParentAuthority: 13 private native Fixtures PASS in 20,49 s; Astra scoped
OK nach zwei Close-Race-Korrekturen. Gehaltenes Parent-Threadhandle und native
PID/FILETIME, reine Mutex-Owner-Abfragen, kompletter aktueller ACTIVE-Marker
mit ID/Hash/Bytes. Gemeinsame Fault-/Abschluss-Synchronisation; kein Retry nach
unklarem Close. FinalClose-Retry-Ergaenzung einzeln PASS in 1,56 s. Kombinierte
Publication-Regression 289 PASS in 147,98 s (nicht die volle Projektsuite).
Keine Source-/Gate-/Job-Authority oder kontinuierliche Ownership behauptet.

Produktionsverifier16704 FINAL complete=true, alle elf logischen Tabellen,
Source unveraendert, RAW/Target verifiziert, null semantische Waisen. Beide
nativen Verifier-Jobs wurden durch den laufenden Controller bereinigt. Danach
scheiterte seine erneute Abwesenheitspruefung: originaler ErrorRecord
REPAIR_OTHER_RUNTIME_PRESENT, Fresh-Absence Zeile41. Native Tests liefen zu
diesem Zeitpunkt parallel; die konkrete PID-Ursache wurde nicht aufgezeichnet.
Controller13292 bleibt RETAINED, niemals als VERIFIED_HELD umgedeutet. Read-only
im originalen Runspace: zwoelf lesbare offene Streams, drei native Mutexes
owned13292/TID10464, aktueller Job CleanupConfirmed=true, CopyResult vollstaendig.
Gate OFF, Publication und Restart weiter gesperrt. Astra verlangt eine neue
Zertifizierung mit gebundener Fehler-/Funktions-/Lebensdauer-/Native-Evidenz.

Single-Writer-Endpunkt integriert (e4e9fd5), Astra scoped OK nach Snapshotfix:
Generation/Binding vor jedem Callback defensiv kopiert. Schema2-Header mit
Vertrag in der Hashkette; RELEASE-DONE nur source_guard_cleanup. Schema1 bleibt
kompatibel. IPC-Sequenz1..12 getrennt von Journal-Sequenz0..11, native FILETIME-
und Rollenbindung, Replay-/Reentranz-/Vorgaengerhash-Pruefungen, permanente
Fehlersperre. 149 Manifest-/Writer-/Move-Tests PASS in 40,50 s. Noch kein
Produktionshost und kein Source-Publish.

ACK-Reader angeschlossen: nativer READ-only Zugriff mit begrenzten 5/32-Retries
innerhalb urspruenglicher gemeinsamer Deadline. CRT-errno13/noWinerror separat
nachgewiesen; native Sharing32 und Wiederaufnahme des echten Worker-ACKs
getestet. Contract-Fehlerdiagnose nennt nur whitelisted Stage/Class/Fileline;
initiale, Fortschritts- und finale Publikationsfehler unterscheidbar, keine
Exception-Texte. Erfolgsschema unveraendert. Astra scoped OK. Betroffene vier
Module 123 PASS in 40,53 s; Recovery-Gruppe 232 PASS, vier globale Mutexfaelle
bewusst wegen des real gehaltenen Controllers ausgenommen, in 48,68 s.
Recovery-Gruppe integriert als 795c784. Historische Flake-Ursache unbewiesen;
volle Projektsuite noch nicht abgenommen.

Native vorbereitete Writer-Scope integriert (5d603b5), Astra scoped OK:
PowerShell-Verzeichnisinventar exakt {volume,file_id}; getrennt von Leaf-
Sentinels {volume,file_id,bytes}. Python pinnt bei diesem ausdruecklichen
Vertrag bis zur Laufwerkswurzel und teilt das echte absolute Deadline-Objekt
mit Client/Authority. Schema2-Prepared-Verzeichnis akzeptiert ausschliesslich
den identischen regularen, einfach verlinkten Nullbyte-Sentinel. Factory prueft
wirklichen Parent/Worker-PID/FILETIME, ACTIVE und gehaltene Namespace-Pins vor
und nach Header-/Event-I/O. Sieben native Inventarfaelle PASS in 7,03 s;
nativer PS/Python-Writer mit echter Job-/Pipe-/Mutex-/Thread-/Markerbindung
und zwoelf fsync-Completions PASS. Drei Factory-Negativfaelle fuer anderes
Deadline-Objekt, Worker-Birth und geschlossene Scopes ebenfalls bestanden;
Writer-Fixture zuletzt 1,75 s. Kompatibilitaetsregression 155 PASS in 48,50 s.
Die Source-/Trade-Fakten dieser Fixtures sind synthetisch. Keine Aussage ueber
ProductionCLI, echte Source-Publikation oder GlobalPASS.

Getrennter kalter Rename-Endpunkt integriert (e16b8df), Astra scoped OK nach
Native-Submission- und ACK-Race-Fixes. Derselbe nativer Client/Role/Nonce wie
ParentAuthority; wirkliche Root-/Worker-PID/FILETIME, ACTIVE und native Source-
Namespace-/File-ID-/SHA-Bindungen. Legacy-MovePhase verwendet dieselben Checks,
behaelt ihre Journal-Semantik. Neuer Worker schreibt kein Journal. Root bleibt
fuer INTENT/DONE, Gate/Ledger/Jobs und unabhaengige Abwesenheiten verantwortlich.
Fault-Registrierung, eigentlicher Nt-Rename und ACK-Freigabe sind geordnet;
schon freigegebene Operationen koennen ausgefuehrt/zugestellt sein. Kein Retry.
Native Fehlerfixtures: Parallelitaet vor Rename (0 Aufrufe), vor ACK (1 Rename,
0 ACKs), Reentranz, Empfangsfehler, verlorene Antwort nach Move, ausgefuehrter
Move mit unbekanntem Native-Ergebnis, WAL/SHM-No-ops. Native Source-FD-/Parent-
Pins bleiben bei Fehlern gehalten. Wiederholungsversuche nach beiden Concurrent-
Fehlern lehnen ohne neue Rename-/ACK-Aufrufe ab: 2 PASS in 3,38 s. Breiter
betroffene Regression 180 PASS in 104,28 s. Volle Projektsuite aktuell mit sechs
explizit ausgenommenen globalen Mutex-/Recoveryfaellen gestartet; noch kein
abschliessendes Ergebnis. ProductionSource und Restart weiterhin gesperrt.

Interimsuite nach e16b8df: 1506 PASS, 0 FAIL, sechs explizit ausgenommene
globale Mutex-/Recoveryfaelle, 383,77 s. Diese sechs werden erst nach sicherem
Release des real gehaltenen Controllers erneut ausgefuehrt. Keine vollstaendige
Abnahme oder Production-Publikation behauptet. Eigener haengender Read-only-
Inspektionsclient17180 (Parent15968, native Birth10:00:53UTC) identifiziert und
beendet; Controller13292 und seine Schutzhandles bleiben unangetastet.

Maintenance-Abschluss integriert (8e6269f): idle API/CLI zwingt den vollstaendigen
validierten ACTIVE-Snapshot und originalen Publisher-SHA; Snapshot READ/noWrite-
Delete, nativer Close vor IDLE, Fehlersperre und kein Retry bei Readback-Unknown.
Astra scoped OK nach Repoint-Korrektur. 54 gezielte Tests PASS/4 globale
ausgenommen in 17,54 s; breitere Recovery-Auswahl 101 PASS/6 ausgenommen in
39,68 s. Keine Produktions-Marker-Aenderung.

Neue Read-only-Rezertifizierung integriert (8b4cf2c): zwei getrennt registrierte
native Jobs, Membership/Exit0/Cleanup, FINAL-Hash und Parse ueber denselben
gehaltenen Stream. Gemeinsame Uhr mit Arbeitsfrist und Cleanup-Reserve; kurzer
nativer ACK-Check. Durable DONE ist ausschliesslich CERTIFICATION_EVIDENCE,
complete=false/authority_grant=false; Abschluss erst im lebenden Ownerzustand.
Native Matrix inklusive verlorenem Report/ACK/Observe, Authorityverlust, Budget,
unbestaetigtem Cleanup und Fehler nach Evidenzwrite: 43 PASS in 163,60 s.
Astra scoped OK nach Zeit-/Binder-Fixes, keine Publikationsfreigabe.

Read-only Ownerinspektion04/05 erzeugt: OriginalError Fresh-Absence41, sechs
geladene Funktionshashes quellenidentisch, drei Mutexes owned13292/TID10464,
zwoelf READ-Streams. Bei SourceMain/WAL, RAWMain/WAL und WORK sind native
Volume/File-ID/Bytes/Creation-/Writezeit gegen frische Pfadhandles und bisherige
Metadaten verlustfrei identisch (fuenf PASS). Historische Cleanup-Ableitung von
Astra akzeptiert; damalige FINAL-Dateien waren nicht dauerhaft gehalten, daher
zwei neue vollstaendige Workerpruefungen erforderlich.

Produktionsstarts e53f4b.../4c217... vor dem Proof-Worker abgebrochen: private
Skriptscope hatte script:IMCProcessRoot nicht gebunden; konkrete Join-Path-
ParameterBinding an IMC_PROCESS_COMMON.ps1:288. Begrenzter nativer Readprobe
FINAL/Gate OFF/ACTIVE13292/schema2zero bestaetigt, Nativecleanup erfolgreich.
Originalcontroller RETAINED und Originaldaten weiterhin unveroeffentlicht.
Scopebindung und dauerhafte Runspace-Retention werden vor einem neuen Start
mit Astra geklaert; keine Aussage VERIFIED_HELD fuer die Gesamtproduktion.

Scope-/Retentionfix integriert (dfc22cf): canonical ProcessRoot und IMCScripts
im privaten Wrapper gebunden; urspruengliche Funktionen geerbt, keine Ersetzung.
Neue State-/Job-/Guardregister im originalen globalen Runspace verankert, nur
Initialisierung bei Fehlen, Eintraege mit Owner/Nonce und ohne Reset. Tatsachliche
private '&'-Scopes mit nested Inspector; Referenz-/JSON-Invarianz und gezielte
Exceptionmatrix plus isolierte GC-Checks. 8 Certified-Faelle PASS in 19,06 s;
danach komplette Controllergruppe 43 PASS in 166,87 s. Astra scoped OK.
Frische Ownerinspektion06: alle zwoelf nativen IDs/Metadaten unveraendert zu05,
drei Mu13292/TID10464, sechs Funktionshashes source_match und aktuelles altes
JobCleanup true, 0,48 s. Originalphase weiterhin RETAINED.

Neuer Read-only-Zertifizierungslauf 09a3bcbbd156435589ab1e7995f3eae2 gestartet
im Originalowner13292: Primary9748, beobachteter nativer Producer2292, ACK
vorhanden. Neuer Scope retained-repair-09a3... mit INTENT; laufende
vollstaendige Original-/RAW-/WORK-Pruefung. Kein FINAL-/Gesamt-PASS und keine
Publikations-/Restartfreigabe behauptet. OwnerGate OFF und Source unveroeffentlicht.
