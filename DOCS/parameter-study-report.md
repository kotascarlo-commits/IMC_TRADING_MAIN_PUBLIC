# IMC Parameterstudie vom 06.10.2026

## Ergebnis

**Kein belastbar profitabler Parametersatz gefunden. Aktive Einstellungen bleiben unveraendert.**
84 vorab definierte Kombinationen wurden auf getrennten Zeitraeumen und drei Kostenstufen getestet.
Keine Kombination erfuellt die vorab fixierten Auswahlbedingungen. Im vollstaendigen Sicherheitsmodus
entstehen in allen drei Zeitraeumen und allen 84 Kombinationen **0 Trades**.
Das beweist keine wirtschaftlich zu strengen Schwellenwerte: Die erforderlichen Daten fehlen.

## Daten und Reichweite

Quelle: `CORE/data/imc-main.db`, Snapshot-IDs 1 bis 14040083.
Zeitraum: 2026-09-04T23:59:46.892490+00:00 bis 2026-10-06T10:33:18.793316+00:00 (UTC), rund 32 Tage.
Alle 14.040.083 Quell-IDs wurden in begrenzten read-only Abfragen durchlaufen; es gab keine Datenbankaenderung.

Der gemeinsame breite Marktfilter liefert **69.852 Beobachtungen**:

- Beide Holderwerte fehlen in **69.852/69.852** Beobachtungen (100 %).
- Security-Zeitstempel fehlen in **69.749/69.852** Beobachtungen (99,85 %).
- Weitere vorgeschriebene Security-Flags sind bei **66.969** Beobachtungen nicht vollstaendig bestaetigt.
- **2.883** Beobachtungen haben diese anderen Flags; nach zeitlicher Entdoppelung und sonstigen
  kanonischen Gates verbleiben **246 Diagnoseereignisse von 104 Tokens** im gesamten Zeitraum.

Diese Zahlen beziehen sich auf den breiten Kandidatenfilter, nicht auf alle Markt-Tokens.
Der Filter umfasst Liquiditaet >=30.000 USD, Kaufanteil >=60 %, Momentum 5 bis 120 % und Alter <=15 Minuten.
Die effektiven inneren Scoring-Grenzen aus `assess(..., Config())` bleiben erhalten, insbesondere
20 Sekunden Mindestalter, 15 Minuten Hoechstalter, 15.000 USD Mindestliquiditaet, 20.000 USD
Mindest-Marktkapitalisierung und Liquiditaet/Marktkapitalisierung >=2 %.
Die urspruenglichen Confirmation-, Drain-, Mikrostruktur- und Kontrollrisiko-Gates bleiben erhalten.

## Was getestet wurde

Eine reproduzierbare Engine: `RESEARCH/parameter_study.py`; keine neue Live- oder Forward-Strategie.
14 Eingangsprofile x 3 Exitprofile x 2 Positionsgroessen = **84 Kombinationen**.
Eingangsprofile pruefen einzelne Lockerungen sowie zwei kombinierte Hypothesen:
Liquiditaet 30k/60k/100k USD, Kaufanteil 60/70/75 %, Momentum-Untergrenze 5/10/20 %,
Score 60/66/72, Volumen/Liquiditaet 0,12/0,5/1 und Tokenalter 5/15 Minuten.
Das ist ein begrenzter Hypothesentest, keine vollstaendige kartesische oder globale Optimierung.
Exitprofile testen 90/180/300 Sekunden Haltedauer, Stops 8/12 %, Ziele 15/20/30 % sowie
zugehoerige Trailing-Regeln; Positionsanteile betragen 5/10 %.

Training: bis 28.09.2026 00:00 UTC. Validierung: 28.09. bis 02.10.2026 00:00 UTC.
Abschlusstest: 02.10. bis 06.10.2026 10:33 UTC. Vor jeder Grenze werden 400 Sekunden ausgespart.
Der Abschlusstest wurde nach dem Einfrieren der Auswahl einmal ausgefuehrt; es gab keine
Nachoptimierung anhand seines Ergebnisses. Fruehere aggregierte Watcher-Ergebnisse waren bereits bekannt;
daher wird kein vollstaendig unberuehrter historischer Datensatz behauptet.

In der **rein hypothetischen Diagnose** werden ausschliesslich fehlende Holderwerte und fehlende
Security-Zeitstempel als Datenluecke ausgeklammert, ohne Ersatzwerte einzusetzen. Bekannte unsichere
Werte und alle anderen Gates bleiben sperrend. Diese Lane ist **nicht ausfuehrbar** und keine Freigabe.

## Kosten und fehlende Ausstiege

Modellkapital: 1.000 USD. Pro Seite: 1,3 % Gebuehren, mindestens 0,35 % Slippage plus
positions-/liquiditaetsabhaengiger Impact, 0,12 USD Netzwerkgebuehr. Sensitivitaet: zusaetzlich
0/1/3 Prozentpunkte Kosten pro Seite. Dies sind transparente Annahmen, keine gemessenen Routekosten.

Entry fruehestens erste gueltige Beobachtung nach Signal+2 Sekunden, maximal 10 Sekunden nach Signal.
Exits ebenfalls um 2 Sekunden verzoegert, maximal weitere 10 Sekunden bis zur ersten Beobachtung.
Keine Interpolation, garantierten Stops oder rueckdatierten Fills. Eine Position gleichzeitig,
hoechstens ein Trade pro Token und Zeitraum. Prioritaet: Zeit, Score, Snapshot-ID.
Verschwundene, unbewertbare oder illiquide offene Positionen werden im konservativen Stress
als vollstaendiger Verlust inklusive modellierter Entrykosten behandelt. **Das sind keine belegten Realverluste.**
Drawdown wird auf die Gesamtequity einschliesslich beobachteter offener Positionswerte gerechnet.
Hoehere Kosten koennen einen frueheren Netto-Stop ausloesen; deshalb sind die verschiedenen
Kostenstufen keine monotone nachtraegliche PnL-Subtraktion.

## Resultate der hypothetischen Diagnose

Tabelle bei +1 Prozentpunkt Kosten pro Seite. Jede Zelle: Positionszahl / Kapitalrendite / unbewertbare Totalverlust-Stressfaelle.

| Vergleich | Training | Validierung | Abschlusstest |
|---|---:|---:|---:|
| Baseline (10 % Positionsanteil) | 8 / -57.89 % / 8 | 7 / -41.61 % / 5 | 2 / -19.44 % / 2 |
| Nicht qualifizierter Vergleichskandidat (5 %) | 10 / -28.04 % / 6 | 9 / -11.89 % / 2 | 3 / -10.20 % / 2 |

Der Vergleichskandidat `liquidity_30000/tight/0.05` ist ausschliesslich die vor dem Abschlusstest
bestplatzierte **nicht qualifizierte** Hypothese. Er ist kein profitabler Sieger und wird nicht empfohlen.
Seine Eckwerte sind 30.000 USD Mindestliquiditaet, ansonsten die urspruenglichen Eintrittsgrenzen,
90 Sekunden Haltedauer, 8 % Stop, 15 % Ziel, Trailing 8/4 % und 5 % Positionsanteil.
Im Abschlusstest gibt es nur drei Tokens an zwei Tagen, zwei davon ohne bewertbaren Exit.
Das genuegt nicht fuer ein belastbares Konfidenzintervall oder die benoetigten 30 unabhaengigen Trades.

Auswahl erforderte mindestens 30 Trainings- und 20 Validierungstrades, 20 verschiedene Validierungstokens,
positive Ergebnisse und PF>=1,2 in beiden Zeitraeumen bei +1 %/Seite. Fuer einen bedingten
Forschungskandidaten im Abschlusstest waren zusaetzlich mindestens 30 Tokens an drei Tagen,
Trefferquote >=70 %, PF>=2, Drawdown <=15 %, Top-3-Anteil am Nettogewinn <=25 %,
positives Ergebnis ohne Top-3, positiver unterer Zeitblock-Bootstraprand und positiver +3 %-Stress verlangt.
Bootstrapintervalle sind deskriptiv und nicht fuer die 84 Mehrfachvergleiche korrigiert.
Es gibt **keinen** qualifizierten Kandidaten, daher keine Parameterpromotion.

## Drawdown-Einschraenkung der eingefrorenen Studie

Astra fand einen Fehler: Nach dem Exitentscheid fehlten Zwischenbeobachtungen waehrend der
Ausfuehrungsverzoegerung in der Mark-to-Market-Kurve, konkret Snapshot 13838099. Dadurch koennen
die gespeicherten Drawdowns zu niedrig sein. **Die historischen Drawdownwerte sind deshalb
nicht vollstaendig validiert und duerfen nicht fuer eine Promotion benutzt werden.**
Die neue Engine erfasst diese Beobachtungen bis zum Exit. Ein synthetischer Regressionstest mit
Kurssturz waehrend der Wartezeit und Erholung vor Fill besteht. Eine gezielte Inspektion des
bereits bekannten Abschlusstrades bestaetigt die neue Markierung bei unveraendertem PnL/Exit;
es wurde keine neue Parameterwahl durchgefuehrt.

Die historische Auswahl, Ergebnisse und der einmalige Abschlusstest bleiben unveraendert.
`engine-frozen.py` bewahrt die exakt gehashte urspruengliche Engine bei den lokalen Artefakten.
Die aktuelle Engine verweigert bewusst einen Plan mit deren altem Codehash. Neue Auswertungen
brauchen einen neuen Plan und fuer unabhaengige Validierung neue Daten. Schon die zu kleinen
Stichproben und negativen PnLs verhindern die Auswahl; eine hoehere korrekte Drawdownbewertung
kann daraus keinen qualifizierten Kandidaten machen.

## Warum die alten Shadow-Ergebnisse nicht reichen

Der alte Watcher meldet fuer SAFE100 129 geschlossene Positionen mit PF 1.155
und modelliertem Nettogewinn 363.07 USD. Weitere **247 akzeptierte
Positionen** sind `EXPIRED_NO_DATA` (65,69 % aller akzeptierten Faelle). SAFE30 und SAFE50 liegen schon
bei den geschlossenen Positionen unter PF 1. Diese Fixed-Notional-/Parallelpositionen sind weder
verifizierte Chain-Fills noch das hier getestete Einzelpositionsportfolio.

## Ausfuehrungsgrenze und naechster technischer Bedarf

`RealityTransport` verwendet `jupiter_route.verify`, das in Zeile 44 nur das Paar SOL/USDC erlaubt.
Beliebige Meme-Token-Routen werden dort ausdruecklich abgelehnt. Auch ein guter Marktpreis-Replay
waere daher noch keine ausfuehrbare Reality-Strategie. Diese Grenze wurde nicht veraendert.

Vor einer belastbaren Einstellungsempfehlung braucht IMC vollstaendige zeitpunktgetreue Security-Daten,
zuverlaessige Entry-/Exit- und Quote-Beobachtungen sowie anschliessende unabhaengige Forward-Evidenz.
Lockerungen der Marktgrenzen beheben die fehlenden Sicherheits-/Exitdaten nicht.

## Reproduzierbarkeit und Pruefung

Planhash: `909f6ab18b3834e70670e9f4dc1a1c6216d0d4dd136cb8a292ba712877e813ac`.
Hash der ausgewerteten Quellzeilen: `6c851a7f30bf633cef758371cb9be58440a1e7e7550b05b6a58ee1eaaec5e1ad`.
Lokale vollstaendige Evidenz: `RUNTIME/test-runs/parameter-study-66519a7672d6422e849dc7a3b137ad17-20261006` mit `plan.json`, `dataset.json`,
`selection.json`, `results.json`, Preflight-Review, Legacy-Vergleich und Quellenpruefungen.
Die Code-SHAs und das obere Quell-ID-Limit sind im Plan fixiert. Neue Forschung benoetigt einen
neuen Plan; dieser Abschlusstest darf nicht erneut zur Auswahl benutzt werden.

```powershell
.\.venv\Scripts\python.exe RESEARCH/parameter_study.py plan --out RUNTIME/test-runs/NEUER_EINDEUTIGER_ORDNER
.\.venv\Scripts\python.exe RESEARCH/parameter_study.py extract --out RUNTIME/test-runs/NEUER_EINDEUTIGER_ORDNER
.\.venv\Scripts\python.exe RESEARCH/parameter_study.py study --out RUNTIME/test-runs/NEUER_EINDEUTIGER_ORDNER
```

70 automatisierte Tests bestanden; 4 Plan- und 8 Ergebnispruefungen der eingefrorenen Studie bestanden; gezielte Drawdown-Fix-Inspektion bestanden.
Astra hat die zentralen Ergebniszahlen, Quellhashes, Auswahlenthaltung und SOL/USDC-Grenze bestaetigt. Sein CHANGE zum Drawdown wurde anhand von Snapshot 13838099 bestaetigt und fuer kuenftige Studien korrigiert; ein Regressionstest besteht. Der korrigierte Code erfordert einen neuen Plan. Astra scoped Ergebnisreview: OK; XML mit 70 bestandenen Tests und Fix-/Quellhashes unabhaengig gelesen, keine Tests durch Astra ausgefuehrt.
Keine aktive Config, Datenbank, Security-/Route-Grenze oder Live-Autoritaet wurde veraendert.

Methodischer Hintergrund: Viele getestete Varianten koennen scheinbare Gewinner erzeugen;
ein einfacher Holdout allein beseitigt dieses Problem nicht. Siehe
[Bailey et al., The Probability of Backtest Overfitting](https://escholarship.org/uc/item/4w1110bb).
