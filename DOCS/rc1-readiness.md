# IMC RC1 — aktuelle Prüfung und historische Ausgangslage

## Aktueller Fortsetzungsstand vom 03.10.2026

Die folgenden alten Sandbox-/Git-Blocker sind durch die Fortsetzung überholt.
Ausgangs-HEAD dieser Sitzung: `de7f999`; native Git-Integration ist durch
`e7f0a9d` belegt. Es gab keinen neuen Klon, Reset, ACL-Eingriff oder
Git-Schreibbroker. Der vorbestehende Working Tree bleibt erhalten.

**Gesamtstatus bleibt NOT_READY / SIGNAL_ONLY. OWNER_BROADCAST_GATE = OFF.**
Das Gate allein aktiviert im aktuellen Stand noch keinen echten Adapter.
Es ist eine gesicherte Owner-Sperre mit Fail-closed-Leser; eine Live-Abnahme
oder abgeschlossene Qualitätsevidenz wird daraus nicht abgeleitet.

Die zwei historischen BUY/SELL-Kalibrierungen sind jetzt unabhaengig durch
vier Read-only-`getTransaction`-Abfragen mit `finalized` bestaetigt:
`meta.err=null`, jeweils genau ein Signer, BUY-/SELL-Tokenmengen identisch
und gegenlaeufig. Quelle der Signaturen ist
`DOCS/00_MILESTONE/IMC_MILESTONE_V480_DOUBLE_VALIDATED.json`; der Bericht nennt
`JUPITER_V2_BUILD_FAST`. Die Slots sind 450094319/450096018 und
450103369/450103804. Token: `FTvGCyZnAEVT81NwENZ1Eg3vTHfh82Bh2JdGjxgpump`;
Roundtrip-Mengen: 141892302 und 5235083041 Raw Units.

Alle vier Transaktionen wurden von
`58Wqgfw5EcV8pkLXDJ2ipvcKkMyEeTEpPB3RiEtFjPdj` signiert, nicht von der heute
per GMGN/Wallet-Read gebundenen Adresse
`7V3ZeVgkhKnK8oLxDg8P1vKNDdZBuA6q2eGWQzMMV5UR`. Damit ist die gemeldete
historische Kalibrierung belegt, aber keine heutige GMGN-Orderberechtigung
oder GMGN-Sendepfad-Abnahme. Die historischen Trades werden nicht in den
neuen GMGN-Ledger oder dessen Qualitaetskohorte importiert. Native Deltas
allein werden wegen Rent-/Accounteffekten nicht als Trade-PnL ausgegeben.
Astra High hat vor dieser Quellenpruefung den Read-only-Projektionsplan
geprueft; unfiltrierte Legacy-ACKs, Transaktionsblobs und Keys wurden nicht
ausgegeben, Legacy-Skripte nicht importiert oder ausgefuehrt.

- Letzter vollständiger Lauf: **768 PASS, 0 FAIL** (114,81 Sekunden);
  `pip check` und `git diff --check` bestanden. Keine bestehenden
  Tests wurden gelöscht oder abgeschwächt.
- ReadMonitor: bestaetigte Inventar-Reconciliation und BUY-Risk-Erlaubnis sind
  getrennt. Session-Loss und DD-Kill bleiben persistent, blockieren BUYs und
  erzeugen Risk-Exits, entwerten aber allein kein korrektes Inventar. Eine
  bestehende Reconciliation-Sperre wird durch einen finanziellen Kill nicht
  geloescht. Der Monitor bindet die exakte Beobachtung an den neuesten,
  eindeutigen Audit samt Generation, Revision, Wallet, Cash, Positionen und
  Orders; Audit- und Statuspruefung verwenden dieselbe Transaktion. Der
  Vergleich ist typensensitiv, auch fuer Integer/Float/Boolean. Native Equity
  behaelt den urspruenglichen Read-Zeitpunkt; Reconciliation-Alter, Quote-Alter,
  monotone Originalfristen und Clock-Rollback werden bei positiven COMMITs
  erneut geprueft. Negative Equity behaelt ihren eigenen Sicherheits-COMMIT.
  Der eigene Heartbeat prueft Thread-Leben, Busy, Stop und Fristen, ohne aus
  einer Cache-Datei Erlaubnis abzuleiten. Stop wartet begrenzt auf den eigenen
  Thread und auch auf HTTP-Ticks; Timeout bleibt sichtbar, spaete Reads stellen
  PASS nicht wieder her. Server-Cleanup erfolgt auch bei Stop-Fehlern.
  134 gezielte Tests bestanden; Astra High: OK nach Typvergleich-Korrektur.
  Dies ist keine globale Live-Abnahme oder Sendetransport-Freigabe.
  Supervisor 15356 hat den integrierten Stand geladen: native Identitaet von
  Supervisor 18468 vor Stop geprueft, zuvor null Positionen, ungeklärte Orders
  und Receipts. Neuer Wallet-/Equity-Read VERIFIED_NATIVE_ONLY, alle fuenf
  Worker ohne Restart, Gate OFF. Nur die eigene Runtime wurde neu geladen.
- ML-Prozessidentitaet: `ml-auto-once` bleibt ohne Elternkontext UNKNOWN.
  Eigene Kontextpruefung bindet exakt kanonische ML-, CORE- und Supervisor-
  Argumente, Interpreter, PIDs, Geburtsreihenfolge und gehaltene native Handles.
  Ein fehlendes ML-BASE-Kind ist ein eigener Viererknotenfall; ein vorhandenes
  fehlerhaftes Kind wird niemals durch diesen Fall freigegeben. Fuenferketten
  und System-Consolen werden separat geprueft; erst die bestehende strenge
  Redirector-Pruefung darf einen VENV-Eintrag entfernen. Verwaiste oder
  mehrdeutige Prozesse bleiben UNKNOWN, auch nach Supervisor-Stop.
  Die echte kanonische VENV-ML/BASE-CORE/VENV-CORE/Supervisor-Elternkette wurde
  zuvor read-only mit nativen Lebenszeiten beobachtet (2228/7736/9196/18468).
  Das ist historische Prozessevidenz, kein aktueller Heartbeat. In einem
  anschliessenden 22-Sekunden-Fenster war kein ML-Kind aktiv; daraus wird kein
  produktiver Positivtest der neuen Kontextfunktion abgeleitet. 35 isolierte
  Prueffälle inklusive 9/10-Tick-Grenze, spätem Tod, Dispose, Zusatzkindern,
  Orphan und Stop bestanden. Astra High: OK fuer den statischen Gruppenreview.
- Position Manager: eigener Gateway-Thread und zuerst verarbeitete SELL-Intents,
  auch ohne Discovery-DB. Die bestehenden Exit-Parameter werden vor dem ersten
  SEND im Ledger samt Audit eingefroren. Stop/Profit/Trailing verwenden echte
  Wallet-Cash-Deltas und konservative executable Marks; Peak und Recovery-
  Verpflichtungen ueberleben einen Restart. Pause bleibt BUY-only; risk,
  session, emergency und recovery exits haben persistente, typisierte Quellen.
  Es gibt keinen neuen Sender und keine angenommenen Bestaende/Fills.
  Preisentscheidungen pruefen alle urspruenglichen Zeitbudgets nochmals vor
  jedem COMMIT; spaetere Positionspruefungen erneuern diese Budgets nicht.
  Negative Liquidation macht Equity UNKNOWN und setzt den DD-Guard in derselben
  Writer-Transaktion mit einmaligem begrenztem Sicherheits-COMMIT. Bei I/O- oder
  COMMIT-Ausfall sperrt die gemeinsame Ledger-Instanz sofort weitere BUYs und
  entwertet alte Send-Permits; das ist keine Persistenzgarantie bei I/O-Ausfall.
  Nur ein erfolgreich committeter gueltiger Mark kann diese RAM-Sperre loeschen,
  generationsgebunden; persistente Kill-Gruende werden dadurch nicht geloescht.
  63 gezielte Position-Manager-Tests bestanden; Astra High: OK fuer diese Gruppe.
  Der passive Supervisor 13184 hat den integrierten Stand geladen: alle fuenf
  Worker ohne Restart, Position Manager PASS (kein Bestand), Wallet/Equity-Read
  VERIFIED_NATIVE_ONLY. Owner-Gate OFF, kein Transport/Broadcast. Die vorherige
  native Supervisor-Identitaet wurde vor Stop geprueft; andere Broker blieben
  unberuehrt. Die generierte Positionsstatusdatei bleibt lokal und Git-ignoriert.
- GMGN-Quote-Read/IPC2: authentifizierte Ready/Request/Response-Version 2,
  ohne Legacy-Fallback. Ready bindet SDK 1.6.6 und Hashes des tatsaechlichen
  Wrappers, Parsers und SDK-Quellen. Der Client vergleicht die ganze Identitaet;
  der Host prueft gehaltene native Prozess-Handles und Inhalte vor jedem Read.
  Standard-Quotes verwenden Exist-Auth, keinen verpflichtenden Private Key.
  Die SDK-Konfiguration bleibt lokal und opak; der Config-Import kann vorhandene
  Credentials laden. Es wird kein privatschluesselfreier SDK-Prozess behauptet.
  Quote-GET ist einmalig, ohne Redirect, mit begrenztem Body und exakter Bindung
  an Tokenpaar, Raw-Betrag und Slippage (1 = 1 Prozent). Fehler sind typisiert
  an Request/Instance und HMAC gebunden; lokale Cooldowns enthalten keine neue
  Providerbeobachtung. Fehlende/mehrdeutige Reset-Fristen bleiben UNKNOWN.
  Eine empfangene HTTP-429 wird vor weiterem Body-Warten als provisorische
  UNKNOWN-Sperre gespeichert; nur eine vollstaendige Reset-Evidenz derselben
  Anfrage bestimmt eine endliche Frist. Hangs, Cancellation und Host-Deadline
  verlieren die Sperre nicht. Gepufferte Marker-Fragmente werden vollstaendig
  ausgewertet. 36 gezielte Tests bestanden; Astra High: OK nach Korrektur der
  Findings. Eigene passive Runtime fuer Migration kontrolliert quiesziert;
  der vorhandene Workbroker blieb unveraendert und wird von IPC2 abgelehnt.
  Eine einzelne echte Standard-Quote ueber einen frischen IPC2-Probe-Broker
  (nur oeffentliche gebundene Wallet, ohne zusaetzlichen Portfolio-Probe)
  ergab erneut Provider-HTTP/API 429 und 31 Sekunden Frist. Diese Evidenz kam
  aus der authentifizierten Request-Antwort; Quota-Hints waren UNKNOWN.
  Der Account-Tier oder die Ursache ist weiterhin nicht bewiesen. Kein Retry,
  kein Order-POST und keine Transaktionssignatur. Nach Ablauf der bekannten
  Frist wurde die passive Runtime mit Supervisor 18468 neu gestartet: Wallet-
  Read VERIFIED_NATIVE_ONLY, Position Manager PASS, alle fuenf Worker ohne
  Restart, Gate OFF. Es gibt weiterhin keinen produktiven Sendetransport.
- Die fuenf kanonischen START-/STOP-/STATUS-Skripte erkennen ihre eigenen
  `powershell -File`-Aufrufe, ausschliesslich bei exakt kanonischem absoluten
  Pfad und ohne Skriptargumente. Sie erscheinen weder als Runtime noch als
  Unknown. Beide STARTs wurden mit isolierten Supervisor-Fixtures geprueft;
  ein echter Standard-START erkannte den bereits laufenden Supervisor 12704
  ohne weiteren Dienststart. Der Interpreter-Check erfolgt nur beim Start.
  Unbestaetigte Python-Kindprozesse bleiben UNKNOWN; keine pauschale Ausnahme.
  Astra High: OK fuer diese begrenzte Gruppe. Die passive Runtime hat die
  zuvor integrierten ACK-/SELL-Guards geladen; Wallet-Read wieder VERIFIED.
- Zentraler Ledger: erlaubte Zustandsübergänge, persistente Request-IDs,
  Session-/Kapitalreservierungen und sichere SELL-Neuversuche ausschließlich
  nach nachweislicher Ablehnung vor SEND sind ergänzt und getestet.
- Equity: konservative Liquidationsbewertung, Current/Peak/Max-DD, echte
  Wallet-Cash-Deltas, revisionsgebundene Marks und persistenter Guard bei
  **DD > 15 %**. Die Marktmarks offener Positionen benötigen echte Quotes.
- Read-Broker: authentifizierte Portfolio-/Holdings-Reads funktionieren;
  der Host prüft die Prozessidentität. Provider-429/Business-Sperren werden
  getrennt klassifiziert und über Broker-Neustarts hinweg koordiniert.
  Quote-Reads sind bisher durch HTTP/API 429 blockiert; Ursache nicht bewiesen.
  Auch ein einmaliger expliziter API-authentifizierter GET-Quote-Probe lieferte
  HTTP/API 429 mit 31 Sekunden Providerfrist. Dies war keine Transaktions-
  Signierung und kein Order-POST. Der normale Runtime-Quotepfad wurde nicht
  ver?ndert.
- Solana: ein echter konsistenter Wallet-Snapshot mit beiden Tokenprogrammen,
  leerem Tokeninventar und **995547461 Lamports** wurde gelesen. Der Leser
  verlangt identische finalisierte Context-Slots, prüft Tokenprogramme und
  verwirft unbekannte Extensions. Ein kurzlebiger Basis-Python-Worker erlaubt
  kontrollierten Abbruch auch bei langsamen HTTP-Headern. Prozessstart/-ende
  können OS-Latenz hinzufügen; verspätete Ergebnisse werden nie akzeptiert.
- Eigener passiver Supervisor mit Windows-Job verwaltet CORE, Watcher,
  Gateway, Bridge und Health. Frische native Handles unterscheiden bewiesene
  venv-Weiterleiter von tatsächlichen Doppelprozessen. Brokerverlust beendet
  ausschließlich die vom Supervisor besessene Gruppe.
- Astra High ist vor den sicherheitskritischen Änderungen konsultiert worden.
  CHANGE/RISK-Befunde werden mit Code-/Regressionsevidenz aufgelöst; eine
  globale **ASTRA FINAL PASS** liegt noch nicht vor.

Integrierte Gruppen: Ledger/Config/Evidence/Dashboard (`833749b`),
Read-only-Broker (`d9e9d4c`), passiver Supervisor (`972cdbb`), eigene
Entwicklungsbroker/Sessionfortschritt (`be1322e`), begrenzte Quote-Diagnostik
und Worker-Abbruchdiagnose (`52e2dc3`), Windows-Projektionsdateien (`7d378fd`),
prospektiver Quote-Paper-Ledger (`b9ac337`) und unabh?ngige Security-Frische
(`2b79208`) sowie Schema-2-State-Machine (`c595392`). Jede inhaltliche Gruppe wurde mit Astra High gepr?ft; CHANGE-
Befunde wurden vor Integration behoben. Das ist keine globale Live-Abnahme.

Die Statusdatei-Retries wiederholen nur einen bereits serialisierten lokalen
Rename, h?chstens innerhalb eines 250-ms-Retryfensters. Ledger-/Ordersends
werden damit niemals wiederholt. Windows-AccessDenied beim Bridge-Heartbeat
war durch eine eigene Diagnose belegt; der tats?chliche externe Leser ist
nicht bestimmt. Der Supervisor hat den neuen Pfad geladen. OS-Dateioperationen
k?nnen l?nger als das Retryfenster dauern; Ersch?pfung bleibt ein sichtbarer
Workerfehler.

Security tr?gt den lokalen GoPlus-Requeststart als eigenen Zeitpunkt. Alte
Snapshots erhalten keinen Backfill. Exakte Solana-Adressidentit?t, strikter
Erfolgscode und monotone Abruf-/Retryfrist sind gepr?ft. Ein echter GoPlus-
Read lieferte checked und checked_at. Maximal vier ausstehende Abrufe werden
prozessweit zugelassen; eine Deadline verwirft versp?tete Ergebnisse, beendet
aber keinen laufenden Thread. Wiederverwendung und Restart erhalten das
urspr?ngliche Datum. Quote-Paper pr?ft Security erneut vor der Cash-Buchung.

Der eingefrorene 200-Intent-Paper-Ledger ist Quote-Forschung: keine echten
Fills, keine Chain-PnL und keine Live-Qualit?tsfreigabe. Es wurden weder eine
Kohorte mit k?nstlichen Erfolgsdaten noch ein laufender Paperprozess erzeugt.
Der Policyhash umfasst Provider, Modelle, Regeln und Broker. Ein Hashwechsel
sperrt vorhandene Kohorten; gespeicherte Daten werden nicht zur?ckgesetzt.
Generierte DBs, IPC, Berichte, Outbox, Cooldown, Tempdateien und lokale
Credentials geh?ren nicht in Commits.

Schema 2 wurde nach erfolgreicher Suite und ber?cksichtigtem Astra-Review
in den echten Ledger integriert. Zuvor wurde der eindeutig gebundene eigene
Supervisor beendet; die native Prozessaufnahme best?tigte alle sechs eigenen
Runtime-Prozesse beendet und keine unbekannten Interpreter. Sieben bestehende
REJECTED-Trades, Events, Receipts und der komplette bisherige Audit blieben
erhalten. Eine konsistente Schema-1-Sicherung bleibt lokal. Der anschlie?end
normal gestartete Gateway meldet Schema 2, keine Positionen/Fills und weiterhin
SIGNAL_ONLY / broadcast=false. Es gab keinen Broadcast.

Die normative Execution-State-Machine umfasst Candidate/Risk/Ready,
seitenspezifische Requested/Acknowledged/Partial/Unknown/Filled und
Reconciliation/Position/CLOSED/REJECTED. Der Legacy-State wird nur noch
berechnet und niemals im Trade gespeichert. PAUSED/KILLED werden getrennt aus
den vorhandenen persistenten Kontrollfakten abgeleitet; offene Orderpflichten
bleiben sichtbar. Jeder Trade-Write pr?ft Graphkante, vollst?ndigen
Dokument-CAS, immutable Identit?t und konkrete Attemptbindung. SQL-Guards
weisen alte Legacy-Writer ab. Terminale Orders und Wallet-Reconciliation
besitzen dauerhaft gebundene Nachweise. Eine Reconciliationgeneration kann
nach erneutem Eintritt nicht wiederverwendet werden. Emergency Exit speichert
zun?chst Absicht und erh?lt erst nach Reconciliation einen SELL-Intent.

Ein normaler Workerstart migriert Schema 1 nicht automatisch. Der ausdr?ckliche
Wartungspfad verlangt ein g?ltiges OFF-Gate und vorher stillgelegte Writer;
der normale Schema-2-Start ben?tigt diesen Pfad nicht. Nicht-Bool-Flags,
fehlende und fehlerhafte Generationen, Triggerliteral-Manipulation und alte
Writer sind in isolierten Regressionen gepr?ft. Diese Wartungsvoraussetzung
ist keine zweite Owner-Live-Sperre und erfordert keinen weiteren Owner-Schritt.

Ein injizierbarer Adapter mit atomarer Kapital-/Gebührenreservierung,
persistiertem Payload, einmaligem SEND-Marker und frischen Freigaben ist
isoliert geprüft. ACK ist kein Fill; unbekannter Send wird nie erneut gesendet.
Der echte GMGN-Schreibtransport ist ausdrücklich noch nicht angeschlossen.
Die initiale reale Wallet-Reconciliation und Equity-Baseline bestanden;
dies ersetzt keine laufend frische Reconciliation vor jedem BUY.

Read-Reconciliation (`dea82d6`) ist im eigenen passiven Gateway gestartet.
Der Walletanker kann nicht neu gebunden werden; verlorene oder ungueltige
Anker mit vorhandener Historie sperren dauerhaft. Der Reader beobachtet ohne
Timeout-/Fill-Schreibzugriffe. Recovery wird nach Walletpruefung zusammen mit
Reconciliation atomar angewendet. Gueltige Pending-/Processed-/Failed-/Expired-
Orders bleiben ungeloest; sie sind weder Fill noch Nachweis eines fehlenden SEND.
Callback und Recovery erzeugen identische Receipts ohne doppelte Cashbuchung.

Monitor und HTTP-Reconciliation teilen einen Ueberlappungsschutz. Das gemeinsame
READ-Budget reicht bis Brokerablauf, Koordinationsmutex, SQLite-Lock und Commit.
Nur COMMIT darf bei SQLITE_BUSY bis zur Deadline wiederholt werden; Anwendung
und SEND werden nicht wiederholt. Jede Commit-Wiederholung prueft Frische.
Budgeterschöpfte Fehlerbuchungen bleiben begrenzt fuer den naechsten Tick.
Unvermeidliche OS-/Dateisystemlatenz ist keine garantierte harte Echtzeitfrist.
Regressionsbelege umfassen einen gehaltenen SHARED-Leser ueber die Deadline,
vollstaendigen Recovery-Rollback und einmaligen Commit nach frueher Freigabe.
[SQLite beschreibt diesen COMMIT-Konflikt](https://www.sqlite.org/lang_transaction.html).
Astra High: **OK fuer diese abgegrenzte Read-Reconciliation**, keine Liveabnahme.

Die erste laufende Pruefung ergab RPCError **-32016** und damit UNKNOWN,
reconciliation_required sowie den persistenten API-Failure-Guard. Zwei
Tokenprogramm-Reads lieferten denselben Kontextslot; der zugehoerige Balance-
Read mit diesem Mindestslot wurde abgewiesen. Ein separater Balance-Read
bestaetigte unveraendert 995547461 Lamports, beweist aber keine kohärente
Reconciliation. Ein Publicnode-READ-Probe lieferte ebenfalls keinen validierten
Snapshot. Es wurde weder der Provider umgestellt noch die Slotpruefung gelockert.
Die neue Runtime meldet deshalb weiterhin **NOT_READY / SIGNAL_ONLY**, nicht
ARMED_NO_BROADCAST. Offene Positionen und Fills bleiben bei null; Gate bleibt OFF.

RPC-Nachlauf und sichere Fehlerdiagnostik (`7ba9201`) sind anschliessend
integriert und im eigenen passiven Job neu geladen worden. Maximal drei
vollstaendige Gruppen plus ein zusaetzlicher Balance-READ sind zugelassen.
Dieser Zusatzread verlangt zwei vollstaendig validierte Tokeninventare auf
demselben Slot. Gruppen werden bei Drift verworfen; der hoechste validierte
Slot einschliesslich Balance und der Receiptfloor bleiben Untergrenzen.
RPC-Code und Kontextslot stammen stets aus derselben Fehlerantwort. HTTP-
Status wird getrennt typvalidiert gespeichert; keine URLs, Header, Bodies,
Keys oder Exceptiontexte gelangen in diese Fehlerprojektion.

Alle RPC-Runden teilen eine vorgezogene Deadline. Im produktiven 15-Sekunden-
Budget bleiben drei Sekunden fuer weitere Reads/Anwendung; kleine isolierte
Testkonfigurationen verwenden dieselbe proportionale Reserve. OS-Latenz bleibt
moeglich. Die entsprechende Gesamtsuite und Astra-Quellpruefung bestanden.
Method-/Attempt-/Elapsed-/Floor-Traces sind noch keine persistente Diagnostik.
Die geladenen Runtime-Reads lieferten danach wieder regulaer VERIFIED und
VERIFIED_NATIVE_ONLY mit frischer Equity; 995547461 Lamports, DD 0 und keine
Positionen/Fills. Einzelne -32016-Phasen bleiben UNKNOWN bis zur naechsten
vollstaendigen erfolgreichen Reconciliation. Ein Langzeit- oder Live-PASS
wird daraus nicht abgeleitet. Es gab keinen Providerwechsel und keinen Broadcast.

Eine instanzgebundene SDK-Transportbibliothek ist getrennt vom Read-Broker
vorbereitet (`TOOLS/GMGN-SingleSwapTransport.mjs`). Sie hat keinen Einstiegspunkt,
laedt weder Konfiguration noch Zugangsdaten und ist nicht an die Runtime
angeschlossen. Der vorhandene Produktionsadapter hat weiterhin keinen Transport.
Der lokale Vertrag bindet Trade-, Event-, Request-ID, Payloadhash und eine feste
UTC-Frist; die lokale monotone Frist wird daraus abgeleitet, nicht aus einer
ungeprueften Python-Monotonzeit. Dieser Einmalverbrauch ersetzt keine dauerhafte
Ledger-/Prozessbindung des spaeteren Host-Handshakes.

Die Bibliothek verlangt explizit die SDK-Bestaetigungsfunktion und prueft das
Owner-Gate vor SDK-Authentifizierung sowie unmittelbar vor dem einen POST.
Ein spaeterer Host muss die absichtliche SDK-Automatisierungsoption aus dem
einzigen Owner-Gate ableiten; es gibt aktuell keinen solchen Produktionspfad.
OFF fuehrt zu keinem Swap-Authentifizierungsschritt. Der signierte Body muss
bytegenau zum persistierten Python-Body passen; integrale Slippage-Prozente
werden deshalb bereits im bestehenden Builder als Integer serialisiert.

Nur der exakte Swap-POST, elf bestehende Builder-Felder, feste Gebuehren,
Anti-MEV und keine automatische Slippage sind zugelassen. Redirects sind
gesperrt; Timeout, 429, 5xx und unklare Antworten werden nach begonnenem Fetch
UNKNOWN ohne Retry. Deadline und 64-KiB-Grenze umfassen den gesamten Antwortstream.
HTTP 200, numerischer Erfolgscode, eindeutige JSON-Schluessel und gueltige
Order-ID werden unabhaengig vom SDK geprueft. Ein ACK bleibt erhalten, wenn
das Gate nach SEND schliesst; er erzeugt keinen Fill. Die SDK-Instanz verwendet
eigene Fetch-/Parserpfade: auch bei aktivem Debug keine Ausgabe von Request,
Providerbody oder Secrets. Das SDK kann seinen Curlstring weiterhin im RAM
konstruieren; eine Ausgabe dieses Strings findet in diesem Pfad nicht statt.

57 neue Tests verwenden das installierte SDK 1.6.6 mit frisch erzeugtem
synthetischem API-Schluessel, kuenstlicher Konfiguration und ausschliesslich
Fake-Fetch. SDK-getConfig, echte Keys, echte Orders und echte Marktperformance
werden damit nicht geprueft. Die vorhandenen Adaptertests bleiben erhalten;
der gezielte Lauf umfasst 91 PASS. Nach dem ersten Gesamtlauf wurde die
Testkonfiguration explizit isoliert und dieser gezielte Lauf erneut bestanden.
Astra High hat die abgegrenzte SDK-Quellpruefung mit **OK** abgeschlossen;
die Konfigurationskopplung der Testfixture wurde nach CHANGE entfernt.
Dies ist noch kein Produktions- oder globaler ARMED-Nachweis.

Der anschliessende Ledger-Handoff schliesst einen isoliert reproduzierten
Guardvergleich nach dem SEND-Marker: Der alte Pfad gab nach einem neuen
Kill-Guard weiterhin Bytes aus, ohne im Test einen Transport aufzurufen.
Jetzt entsteht der Post-Marker-Inhaltshash in derselben Markertransaktion;
vor Ausgabe werden Marker, Event, Trade, Request, Payload und aktueller
Guardinhalt unter dem verbleibenden Permitbudget erneut verglichen.
Risk bindet zuvor eine validierte unveraenderliche Kopie seiner Konfiguration.
Alte Dictreferenzen und oeffentliche Neuzuweisungen koennen sie nicht aendern.

Die urspruenglichen sieben Hostfelder besitzen die urspruengliche UTC-Frist und keine
prozessfremde Monotonzeit. Das Originalintervall darf exakt hoechstens zwei
Sekunden betragen; danach wird die Ausgabezeit auf-, die Ablaufzeit abgerundet.
Kollabierte Intervalle werden verworfen. Beide Ausgabewege verbrauchen dasselbe
RAM-Permit vor Ledger-I/O. Lock-/COMMIT-Wartezeiten nutzen die urspruengliche
Deadline; Gate und Zeit werden vor/nach Lock sowie vor jedem Commitversuch
und nach Commit geprueft. Fehler erzeugen keine neue Send-Berechtigung.

25 neue isolierte Regressionen enthalten einen echten gehaltenen SQLite-
Writer, SHARED-Reader/COMMIT-Rollback, verspäteten Commit, Guardaenderungen,
Konfigurationspinning, Klon/Restart/Parallelzugriff, Rundungsgrenzen und den
vollstaendigen Python-Vertrag bis zum installierten SDK mit Fake-Fetch.
Astra High: **OK fuer dieses begrenzte Handoff-Paket** nach Korrektur der
Originalintervallgrenze. Keine Echtorders oder echten SDK-Zugangsdaten.

Diese Revision ist ein Inhaltshash, kein monotoner Mutationszaehler: Eine
Aenderung mit vollstaendiger Rueckkehr zum Ausgangsinhalt wird nicht als ABA
widerrufen. Die Garantie endet beim letzten geschuetzten Ledgervergleich;
sie ist keine atomare Sperre bis zum Remote-SEND. Der tatsaechliche Host muss
aktuelle Probes, Prozessbindung und letzte Freigabe unmittelbar vor Fetch
noch integrieren. Kein Produktionsadapter ist durch dieses Paket angeschlossen.

Beide Pakete wurden im eindeutig gebundenen eigenen passiven Job neu geladen.
Vor Stop/Start waren Gate OFF, Positionen/Fills und unbekannte Orders null;
alle eigenen Prozesse wurden beendet, Entwicklungsbroker blieben erhalten.
Die neue Runtime lieferte Schema 2, RUNNING/SIGNAL_ONLY, fuenf Worker ohne
Neustarts und frische VERIFIED/NATIVE_ONLY-Reads. Kein Produktionstransport,
keine Funds-Signierung und kein Broadcast wurden dadurch aktiviert.

Die isolierte Signed-GET-Quote-Diagnostik kann zwei feste Rate-Limit-Header als
Ganzzahlhinweise von 0 bis 1000000000 uebernehmen. Nur Fetch-Evidenz, keine
SDK-Fehlerfelder oder beliebigen Header werden verwendet. Der Broker nimmt
sie ausschliesslich vom verifizierten Probe-Pfad an. Doppelte, leere,
kommagetrennte und ungueltige Tokens werden verworfen; null bleibt UNKNOWN
und ist kein Beweis fuer Header-Abwesenheit. Lokale Cooldown-Ablehnung besitzt
keine neue HTTP-Evidenz. Hints beeinflussen weder Cooldown noch Tradingregeln
und erlauben keine Ableitung von Accounttier oder Livebereitschaft.
Die 23 gezielten Tests bestanden nach Astra-CHANGE fuer leere Duplikate erneut;
der vollstaendige 593-Lauf lag vor dieser engen Parserkorrektur. Astra High
schloss diesen spezifischen CHANGE mit OK. Ein echter Quote-GET wird getrennt
hoechstens einmal ueber den gemeinsamen Cooldown geprueft.

Der anschliessende einzelne signierte GET lieferte erneut HTTP/API 429 mit
31 Sekunden Providerfrist. Beide Quota-Hints waren UNKNOWN: kein verwertbarer
Hinweis auf Kapazitaet oder Accounttier, kein Beweis fuer Header-Abwesenheit.
Die Ursache ist weiterhin nicht bestimmt. Der Probe betraf ausschliesslich
API-Authentifizierung eines READ; kein Funds-Signing, Order-POST oder Broadcast.
Der passive Job und das OFF-Gate blieben bestehen.

SEND-Frische und ACK-Beobachtung sind im vorbereiteten Transport jetzt
getrennt. Risk gibt ein achtes Feld `responseUntilMs` aus: vom urspruenglichen
Claim-`sent_at` plus unveraenderlichem Requesttimeout, maximal 30 Sekunden,
konservativ auf Mikrosekunden und Millisekunden abgerundet. Der spaetere
Handoff erneuert dieses Budget nicht. Die urspruengliche SEND-Frist bleibt
hoechstens zwei Sekunden. Sieben-Feld-Vertraege behalten ihre strengere
gemeinsame Frist; ein fehlerhaftes achtes Feld eroeffnet keinen Fallback.

Unmittelbar vor Fetch wird das Gate vor einem abschliessenden Uhrenpaar
gelesen; dasselbe Paar validiert SEND-Frische und berechnet die verbleibende
Antwortwartezeit. Uhr-Rueckspruenge werden gegen die jeweils letzte Messung
geprueft. Ein gueltiger ACK nach SEND-Fristablauf oder Gate-OFF bleibt innerhalb
des Antwortbudgets erhalten. Nach Ledger-Timeout kann die Order-ID durch
`bind_order` erhalten bleiben; UNKNOWN, Halt und Reconciliation-Pflicht werden
nicht aufgehoben. Kein ACK erzeugt einen Fill oder einen neuen SEND.

27 zusaetzliche Regressionen pruefen unter anderem den originalen Claim-Anker,
Rundung, ungueltige Antwortfenster, spaete ACKs nach Ledger-Timeout fuer BUY/SELL,
echte Uhren mit verzoegertem Fake-ACK und Timeout sowie Fristueberschreitung
waehrend des letzten Gate-Reads mit null Fetches. Die gezielte Suite umfasst
143 PASS; die finale Gesamtsuite 620 PASS / 0 FAIL. Astra High: **OK fuer diesen
abgegrenzten Fristen-Diff** nach Korrektur der zusaetzlichen SEND-Grenzluecke.
Alle SDK-Requests blieben synthetisch mit Fake-Fetch; der Produktionstransport
ist weiterhin nicht angeschlossen. Das ist keine globale Live-Abnahme.

Alle SELL-Materialisierungspfade verwenden jetzt denselben aktuellen
Inventaranker: direkte Vorbereitung, Emergency-Control, Reconciliation,
Pending-Ausgabe und Claim. Der Anker verlangt genau einen aktuellen Audit-
Datensatz mit passender Revision, Generation, Beobachtungszeit, Wallet,
positivem Ledger-/Chain-/Executorinventar und bekannten finalen Orders.
Ein historischer Positionsnachweis allein reicht nicht. Andere ungeloeste
Orders sperren; beim Claim wird nur der exakt gebundene eigene SENT-Event
ausgenommen. Menge und Wallet werden nie aus einer Erwartung geschaetzt.

Exit-Intents werden vor der Materialisierung separat persistent gespeichert.
Die acht festen Gruende haben die explizite Prioritaet Emergency, Risk,
Session, Recovery, Stop Loss, Maximum Hold, Take Profit, Strategy. Wiederholte
Aufrufe erzeugen keinen zweiten Request; ein hoeherrangiger Intent ersetzt
keine bereits gefrorenen Orderdaten. Reconciliation bewahrt den Intentgrund.
Stale/UNKNOWN verhindert eine neue SELL-Materialisierung, loescht aber die
Exit-Absicht nicht. Bereits vorhandene READY-SELLs werden erneut geprueft;
ihre Blockierung fuehrt zu keinem BUY-Fallback.

Transaktionslokale, nur lesende Commit-Guards werden nach dem Caller-Hook vor
jedem begrenzten COMMIT-/BUSY-Versuch ausgefuehrt und immer bereinigt. Schon
vor den ersten Metadata-/Audit-Reads werden Wallclock, Limit und monotone
Restfrist erfasst. Audit-Latenz, Clockrollback oder ein spaeteres Limit duerfen
dieses Budget nicht erneuern. Fehler rollen Event/Claim zurueck; der vorher
committete Intent bleibt erhalten. Das sind keine SEND-Retries.

43 neue isolierte Regressionen umfassen alle Gruende/Nebenpfade, Partial/
Restart/Parallelaufrufe, fremde Orders, korrupte/orphan Intents, doppelte oder
inkohärente Audit-Anker, echte gehaltene SHARED-Reader bei Event- und Claim-
Commit, langsame erste Audit-Abfrage und Rollback vor Guard-Registrierung.
Der bestehende Unsigned-Rejection-/Recovery-Vertragstest ist mit Blockierung
vor erneutem Abgleich gestaerkt; alle bisherigen Assertions bleiben erhalten.
Die Schema-1-Migration bewahrt ausschliesslich validierte bereits vorhandene
Exit-Metadaten mit BUY-Receipts. Invalide Metadaten rollen die Migration
vollstaendig zurueck. Die echte Runtime bleibt Schema 2; es wurde keine
erneute Runtime-Datenmigration ausgefuehrt.

Die finale Gesamtsuite umfasst 663 PASS / 0 FAIL. Astra High: **OK fuer den
begrenzten zentralen SELL-Diff**, nach Korrektur des Zeitbudgets vor der
Guard-Registrierung. Eine unabhaengige Positionsschleife und echte SELL-Quotes
sind weiterhin getrennte offene Integrationspunkte; diese Abnahme ersetzt
weder den Managerloop noch einen Live- oder Gesamt-ARMED-Nachweis.

Verbleibende Gesamtarbeit umfasst insbesondere den verifizierten realen
Send-/ACK-/Recovery-Transport, echte Quoteverträge, automatisch aktuelle
Reconciliation/Equity, vollständige Heartbeats, Positionsmanagement unabhängig
von Discovery, Realitätssimulator, Rollout und belastbare Performance-Gates.
Der historische Bericht unterhalb dieses Abschnitts ist keine aktuelle
Zugangsanweisung und keine Abnahme dieser offenen Funktionen.

## Historischer Bericht vor dieser Fortsetzung

Stand: 02.10.2026. Basiscommit:
`982892366038ccf7e5bfd931b87e95618b841074` plus erhaltener Working Tree.
Release-Status: **NOT_READY**. Kein RC1-Tag oder Release-Commit vorhanden.

Dieser Auftrag wurde mit Phase 1 begonnen. Die Git-Schreibfähigkeit ist durch
die aktuelle Sandbox-Berechtigung blockiert. Es wurden keine Locks gelöscht,
ACLs geändert, Codebasen kopiert oder Schreibaktionen an den Read-only-Advisor
delegiert. Die folgende Matrix ist der Arbeitsplan für die Fortsetzung nach
Wiederherstellung des autorisierten Zugriffs, keine RC1-Abnahme.

## Phase 1 — Git-Ursache

Lesend geprüft mit `Test-Path .git/index.lock`,
`Get-Process -Name git,git-lfs -ErrorAction SilentlyContinue`,
`Get-Acl .git,.git/index` und `whoami /user`:

- `index.lock` existiert nicht; kein Git-/Git-LFS-Prozess sichtbar. Die
  Prozessaufnahme ist eine Zeitpunktaufnahme, kein exklusiver Git-Lock.
- Owner von `.git` und Index: `Kotaslaptop\<local-user>`. Der Host-Benutzer besitzt
  FullControl. Kein verwaister Lock und kein falscher Owner belegt.
- Explizite Deny-Einträge sperren Write/Delete für Sandbox-SIDs. Diese
  Sitzung läuft als `CodexSandboxOffline`; `.git` ist in ihren zugelassenen
  Dateisystemrechten ausdrücklich read-only. Die Deny-Regeln erklären den
  reproduzierten Fehler beim Anlegen von `.git/index.lock`.
- Der vor der Reparatur untersuchte `TOOLS/Start-IMC-Work.ps1` startete
  Codex mit `workspace-write` und Approval `never`. Die aktuelle Sitzung
  behält ihre unveränderlichen Grenzen; die nachfolgend beschriebene
  Launcher-Reparatur wirkt ausschließlich bei einem neuen Start.
- Vor diesem neuen Dokument: 43 geänderte/ungetrackte Pfade, nichts staged.
  Kein Inhalt zurückgesetzt und keine fremde Änderung übernommen/entfernt.

**Erforderliche externe Aktion:** Die Sitzung muss für genau dieses bestehende
Repository autorisiert schreibbaren Zugriff auf `.git` erhalten. Alle lokalen
Änderungen erhalten; danach Schreibfähigkeit erneut prüfen. Kein neuer Klon,
keine pauschale ACL-Manipulation, keine Freigabe von Live-/Wallet-Zugriff.
Der Hauptagent kann die explizite Sitzungsgrenze hier nicht selbst erweitern.

Astra High hat diese Entscheidung vor Reparaturmaßnahmen über
`imc-ask-advisor -RiskReview` geprüft: **OK**. Empfehlung: denselben Working
Tree erhalten, die autorisierte Sitzungsberechtigung ändern lassen; keine
Locklöschung, ACL-Umgehung oder Schreibdelegation. Die CIM-Hürde getrennt halten.

### Launcher-Reparatur für die nächste Sitzung

`config/imc-advisor.json` enthält jetzt den regulären Arbeits-Freigabeweg:
`workspace-write/on-request`, ausdrücklich `approvals_reviewer=user`.
`TOOLS/IMC_WORK_SESSION.ps1` prüft und baut die Startargumente;
`Start-IMC-Work.ps1` verwendet sie. Ungültige Werte brechen vor dem Start ab.
Ein möglicherweise geerbter Auto-Reviewer wird durch das explizite CLI-Flag
übersteuert. `-ShowLaunchConfig` funktioniert vor CLI-/Credential-/Broker-
Prüfungen und gibt `git_write_verified=false`, `applies_to=new_session` aus.

Git-Hostbroker-Autostart, Git-Shim-Zuweisung und Indexänderungen im Brokercheck
wurden aus diesem Launcher entfernt. Bestehende Git-Broker-Dateien bleiben
erhalten; GMGN/Astra bleiben Read-only-Broker. Der Startprompt nennt native
Git-Freigaben statt des Host-Schreibproxies. Live-Flags bleiben unverändert.

Astra High verlangte vor Umsetzung **CHANGE**: expliziten Reviewer `user`,
Sandbox-Freigaben im Startprompt, frühen Konfigurationscheck und keine
Indexänderung als Brokerprobe. Diese Punkte sind umgesetzt. Die 15 neuen
deterministischen Regressionen prüften zunächst Rollen/Levels, die exakten
Argumente, Fail-closed bei falschen Berechtigungen, Start ohne CLI/Broker
und einen unveränderten Index. Inzwischen sind es 18 Tests einschließlich
echtem Node-Argumenttransport und blockiertem CLI-Fallback ohne Broker.
Der GMGN-Shim wird auch ohne Ready-Broker vorgeschaltet; geerbte IPC-Zuordnung
wird im Launcher-Kindprozess verworfen. Kein neuer Codex-Chat wurde gestartet.
Die ausdrückliche Löschfreigabe aus AGENTS.md steht jetzt im Startprompt.
Die High/High-Advisorwerte waren vorbestehend und entsprechen dem
ausdrücklichen Nutzerauftrag; sie wurden mit dieser Reparatur nicht erhöht.

Die Git-Phase bleibt **BLOCKED**, bis in der neuen Sitzung eine konkrete
genehmigte native Git-Schreiboperation erfolgreich ist. Kein Commit oder
Tag wird aus der bloßen Konfigurationsprüfung abgeleitet. Nächster Schritt:
bisherigen Chat beenden und in einer frischen PowerShell `IMC -Level complex`
starten; siehe [Betriebsanleitung](05_operations.md).

### Im Regressionstest gefundene Ledger-Race

Der erste Gesamtlauf nach der Launcher-Reparatur ergab 167 PASS/1 FAIL beim
gleichzeitigen Ledger-Anlegen. Nach separatem Astra-High-RiskReview wurde
Existenzaufnahme bis Commit mit einem kanonischen OS-Sidecar-Lock geschützt;
vorbestehende leere/beschädigte DBs werden weiterhin ohne Reset abgewiesen.
Zwölf neue Regressionen prüfen Thread-/Prozesskonkurrenz, Timeout, Crash,
Rollback und Konfigfehler. Details: [Execution-Härtung](execution-hardening.md).
Finaler Gesamtlauf: **183 PASS, 0 FAIL, 0 SKIP in 21.66s**. Dies bestätigt
die aktuellen Verträge, keine Abnahme der offenen RC1-Funktionen.

Gezielter Lauf (isoliertes UUID-Verzeichnis unter `.local-tools/`):

```powershell
$testRun = '.local-tools/init-session-' + [guid]::NewGuid().ToString('N')
& .venv/Scripts/python.exe -m pytest tests/test_execution_initialization.py tests/test_work_session.py tests/test_execution.py -q -p no:cacheprovider --basetemp=$testRun
```

Ergebnis vor den letzten beiden CLI-Fallback-Testfällen: **109 PASS in 13.73s**.
Der oben angegebene Gesamtlauf enthält beide zusätzlichen Testfälle.
Astra High hat die endgültige Reparatur anschließend über den Host-IPC
read-only geprüft: **OK**, vorheriges CHANGE auf Codeebene aufgelöst, keine
neue blockierende Auffälligkeit in diesem Umfang. Astra hat die Tests nicht
selbst ausgeführt; die Testresultate stammen vom Sol-Hauptagenten.
Dieses OK ist keine RC1-/Live-Abnahme.

Abschließender nativer Versuch, ausschließlich
`TOOLS/IMC_WORK_SESSION.ps1` und `tests/test_work_session.py` zu stagen:
weiterhin `Unable to create .../.git/index.lock: Permission denied`.
Index bleibt unstaged, kein Commit oder Tag. `-ShowLaunchConfig -Level complex`
bestätigt die zukünftigen High/workspace-write/on-request/user-Parameter und
ausdrücklich `git_write_verified=false`. Der notwendige nächste Nachweis
bleibt die genehmigte Git-Schreiboperation in einer neuen Sitzung.

## Gap-Matrix

DONE beschreibt nur den angegebenen belegten Komponentenvertrag. Es bedeutet
weder vollständige End-to-End-Produktionsabnahme noch reale Marktvalidierung.
PARTIAL/OPEN/BLOCKED werden weiter bearbeitet; korrekt vorhandene Funktionen
werden erhalten. Quellpfade der GMGN-Komponenten beziehen sich auf
`RUNTIME/EXECUTOR_GATEWAY/GMGN_BRIDGE/`.

| Bereich | Status | Beleg und verbleibender Schritt |
|---|---|---|
| Architektur | PARTIAL | `01_architecture.md`, `execution-hardening.md`, kanonischer Signal-only-Start vorhanden; RC1-Adapter und Reality-Pfad fehlen noch in der Gesamtarchitektur. |
| Clean Reset | PARTIAL | `clean-reset-progress.md`: Runtime-State, Launcher, Python und Health verbessert; dauerhafte Datenkomprimierung/Retention und optionaler Supervisor noch offen. |
| Single Source of Truth | DONE | `execution_state.py`: transaktionaler Ledger für Execution, getrennt von CORE-Paper/SAFE100; Projektionen sind keine Buchungswahrheit. |
| Trade-ID | PARTIAL | `Ledger.candidate` erzeugt ID erst aus Quelle/Variante/Mint/Signalzeit am Gateway; frühere CORE-/Watcher-Schritte transportieren noch keine durchgehende Candidate-ID. |
| State Machine | DONE | `execution_state.py`, `test_execution.py`: lokaler persistierter Zustand, Teilmengen, Unsicherheits-/Terminalzustände, atomare Übergänge. Externe Adapterintegration noch separat offen. |
| BUY Execution Contract | PARTIAL | Claim vor I/O, vertrauenswürdige Orderbindung, unabhängige Fills lokal getestet; Bridge erzeugt nur passive Outbox. Vollständiger Adapter mit simulierbarem Transport fehlt. |
| SELL Execution Contract | PARTIAL | Ledger/Bridge verwenden bestätigte Restmenge statt Wallet-Prozent; Adaptertransport, reale Response-Zuordnung und vollständiger Recovery-Vertrag fehlen. |
| Fill Verification | PARTIAL | `execution_evidence.py`: GMGN-Order plus finalized RPC-Beleg; echte GMGN-Fixtures und Orders mit mehreren Transaktionen noch nicht verifiziert. |
| Restart Recovery | DONE | `Ledger.recover`, Neustart-/späte-Fill-/Crash-Tests bewahren Verpflichtungen und blockieren neue BUYs. Künftiger Adapter muss dieselben Regeln benutzen. |
| Idempotenz | PARTIAL | Ledger-Claim, Feedback-ID und Receipts getestet; externer Request/Send/ACK-Adapter mit gleicher Trade-ID noch nicht geschlossen. |
| Reconciliation | PARTIAL | Eigenständiger paginierter Holdings-/Order-/RPC-Leser mit Slotprüfung vorhanden; reale API-/Wallet-Snapshot-Kompatibilität noch zu prüfen. |
| Risk Engine | PARTIAL | Freshness, Slippage, Impact, Quote-Zuordnung, Reserve, Positions-/Session-Limits getestet; belegte Quotequelle und MTM/DD fehlen. |
| Kill Switch | DONE | Lokale kumulative Sperren, Stop/Pause/Emergency, API- und realisierte Verlustgrenzen getestet. Zusätzlicher Equity/DD-Auslöser ist noch offen. |
| GMGN Adapter | BLOCKED | Read-only Broker vorhanden, Client scheitert an CIM PermissionDenied; Quote im aktuellen engen Allowlist-Vertrag nicht implementiert. Keine echten Read-Fixtures und kein ausführender Adapter. |
| Shadow vs Live | PARTIAL | `Ledger.shadow/compare`: externer Simulations-PnL mit Quelle und gleicher Ledger-ID vergleichbar; vollständige Entry/Exit/Latency/Cost-Deviations und automatischer positionsgleicher Lauf fehlen. |
| Equity/DD | OPEN | `Ledger.status` liefert bewusst `drawdown=None`; keine belegte laufende MTM-Reihe, Peak/Current/Max-DD oder 15%-Guard. |
| Performance Metrics | PARTIAL | Count, Net-PnL, TQ, PF, Top-3-Anteil aus geschlossenen reconciled Trades; Gross-P/L, vollständige Kosten-/Latenz-/Hold-/Fehlerkennzahlen und Equity-DD fehlen. |
| Logging | PARTIAL | `audit/events/feedback/receipts` rekonstruieren den lokalen Ledger-Vertrag; frühe Candidate-Linie und vollständige Adapter-/Reality-Zeitpunkte fehlen. |
| Dashboard | PARTIAL | Execution-Ansicht getrennt von Paper, UNAVAILABLE und geschützte Steuerung getestet; vollständige Reality/Shadow-/Equity-/RC1-Ansicht fehlt. |
| Watchdog | PARTIAL | Gemeinsame Prozesszuordnung und mode-aware Health vorhanden; optionale Supervisoren und Schutz gegen konkurrierende Starter noch nicht vollständig vereinheitlicht. |
| Training Provenance | DONE | `sqlite_source.py` und alle drei Research-Verbraucher: WAL-Snapshot, doppelte Main/WAL-Hashes, Verifikation vor Veröffentlichung; ruhende Quelle nötig, keine Löschfreigabe. |
| Simulation Reality Model | PARTIAL | `simulation.py`: markierte synthetische/historische Research-Daten, keine reale Evidenz; vollständiger kausaler Quote/Delay/Partial/Recovery/Failure-Forward-Simulator fehlt. |
| Legacy Cleanup | PARTIAL | `execution-audit.json`, ADR 0001 und Fortschrittsdoku ordnen Komponenten zu; historische Klassifikation ist kein aktueller Nichtverwendungsbeweis. Keine neue Löschung. |
| Git/Deployment | BLOCKED | `.git` ausdrücklich read-only für die Sitzung; noch keine Commit-Gruppen integriert und kein RC1-Tag. |
| Tests | PARTIAL | Jetzt 183 PASS, 0 FAIL, 0 SKIP; die derzeit offenen RC1-Funktionen können damit noch nicht als getestet gelten. |
| Broker Identity | BLOCKED | `Assert-GmgnReady/Get-GmgnProcessBinding` brauchen CIM-Felder; realer Client erhält PermissionDenied. Endgültiger enger Identitätsvertrag mit Astra noch zu erarbeiten. |
| Astra IPC | PARTIAL | Host-IPC und aktueller Astra-High-Aufruf funktionieren; eigenständige RC1-Kerntests/abschließende Broker-Sicherheitsprüfung fehlen. |
| GMGN Write Block | DONE | Enge Allowlist in `GMGN-ReadProtocol.ps1`; lokale Negativtests blockieren Schreibkommandos, unbekannte Optionen und Argument-Injection. Eine Erweiterung darf diesen Schutz nicht schwächen. |
| Echtgeldtransaktionen im RC1-Auftrag | NOT_REQUIRED | Ausdrücklich ausgeschlossen. Adaptertests müssen isolierte Transports verwenden; keine Order zum Erzeugen einer Fixture senden. |

## Erhaltener Working Tree und Commit-Gruppen

Die uncommitteten Dateien sind nach Pfad/Funktion folgenden Gruppen zugeordnet.
Herkunft einzelner vorbestehender Änderungen, insbesondere eine Zuschreibung
an Claude, ist durch den aktuellen Git-Stand nicht belegt und wird nicht erfunden.
Vor Integration jeder Gruppe finalen Diff und ihre betroffenen Tests prüfen.

1. **Execution-Ledger und Monitoring:** `execution_state.py`,
   `execution_evidence.py`, `CORE/mykonos_meme_scout/execution_config.py`,
   `config/imc-execution.json`, bestehender Pure-Gateway, Bridge, Fill-Import,
   beide Dashboard-Dateien, Signal-only-Start sowie Execution-/Dashboard-Tests.
   Dazu vorhandene Architektur-/Betriebs-/Execution-Dokumentation.
2. **Host-Broker und Arbeitslauncher:** `TOOLS/GMGN-ReadProtocol.ps1`,
   GMGN-/Astra-Broker und Clients/Shims, `TOOLS/Start-IMC-*.ps1`,
   `config/imc-advisor.json`, `config/imc-gmgn-read-broker.json`,
   GMGN-Tests und Service-/Configdoku. CIM-Reparatur und Broker-Security-Review
   sind vor RC1-Abnahme erforderlich.
3. **Arbeitsfortschritt:** `TOOLS/IMC_WORK_PROGRESS.ps1`,
   `config/imc-work-progress.json` sowie zugehörige Launcher-Anbindung.
4. **WAL-Provenienz:** gemeinsames `sqlite_source.py`, drei Verbraucher,
   beide Quellenintegritätstests, `research-source-integrity.md` und der
   einzelne zugehörige Schlussabsatz in `clean-reset-progress.md`.
   `.local-tools/research-source-integrity.patch` ist hierfür vorbereitet
   und mit `git apply --check --reverse` gegen den aktuellen Inhalt geprüft.
5. **RC1-Ausgangsaudit:** dieses Dokument.

Gemeinsam betroffene Dateien wie `.gitignore`, Launcher und Fortschrittsdoku
werden über gezielte Hunks integriert; kein pauschales `git add .`. Keine
Datenbank, WAL/SHM, Credentials oder generierten Runtime-Daten committen.
Ignorierte lokale Testverzeichnisse sind isolierte Fixtures und keine
Deployment-/Codeklone. Eine Bereinigung darf keine fremden Artefakte löschen.

## Erneute Verifikation in dieser Sitzung

```powershell
$testRun = '.local-tools/rc1-audit-' + [guid]::NewGuid().ToString('N')
& .venv/Scripts/python.exe -m pytest -q -p no:cacheprovider --basetemp=$testRun
```

Ergebnis: **153 passed in 11.72s**, keine Failures oder Skips. Kleine isolierte
Fixtures, keine große Simulation oder produktive DB-Extraktion. Keine IMC-Dienste
gestartet/gestoppt. Keine Behauptung über laufende Trading-Prozesse aus JSON.

`config/imc-execution.json` wurde gelesen: `execution_authority_pct=0`,
`broadcast_enabled=false`, `live_trading_enabled=false`, `signing=false`,
`wallet_access=false`. Keine Flags geändert.

GMGN-Diagnose: IPC-Umgebung, Config, lokaler Pfad und Ready-Identität lesbar;
Fehler erst bei Prozessidentität: `CimException`, Kategorie `PermissionDenied`.
Kein authentifizierter Portfolio-/Holdings-/Order-/Quote-Nachweis in dieser
Sitzung. Keine Credentials gelesen/ausgegeben/kopiert, kein unsicherer Fallback.

## Fortsetzungsreihenfolge

1. Autorisierten Git-Schreibzugriff im selben Repository erhalten und prüfen;
   fertige Gruppen ohne Verlust vorbestehender Änderungen committen.
2. Broker-Identität mit kleinem, geprüftem Vertrag ohne fragilen CIM-Zwang
   schließen; tatsächliche Reads und sanitierte Response-Fixtures gewinnen.
   Order-Abfragen benötigen bekannte bestehende Order-IDs; keine Order erzeugen.
3. Frühe Candidate-/Trade-ID durchziehen und den vollständigen passiven bzw.
   simulierten Adapter mit Crash-/Unknown-Send-/Teilfill-Vertrag integrieren.
4. Positionsgleichen Reality-Vergleich, kausalen Forward-Simulator und
   Equity/DD mit belegten zeitgleichen Bewertungen/UNKNOWN-Verhalten integrieren.
5. Vollständige Metrics und getrennte Dashboard-/Audit-Ansicht ergänzen.
6. Optionalen Prozesspfad/Clean-Reset-Lücken belegen und technisch schließen,
   ohne relevante historische Daten/Modelle/Legacy-Komponenten zu verlieren.
7. Vollständiger Regressionstest, Astra High finaler RC1-Review über die
   zehn angeforderten Bereiche, notwendige Korrekturen, Dokumentation/Commits;
   erst danach IMC RC1 markieren. Live bleibt aus.

Der Reality-Validation-Plan wird vor RC1-Abschluss konkretisiert: historische
Replays, echte kausale Forward-Marktdaten, positionsgleicher Shadow gegen
belegte Quotes/Execution-Bedingungen und mehrere vollständige BUY→SELL-Zyklen.
TQ/PF/DD/Net-PnL/Kosten/Latenz/Fehler/Konzentration werden aus klar bezeichneten
Quellen gemessen, nicht als synthetisch erzwungene Erfolgsbedingungen behandelt.
