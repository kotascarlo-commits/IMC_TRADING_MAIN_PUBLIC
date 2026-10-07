# 0001 — Aufräumen, Git-Einführung, Second-Brain-Vault (01.10.2026)

## Kontext

Drei vorherige Audits (Systemaudit, Verbesserungsplan, Cross-Check gegen
ein unabhängig erstelltes ChatGPT-Audit) haben den Projektzustand
vollständig mit Code- und Datenbank-Evidenz verifiziert. Dieses Dokument
hält die daraus abgeleiteten Entscheidungen fest.

## Entscheidungen

1. **Git eingeführt.** Vorher keine Versionierung — `DOCS/CLEANUP_MANIFEST.json`
   zeigt 62 frühere, aufgegebene Projektstandorte als Beleg für das
   Muster "neu an anderer Stelle bauen" statt zu versionieren. Ab jetzt:
   jede Arbeitssitzung endet mit einem Commit, keine neuen `vNNN`-Dateien.
2. **Vier Löschungen, alle git-abgesichert** (wiederherstellbar über
   `git log`/`git show`):
   - `exploration_engine.py`, `learning_quality.py`, `research_factory.py`,
     `x_context.py` — null Referenzen irgendwo im Projekt, projektweit
     per `grep` verifiziert.
   - `RESEARCH/ARCHIVE_DISABLED/*.disabled` — vom Projekt selbst bereits
     als tot markiert.
   - 8 alte PID-Logs in `CORE/logs/` (~67 MB) — Logs längst beendeter
     Prozesse, nie rotiert.
   - `real_market_execution_v480_build_parity.py.pre_live_v490.bak` und
     `TEMP_LOOSE_ONE_SIGNAL.ps1` — Backup bzw. ein riskantes
     Live-Code-Patch-Muster, beide mit Git überflüssig.
3. **`brain_v1/` bewusst NICHT gelöscht.** Projektweite Codesuche findet
   null Referenzen außerhalb des eigenen Ordners — es ist aktuell nicht
   mit dem laufenden Entscheidungspfad (`engine.py`/`cli.py`) verbunden.
   Da unklar ist, ob es ein abgeschlossenes Experiment oder ein geplanter
   Nachfolger ist, bleibt es unangetastet und ist hier nur als
   "nicht verdrahtet" dokumentiert (siehe [[../01_architecture]]). Vor
   einer Löschung: kurze Rückfrage an den Projekteigner.
4. **Bestehende Config-JSONs NICHT nach `config/` verschoben.** 18 aktive
   Dateien referenzieren sie unter ihrem heutigen Pfad, darunter der
   real validierte Signier-/Broadcast-Pfad. Ein Massen-Umzug mit Anpassung
   aller 18 Referenzen hätte ein hohes Risiko, genau den wertvollsten Teil
   des Systems zu beschädigen, ohne einen entsprechenden Nutzen (die
   Dateien hatten bereits eindeutige Namen). Stattdessen: `config/README.md`
   und [[../02_configuration]] dokumentieren, wo jede Einstellung lebt.
5. **Erste Testsuite angelegt** (`tests/`, 16 Fälle) für die drei
   sicherheitsrelevantesten Pfade: Config-Validierung, Scoring-Hard-Gates,
   Risk-Kill-Switch. Alle 16 liefen grün (verifiziert, nicht nur geschrieben).
6. **`docs/`-Vault + `AGENTS.md`/`CLAUDE.md` angelegt** als gemeinsame
   Wissensbasis für Menschen (Obsidian-kompatibel) und KI-Agenten
   (Codex/ChatGPT und Claude Code lesen dieselben Regeln).

## Verifizierter Live-Status zum Zeitpunkt dieser Entscheidung

Am 01.10.2026 liefen 0 Python-Prozesse (CORE offline seit 30.09., 14:36
UTC) und 3 Node-Prozesse ohne aktive Signalquelle. `RUNTIME/IMC_SYSTEM_HEALTH.json`
selbst war zu diesem Zeitpunkt bereits 5 Tage alt, obwohl das System in
der Zwischenzeit weiterlief — ein konkretes Beispiel dafür, warum
Status-JSONs allein nicht als Beweis für "Prozess läuft" gelten dürfen
(siehe [[../05_operations]]).

## DB-Retention — Dry-Run-Ergebnis (nicht ausgeführt)

`scripts/db_retention.py --days 30` (Dry-Run, 01.10.2026) ergab: **0%**
der Zeilen in allen fünf großen Tabellen sind älter als 30 Tage —
`snapshots`, `decisions`, `learning_predictions`, `learning_outcomes`
und `cookie_ledger` enthalten ausschließlich Daten der letzten ~4 Wochen.

**Das bedeutet: eine zeitbasierte Retention-Policy löst das aktuelle
32-GB-Problem nicht.** Die Datenbank ist nicht groß, weil alte Daten
nie gelöscht wurden — sie ist groß, weil das tatsächliche Scan-Intervall
0.5 Sekunden beträgt (siehe `docs/02_configuration.md`) und das Projekt
erst seit ca. 26 Tagen läuft. `--execute` wurde deshalb bewusst **nicht**
ausgeführt — es hätte ohnehin null Zeilen betroffen.

Das Skript bleibt sinnvoll für die Zukunft (sobald Daten über das
gewählte Zeitfenster hinaus altern, greift es automatisch), löst aber
nicht das Ist-Problem. Echte Optionen, die der Projekteigner bewusst
entscheiden muss:

1. Scan-Intervall erhöhen (z. B. zurück auf die Config-Default-15s oder
   einen Mittelweg) — reduziert zukünftiges Wachstum drastisch, ist aber
   eine Research-/Strategie-Entscheidung, keine reine Infra-Änderung.
2. Ein kürzeres Retention-Fenster wählen (z. B. 7 Tage) und es erst in
   ein paar Wochen wirksam werden lassen, wenn genug Daten gealtert sind.
3. Die Größe akzeptieren und stattdessen auf schnellere Disks/mehr
   Speicher setzen, falls das Lerntraining die volle Auflösung braucht.

## Cross-Check

Ein unabhängig erstelltes zweites Audit desselben Projekts (ChatGPT)
wurde stichprobenhaft gegen Code und Live-Datenbanken nachgerechnet.
Geprüfte Beispiele, alle exakt bestätigt: SAFE100-Statistik (119 Closed,
PF 1.127, +$275.34), PASS/WATCH-Verteilung der letzten 100.000
Entscheidungen (191/99.809), das tatsächliche 0.5s-Scan-Intervall,
der wörtliche GMGN-Fehler `ConnectionResetError [WinError 10054]`,
die Ablehnung von Authority-Eskalation in `brain_v1/safety.py`.
