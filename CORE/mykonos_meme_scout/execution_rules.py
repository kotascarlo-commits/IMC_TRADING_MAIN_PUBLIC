"""Pure economic guards shared by the executor and no-broadcast observations."""
from decimal import Decimal, InvalidOperation


def number(value):
    if isinstance(value, bool):
        raise ValueError('invalid_number')
    try:
        result = Decimal(str(value))
    except InvalidOperation:
        raise ValueError('invalid_number') from None
    if not result.is_finite():
        raise ValueError('invalid_number')
    return result


def quote_failures(quote, cfg):
    failures = []
    for field, limit in (('slippage_bps', 'max_slippage_bps'),
                         ('price_impact_bps', 'max_price_impact_bps'),
                         ('price_deviation_bps', 'max_price_deviation_bps')):
        if number(quote.get(field)) > number(cfg[limit]):
            failures.append(field)
    if number(quote.get('liquidity_usd')) < number(cfg['min_liquidity_usd']):
        failures.append('liquidity')
    return failures


def price_exit(invested, liquidation, peak, policy):
    pnl = liquidation-invested
    return ('stop_loss' if 100*pnl <= -policy['hard_stop_pct']*invested else
            'take_profit' if 100*pnl >= policy['take_profit_pct']*invested else
            'trailing_stop' if 100*peak >= (100+policy['trailing_arm_pct'])*invested
            and 100*liquidation <= (100-policy['trailing_stop_pct'])*peak else None)
