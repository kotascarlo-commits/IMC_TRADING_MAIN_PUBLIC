# GMGN-API: Befund und unabhängiger Quote-Zugang

Stand: 5. Oktober 2026. Keine Walletmigration, Signatur oder Echtgeldorder.

Fortsetzung: Der lokale Wallet-Read, ein Jupiter-Quote-Paper-Vertrag für
100 Abschlüsse und unsigned Build/Simulation sind implementiert. Die echte
Startprüfung blockiert beim vorhandenen Kapital und den Roundtrip-Kosten.
Details und Grenzen: [Jupiter-Forward-Readiness](jupiter-forward-readiness.md).
Dies ist keine Freigabe für 100 On-Chain-Trades oder autonomes Echtgeld.

## Entscheidung

Ein Walletwechsel innerhalb derselben GMGN-Anbindung behebt die belegte
Provider-Sperre nicht. IMC sollte Marktbeobachtung und Trade-Ausführung
entkoppeln. GMGN bleibt für funktionierende Lesezugriffe erhalten; für einen
künftigen eigenen Solana-Ausführungspfad ist Jupiter v2 der erste unabhängig
geprüfte Quote-Anbieter. Eine eigene dedizierte Trading-Wallet mit lokalem
Signer und unabhängigem RPC ist das Zielbild, aber noch nicht implementiert
oder freigegeben. Es ist kein Guthaben zu verschieben, um Quotes zu prüfen.

Die minimale, jetzt implementierte Änderung ist ein isolierter Diagnoseweg.
Er ist **kein automatischer Fallback im laufenden IMC-Executor**.

## Nachweis

- Bestehende GMGN-Evidence: 2026-10-05 15:40:20 UTC,
  `GET /v1/trade/quote`, HTTP/API 429, `RATE_LIMIT_EXCEEDED`, nach abgelaufener
  Sperre und Einzelrequest. Portfolio-Read bestand.
- [GMGN dokumentiert](https://github.com/GMGNAI/gmgn-skills/blob/main/skills/gmgn-swap/SKILL.md#rate-limit-handling)
  Free-Kapazität 5 und Quotegewicht 10. Unter diesen Regeln genügt die volle
  Free-Kapazität nicht für einen Quote. **Der Tarif und die konkrete Ursache
  dieses Kontos sind weiterhin unbekannt.** Kein Tarifkauf oder Supportkontakt
  wurde ausgeführt.
- Genau eine keyless Jupiter-v2-Probe: 2026-10-05 16:17:27 UTC, SOL → USDC,
  10.000.000 Lamports, 50 bps Slippage. Valider Quote, 4 Routenschritte,
  0,390 s gemessene Antwortzeit; Output 1.192.106 USDC-Basiseinheiten,
  Minimum 1.186.145. Das sind Preisbeobachtungen, keine Fills oder Gewinne.
- Quelle: `.local-tools/gmgn-api-solution-20261005/jupiter-quote.json`.
  Diese Probe lief vor der abschließenden zusätzlichen Query-Feldvalidierung;
  danach nur Offline-Tests, kein weiterer Providerrequest.

## Verwendung

Vom Repository aus mit dem bestehenden Interpreter:

```powershell
.venv/Scripts/python.exe TOOLS/imc_quote_diagnostic.py
```

Optional `--input-mint`, `--output-mint`, `--amount` in Basiseinheiten und
`--slippage-bps` zwischen 0 und 150. Der Default ist die oben beschriebene
SOL/USDC-Leseprobe. Ein Aufruf bedeutet genau einen GET-Versuch. Keine Schleife
starten; keyless ist laut [Jupiter](https://developers.jup.ag/docs/portal/rate-limits)
auf 0,5 Requests/s beziehungsweise 30/min begrenzt.

Der Endpunkt ist fest `https://api.jup.ag/swap/v2/order`. `taker`, `receiver`,
`payer`, API-Schlüssel und private Schlüssel werden nicht verwendet. Laut
[Endpunktvertrag](https://developers.jup.ag/docs/api-reference/swap/order)
liefert ein Request ohne `taker` einen Quote ohne Transaktion. Der Adapter
prüft Mint-/Amount-/Slippagebindung, Output-Schwelle und Route. Redirects,
Transaktionsinhalt, Providerfehler bei HTTP 200, übergroße Antworten und
mehrdeutige JSON-Felder werden abgewiesen. Die CLI begrenzt den gesamten
Worker einschließlich DNS auf 12 Sekunden. Keine automatischen Retries.
Nur der normale CLI-Aufruf besitzt diese äußere Prozessgrenze; direkte
`fetch()`- und interne `--worker`-Aufrufe sind keine unterstützten Launcher.

Der aktuelle [Portal-Setup-Vertrag](https://developers.jup.ag/docs/portal/setup)
erlaubt keyless Requests; die Endpunktreferenz bezeichnet den API-Key trotzdem
als erforderlich. Die erfolgreiche Einzelprobe belegt den hier beobachteten
Zugang, keine dauerhaft garantierte Verfügbarkeit oder Produktionskapazität.

`priceImpact` in Jupiter v2 verwendet Prozentpunkte und wird mit Faktor 100
in Basispunkte umgerechnet. Das veraltete `priceImpactPct` wird nicht als
Ersatz genutzt. Fehlende Liquidität und gesamte Ausführungskosten bleiben
`null`; `market_data_readiness=UNKNOWN`, `execution_eligible=false`.

## Grenzen und nächster produktiver Schritt

Der bestehende Executor verlangt `GMGN_ORDER_QUOTE`. Jupiter-Quotes werden
deshalb weder umetikettiert noch in GMGN-Broker, Ledger, Health oder Readiness
eingespeist. Quote-Diagnose und produktive Providerumstellung sind getrennte
Arbeiten; die GMGN-Quote-Sperre selbst ist nicht behoben.

Vor einem eigenen produktiven Pfad müssen Quote, konkrete Route, frische
Pool-/Liquiditätsevidence, sämtliche Gebühren, kontrollierter Build,
Simulation, Exit und Recovery mit demselben Provider-/Transaktionsvertrag
abgenommen werden. Ein Jupiter-Quote allein zertifiziert weder diese Kette
noch die Handelbarkeit aller IMC-Kandidaten. Eine Wallet ersetzt diese
Prüfungen nicht. Signer und Broadcast bleiben bis zur gesonderten Owner-Live-
Autorisierung deaktiviert; der Diagnoseadapter enthält keinen Send-Pfad.

## Review und Validierung

Astra High RiskReview vor Umsetzung: **OK** für den isolierten Diagnoseweg,
mit verbindlicher Trennung von GMGN-Ausführungsevidence und unbekannten
Risiko-/Kostenfeldern. Relevante Tests prüfen Providerfehler, Redirects,
Mint-/Amountbindung, Schwellen, Impact-Einheiten, Stream-/JSON-Grenzen,
Query-Feldgrenzen und den gesamten Worker-Timeout.

Abschließender Astra High RiskReview nach erfolgreicher Probe: **OK**, kein
konkreter CHANGE-Bedarf im isolierten Tool. Produktive Migration bleibt offen.
Abschließender Testlauf: **142 PASS / 0 FAIL / 0 SKIP**, 33,24 s, umfasst
`test_quote_diagnostic.py`, `test_gmgn_read_evidence.py`,
`test_signed_quote_probe.py` und `test_gmgn_read_cli_bootstrap.py`.
Zuvor 107 PASS und 35 Fixture-Setup-Fehler am bestehenden Windows-Temp-ACL;
mit einem frischen lokalen `--basetemp` vollständig behoben. Keine
Produktlogik oder Zugriffsrechte dafür verändert.

Die SHA256-Werte von `config/owner-broadcast-gate.json`,
`config/imc-execution.json` und
`RUNTIME/EXECUTOR_GATEWAY/LIVE_V490/config_v490_live.json` stimmen vor/nach
der Arbeit überein. Vorbestehende Protocol-/Storage-Änderungen wurden nicht
bearbeitet oder als Bestandteil dieses Arbeitspakets übernommen.
