# EUR reality run: implementation and acceptance

Target: `READY_FOR_1000_EUR_REALITY_RUN`.

Current result: **BLOCKED**. The terminal, EUR ledger and isolated lifecycle
are implemented. A qualifying current-market BUY/SELL lifecycle is not yet
proved. Neither a zero-trade control test nor an isolated fixture proves
forward performance or authorizes a live trade.

## Commands

Current-user PowerShell profiles load `TOOLS/IMC_T_Command.ps1`; installation
is idempotent through `TOOLS/Install-IMC-Terminal.ps1`. The existing IMC work
command remains available. A new normal PowerShell session loads the command.

```powershell
imc t view
imc t start 30m
imc t start 1h
imc t start 8h
imc t stop
imc t status
```

`view` opens a new native PowerShell console. `start` inside the view prompts
for duration; `start 2h` accepts it directly. `exit` closes only the view.
The single hidden supervisor and owned Python job remain independent of it.
The view reads persisted state every five seconds and redraws the countdown
every second without waiting for input. It has the requested IMC header and
one muted orange accent. No browser, dashboard service or extra watcher starts.

Valid duration is a positive integer followed by lowercase `m` or `h`, from
one minute to 31 days. There is no overnight schedule or fixed clock deadline.
Expiry or manual stop prevents entries, enters FINISHING and reconciles the
existing position. An unprovable exit remains BLOCKED, not fictitiously CLOSED.
The finishing evidence deadline is 60 seconds; native cleanup follows the
ledger operation. A verified open position recovered on launch is exited in
its original run before another run can start. Unknown claims are never resent.

## Contracts

Each new run has EUR 1,000.00 virtual cash and a frozen policy. Sizing is
10% of current verified net equity, capped by free cash/reserve and stricter
existing risk limits. The existing execution configuration still limits
admissions to three trades and session losses to 10,000,000 lamports converted
using observed SOL/EUR. Hitting the admission limit stops entries, not the
user-selected runtime. No cap is silently loosened.

The claim binds the frozen network-fee ceiling. Receipt/mark acceptance applies
that ceiling as well as the absolute ceiling. Modeled BUY and SELL acceptance
must leave the claimed reserve after execution fees/account costs. Exit hold
times and price thresholds come from the frozen run policy, not mutable module
defaults.

`RUNTIME/reality-run.db` is the run's source of truth. It stores frozen
run policy, duration/deadline, heartbeat, audited equity points, claims, route
and unsigned-message evidence, modeled receipts, rest quantities, cost/PnL
decomposition, recovery and cleanup. SQLite transactions and claim uniqueness
prevent a second run, second position or duplicate leg. Current cash/equity,
closed arithmetic, net marks and historical drawdown points are checked
against persisted evidence before projection, claim sizing and fill acceptance.

The dispatcher uses the existing scanner, candidate snapshots and causal
entry brain. Economic quote checks and price exits are shared with the
canonical execution modules. It uses the canonical execution state graph,
Jupiter instruction/route validator, unsigned builder, compute measurement
and same-bank cost verifier. It deliberately uses a separate virtual EUR
ledger, not the live THREE_TRADE_VALIDATION wallet ledger. The actual owner,
live, signing and broadcast gates must remain OFF; the existing kill switch
is observed read-only. No `imc t` command writes those gates or has a sender.

After the configured execution delay, a fresh quote and transaction are built;
the earlier quote is drift evidence only. A successful same-size unsigned
simulation, exact message/account identity, fee proof, route/risk and freshness
are required for a modeled receipt. Output uses the later route's minimum,
not the perfect quote output. BUY acceptance debits virtual cash and creates
a verified rest position atomically. SELL uses exactly that rest quantity,
with a separate claim and fresh transaction. ACK never opens a position.

Net liquidation marks include both execution loss and exit fees; those net
marks drive stops, trailing peak and equity drawdown. An initial valuation
is pending until an exit mark is verified. A missing valuation is UNKNOWN;
gaps invalidate complete maximum-drawdown reporting. UNKNOWN trades cannot
enter normal performance statistics or be retried automatically.

FX comes from credential-free Kraken SOL/EUR and EUR/USD bid/ask observations,
with HTTP observation times and a checked server clock. This timestamp is
explicitly not a last-trade timestamp. There is no invented USDC/USD peg.

Gross PnL is the change in independently observed mid value of the acquired
quantity. Execution cost is the input/output shortfall against those mids;
it already includes embedded route fees, price impact, FX spread, drift and
slippage. Network fees and net bound-account rent are separate. Slippage is
shown as an included subset and is not deducted twice. Separately attributable
price-impact EUR is UNKNOWN, never an assumed zero. Net PnL reconciles to
final cash after all verified components.

Metrics include all qualifying CLOSED/reconciled trades: net wins/losses,
win rate, gross/net PF, EUR/% PnL, maximum drawdown from the recorded equity
curve, best/worst trade and Top-3 net profit contribution/share/PF without
Top-3. Zero denominators produce N/A; open/unknown trades are not realized wins.
The chronological history includes mint, entry/exit times/prices, stake,
gross/net percentages, all costs, net EUR and exit reason.

## Remaining route/transport blockers

The production route verifier currently permits only legacy, fully decoded
direct SOL/USDC Whirlpool/Raydium CLMM routes. It does not permit arbitrary
meme tokens. Many scanner candidates therefore fail closed at the route gate.
The current mainnet unsigned simulation also uses the real payer balances and
SELL ATA: virtual EUR 100 input cannot be funded by that virtual ledger in a
mainnet bank; a future virtual BUY does not create a real SELL token account.
Smaller-size extrapolation, fake SELL balances and failed compute consumption
are not accepted as full-size cost proof.

An exploratory native `solders==0.29.0` LiteSVM probe simulated a current
SOL/USDC Jupiter BUY at EUR 100 virtual size. It is **not integrated** into
the runtime, not a chain receipt and not qualified performance evidence.
Read-only Feature-program enumeration found 309 active on-chain features,
including 66 IDs outside the library's known feature set and seven activated
after its bundled mainnet snapshot. Some older features may be implemented
unconditionally; the seven newer features still lack a semantic/parity proof.
Setting feature IDs alone does not implement missing runtime semantics.
The current npm LiteSVM package also has no published Windows native target.
No native-engine rebuild, local validator or extra permanent service was started.

Sources: [LiteSVM API](https://heavey.dev/solders/api_reference/litesvm.html),
[Solders 0.29.0 package](https://pypi.org/project/solders/0.29.0/),
[Solana account enumeration](https://solana.com/docs/rpc/http/getprogramaccounts),
[npm package metadata](https://registry.npmjs.org/litesvm).

Readiness needs a verified virtual-capital transport with current runtime
feature semantics, explicit shadow-only proof domains, standard mint/decimal
and fresh independent-price handling for supported strategy routes, and a
current-market BUY → POSITION → separate SELL → CLOSED → restart proof.
Canonical live route permissions must remain unchanged by that work.

## Acceptance evidence

- Relevant execution, Jupiter, paper-forward, launcher, maintenance, supervisor
  and reality tests: `RUNTIME/reality-final-tests.xml` (exact current totals).
- Native controls: `RUNTIME/reality-native-controls.json`; direct starts
  15m/30m/1h/8h, duplicate prevention, real new console/header, timed refresh,
  view exit/reopen, independent runtime, interactive duration prompt/stop,
  persistence and owned process cleanup.
- Real-clock native automatic end: `RUNTIME/reality-native-auto-end.json`;
  one-minute user-selected duration, FINISHING/COMPLETED, remaining zero and
  native cleanup. No qualifying performance trade is claimed by that test.
- Current funding/unsigned cost observation: `RUNTIME/reality-route-readiness.json`.
- Exploratory bank/feature observations: `RUNTIME/reality-bank-probe.json`
  and `RUNTIME/reality-bank-feature-readiness.json`.
- Final source-bound acceptance and gate/ledger audit:
  `RUNTIME/reality-acceptance.json`.

All these are local runtime evidence, not committed configuration or the
terminal's source of truth. Full isolated lifecycle/restart tests are named
as isolated evidence. The final report must retain BLOCKED while the route,
virtual-bank and separate current-market SELL evidence is missing.
