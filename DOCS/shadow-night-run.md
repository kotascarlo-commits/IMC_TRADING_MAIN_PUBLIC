# Begrenzter diagnostischer Nachtlauf

Start im bestehenden Supervisor (Windows PowerShell, Repository als Arbeitsverzeichnis):

```powershell
powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File .\LAUNCHERS\RUN_IMC_SIGNAL_ONLY.ps1 -ShadowDurationSeconds 28800 -ShadowDataOnly
```

Dieser Modus startet CORE, SAFE100-Watcher und Health im vorhandenen nativen
Prozess-Job. Er startet keinen Gateway, Bridge oder GMGN-Broker und fordert keine
Wallet-/Order-Zugangsdaten an. Automatische ML-Unterprozesse sind ausschliesslich
hier unterdrueckt; Normalbetrieb und strikte Prozessidentitaeten bleiben erhalten.
Das beseitigt keine bislang unbewiesene Ursache kurzzeitig unbekannter Prozesse.
Dauer maximal 12 Stunden, hier 8 Stunden; UTC-Ende
und monotone Laufzeit gelten auch bei Startverzoegerungen. Kein automatischer
Neustart nach Fehlern. Native Aufraeumung und Monitoring-Takt koennen das
publizierte Ende um wenige Sekunden ueberschreiten.

Der bestehende Ledger-Vertrag bleibt unveraendert und wird als
`source_execution_mode` ausgewiesen. Auch `THREE_TRADE_VALIDATION` kann Quelle
sein, sofern Broadcast, Signing und Live-Trading aus und Ownergate gueltig OFF
sind. Vertragsdateien bleiben identisch; Abweichungen beenden die Sitzung.
Maintenance-Sperren und deren bestehende Crash-Recovery bleiben verbindlich.

Nach 90 Sekunden Anlaufzeit blockieren fehlende, veraltete oder widerspruechliche
Health-Pflichtdaten und unbekannte/fremde Prozesse. Beobachtungen erfordern
fortschreitende Snapshot-IDs, frische Quellen, fehlerfreien Watcher und
vollstaendige Beobachtung aktuell gebundener SAFE100-Paare. Drei aufeinanderfolgende
schlechte Datenpruefungen (30-Sekunden-Takt) beenden den Lauf. Wachstum von CORE-,
Watcher- und Execution-DB einschliesslich WAL/SHM ueber 2 GiB oder weniger als
5 GiB freier Plattenplatz beendet ihn unabhaengig von Feedfehlern.
Nur fuer `-ShadowDataOnly` kann `-ShadowMaxStorageGrowthBytes` explizit ein
Budget bis maximal 6442450944 Bytes (6 GiB) waehlen; Standard bleibt 2 GiB.
Bei einer Verlaengerung mit Neustart wird die urspruengliche
`storage_baseline` als `-ShadowStorageBaselineBytes` uebergeben, damit das
Wachstum beider Abschnitte gemeinsam zaehlt. Eine Basis oberhalb des aktuellen
Speicherverbrauchs oder ein bereits ueberschrittenes Budget blockiert vor dem
Workerstart. Beide Werte stehen im Sitzungsstatus. Prozess- und Datenwachen
bleiben unveraendert; ein Sicherheitsstopp ist weiterhin moeglich.

Scannerfortschritt wird in diesem Modus durch `RUNTIME/shadow-scan-status.json`
gemessen: erfolgreicher abgeschlossener Scan, echte Worker-PID und native
Prozessgeburtszeit, maximal 30 Sekunden alt, fortschreitende Zyklusnummer.
Ein leerer Scan muss keine Decision erzeugen. Veraltete Decisions sind hier
eine Warnung; fehlende DB-/Schema-, Scan- oder Snapshot-Evidenz blockiert weiter.
Normal-/Live-Modi behalten ihre bisherige Decision-Frischepruefung.

SAFE100-Picks speichern ihr Eingangspaar. Folgepreise muessen dasselbe Paar
betreffen; historische ungebundene Picks werden `UNVERIFIED_PAIR`, ohne
nachtraeglich eine Marktidentitaet zu erraten. Marktbeobachtung erzeugt keine
Paper-Position oder Entry-Entscheidung. Security-Zeitstempel werden nicht erneuert.
Fehlende Holder-/Creator-Angaben bleiben unbekannt. Ausstehende HTTP-Arbeit ist
begrenzt; Timeouts geben deren Slot erst bei tatsaechlichem Ende wieder frei.

Status: `RUNTIME/signal-runtime-status.json`, `RUNTIME/IMC_SYSTEM_HEALTH.json`,
`RUNTIME/shadow-session-observation.json`, `RUNTIME/shadow-watch-status.json`.
Prozessanwesenheit weiter ueber `LAUNCHERS/STATUS_IMC.ps1` pruefen. Der normale
Stop verwendet `LAUNCHERS/STOP_IMC.ps1`; eigene Kinder enden auch beim nativen
Job-Close des Supervisors.

`imc t view` und `imc t status` zeigen jetzt diesen diagnostischen Lauf direkt
aus atomaren JSON-Dateien, ohne Python-Statusprozess, Dependency-Probe oder
Datenbankzugriff. Status und Beobachtungen werden getrennt auf Aktualitaet
geprueft; ein laufender Supervisor erfordert native PID und Prozessgeburtszeit.
Ein gueltiger RUNNING-Status bleibt unabhaengig von Statistik-Frische sichtbar:
STALE/UNKNOWN blendet unverifizierte Beobachtungen und abgeleitete Zahlen aus;
LOADING gilt nur waehrend der 90-Sekunden-Anlaufphase. Jeder Render prueft
Statusalter, Endzeit, Health- und Beobachtungsalter erneut, auch zwischen Polls.
Health wird nur mit frischer, an dieselbe Supervisor-Identitaet gebundener
Datei angezeigt. Fehlende/veraltete/widerspruechliche Quellen werden UNKNOWN;
die ausgewaehlte Diagnosequelle faellt dabei nicht auf die Reality-CLI zurueck.
STOPPED zeigt historische Beobachtungen und gespeicherte Gates, ohne daraus
aktuelles Cleanup abzuleiten. PnL/Trefferquote/Profit Factor bleiben N/A.
Die Diagnoseansicht ist read-only: status/refresh/exit. Start/Stop des anderen
Modus sind hier blockiert, auch ueber die CLI. Nach einem gespeicherten STOPPED
kann der Reality-Modus explizit mit `-Reality` ausgewaehlt werden; bei aktiver
oder unklarer Diagnose bleibt die Sperre bestehen. Die strenge Prozesswache
ist unveraendert; der Viewer darf waehrend des Statistiklaufs geoeffnet bleiben.
Die PowerShell-Funktion und der installierte `IMC.cmd` fuehren `imc t ...`
direkt zum absoluten Terminalpfad; der CMD-Dispatch erfolgt vor dem
Entwicklungslauncher, ohne neue Advisor-/GMGN-Broker oder Codex-Sitzung.
Direkte PowerShell-Aufrufe verwenden ebenfalls den absoluten Skriptpfad.
Relative Repo-Skriptaufrufe bleiben in der Prozesssicht absichtlich unbekannt.
Windows-CIM kann beim Ende kurzer Statusprozesse deren Datensatz noch ohne
Kommandozeile liefern. Ausschliesslich solche Eintraege mit gueltiger PID und
Geburtszeit werden nativ auf aktuelle Lebensdauer geprueft: OpenProcess-Fehler
87 bedeutet punktuelle OS-Abwesenheit; ein signalisiertes Handle erfordert
GetProcessTimes-Bindung unter 10 FILETIME-Ticks und bestaetigtes CloseHandle.
Lebende, unpruefbare oder wiederverwendete PIDs bleiben unbekannt und blockierend.
Die Entscheidung steht mit Snapshot-ID, Probezeit und Geburtsdaten in
`expired_process_evidence`; Unknown-Herkunftsmetadaten stehen daneben, ohne
rohe Kommandozeilen, Geheimnisse oder eine neue Rollenfreigabe.

Evidenzklasse ist ausschliesslich `DIAGNOSTIC_SHADOW_NOT_PERFORMANCE`.
`qualified_trading_ready` bleibt false. Fehlende qualifizierte Security-Daten,
echte Fill-/SELL-/Accounting-Evidenz und Strategieziele bleiben separate Arbeit.
Vor einem Nachtstart muss ein nativer 300-Sekunden-Lauf mit frischen Daten,
`DURATION_ELAPSED` und bestaetigtem Job-Cleanup bestanden sein.

Abnahme 2026-10-06: 389 Regressionen bestanden. Native Probe von
18:59:13 bis 19:04:16 UTC: Health HEALTHY, fortschreitende Snapshot-IDs,
Lernstart unterdrueckt, DURATION_ELAPSED, Exit 0 und danach leere Prozesssicht.
Die beiden geschuetzten Ausfuehrungsdateien blieben bytegleich.
In dieser Probe gab es keine aktuell gebundenen SAFE100-Watchtargets; der
positive Paar-/Settlement-Pfad ist isoliert getestet, noch kein nativer
Trade-Beweis. Die letzte Stichprobe hatte weiterhin keine Holder-Evidenz.
Details und der vorherige sicher abgebrochene Versuch bleiben lokal in
`RUNTIME/shadow-night-smoke.json` und `RUNTIME/test-runs/` erhalten.
