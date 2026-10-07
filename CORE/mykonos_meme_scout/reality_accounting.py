"""EUR observation accounting. Unknown evidence is never a zero-cost win.

Execution loss is measured against a contemporaneous independent mid price.
Quote fees, impact and drift are already embedded in that loss. Slippage is
its identifiable subset, not an additional charge. Rent and network fees are
separate cash debits. These are modeled observations, never chain receipts.
"""
from decimal import Decimal, ROUND_DOWN
import re

from .execution_rules import number as D

ZERO = Decimal(0)


def duration(value):
    match = re.fullmatch(r'([1-9][0-9]{0,5})([mh])', value or '')
    if not match:
        raise ValueError('duration_positive_minutes_or_hours_required')
    seconds = int(match[1])*(60 if match[2] == 'm' else 3600)
    if seconds > 31*86400:
        raise ValueError('duration_exceeds_31_days')
    return seconds


def size(equity, cash, fraction, reserve):
    result = min(D(equity)*min(D('.10'), D(fraction)), D(cash)-D(reserve))
    if result <= 0 or not 0 < D(fraction) <= 1:
        raise ValueError('capital_or_fraction_unknown')
    return result.quantize(Decimal('.000001'), rounding=ROUND_DOWN)


def pf(values):
    profits = sum((v for v in values if v > 0), ZERO)
    losses = -sum((v for v in values if v < 0), ZERO)
    return None if losses == 0 else str(profits/losses)


def metrics(trades, curve, *, valuation_complete=True):
    # Callers must provide ALL closed reconciled modeled observations.
    closed = [t for t in trades if t['execution_state'] == 'CLOSED'
              and t.get('reconciled') is True and t.get('performance_qualified') is True]
    gross = [D(t['gross_eur']) for t in closed]
    net = [D(t['net_eur']) for t in closed]
    costs = [D(t['costs_eur']) for t in closed]
    wins, losses = sum(v > 0 for v in net), sum(v < 0 for v in net)
    positives = sorted((v for v in net if v > 0), reverse=True)
    top = sum(positives[:3], ZERO)
    total_profit = sum(positives, ZERO)
    ranked = sorted((t for t in closed if D(t['net_eur']) > 0),
                    key=lambda t: (-D(t['net_eur']), t['trade_id']))[:3]
    top_ids = {t['trade_id'] for t in ranked}
    peak, dd, dd_pct = Decimal(1000), ZERO, ZERO
    complete = valuation_complete
    for point in curve:
        if point['equity_eur'] is None:
            complete = False
            continue
        equity = D(point['equity_eur'])
        peak = max(peak, equity)
        drawdown = max(ZERO, peak-equity)
        dd = max(dd, drawdown)
        dd_pct = max(dd_pct, 100*drawdown/peak)
    return {'closed': len(closed), 'wins': wins, 'losses': losses,
        'win_rate': str(Decimal(wins)/len(closed)) if closed else None,
        'gross_pnl_eur': str(sum(gross, ZERO)), 'net_pnl_eur': str(sum(net, ZERO)),
        'gross_pnl_pct': str(sum(gross, ZERO)/10), 'net_pnl_pct': str(sum(net, ZERO)/10),
        'gross_pf': pf(gross), 'net_pf': pf(net), 'costs_eur': str(sum(costs, ZERO)),
        'network_fees_eur': str(sum((D(t['network_eur']) for t in closed), ZERO)),
        'execution_costs_eur': str(sum((D(t['execution_eur']) for t in closed), ZERO)),
        'slippage_eur': str(sum((D(t['slippage_eur']) for t in closed), ZERO)),
        'price_impact_eur': None, # Cannot separate AMM fees/impact/drift honestly.
        'other_verified_costs_eur': str(sum((D(t['other_eur']) for t in closed), ZERO)),
        'peak_equity_eur': str(peak), 'max_drawdown_eur': str(dd) if complete else None,
        'max_drawdown_pct': str(dd_pct) if complete else None,
        'observed_max_drawdown_eur': str(dd), 'equity_curve_complete': complete,
        'top3_contribution_eur': str(top),
        'top3_profit_share': str(top/total_profit) if total_profit else None,
        'net_pf_without_top3': pf([D(t['net_eur']) for t in closed if t['trade_id'] not in top_ids]),
        'best_trade': max(closed, key=lambda t: D(t['net_eur']))['trade_id'] if closed else None,
        'worst_trade': min(closed, key=lambda t: D(t['net_eur']))['trade_id'] if closed else None}
