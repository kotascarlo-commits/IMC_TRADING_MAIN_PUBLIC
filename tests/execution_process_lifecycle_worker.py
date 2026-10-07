"""One abrupt process boundary for the existing controlled venue; never live."""
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
from test_execution import flow
import test_execution_adapter as fixtures
from test_execution_adapter import ready
from test_execution_lifecycle_e2e import ControlledVenue, TOKEN
from execution_adapter import SingleSendAdapter, SOL
from execution_positions import PositionManager
from execution_state import Ledger, canonical

CHECKPOINTS = ('BUY_ACK', 'BUY_FILL', 'POSITION_OPEN', 'SELL_ACK', 'SELL_FILL')


def fixture_now():
    now = datetime.now(timezone.utc)
    return now.replace(microsecond=now.microsecond // 1000 * 1000)


def crash_at(root, target):
    assert target in CHECKPOINTS and root.name == 'process-flow'
    assert not (root / 'checkpoint.json').exists()
    f = flow.__wrapped__(root)
    f.now = fixture_now()
    f.store = Ledger(root / 'process-lifecycle.db', dict(f.store.cfg), clock=f.clock)
    f.store.bootstrap(f.clock())
    assert f.store.reconcile({'wallet': SOL, 'observed_at': f.clock(),
        'chain_positions': {}, 'executor_positions': {}, 'orders': {}, 'balance_lamports': '1000000000'})
    patch = pytest.MonkeyPatch()
    candidate, quote, equity = f.candidate, f.quote, fixtures.observe
    patch.setattr(f, 'candidate', lambda **kw: candidate(**({'mint': TOKEN} | kw)))
    patch.setattr(f, 'quote', lambda event, **kw: dict(quote(event, **kw), wallet=SOL))
    patch.setattr(fixtures, 'observe', lambda item: dict(equity(item), wallet=SOL))
    buy, buy_quote, risk, probes = ready(f, root, patch)
    venue = ControlledVenue(f, root / 'controlled-venue.json')
    adapter = SingleSendAdapter(risk, probes, contract_transport=venue.send)
    sell = None

    def checkpoint(name):
        if name != target:
            return
        with f.store.transaction() as c:
            journal_mode = c.execute('PRAGMA journal_mode').fetchone()[0]
            intent = f.store.meta(c, 'position_exit:' + buy['trade_id'])
        # Provider state comes from the venue, never from ledger quantities.
        snapshot = {'checkpoint': name, 'clock': f.clock(), 'buy': buy, 'buy_quote': buy_quote,
            'sell': sell, 'exit_intent': intent, 'journal_mode': journal_mode,
            'ledger_before': f.store.status(), 'provider': {key: getattr(venue, key) for key in
                ('transactions', 'wallet', 'mint', 'balance', 'quantity', 'slot', 'send_calls', 'sdk_calls')}}
        path = root / 'checkpoint.json'
        with path.open('x', encoding='utf-8') as stream:
            stream.write(canonical(snapshot)); stream.flush(); os.fsync(stream.fileno())
        with venue.path.open('r+b') as stream:
            stream.flush(); os.fsync(stream.fileno())
        os._exit(91)  # no fixture cleanup, Ledger recovery, or orderly shutdown

    def ledger_fill(event):
        venue.finalize(event)
        observation = venue.reader.snapshot(f.store)
        fill = next(item for item in observation['recovered_fills'] if item['data']['event_id'] == event['event_id'])
        assert f.store.feedback(fill['data'], fill['proof'])
        assert f.trade(event)['execution_state'] == 'RECONCILING'
        return observation

    assert adapter.execute(buy, buy_quote)['state'] == 'ACKNOWLEDGED'
    assert f.trade(buy)['remaining_raw'] == '0'
    checkpoint('BUY_ACK')
    observation = ledger_fill(buy)
    checkpoint('BUY_FILL')  # already committed ledger Fill, no reconciliation
    assert f.store.reconcile(observation)
    assert f.trade(buy)['execution_state'] == 'POSITION_OPEN'
    with f.store.transaction() as c:
        f.store.request_exit(c, f.store.get(c, 'trades', buy['trade_id']), 'strategy_exit')
    checkpoint('POSITION_OPEN')  # committed exit intent also survives
    assert PositionManager(f.store).tick()['status'] == 'PASS'
    sell = f.store.pending()
    assert sell['side'] == 'SELL' and sell['amount_raw'] == '1000'
    f.now = fixture_now()
    sell_quote = dict(f.quote(sell, minimum_output_raw='50000000'), source='GMGN_ORDER_QUOTE')
    assert adapter.execute(sell, sell_quote)['state'] == 'ACKNOWLEDGED'
    checkpoint('SELL_ACK')  # SEND and ACK durable, no Fill yet
    ledger_fill(sell)
    assert f.trade(sell)['real_net_pnl_lamports'] is None
    checkpoint('SELL_FILL')
    raise AssertionError('checkpoint not reached')


if __name__ == '__main__':
    crash_at(Path(sys.argv[1]).resolve(strict=True), sys.argv[2])
