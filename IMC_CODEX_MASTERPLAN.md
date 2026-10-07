# IMC CODEX MASTERPLAN — CLEAN RESET

## Ziel
IMC wird ab jetzt wie ein neu aufgesetztes Produkt behandelt: eine aktive Codebasis, eine klare Pipeline, eine Datenwahrheit und eine reproduzierbare Arbeitsweise.

Das bestehende Wissen der letzten Monate bleibt erhalten, aber nur in komprimierter, nachweislich nuetzlicher Form. Alte Klone, tote Experimente, redundante Rohdaten und ungenutzte Runtime-Artefakte werden entfernt.

## Harte Regeln
1. Vor jeder Loeschung pruefen: aktive Referenzen, Prozesse, Imports, Launcher, Datenabhaengigkeiten und Git-Historie.
2. Was durch Git sicher wiederherstellbar ist und nachweislich unbenutzt ist, darf entfernt werden.
3. Keine aktiven Live-/Broadcast-Flags ohne ausdrueckliche Freigabe des Owners veraendern.
4. Keine neue vNNN-Datei fuer normale Weiterentwicklung. Git Commit/Branch/Tag statt Dateiklon.
5. Keine Runtime-String-Replacement-Patches.
6. Keine Performance-Zahl ohne konkrete Datenquelle.
7. Synthetische/Replay-Ergebnisse niemals als Forward-/Live-Beweis darstellen.
8. Vor destruktiver Datenbankarbeit CORE und DB-Schreiber kontrolliert stoppen.
9. Nach jeder Phase Tests ausfuehren und einen kleinen, klaren Git-Commit erstellen.
10. Ziel ist weniger Komplexitaet, nicht mehr Features.

## Phase 0 — Aktuellen Stand sichern
- Git-Status, Prozesse, Ports und aktive Launcher erfassen.
- Aktuellen Stand taggen/committen, ohne Runtime-State versehentlich als Code zu versionieren.
- Aktive Pipeline end-to-end dokumentieren: Discovery -> Security -> Scoring -> SAFE100 -> Execution Twin -> Gateway -> Bridge/Executor.
- Fuer jede Komponente markieren: ACTIVE, OPTIONAL, LEGACY oder DEAD.
## Phase 1 — Projekt radikal entruempeln
- Alle vNNN-Klone, .bak/.old/.disabled-Dateien, alte Launcher, Test-Hotfixes, doppelte Dashboards und verwaiste Module inventarisieren.
- Mit Imports, Textreferenzen, Prozesslisten und Startskripten beweisen, was wirklich unbenutzt ist.
- DEAD entfernen. LEGACY nur behalten, wenn es einen belegbaren Zweck hat.
- Alte Logs und generierte Artefakte aus dem Repo entfernen und sauber ignorieren.
- Zielbild: ein Hauptpfad, ein Start, ein Stop, ein Status, eine Health-Quelle.

## Phase 2 — 37-GB-Datenproblem dauerhaft loesen
- SQLite read-only analysieren: Dateigroesse, Tabellen, Zeilenzahlen, Wachstum pro Stunde/Tag und groesste Datenklassen.
- Nicht blind nach Alter loeschen. Zuerst bestimmen, welche Tabellen fuer Trading, Lernen, Audit und Reproduzierbarkeit wirklich gebraucht werden.
- Daten in drei Klassen teilen:
  A) KEEP: Trades, Outcomes, relevante Features, Labels, Modell-Checkpoints, Auditdaten.
  B) COMPACT: Hochfrequente Snapshots auf Ereignisse/Deltas/Aggregate reduzieren.
  C) DELETE: redundante identische Beobachtungen, tote Research-Lanes, abgelaufene Temp-/Debug-Daten und nicht referenzierte Artefakte.
- Einen kompakten Learning-Datensatz erzeugen, der pro relevantem Ereignis Features + Entscheidung + spaeteres Outcome + Quelle + Zeit enthaelt.
- Rohdaten nur so lange halten, wie sie fuer Debug/Training nachweislich gebraucht werden.
- Automatische Retention/Compaction bauen, sodass die DB nie wieder unbegrenzt waechst.
- Vorher/Nachher dokumentieren: GB, Tabellen, Rows und was bewusst erhalten wurde.

## Phase 3 — Lerner sauber machen
- Lerner darf nur ACTIVE-Datenquellen und valide Outcomes verwenden.
- Keine stale/dead Watcher als Lerninput.
- Laufender Modellzustand gehoert in Runtime-State, nicht als staendig veraenderte Git-Datei.
- Versionierte Checkpoints nur bewusst promoten.
- Trainingsdaten muessen nachvollziehbar und reproduzierbar sein.

## Phase 4 — Entwicklungsumgebung vereinheitlichen
- Eine Python-Umgebung (.venv) und eine reproduzierbare Dependency-Datei.
- Alle Launcher auf denselben Interpreter umstellen.
- Einen Prozess-Supervisor/saubere PID-Verwaltung statt lose Mehrfachprozesse.
- Health mode-aware machen: nur Komponenten warnen, die im aktuellen Betriebsmodus laufen sollen.
- Sicherstellen, dass Dashboard/Gateway nie mehrfach unkontrolliert gestartet werden.
## Phase 5 — Erst danach Strategie verbessern
Zielmetriken bleiben:
- Trefferquote >= 70 %
- Profit Factor >= 2 nach realistischen Kosten
- Max Drawdown <= 15 %
- Top-3 Gewinner <= 25 % des Gesamtprofits

Prioritaet: echte Forward-Evidenz. Replay/Simulation dient Forschung, nicht Erfolgsbeweis.

SAFE100 ist aktuell der beste belegte Forward-Zweig, aber PF ist noch unter Ziel. Erst nach Cleanup und Datenbereinigung Ursachen analysieren: Entries, Exits, Kosten, Timing, Liquiditaet, False Positives und Regime.

## Phase 6 — Execution
Erst wenn Strategie und Datenpfad sauber sind:
- den echten Signalpfad bis zum Executor end-to-end tracen;
- keine Annahmen aus Config-Flags ableiten;
- Quote -> Build -> Risk Checks -> Executor -> Fill -> Exit -> Accounting muss technisch nachweisbar geschlossen sein;
- danach kleine kontrollierte End-to-End-Tests.

## Arbeitsweise fuer Codex
Du bist der technische Hauptagent dieses Projekts.
Arbeite autonom, aber destruktive/irreversible Schritte nur nach sauberem Nachweis und mit Wiederherstellungsweg.
Bei Architekturentscheidungen, Datenverlust-Risiko, komplexem Debugging oder widerspruechlichen Befunden soll ein Astra-Advisor/Subagent die Entscheidung unabhaengig pruefen.
Nicht gleichzeitig mehrere Varianten bauen. Waehle nach Evidenz einen Weg und vereinfache.

## Definition of Done fuer den Clean Reset
- nur eine aktive Hauptpipeline;
- keine sinnlosen Code-Klone;
- DB deutlich reduziert und Wachstum begrenzt;
- Lerner nutzt nur relevante, valide Daten;
- Runtime-State von Git getrennt;
- eine Python-Umgebung;
- ein Start/Stop/Status/Health-Weg;
- Tests gruen;
- klare Performance-Truth;
- Git sauber und jede Phase nachvollziehbar committed.

Beginne mit Phase 0 und Phase 1. Fuehre noch keine Strategie-Aenderung und keine Live-Aktivierung durch, bevor der Clean Reset technisch abgeschlossen und verifiziert ist.
