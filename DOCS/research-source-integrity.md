# WAL-Provenienz für Research-Leser

Stand: 02.10.2026. Teilabschluss von Phase 2/3 des Clean Reset.

## Problem und Änderung

`simulation.py` und `temporal_intelligence.py` lasen SQLite mit
`mode=ro&immutable=1`. Damit konnten bereits committete WAL-Inhalte fehlen,
einschließlich Domainmarkern. Ihre Quellenbelege erfassten nur die Hauptdatei.
Die Replay-Run-Config berechnete deren Hash außerdem nach der Extraktion erneut.

`CORE/mykonos_meme_scout/sqlite_source.py` enthält jetzt den gemeinsamen
Leservertrag für `training_data.py`, historische Replay-Sequenzen und temporale
Research-Datasets. Er verwendet `mode=ro`, query-only und eine durch einen
Schema-Read tatsächlich fixierte BEGIN-Lesetransaktion. Dateiidentität, Größe
und Zeiten sowie vollständige Main-/WAL-Hashes werden vor und nach allen
Feature-/Label-Abfragen geprüft. Bei Änderung wird das Ergebnis verworfen.
Fehlende Quellen werden nicht erzeugt. Der Verifikationsstatus wird erst nach
erfolgreicher Abschlussprüfung wahr; alle drei Verbraucher geben ihre
Ergebnisse anschließend zurück.

Die vorhandenen Main-Hash-Felder behalten ihre Bedeutung. Fingerprint-Version 2
bindet Main-Hash und WAL-Hash bzw. WAL-Abwesenheit. Historische Sequenzen geben
diesen Beleg an Scenario-Metadaten und Run-Config weiter. Eine spätere Änderung
der Quelle ändert den Beleg der zuvor extrahierten Daten nicht. Ohne historische
Extraktion enthält die Run-Config keinen historischen Quellenbeleg. Manuell
erzeugte ältere `HistoricalSequence`-Objekte bleiben ohne neuen Verifikationsbeleg
verwendbar; ihr bisheriger Main-Fingerprint wird nicht als Version 2 ausgegeben.

Temporal-Records und ihr Source-Manifest verwenden denselben Fingerprint.
Der bisherige Main-only-Nachcheck ist durch den gemeinsamen Main-/WAL-Vertrag
ersetzt. Jede Quelle wird für ihre eigene Extraktion geprüft; mehrere Quellen
bilden keinen gemeinsamen zeitgleichen SQL-Snapshot. Die read-only Ansicht von
`SimulationStore` sieht ebenfalls WAL-Inhalte und hält einen SQL-Snapshot,
liefert allein aber keinen vollständigen Datei-Integritätsbeleg.

## Grenzen

Die Prüfung ist kein exklusiver Schreiber-Lock und erkennt nicht jede vollständig
zurückgenommene Zwischenmutation. SQLite kann SHM-Buchführung anlegen/verändern.
Produktive Exporte verlangen weiterhin ruhende Quellen; zwei vollständige
Hashläufe können bei großen Datenbanken lange dauern. Vorhandene Exportgrenzen
und die Trennung von Simulation und realer Evidenz bleiben bestehen.

Dieser Teilabschluss rechtfertigt keine Rohdatenlöschung. Datenkomprimierung,
Retention und der gesamte Clean Reset sind weiter offen. Es wurden ausschließlich
isolierte Testdatenbanken verwendet, keine produktiven Quellen exportiert und
keine IMC-Dienste gestartet oder gestoppt. Authority-, Live-, Signing- und
Broadcast-Einstellungen wurden nicht geändert.

## Prüfung

Astra High prüfte vor Umsetzung über den lokalen Host-Broker mit
`imc-ask-advisor -RiskReview`: **OK**. Quellen waren die drei betroffenen
Verbraucher, die vorhandenen Integritätstests und der letzte Abschnitt von
`clean-reset-progress.md`. Seine Hinweise sind umgesetzt: erst nach erfolgreichem
Kontextende veröffentlichen, Main-Hash-Semantik erhalten, Extraktionsbeleg in
Run-Config übernehmen und Mutation nach Extraktion separat testen.

Gezielte Prüfung: **19 Tests bestanden**, davon 12 neue Fälle in
`tests/test_research_source_integrity.py`. Abgedeckt sind WAL-Daten/-Marker,
Belegweitergabe, Mutation während und nach Extraktion, Nichtveröffentlichung bei
Fehler, unbenutzte historische Quelle, fester query-only SQL-Snapshot und fehlende
Quellen. Vorbestehende Monkeypatch-Ziele wurden auf das gemeinsame Modul angepasst.

Gesamtlauf nach Integration: **153 Tests bestanden**, keine fehlgeschlagenen oder
übersprungenen Tests. Aufruf:

```powershell
$testRun = '.local-tools/source-full-' + [guid]::NewGuid().ToString('N')
& .venv/Scripts/python.exe -m pytest -q -p no:cacheprovider --basetemp=$testRun
```

Der Ausgangslauf bestand 141 Tests. Die vorbestehenden Execution-/Broker-
Änderungen wurden nicht in diesen Entwicklungsblock aufgenommen.

Der zusätzliche enge GMGN-Lesecheck `gmgn-cli portfolio info --raw` meldete
`GMGN_READ_ONLY_BROKER_UNAVAILABLE`, Exit 125. Eine lesende Diagnose konnte
IPC-Umgebung, lokalen Pfad und Ready-Identität lesen; sie scheiterte erst bei
der Prozessidentitätsprüfung mit `CimException`. Das belegt eine Zugriffsgrenze
dieser Sitzung, keinen fehlenden Host-Broker oder falsche Credentials. Die
Identitätsprüfung wurde nicht umgangen. Es wurden keine GMGN-Schlüssel gelesen,
ausgegeben oder kopiert.

## Git-Stand

Der thematisch begrenzte `git add` für die drei Verbraucher, das gemeinsame
Modul, die beiden Testdateien und diese Dokumentation scheiterte an
`Permission denied` für `.git/index.lock`. Es ist nichts staged; ein Commit
konnte in dieser Sitzung nicht erstellt werden. Die vorbestehenden Änderungen
bleiben erhalten. Der entsprechende neue Schlussabsatz in
`clean-reset-progress.md` gehört ebenfalls zu diesem Teilabschluss; sein
vorbestehender Execution-Abschnitt gehört nicht zu diesem Commit.

`.local-tools/research-source-integrity.patch` enthält ausschließlich diesen
Entwicklungsblock einschließlich des einzelnen neuen Schlussabsatzes im
Arbeitsstand. Der Patch ist für einen späteren gezielten Commit vorbereitet;
die übrigen uncommitteten Änderungen sind darin nicht enthalten.
