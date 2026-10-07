"""Five actual process crashes, using existing isolated lifecycle evidence.

No production provider recovery, wallet signing, real PnL or power-loss claim.
"""
import json
import time
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_execution_lifecycle_e2e import ControlledVenue
from execution_process_lifecycle_worker import fixture_now
from execution_adapter import SingleSendAdapter, SOL
from execution_evidence import ReconciliationReader, SolanaEvidence
from execution_policy import RiskEngine, CRITICAL, CAPABILITIES
from execution_positions import PositionManager
from execution_state import Ledger


@pytest.mark.parametrize('checkpoint', ['BUY_ACK', 'BUY_FILL', 'POSITION_OPEN', 'SELL_ACK', 'SELL_FILL'])
def test_crash_recovers_same_obligation_then_closes(tmp_path, checkpoint):
    root = tmp_path / 'process-flow'
    root.mkdir()
    project = Path(__file__).parents[1]
    protected = [project / 'config/owner-broadcast-gate.json', project / 'config/imc-execution.json']
    unchanged = [p.read_bytes() for p in protected]
    worker = Path(__file__).with_name('execution_process_lifecycle_worker.py')
    result = subprocess.run([sys.executable, str(worker), str(root), checkpoint],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 91, result.stderr
    saved = json.loads((root / 'checkpoint.json').read_text())
    assert saved['checkpoint'] == checkpoint
    f = SimpleNamespace(now=fixture_now())
    f.clock = lambda: f.now.isoformat()
    # Open only the same crashed execution DB; no ready()/bootstrap in parent.
    f.store = Ledger(root / 'process-lifecycle.db', clock=f.clock)
    before = f.store.status()
    assert before['events'] == saved['ledger_before']['events']
    assert before['trades'] == saved['ledger_before']['trades']
    with f.store.transaction() as c:
        assert c.execute('PRAGMA journal_mode').fetchone()[0] == saved['journal_mode']
        assert f.store.meta(c, 'position_exit:' + saved['buy']['trade_id']) == saved['exit_intent']
    trade = lambda: next(t for t in f.store.status()['trades'] if t['trade_id'] == saved['buy']['trade_id'])
    expected = {'BUY_ACK': 'BUY_ACKNOWLEDGED', 'BUY_FILL': 'RECONCILING',
                'POSITION_OPEN': 'POSITION_OPEN', 'SELL_ACK': 'SELL_ACKNOWLEDGED', 'SELL_FILL': 'RECONCILING'}
    assert trade()['execution_state'] == expected[checkpoint]
    if checkpoint == 'POSITION_OPEN':
        assert saved['exit_intent'] is not None
    if checkpoint in ('BUY_ACK', 'SELL_ACK'):
        assert len(before['events']) == (1 if checkpoint == 'BUY_ACK' else 2)
        assert trade()['remaining_raw'] == ('0' if checkpoint == 'BUY_ACK' else '1000')
    f.store.recover()
    # Reattach the independently saved provider state without __init__ (which
    # creates a new journal). No balances/transactions inferred from Ledger.
    venue = ControlledVenue.__new__(ControlledVenue)
    venue.flow, venue.path = f, root / 'controlled-venue.json'
    for key, value in saved['provider'].items():
        setattr(venue, key, value)
    venue.rpc = SolanaEvidence(f.store.cfg, transport=venue.rpc_read)
    venue.reader = ReconciliationReader(f.store.cfg, cli=venue.read, rpc=venue.rpc, clock=f.clock)
    risk = RiskEngine(f.store, gate_path=root / 'isolated-owner-gate.json', clock=f.clock)
    probes = lambda side: {'heartbeats': {name: {'status': 'PASS', 'source': 'isolated:process-probe',
        'observed_at': f.clock(), 'valid_until_monotonic': time.monotonic()+2}
        for name in CRITICAL}, 'capabilities': {name: True for name in CAPABILITIES}}
    adapter = SingleSendAdapter(risk, probes, contract_transport=venue.send)

    def quote(event):
        return dict(saved['buy_quote'], event_id=event['event_id'], quoted_at=f.clock(),
            input_token=SOL if event['side'] == 'BUY' else event['mint'],
            output_token=event['mint'] if event['side'] == 'BUY' else SOL,
            input_raw='50000000' if event['side'] == 'BUY' else event['amount_raw'],
            minimum_output_raw='900' if event['side'] == 'BUY' else '50000000')

    original_ids = {e['event_id']: tuple(e[k] for k in ('trade_id', 'request_id', 'order_id', 'payload_hash'))
                    for e in before['events']}
    send_count = len(venue.send_calls)
    blocked = adapter.execute(saved['buy'], quote(saved['buy']))
    assert blocked['state'] == 'BLOCKED'
    assert {'reconciliation_required', 'unresolved_order'} & set(blocked['risk']['failures'])
    if saved['sell'] is not None:
        assert adapter.execute(saved['sell'], quote(saved['sell']))['state'] == 'BLOCKED'
    assert len(venue.send_calls) == send_count
    assert f.store.pending() is None  # no executable intent before wallet reconciliation
    assert f.store.materialize_exit(saved['buy']['trade_id']) is None
    if checkpoint == 'BUY_ACK':
        assert not venue.reconcile() and trade()['remaining_raw'] == '0'
        venue.finalize(saved['buy'])
    if checkpoint == 'SELL_ACK':
        assert not venue.reconcile() and trade()['remaining_raw'] == '1000'
        venue.finalize(saved['sell'])
    assert venue.reconcile()
    assert venue.reconcile()  # duplicate finalized evidence cannot book twice
    if checkpoint not in ('SELL_ACK', 'SELL_FILL'):
        assert trade()['execution_state'] == 'POSITION_OPEN'
        with f.store.transaction() as c:
            f.store.request_exit(c, f.store.get(c, 'trades', saved['buy']['trade_id']), 'strategy_exit')
        assert PositionManager(f.store).tick()['status'] == 'PASS'
        sell = f.store.pending()
        assert sell['trade_id'] == saved['buy']['trade_id']
        assert sell['amount_raw'] == trade()['remaining_raw'] == '1000'
        f.now = fixture_now()
        assert adapter.execute(sell, quote(sell))['state'] == 'ACKNOWLEDGED'
        assert trade()['remaining_raw'] == '1000'
        venue.finalize(sell)
        observation = venue.reader.snapshot(f.store)
        fill = next(item for item in observation['recovered_fills'] if item['data']['event_id'] == sell['event_id'])
        assert f.store.feedback(fill['data'], fill['proof'])
        assert trade()['execution_state'] == 'RECONCILING' and trade()['real_net_pnl_lamports'] is None
        assert f.store.reconcile(observation)
    assert venue.reconcile()
    final = f.store.status()
    assert trade()['execution_state'] == 'CLOSED' and trade()['remaining_raw'] == '0'
    assert trade()['real_net_pnl_lamports'] == '5000000' and trade()['fees_lamports'] == '10000'
    assert len(final['events']) == len(venue.records()) == 2
    assert venue.send_calls == ['BUY', 'SELL'] and len(set(venue.sdk_calls)) == 2
    assert all(e['state'] == 'FILLED' for e in final['events'])
    assert final['reconciliation_required'] is False and f.store.pending() is None
    with f.store.transaction() as c:
        assert len(f.store.all(c, 'receipts')) == 2
    for event in final['events']:
        if event['event_id'] in original_ids:
            assert tuple(event[k] for k in ('trade_id', 'request_id', 'order_id', 'payload_hash')) == original_ids[event['event_id']]
    assert [p.read_bytes() for p in protected] == unchanged
