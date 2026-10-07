# Jupiter / lokale Wallet: Stand 05.10.2026

**NOT_READY: keine Freigabe für 100 echte On-Chain-Trades. Auch die
Quote-Paper-Serie blockiert weiterhin an der unveraenderten Kostenpolicy.**

## Implementiert

Die Jupiter-Beobachtung nutzt den bestehenden CORE-Scanner, die kanonische
Datenbank und die bestehende kausale Auswahl-/Exit-Policy. Sie verändert
keine GMGN-Cohort und etikettiert Jupiter-Quotes nicht als GMGN-Evidence.
`config/imc-jupiter-forward.json` friert **100 CLOSED / maximal 200 Versuche**
ein. Fehlversuche bleiben im Nenner/Audit; Drawdown, API-Kill oder Kapitalmangel
können die Serie vorher stoppen. 100 Abschlüsse werden nicht erzwungen.

Das eigene SQLite-Buch bewahrt Quellen-/Policyhash, Checkpoint, Cash, Equity
und ausstehende Exits über Restarts. Der erste Checkpoint liegt nach alten
Snapshots: kein Backfill, kein Löschen von Fehlversuchen. Ein neuer Code-/
Vertragshash verhindert das Fortsetzen derselben eingefrorenen Cohort.

Credentialfreie Jupiter-Reads teilen einen durchgehend gehaltenen Dateilock
über Ratejournal-COMMIT und GET. Mindestens 2,2 Sekunden Abstand; nach
429/401/403 60 Sekunden Sperre; keine automatischen Retries. Netzwerkworker
nutzen den tatsächlichen Base-Interpreter und eine gemeinsame monotone Frist.
Mint/Betrag/ExactIn bleiben exakt gebunden. Kleinere zurückgegebene Slippage
ist nur mit dem entsprechend strengeren Minimum zulässig; angeforderte und
tatsächliche Werte bleiben getrennt.

Der kurzlebige DPAPI-Verifier prüft den öffentlichen Wallet-Key ohne Signatur
oder Export. Er startet isoliert über Base-Python ohne `.pth`-Startup.
Native Klartextbytes und die mutable Seedkopie werden gelöscht; vollständige
Löschung sämtlicher Python-/solders-Kopien ist nicht belegt. Danach endet der
Prozess. Der Legacy-Key-Exporter wird weder importiert noch ausgeführt.

Finalisiertes Inventar verlangt initialisierte Accounts. Nur exakt
`[{"extension":"immutableOwner"}]` beim Token2022-Programm wird auf einer
Kopie für den bestehenden Parser entfernt. Fremde Owner/Programme, unklare
Mints, andere Extensions und frozen Accounts blockieren.
[ImmutableOwner](https://solana.com/docs/tokens/extensions/immutable-owner)
verhindert Ownerwechsel; dieser Read belegt keine Sellability oder vollständige
Mint-Extension-Prüfung. Altbestände sind Inventar, keine Cohort-Trades.

Die Launcher halten Maintenance-Fence und globalen Runtime-Mutex. Feed,
Preflight, Verifier und Netzwerkdescendants gehören zum nativen Kill-on-close
Job. Start verlangt frische Prüfungen und blockiert fremde/unklare Prozesse.
Stop bindet Session und gehaltenes natives Prozesshandle. Eine alte
STOPPED-Datei allein beweist keinen aktuellen Stop. Der vorherige Blocker
bleibt nach dem Cleanup als `outcome` sichtbar.

## Echte Beobachtungen, keine Trades

Lokale Wallet: `58Wqgfw5EcV8pkLXDJ2ipvcKkMyEeTEpPB3RiEtFjPdj`, nicht die
GMGN-Wallet. Neuer Feed: frische CORE-Snapshots, ohne Learner/Echtgeldpfad.
Public-Key-Verifikation, finalisiertes Inventar und SOL/USDC-Quotes in beiden
Richtungen bestanden. Quelle: `RUNTIME/jupiter-forward-readiness.json`.

Ein walletgebundener [Jupiter-v2-Build](https://developers.jup.ag/docs/api-reference/swap/build)
wurde ohne Signatur (`sigVerify=false`, `replaceRecentBlockhash=true`) auf
finalisiertem Mainnet simuliert. Eigene Job-Probe **05.10.2026 16:57 UTC**:
579 Bytes, 73.153 Compute Units, 718.201 Lamports Netzwerkfee, Slot 453639394,
PASS. Quelle: `RUNTIME/jupiter-preparation.json`. Kein BUY, Fill, PnL oder
Sendetransport-Nachweis.

Die aktuelle `route_v2`-Definition wurde read-only aus der finalisierten,
Jupiter-programmeigenen Anchor-IDL gelesen: `bb64facc31c4af14`; fester Präfix
30 Bytes, Vec-Anzahl bis Offset 34. Beträge, beide Fee-Felder, Wallet-Signer,
Mints und Tokenprogramme sind gebunden. Lookup-Tables werden gegen die
program-owned Chainaccounts verglichen. Unbekannte Versionen blockieren.
Gesamte Routensemantik, Empfängerbindung, Rent und Pool-Liquidität bleiben
unvollständig abgenommen: false/null, `execution_eligible=false`.

Keyless funktionierte hier. Der [Portalvertrag](https://developers.jup.ag/docs/portal/setup)
beschreibt keyless Zugang, die Endpunktreferenz nennt API-Key-Pflicht.
Einzelbeobachtungen garantieren keine dauerhafte Produktionskapazität.

## Kostenpruefung nach der Einzahlung

Finalisierter Saldo am 05.10.2026 17:18 UTC: **368.357.069 Lamports =
0,368357069 SOL**, Slot 453644108. Bei 5 Prozent Einsatz (18.417.853 Lamports)
besteht die Kapitalreserve. Quelle: `RUNTIME/jupiter-funding-check.json`.
Der RPC-Saldo belegt Guthaben; eine konkrete Einzahlungstransaktion wurde
nicht zugeordnet. Altbestaende sind keine Trades der neuen Cohort.

Die unveraenderte Forwardpolicy reserviert **3.000.000 Lamports pro Seite**
und lehnt den beobachteten SOL/USDC/SOL-Quote-Roundtrip mit **29,7609 Prozent
Modellbelastung** gegen die 12-Prozent-Grenze ab. Dieser Wert ist kein
bezahlter Verlust und kein Nachweis tatsaechlicher Ausfuehrungskosten.

Die unsigned Vorbereitung prueft jetzt standardisierte ATA-Erstellung,
exakte Wallet->WSOL-Finanzierung, SyncNative und CloseAccount mit Refund
an dieselbe Wallet. Andere Zusatzprogramme, Tips, unbekannte Empfaenger,
doppelte Compute-Instructions und nicht unterstuetzte Varianten blockieren.
Route-V2 und Shared-Route-V2 binden Wallet, beide Standard-ATAs, Mints und
Tokenprogramme. Accountpositionen stammen aus der finalisierten,
program-owned Anchor-IDL `C88XWfp26heEmDkmfSzeXP7Fd7GQJ2j9dDTUsyiZbUTa`,
Slot 453646944. Token2022 ist in dieser strengen Zusatzpolicy nicht freigegeben.
Dies ist keine vollstaendige Route/CPI-, Mint-Extension- oder Liquiditaetspruefung.

Eine erste unsigned Simulation vermisst den Compute-Verbrauch. Das neue
Limit ist `min(1400000, ceil(units*1.2)+10000)`; der originale Jupiter-Preis
bleibt erhalten. Eine zweite Message mit frischem festem Blockhash wird
erneut bepreist und simuliert. `replaceRecentBlockhash=false` verhindert
unbemerkten Message-Ersatz. Nur diese zweite erfolgreiche Message liefert
das Kosten-/Hash-/Balance-Ergebnis. Scheitern, unbekannte Fees oder fehlende
Metadaten ergeben keine Freigabe. [Solanas Gebuehrenformel](https://solana.com/docs/core/fees)
berechnet Priorityfees anhand des angeforderten Limits.

Native und Token-Pre/Post-Balances stammen aus **einer Simulation und einem
Bank-Slot**, nicht aus zeitlich zusammengefuehrten RPC-Snapshots. Aufgeloeste
ALT-Adressen muessen zur kompilierten Message passen; Native-Gesamtdelta muss
exakt die Netzwerkfee ergeben. Tokenowner, ATA, Menge und Identitaet werden
geprueft. Fehlende Tokenstates gelten nur bei gleichzeitig null Nativebalance
als abwesend. Nettodebit enthaelt Fees und Kontobindung bereits; decomponierte
Kosten werden nicht nochmals abgezogen.

Probe 17:31:49 UTC, Slot 453647025: 43.328 CU Verbrauch, Limit 61.994,
**46.330 Lamports Netzwerkfee** statt 938.336 mit 1,4m CU. **1.488.440
Lamports** bleiben im neuen Outputkonto gebunden; simuliertes Nettodebit
19.952.623 fuer 18.417.853 Einsatz. Quelle: `RUNTIME/jupiter-cost-observation.json`.
Diese Einzelprobe ist keine allgemeine Fee-Garantie und keine Fill-Evidenz.

Atomare unsigned BUY+SELL-Probe 17:34:38 UTC, Slot 453647669: 724 Bytes,
170.431 CU, Limit 214.219, Netzwerkfee **58.555 Lamports**, Walletdelta
**-1.736.626 Lamports**, einschliesslich **1.488.440** Kontobindung. Zudem
bleiben **22.069 USDC-Raweinheiten** hypothetisch in der Wallet. Quelle:
`RUNTIME/jupiter-roundtrip-observation.json`. Beide Vertragsminima und die
Setup/Swap/Cleanup-Reihenfolge sind gebunden; SELL verbraucht hoechstens das
BUY-Minimum. Die gemeinsame Transaktion wird selbststaendig vermessen und
erneut simuliert. Ihre Cashdifferenz beweist keine individuellen Leg-Outputs,
keinen getrennten Exit und keine zwei getrennten Broadcasts. Die separaten
Messagegebuehren bei gemeinsamem CU-Budget werden separat ausgewiesen und
nicht als gemessene Ausfuehrungskosten behandelt.

## Aktuelle Blocker

Die 3m-Forwardpolicy wird durch diese Einzel-/Atomarproben **nicht gesenkt**.
Die Pruefung lautet weiterhin:

`100 * (SELL_net - Exitfee) > (100 - Hardstop_pct) * (Einsatz + Entryfee)`.

Fuer echte 100-Chain-Trades fehlen weiterhin durchgaengiger lokaler
Jupiter-Contract im bestehenden Ledger/RiskEngine/Adapter, dauerhaft gebundene
Signatur vor Broadcast, durchgaengige providergebundene Restart-Recovery,
vollstaendige Route-/Empfaenger-/Mint-/Liquiditaetspruefung und separate
Exit-Abnahme. Bestehende Claim-, Assess-, Bewertung- und Reconciliationpfade
sind GMGN-gebunden; Jupiter wird dort nicht als GMGN umetikettiert.

Die Produktions-RiskEngine verlangt bereits 100 finalisierte Trades vor
BUY. Ein initialer Chaintrial benoetigt einen gesonderten, begrenzten
Owner-Scope innerhalb derselben RiskEngine; Paper-/Simulationsergebnisse
ersetzen diese Grenze nicht. Die aktuelle Konfigurationsvalidierung erzwingt
alle Geldflags OFF und Authority 0. Keine Echtgeldfreigabe ist vorbereitet
oder aktiviert. Der Stand ist weiterhin **NOT_READY**.

## Bedienung

Vom Repository aus:

```powershell
& .\LAUNCHERS\JUPITER_FORWARD.ps1 -Action Preflight
& .\LAUNCHERS\JUPITER_FORWARD.ps1 -Action Prepare
& .\LAUNCHERS\JUPITER_FORWARD.ps1 -Action Status
```

`Prepare` ist ausschließlich unsigned Simulation. `Start` startet nach neuem
erfolgreichem Preflight Feed und Quote-Paper-Serie; aktuell blockiert es.
`Stop` beendet die identifizierte eigene Session. Statusdateien sind
historische Beobachtungen, keine aktuelle Ausführungserlaubnis.
Signing/Broadcast/Execution Authority bleiben OFF.

## Validierung

Kontrollierte 100 Roundtrips in Unit-Tests sind keine 100 Forward-Trades.
Es wurde keine neue Forward-Cohort angelegt. Astra High prüfte Architektur,
Wallet-Read, V2-Diagnostik, Kosten, Serialisierung und die kleinere Slippage
vor Umsetzung. CHANGE-Befunde wurden an Code/frischen Reads aufgelöst.
Die vollständige Chain-Abnahme bleibt offen.

Astra-Abschlussbefund umgesetzt: wirtschaftliche Ablehnung wird im
Jupiter-Runner als fachlicher FAILED-Grund behandelt, ohne API-Fehlerzähler
oder API-Cooldown. Die autoritative Kostenprüfung bleibt im Writer-COMMIT;
sechs kontrollierte Kostenablehnungen dürfen keinen API-Kill auslösen.

Regressionslauf nach Astra-Korrektur: **809 PASS, 0 FAIL, 0 SKIP**,
65,38 Sekunden; neue
Jupiter-/Quote-/Paper-Tests, gesamte bestehende Execution-Gruppe und
Maintenance-State. Zusätzlich 55 Prozessidentitätschecks und 21 Redirector-/
Console-/Transient-Read-Checks bestanden. `pip check` und Diff-Whitespaceprüfung
bestanden. Eigene Preflight-/Prepare-Jobs räumten native Prozesse vollständig
auf. Ein Stop-Versuch während noch laufender Tests blieb wegen unbekannter
Interpreter korrekt gesperrt; nach Testabschluss bestätigte Stop null
Jupiter-/IMC-Worker, null Redirectors und null unbekannte Interpreter.

Kostenphase nach den neuen Guard-/Accounting-Tests: **871 PASS, 0 FAIL,
0 SKIP**, 62,38 Sekunden (Jupiter, Quote, Paper, gesamte Execution-Gruppe,
Maintenance-State). Der native Prepare-Launcher bestand bei 5 Prozent des
aktuellen Wallets: 77.610 Lamports Einzelmessage-Fee statt initial 705.000;
atomare Probe 246.618 Lamports Fee und -1.923.578 Cashdelta. Es wurden keine
Signaturen erzeugt und keine Transaktionen gesendet. Job-Cleanup und frische
native Prozesspruefung bestaetigten null Jupiter-Worker, null unbekannte
Interpreter und null Redirectors. Ownergate/Execution-Konfiguration sind
hashgleich zum Beginn dieser Arbeit und bleiben OFF.

## Finalisierter Jupiter-Receipt-Pruefer (noch keine Sendefreigabe)

`execution_jupiter_evidence.py` liest ausschliesslich `getTransaction` mit
`encoding=base64`, `commitment=finalized`, Transaktionsversion 0. Vor jedem
Read verlangt es eine persistierte V2-Providerbindung, kanonisches
Request-Payload mit unveraendertem SHA, gebundene Signatur sowie Claim <=
Signaturbindung <= Send <= aktuelle Zeit. Die Chain-Blockzeit darf hoechstens
zwei Sekunden vor Send liegen (Whole-second-Zeit/Node-Clock-Skew); das erneuert
keine Quote-, Request- oder Broadcast-Frist.

Wire-Message-Hash, aufgeloeste Kontoadressen, kryptografisch gueltige einzelne
Wallet-Signatur und Payer muessen exakt passen. Native Pre/Post-Deltas und
Gebuehren werden konserviert; standardisierte Legacy-Token-ATA, Owner, Mint,
Input und Mindestoutput werden geprueft. Das Cashdelta enthaelt Gebuehren und
Kontobindung bereits. Fehlende/fehlerhafte/unfinalisierte Daten bleiben
UNRESOLVED oder unverifiziert. Dieser Reader hat keinen Send-/Retry-Pfad.

Der vorhandene `Ledger.apply_fill` weist Jupiter-Receipts fuer GMGN-Claims
vor einer Mutation zurueck. Prepare protokolliert nun auch den Hash der
aufgeloesten Kontoadressen; der Cohort-Policyhash umfasst den neuen Reader.

**Diese Erweiterung ist noch keine durchgaengige Recovery-Abnahme.**
Claim, Assess, Autorisierung, Bewertung und laufender Reconciliation-Reader
bleiben GMGN/V1-gebunden. Es gibt keinen neuen aktiven Jupiter-Signer oder
Sendepfad. Der Receiver wird nicht als Transport-/Restart-Capability gemeldet.
Die eigentliche V2-Integration und vollstaendige Route-/Exit-Abnahme bleiben
Arbeit vor einer Owner-Live-Startbereitschaft.

Astra-CHANGE wurde umgesetzt: Zukunfts-Sendzeiten blockieren vor RPC,
Chainzeit wird an Send gebunden; negative Wire-/ALT-Tests halten das Payload
konsistent und erreichen dadurch die tatsaechlichen Receipt-Hashpruefungen.
Fixtures nutzen ausschliesslich wegwerfbare Testkeys ohne Guthaben. Kein
lokaler Echtgeld-Wallet-Key wurde dafuer verwendet; keine synthetische Zahl
wird als Forward-/Chain-Ergebnis gezaehlt.

Receiptphase nach Astra-Korrekturen: **912 PASS, 0 FAIL, 0 SKIP**,
61,62 Sekunden, dieselbe relevante Regression inklusive neuer adversarialer
V2-Receipt-Tests. Erneuter nativer Prepare-Launcher PASS; keine neue
Forward-Cohort und keine Echtgeld-Signatur/Broadcast-Aktion.
