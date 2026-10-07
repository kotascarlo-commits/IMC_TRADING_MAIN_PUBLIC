# IMC TRADING — verbindlicher Systemstand

Dieser Stand und `RUNTIME/current-system-state.json` sind die Single Source of
Truth für Codex, Astra und IMC-Komponenten. Frühere GMGN-Ausführungsbeschreibungen
sind historisch und geben keine Laufberechtigung.

```text
SYSTEM: IMC TRADING
EXECUTION_PROVIDER: JUPITER
WALLET: OWN_SOLANA_WALLET
GMGN_EXECUTION: DISABLED
GMGN_BROADCAST: DISABLED
GMGN_RUNTIME_DEPENDENCY: NONE
LIVE_TRADING: FALSE
BROADCAST_ENABLED: FALSE
SIGNING_ENABLED: FALSE
OWNER_GATE: OFF
MODE: LIVE_PARITY_NO_BROADCAST
PRIVATE_KEY_ACCESS: FALSE
REAL_WALLET_MOVEMENT: FALSE
```

Die öffentliche eigene Wallet darf ausschließlich gelesen werden. Kein privater
Schlüssel wird geladen. Ein Signing-/Broadcast-Versuch beendet den Lauf
fail-closed. GMGN ist keine Quote-, Execution-, Wallet-, Fallback- oder
Healthcheck-Abhängigkeit des aktiven Pfads. Historische Dateien bleiben bestehen.

## Zielkette

Token Discovery → Market Data → Security → Holder Analysis → SAFE100 / Risk
→ Entry Decision → Jupiter Live Quote → Transaction Build
→ Stateful Solana BUY Simulation → Virtual Fill → Position Management → Exit Decision
→ Jupiter Live Sell Quote → Sell Transaction Build → Stateful Solana SELL Simulation
→ Virtual Settlement → Reconciliation → CLOSED.

Diese Zielkette beschreibt den verbindlichen Vertrag, keine bereits bestandene
Abnahme. UNKNOWN bleibt UNKNOWN. Sicherheitsfilter, Strategie, Produktions-Exit
und Kostenmodell einschließlich Stressaufschlag werden nicht für einen Test
gelockert. Startkapital ist 1.000 EUR virtuell.

`LIVE_PARITY_CLOSED` erfordert die vollständige belegte Kette einschließlich
beider konkreter unsignierter Transaktionen, separater erfolgreicher Simulationen,
realistischer Fills/Kosten und Reconciliation. Quotes oder ein atomarer BUY+SELL
belegen keinen getrennten HOLD-/Exit-Lifecycle. Mindestens drei unabhängige
Abschlüsse sind erforderlich; bis dahin `LIVE_PARITY_READY = FALSE`.

Der neue Start muss diesen Vertrag vor Netzwerk-/Runtime-Start prüfen und einen
GMGN-Execution-Pfad sowie jede Geldfreigabe zurückweisen. Alte Launcher sind
keine autorisierten LIVE_PARITY-Startpunkte. Externe Daten-/RPC-Grenzen werden
belegt gemeldet; sie werden nicht durch erfundene Daten oder Fills umgangen.

## Historischer Mainnet-RPC-Befund

Frühere Read-only-Probe am 07.10.2026: Jupiter baut und simuliert den
unsignierten SOL/USDC-BUY erfolgreich (Slot 454172692). Das USDC-Tokenkonto
ist davor (454172683) und danach (454172693) nicht vorhanden. Der anschließend
separat gebaute SELL scheitert im Jupiter-Programm mit 6025
`InvalidTokenAccount`. Dies ist Diagnose, kein Strategie-Signal oder Trade.
Beweis: [separate-simulation-proof.json](../RUNTIME/test-runs/live-parity-81a4c902198a4e4788d31446032890db/separate-simulation-proof.json).

`simulateTransaction` persistiert keine Zustandsänderungen. Ohne echten BUY
oder einen geprüften zustandsfähigen Mainnet-Simulationstransport kann der
spätere separate SELL den virtuellen Bestand nicht nutzen. Der bestehende
experimentelle LiteSVM-Nachweis ist unintegriert und besitzt keine vollständige
Runtime-Parität; er erfüllt diese Abnahme nicht. Siehe
[bestehende Grenzen](IMC_REALITY_RUN_READINESS.md).
Die [Solana-RPC-Referenz](https://solana.com/docs/rpc/http/simulatetransaction)
und [Jupiter-Fehlerreferenz](https://developers.jup.ag/docs/swap/v1/common-errors)
beschreiben die verwendeten Schnittstellen.

Drei aktuelle GoPlus-Antworten enthalten weder Holder noch Creatoraggregate;
die Pflichtfelder bleiben UNKNOWN. Supply-RPC antwortet erfolgreich,
`getTokenLargestAccounts` mit HTTP/RPC 429. Das belegt einen separaten
Datenblocker, keinen generellen RPC- oder Jupiter-Ausfall. Beweis:
[provider-proof.json](../RUNTIME/test-runs/live-parity-81a4c902198a4e4788d31446032890db/provider-proof.json).

`START_IMC_LIVE_PARITY.ps1` prüft den kanonischen Stand, Geldflags und den
erlaubten Read-only-Jupiter-Worker. Es erzeugt derzeit ausschließlich einen
BLOCKED-Preflight-Bericht und startet keinen Handelslauf. Eine geänderte
Ready-Kennzeichnung allein schaltet keinen Runner frei. Der verlangte
vollständige Runtime-Start bleibt wegen der unten belegten Pflichtdatenblocker
und der noch nicht abgenommenen Transportparität gesperrt.
`STOP_IMC_LIVE_PARITY.ps1` meldet entsprechend keinen zugelassenen aktiven
Prozess; es beendet keine fremden oder historischen Prozesse.

## Aktueller Stand: lokale Zustandsfortführung belegt, Abnahme BLOCKED

`CORE/mykonos_meme_scout/jupiter_stateful.py` verwendet den vorhandenen nativen
LiteSVM. Ein aktueller Mainnet-Ausgangszustand enthält echte Route-Konten,
Lookup-Tables, Clock/Rent und die tatsächlichen Programmbytes. Nur initiales
virtuelles SOL-Cash wird explizit ersetzt; kein Tokenbestand wird gutgeschrieben.
Nur die lokale Ausführung unsignierter Jupiter-Transaktionen verändert danach
Wallet-Tokenbestände. Compute-/Mark-Proben verändern den Bankzustand nicht. Ein bereits
ausgeführter Payload wird zurückgewiesen; ein unerwarteter Commit vergiftet
die Bank und sperrt weitere Ausführung. Neue Konten dürfen bestehende
BUY-Zustände nicht überschreiben; Wallet-Token-Injection ist gesperrt.

Die echte technische Probe am 07.10.2026 beginnt ohne USDC-Tokenkonto. BUY
erzeugt **111.990824 USDC**, derselbe Zustand bleibt über einen **3-Sekunden-
HOLD** erhalten. Ein neuer echter Jupiter-SELL verbraucht exakt die erworbene
Menge; Restbestand **0**. Die virtuelle Cashdifferenz **-4.923.504 Lamports**
entspricht exakt der Summe beider Leg-Deltas. Netzwerk-/Priorityfees:
**2.810.000 Lamports**, zusätzliche Kontobindung **1.488.440 Lamports**;
beides ist im Cashdelta enthalten und wird nicht nochmals abgezogen.
[Beweis und State-Checkpoints](../RUNTIME/test-runs/stateful-parity-986f7307eab5442bb67c1530797e0742/stateful-technical-proof.json).
Eine zweite aktuelle Probe bestätigt
dieselbe Zustandsfortführung und vollständige Cash-/Token-Reconciliation:
[erneuter technischer Nachweis](../RUNTIME/test-runs/stateful-parity-986f7307eab5442bb67c1530797e0742/verification/stateful-technical-proof.json).

Dies ist **kein Strategie-Trade**: Signal, Security, Holder, SAFE100/Risk und
Produktions-Exit fehlen. Featuresemantik, Marktaktualisierung über längeres
HOLD und crashkonsistente Recovery sind nicht abgenommen. Insbesondere ist
der dokumentierte kurze technische HOLD kein neuer Trading-Exit. Der Engine
fehlen derzeit Nachweise für aktuelle Mainnet-Features; ihre IDs einzutragen
implementiert keine Semantik. Daher **0/3 LIVE_PARITY_CLOSED**, keine
Performance-Aussage und kein freigegebener Runtime-Start.
Die technische Route verwendet Legacy-SOL/USDC; sie belegt keine Token-2022-
Ausführungsfähigkeit. Token-2022-Holder lesen und Token-2022-Trades ausführen
sind unterschiedliche Nachweise.

**Aktuelle externe Datenblocker:**

- GoPlus liefert für drei unabhängig entdeckte Tokens leere `creators`.
  Der offiziell unterstützte Solana-Address-Security-Check liefert bei allen
  drei verschiedenen RugCheck-attribuierten Creatoradressen erneut
  **HTTP 200 / code 5000 / system error / result null**. Der Pflichtbefund
  `malicious_creator=false` fehlt. Ein Bestand oder fehlende Meldungen beweisen
  keinen sicheren Creator. UNKNOWN bleibt gesperrt.

[Datenbeweis](../RUNTIME/test-runs/stateful-parity-986f7307eab5442bb67c1530797e0742/security-holder-proof.json),
[alternative RPCs und erneuter Creator-Check](../RUNTIME/test-runs/stateful-parity-986f7307eab5442bb67c1530797e0742/data-alternative-proof.json).
[Drei unabhängige Creator-Abfragen](../RUNTIME/test-runs/stateful-parity-986f7307eab5442bb67c1530797e0742/creator-multi-proof.json).

**Holder-Befund korrigiert:** `getTokenLargestAccounts` bleibt rate-limitiert,
die getesteten Alternativprovider verweigern ihren kostenlosen Zugriff.
Das beweist keinen generellen Holder-Zugangsblocker: Die direkte vollständige
Mainnet-Abfrage `getProgramAccounts` mit dem tatsächlichen Mint-Programmowner
funktioniert. Die anfängliche Legacy-Abfrage ergab für den Token-2022-Mint
eine leere Liste; diese wird niemals als null Konzentration akzeptiert.

`CORE/mykonos_meme_scout/solana_holders.py` prüft vollständige Kontodaten,
Pubkeys, Programmowner, Mintbindung, Initialisierung, Token-2022-Erweiterungen
und identische finalisierte Kontextslots. Alle Raw-Beträge einschließlich Pools
werden nach Tokenaccount-Owner aggregiert; das ist kein Nachweis wirtschaftlich
Berechtigter. Unbekannte Erweiterungen, nicht aufgelöste Beträge, Teilantworten
oder Supply-Abweichungen ergeben UNKNOWN. Budget, Retry-After, Deduplizierung
und maximal fünf Sekunden alte Evidenz sind begrenzt; Signing/Send fehlen.

Aktueller vollständiger Read-only-Nachweis bei Slot **454181635**:
**1.285 Konten**, Raw-Supply **958.399.588.498.981**, exakter Supply-Abgleich
**PASS**, Top10 nach Tokenaccount-Owner **36,6362 %**. Die abgefragte,
RugCheck-attribuierte Adresse hält **0**. Creatoridentität und Creator-Risiko
werden daraus nicht abgeleitet. Der Adapter ist noch nicht in den freigegebenen
Trading-Runtime-Pfad integriert; dies ist keine vollständige Holder-/Security-
Abnahme. [Holder-Evidenz](../RUNTIME/test-runs/stateful-parity-986f7307eab5442bb67c1530797e0742/holder-live-proof.json).

Kleinster externer nächster Schritt: funktionierender attribuierbarer
Solana-Creator-Risk-Befund des bestehenden Providers. Fehlende Providerbefunde
können durch lokale Änderungen, Balanceprüfung oder Caching nicht erzeugt
werden. Ein kostenpflichtiger Holder-RPC ist durch diese Probe nicht notwendig.
Nach Verfügbarkeit bleiben die genannten Datenintegrations- und Transportnachweise
vor Runtime-Abnahme erforderlich. Signing/Broadcast/Ownergate bleiben geschlossen.

## Historische Creator-Risk-Quellenprüfung: 07.10.2026, 09:47 UTC

Eng begrenzte Nachprüfung des vorhandenen RugCheck und genau einer weiteren
Adressquelle: **CREATOR_RISK_RESOLVED = FALSE**, **SECURITY_COMPLETE = FALSE**.
Das bestehende Gate und die Strategie wurden nicht verändert; **0/3 CLOSED**.

Geprüfter Mint: `FNgMfPgP6pHwxkqYYJT2jTzfzQgoSiCtEXZhGB13pump`.
RugCheck-attribuierte und abgefragte Adresse:
`AwknxcVV9u7Yox1LKNQzTkDMrTuJUpXbyc1ff5zPevWM`.

- **GoPlus:** erfolgreicher Tokenrequest mit leeren `creators`; der frische
  Address-Security-Request erneut **HTTP 200 / code 5000 / system error /
  result null**. `malicious_creator` bleibt UNKNOWN.
- **RugCheck:** **HTTP 200**, Mint und Creator stimmen mit der vorherigen
  Attribution überein. `creatorTokens = null`, kein expliziter Creator-Risk-
  Negativbefund. Die [offizielle API-Spezifikation](https://api.rugcheck.xyz/swagger/doc.json)
  dokumentiert Tokenberichte, aber kein gleichwertiges vollständiges negatives
  Creator-Adressprädikat. `risks = []`, `rugged = false`, Score oder
  Creatoridentität ersetzen dieses Prädikat nicht. `detectedAt` ist weder ein
  aktueller Risk-Zeitstempel noch ein Nachweis kompletter Creator-Abdeckung.
- **Chainabuse:** genau ein öffentlicher Read-only-Versuch auf
  `/v0/reports?address=<Adresse>&chain=SOL&perPage=1&page=1` ergibt
  **HTTP 401 / {"message":"Invalid credentials"}**. Keine Zugangsdaten wurden
  gesucht, geladen oder erzeugt. Die [dokumentierte Solana-Abdeckung](https://docs.chainabuse.com/docs/supported-blockchain-list)
  und [Adressreport-API](https://chainabuse.readme.io/reference/reports-1)
  betreffen gemeldete Scamfälle; selbst eine erfolgreiche leere Liste würde
  allein keine äquivalente vollständige Creator-Risk-Negativaussage beweisen.

[Quellen, Abrufzeiten, unveränderte Rohantworten und SHA256](../RUNTIME/test-runs/creator-risk-120f114f579d44588406f51e2cd40566/creator-source-proof.json)
belegen diese Ergebnisse. On-chain-Authorities und Holderbestände beantworten
andere Sicherheitsfragen; sie beweisen keine Abwesenheit der bislang vom
Creator-Risk-Oracle bewerteten Risiken. Eine neue Empfangszeit darf eine ältere
Security-Evidenz nicht neu datieren.

**Objektiver externer Blocker:** Das verlangte adressgebundene Risk-Prädikat
fehlt beim bestehenden Provider; die einzige zusätzlich abgefragte Quelle
verweigert den unautorisierten Zugriff und ist semantisch noch nicht qualifiziert.
Lokale Parser-, Cache- oder RPC-Änderungen können weder das fehlende Provider-
Ergebnis noch die externe Zugangsberechtigung erzeugen.

Kleinster nächster Schritt: funktionierender expliziter, attribuierbarer
GoPlus-Creator-Risk-Befund. Alternativ benötigt eine andere Quelle autorisierten
Read-only-Zugang **und** einen belegten gleichwertigen Risk-Vertrag einschließlich
Creator-Abdeckung und Zeitbindung. Ein API-Schlüssel allein löst die Abnahme
nicht. Keine Runtimeintegration vor diesem Nachweis und den verbleibenden
Parity-Prüfungen; Signing/Broadcast/Private-Key-Zugriff bleiben FALSE.


## Historischer GoPlus-Creator-Befund: 2026-10-07T10:05:00.704259+00:00

Die jüngste Fokuskorrektur beschränkt die Prüfung auf genau drei vorhandene
Tokenkandidaten und den offiziellen GoPlus-Solana-Token-Endpunkt. Frühere
Alternativquellenprüfungen oben sind historische Evidenz, kein aktiver Arbeitsauftrag.
Keine neue Chainabuse-, Address-Security- oder sonstige Providerabfrage.

Das [offizielle Response-Schema](https://docs.gopluslabs.io/reference/response-detail-1)
beschreibt `creator.address` und `creator.malicious_address` (1 = malicious).
Der [offizielle Endpunkt](https://docs.gopluslabs.io/reference/solanatokensecurityusingget)
ist `/api/v1/solana/token_security?contract_addresses=<Mint>`.
Alle drei frischen Einzelrequests liefern **HTTP 200 / code 1** mit exakt
passendem Mint-Schlüssel. In jeder Rohantwort fehlt jedoch `creator` vollständig;
`creators` ist vorhanden, aber **[]**. Adresse und Flag sind somit **UNKNOWN**,
nicht null Risiko. Die pluralen und singularen Felder wurden separat erfasst.

| Token | Vorhandene RugCheck-Creator-Attribution | GoPlus-Creator / Flag |
| --- | --- | --- |
| `FNgMfPgP6pHwxkqYYJT2jTzfzQgoSiCtEXZhGB13pump` | `AwknxcVV9u7Yox1LKNQzTkDMrTuJUpXbyc1ff5zPevWM` | fehlend / UNKNOWN |
| `4uBDZXTGdUYfNTWxr1L1V69TwcfdXYhoErb4P22Spump` | `Kyn4DYYBRHS6mafjYcr8TZVWGEoAR11K9EPv81WXUsm` | fehlend / UNKNOWN |
| `43CTzwxRtoUW38rWVztQdAzZ3gQU4J7t2gCn5Qqj9i6D` | `B7hVQWbRoctGyY2PdTzNS1A43fPEL5FMneJtQMcyqfwv` | fehlend / UNKNOWN |

[Unveränderte Rohantwortbytes, SHA256, Request-/Response-UTC und bisherige
RugCheck-Mintberichte](../RUNTIME/test-runs/goplus-creator-879230768dc8420dbdbfa31aed85c0ad/goplus-token-creator-proof.json) sichern die Attribution und das neue Ergebnis.
RugCheck wurde hierfür nicht erneut abgefragt. Die vorhandenen Berichte dienen
ausschließlich der erwarteten Adressbindung, nicht einem neuen Risk-Negativbefund.
Die Empfangszeiten sind Diagnosezeiten; ältere Securitydaten werden nicht neu datiert.
Genau **3 HTTP-Requests**, **0 Retries**, danach Ende dieses Prüfpfads.

**BLOCKED – Security vor SAFE100/Risk. CREATOR_RISK_RESOLVED = FALSE,
SECURITY_COMPLETE = FALSE, SAFE100_RISK_PASSABLE = FALSE, 0/3 LIVE_PARITY_CLOSED.**
Das Gate und alle Holder-/Jupiter-/Strategiepfade bleiben unverändert.
Lokal kann weder ein Parser noch On-chain-Balancewissen die fehlende externe
Creator-Risk-Aussage herstellen. Kein Runtime-Start ohne diesen Pflichtbefund.

Kleinster externer Schritt: diese drei Rohantworten mit Mints, erwarteten
Creatoradressen und UTC-Zeiten an GoPlus übergeben, damit der Provider den
dokumentierten, expliziten, adressgebundenen Creator-Risk-Befund liefert bzw.
den dafür tatsächlich notwendigen Read-only-Zugang benennt. HTTP 200/code 1
belegt keinen Loginfehler; ein API-Schlüssel allein wird nicht als Lösung behauptet.
Keine weiteren Provider-Sweeps. Signing, Broadcast, Private-Key-Zugriff und
Echtgeldbewegung bleiben FALSE; Ownergate OFF.


## Historische A/B/C-Ersatzqualifikation: 2026-10-07T10:52:48.877790+00:00

**BLOCKED, CREATOR_RISK_STATUS = UNKNOWN, SECURITY_COMPLETE = FALSE,
0/3 LIVE_PARITY_CLOSED.** Keine Produktions-Gate- oder Runtimeänderung.
GoPlus wurde nicht erneut abgefragt. Die jüngste Aufgabe erlaubt ausschließlich
RugCheck, MadeOnSol und xAxios; diese begrenzte Prüfung ist jetzt beendet.

**A – RugCheck:** Drei einmalige frische HTTP-200-Requests am öffentlichen
`/v1/tokens/<mint>/report` bestätigen die vorhandenen Creatoradressen exakt.
`knownAccounts[creator].type = CREATOR` bestätigt jeweils dieselbe Attribution.
Alle drei Antworten enthalten `creatorTokens = null` und `insiderNetworks = null`.
Dies ist keine vollständige History-/Insider-Negativaussage. Es gibt keinen
verifizierten Analysezeitpunkt; `detectedAt` liegt bei 08:53–08:55 UTC,
die Requests bei 10:52 UTC. Detektionszeit und HTTP-Empfangszeit beweisen
kein aktuelles Analysealter. Kein Creator-Danger-Befund wurde in diesen
Tokenreports identifiziert; das ist kein Reputations-PASS. Die vorhandene
Liquiditätswarnung bleibt ein eigenständiger Token-Veto-Befund.
Die [offizielle API-Spezifikation](https://api.rugcheck.xyz/swagger/doc.json)
liefert beim Report kein 200-Antwortschema mit Coverage-Vertrag und dokumentiert
`refresh` nur für bezahlte API-Keys. Kein Refresh-/Loginversuch wurde ausgeführt.

**B – MadeOnSol:** Kein vorhandener konfigurierter Zugang in Projektintegration
oder passenden Prozess-/User-/Machine-Umgebungsnamen. Nur Präsenzmetadaten,
keine Secretwerte, wurden geprüft. Die [offizielle Dokumentation](https://madeonsol.com/api-docs)
beschränkt den öffentlichen Demo-Key auf drei Feedendpunkte; benötigte
Wallet-Profile und Tokenhistorie gehören nicht dazu. Der
[OpenAPI-Vertrag](https://madeonsol.com/api/v1/openapi.json) bietet Free-Tier-
Profile/Tokens mit Bearer-Key, aber `LaunchCoverage` warnt ausdrücklich:
Abwesenheit und false-Reputationsflags bedeuten nicht beobachtet, nicht clean.
Graduierungs-/Bonding-Erfolg ist keine gleichwertige negative Malicious-Aussage.
**0 authentifizierte Datenrequests**, keine Anmeldung oder Kaufaktion.

**C – xAxios:** Kein vorhandener konfigurierter Zugang. Die
[Authentifizierungsreferenz](https://xaxios.com/docs/api) verlangt einen Premium-
Key und verifizierte E-Mail. Die [dev-tokens-Referenz](https://xaxios.com/docs/api/rug-check)
beschreibt Rug-Historie, ihr Beispiel liefert aber keinen Creatoradress-Echo
oder vollständigen Coverage-/Indexierungsvertrag. **0 Datenrequests**,
kein Kauf, keine Anmeldung und kein geratenes Mapping.

**Composite-Prüfung:** Reale Holder-, Supply- und Mint-/Freeze-Daten beantworten
aktuelle Tokenmechanik. Sie schließen unbekannte Creatorhistorie oder
Insiderabdeckung nicht. Alle bestehenden Vetos bleiben erhalten; fehlende
Reputation wird durch zusätzliche Mechanikchecks nicht zu PASS.

[Attribuierte vollständige RugCheck-Rohantworten, SHA256, UTC, Coverage-
Auswertung sowie genaue B/C-Dokumentationsextrakte](../RUNTIME/test-runs/creator-contract-e70848b4315247898cb5fe05f879211d/creator-contract-evidence.json)
sichern den engen externen Blocker. Vollständige Dokumentationsantworten bleiben
zusätzlich lokal in derselben Run-ID persistiert. Dieser Befund ist Diagnose,
keine Handelsperformance und keine Providerfreigabe.

Kleinster externer Schritt: ein belegter frischer adressgebundener Creator-Risk-
Vertrag samt History-/Insider-Coverage und Analyse-/Indexierungszeit. Als
günstigster Zugang innerhalb der erlaubten Kandidaten kommt ein vom Owner
bereitgestellter MadeOnSol-Free-Read-only-Key für Profile/Tokens infrage;
die Gleichwertigkeit muss danach weiterhin nachgewiesen werden. Ein Key
allein wird nicht als Lösung behauptet. Alternativ muss RugCheck seinen
Coverage-/Freshness-Vertrag belegen und zulässig aktuelle Analysen bereitstellen.
Keine weiteren Provider-Sweeps. Signing, Broadcast, Private-Key-Zugriff,
Live-Trading und Echtgeldbewegung FALSE; Ownergate OFF.

## Aktueller Cabal-Vertrag und virtueller Testpfad: 2026-10-07

Execution bleibt **JUPITER / OWN_SOLANA_WALLET**. GMGN bleibt ohne aktive
Runtime-Abhängigkeit. MODE ist `LIVE_PARITY_NO_BROADCAST`; Signing, Broadcast,
Private-Key-Zugriff, Live-Trading und Echtgeldbewegung sind FALSE, Ownergate OFF.

`cabal_security.py` integriert ausschließlich einen beobachteten virtuellen
Risiko-Vertrag: `PASS / FAIL / UNKNOWN`. Ein PASS bedeutet vollständige aktuelle
definierte Beobachtungen ohne Veto **innerhalb dieses Vertrags**. Es bedeutet
weder `malicious_creator=false` noch universelle Creator-Unbedenklichkeit oder
Live-Freigabe. Der bisherige Standardpfad bleibt unverändert fail-closed;
der optionale virtuelle Pfad behält bekannte Malicious-Befunde und alle anderen
SAFE100-, Konzentrations-, Tokenmechanik- und Risk-Vetos bei.

Der Client kann nur einen anonymen GET zum festen Scan-Endpunkt ausführen.
Keine Authentifizierung, Registrierung, Zahlung, POSTs, Redirects oder Retries.
HTTP 402/429/5xx bleiben UNKNOWN; 429 übergibt Retry-After an das vorhandene
Rate-Budget. Unit-Tests sind offline und blockieren Netzwerkzugriffe.

Verbindliche Auswertung gemäß [Dokumentation](https://cabal-hunter.com/docs),
[Schema](https://cabal-hunter.com/openapi.json) und
[Methodik](https://cabal-hunter.com/methodology):

- Rohbytes/SHA256, exakt gebundener Mint, UTC und Provider-`computed_at` werden
  unabhängig validiert. Analyse und Empfang dürfen höchstens zehn Sekunden alt
  sein. Ein neuer HTTP-Empfang datiert die Analyse niemals neu.
- `scan_complete=true`, `degraded=false`, bekannte Quelle und widerspruchsfreie
  originale Deployer-/aktuelle Owner-Attribution sind Pflicht. Ownership-Transfer
  ist neutral; die Holder-Konzentration muss den **originalen Deployer** prüfen.
  Das vorhandene Holder-Adapterergebnis braucht Mint, Slot, Supply-Abgleich,
  vollständige Konzentration und eigene Frische von höchstens fünf Sekunden.
- Frische valide HIGH-/AVOID-Befunde, Score ab 65, Honeypot, aktive Mint-/Freeze-
  Authority, koordinierter Exit, Exit-Liquiditätsrisiko oder Token-2022-Risiken
  sind unabhängige FAIL-Vetos. Unvollständige, degraded oder stale Antworten
  bleiben UNKNOWN und blockieren ebenfalls.
- PASS verlangt explizite LOW_SIGNAL/CLEAN/SAFE-, HIGH-Confidence-, mindestens
  fünf geprüfte Wallets und vollständig gelieferte risikoarme Pflichtfelder.
  LOW_SIGNAL allein ist kein PASS. Deployerhistorie darf nicht nur gesampelt
  sein; fehlende/UNKNOWN/widersprüchliche Abdeckung blockiert. Tote Launches sind
  keine erfundenen Betrugsbefunde: adverse Historie verlangt Prüfung (UNKNOWN).

**Einzige neue echte Qualifikationsprobe:** HTTP 200 um
`2026-10-07T11:00:52.670714+00:00` für
`FNgMfPgP6pHwxkqYYJT2jTzfzQgoSiCtEXZhGB13pump`.
`scan_complete=true`, `degraded=false`; originaler Deployer
`FVZRwUp6E4m9jV4VumF8q7m8q3mF9fpikRrJSCCfFAdP`, aktueller Owner
`AwknxcVV9u7Yox1LKNQzTkDMrTuJUpXbyc1ff5zPevWM`.
Damit ist die alte RugCheck-Adresse eine aktuelle Owner-Attribution und darf
nicht als originaler Deployerbestand behandelt werden.
Die Probe war bereits beim Empfang **5229 Sekunden alt**, meldet HIGH/AVOID,
hat Historienabdeckung **58/59** und kein `exit_liquidity_risk`.
**Admissibility UNKNOWN, kein Sicherheits-PASS, kein Trade.**
[Vollständige unveränderte Rohantwort](../RUNTIME/test-runs/cabal-parity-503ad1863046440eb9846df5440e1062/cabal-qualification.json)
und [zeitgebundene Gate-Auswertung](../RUNTIME/test-runs/cabal-parity-503ad1863046440eb9846df5440e1062/cabal-admission-proof.json)
sichern Quelle, Identitäten, Hash und Ablehnung. Keine weiteren Live-Proben für Tests.

`live_parity_virtual.py` verbindet das vorhandene lokale Bank-Modell mit dem
Entry-Gate, unveränderten Exit-Regeln, BUY-Bestand, HOLD, SELL und Reconciliation.
Quote/Wallet/Mint/Amount, Slippage/Impact, Gebührenlimit, vorbereitete Bytes und
Authority werden vor lokaler Ausführung geprüft; Frische und Authority erneut
**nach Preview unmittelbar vor Commit**, einschließlich sämtlicher zeitabhängiger
BUY-Eintrittsdaten (Markt, Security, Cabal und Holder). SELL verwendet ausschließlich den
im selben Bankzustand entstandenen BUY-Bestand; Replay und Statewechsel stoppen.
Gebühren/Account-Effekte stammen aus nativer Ausführung, vorhandener Stressaufschlag
bleibt zusätzliche modellierte Kosten. Es gibt keinen Signing-/RPC-Broadcast-Pfad.

Der reproduzierbare **Offline-Vertragstest** startet mit:

```powershell
.\START_IMC_LIVE_PARITY.ps1 -DurationMinutes 60 -VirtualCapitalEUR 1000 -TargetClosedTrades 3 -OfflineContractTest
```

Er prüft drei unabhängige lokale SPL-/Nonce-Instruktionsfixtures in der echten VM,
jeweils BUY → unveränderter HOLD → bestehender MAX_HOLD-Exit → SELL → Restbestand 0
→ Reconciliation PASS. Diese synthetischen Fixtures ersetzen ausschließlich im
Test die Jupiter-Paketdekodierung. Sie beweisen **keine Jupiter-Liveroute, keine
unabhängigen realen Setups und keine Handelsperformance**. Das Ergebnis ist
`CLOSED_TECHNICAL_VIRTUAL`, niemals `LIVE_PARITY_CLOSED`.
[Offline-Report](../RUNTIME/test-runs/cabal-contract-3c43dd7fa855491ea486d8771c0f6668/live-parity-report.json)
enthält drei technische CLOSED und **0 LIVE_PARITY_CLOSED**. EUR-Kapital/PF/
Trefferquote bleiben null, weil native Fixture-Lamports kein 1000-EUR-Portfolio sind.

Der Start ohne `-OfflineContractTest` bleibt mit konkretem Bericht **BLOCKED**.
`LIVE_PARITY_READY=false`, keine Produktionsabnahme: Es fehlt eine tatsächlich
frische vollständige zulässige reale Security-/Holder-Beweiskette. Zusätzlich
bleiben die schon dokumentierten Mainnet-/Token-2022-/Lang-HOLD-/Recovery-
Transportprüfungen offen. Drei Unit-Zyklen dürfen diese Voraussetzungen nicht
überschreiben. Kleinster Daten-Schritt ist ein aktueller vollständiger gebundener
Providerbefund für ein wirklich zulässiges Setup, einschließlich Pflichtfeldern
und Coverage. Keine Cache-Umdatierung, Regelabsenkung oder Zahlungs-/Key-Aktion.

**Abnahme dieses begrenzten Änderungssatzes:** 383 relevante Tests PASS,
71 isolierte Vertrags-/Zyklentests PASS. Astra-Read-only-RiskReview **OK** nach
Korrektur der Original-Deployer-Bindung und vollständiger BUY-Frischeprüfung
im Precommit. [Abnahmeprotokoll](../RUNTIME/test-runs/cabal-parity-503ad1863046440eb9846df5440e1062/acceptance.json).
Dies bestätigt ausschließlich den virtuellen Vertrag; reale Provider-PASS-,
Mainnet- und Drei-Trade-Abnahme bleiben ausstehend.
