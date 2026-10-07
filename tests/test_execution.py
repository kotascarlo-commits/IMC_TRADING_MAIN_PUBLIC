"""Execution contract fixtures: isolated ledgers, fake executor, zero broadcast."""
import copy
import json
import sqlite3
import sys
import threading
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

BRIDGE = Path(__file__).parents[1] / "RUNTIME/EXECUTOR_GATEWAY/GMGN_BRIDGE"
sys.path.insert(0, str(BRIDGE))
from execution_state import Ledger, StateError, load_config, raw
from execution_evidence import SolanaEvidence, GMGNOrderEvidence, ReconciliationReader, SOL
import gmgn_signal_bridge as bridge
import imc_pure_signal_gateway as gateway


@pytest.fixture
def flow(tmp_path):
    class FakeExecutor:
        def __init__(self):
            self.now = datetime.now(timezone.utc).replace(microsecond=0)
            self.store = Ledger(tmp_path / "execution.db", clock=self.clock)
            self.store.bootstrap(self.clock())
            self.base = 1_000_000_000
            assert self.reconcile()

        def clock(self):
            return self.now.isoformat()

        def reconcile(self, **changes):
            status = self.store.status()
            positions = {}
            for t in status['trades']:
                if raw(t['remaining_raw']):
                    positions[t['mint']] = t['remaining_raw']
            orders = {e['event_id']: {'state': 'FILLED', 'order_id': e['order_id']}
                      for e in status['events'] if e['state'] == 'FILLED'}
            balance = self.base + sum(raw(t['wallet_delta_lamports'], signed=True) for t in status['trades'])
            observation = {'wallet': 'wallet', 'observed_at': self.clock(), 'chain_positions': positions,
                           'executor_positions': positions, 'orders': orders, 'balance_lamports': str(balance)}
            observation.update(changes)
            return self.store.reconcile(observation)

        def candidate(self, **changes):
            signal = {'source': 'isolated-fixture:picks', 'variant': 'SAFE100', 'mint': 'mint',
                      'signal_at': self.clock(), 'reference_price': '0.1',
                      'reference_liquidity_usd': '200000', 'safe100_approved': True}
            signal.update(changes)
            return self.store.candidate(signal)

        def buy(self, **changes):
            tid = self.candidate(**changes)
            return self.store.prepare_buy(tid)

        def quote(self, event, **changes):
            quote = {'wallet': 'wallet', 'quoted_at': self.clock(), 'slippage_bps': 100,
                     'price_impact_bps': 100, 'price_deviation_bps': 100, 'liquidity_usd': '200000',
                     'input_raw': '50000000' if event['side'] == 'BUY' else event['amount_raw'],
                     'minimum_output_raw': '900'}
            quote.update(event_id=event['event_id'], input_token=SOL if event['side'] == 'BUY' else event['mint'],
                         output_token=event['mint'] if event['side'] == 'BUY' else SOL)
            quote.update(changes)
            return quote

        def claim(self, event, **changes):
            claimed = self.store.claim(event['event_id'], self.quote(event, **changes))
            assert self.store.bind_order(event['event_id'], 'order:' + event['event_id'])
            return claimed

        def data(self, event, status, suffix=''):
            return {'feedback_id': event['event_id'] + ':' + status + suffix,
                    'event_id': event['event_id'], 'trade_id': event['trade_id'], 'mint': event['mint'],
                    'side': event['side'], 'status': status, 'order_id': 'order:' + event['event_id']}

        def fill(self, event, *, status='FILLED', delta=None, cash=None, signature=None, suffix='', swap_input=None):
            signature = signature or ('b' if event['side'] == 'BUY' else 's') * 88
            data = self.data(event, status, suffix)
            data['signature'] = signature
            proof = {'signature': signature, 'wallet': 'wallet', 'mint': event['mint'], 'side': event['side'],
                     'slot': 42, 'block_time': self.clock(), 'token_delta_raw': str(delta if delta is not None else (1000 if event['side'] == 'BUY' else -raw(event['amount_raw']))),
                     'wallet_delta_lamports': str(cash if cash is not None else (-50_000_000 if event['side'] == 'BUY' else 55_000_000)),
                     'fees_lamports': '5000', 'source': 'isolated-fixture', 'commitment': 'finalized'}
            proof.update(event_id=event['event_id'], order_id='order:' + event['event_id'], order_terminal=status == 'FILLED',
                         swap_input_raw=str(swap_input if swap_input is not None else (50_000_000 if event['side'] == 'BUY' else -int(proof['token_delta_raw']))),
                         swap_output_raw=proof['token_delta_raw'] if event['side'] == 'BUY' else str(cash if cash is not None else 55_000_000))
            return data, proof

        def open(self):
            event = self.claim(self.buy())
            assert self.store.feedback(self.data(event, 'ACK'))
            assert self.store.feedback(*self.fill(event))
            assert self.reconcile()
            return event

        def trade(self, event):
            return next(t for t in self.store.status()['trades'] if t['trade_id'] == event['trade_id'])

    return FakeExecutor()


def test_normal_buy_requires_confirmed_fill(flow):
    event = flow.claim(flow.buy())
    assert flow.store.feedback(flow.data(event, 'ACK'))
    assert flow.trade(event)['state'] == 'BUY_ACK'
    assert flow.trade(event)['remaining_raw'] == '0'
    assert flow.store.feedback(*flow.fill(event))
    assert flow.trade(event)['state'] == 'POSITION_OPEN'
    assert flow.trade(event)['remaining_raw'] == '1000'
    assert flow.store.status()['reconciliation_required']


def test_buy_sell_cycle_closes_only_after_balance_check(flow):
    buy = flow.open()
    sell = flow.store.prepare_sell(buy['trade_id'], generation=flow.clock())
    assert sell['amount_raw'] == '1000'
    sell = flow.claim(sell)
    assert flow.store.feedback(flow.data(sell, 'ACK'))
    assert flow.store.feedback(*flow.fill(sell))
    assert flow.trade(sell)['state'] == 'SELL_FILLED'
    assert flow.trade(sell)['real_net_pnl_lamports'] is None
    assert flow.reconcile()
    trade = flow.trade(sell)
    assert trade['state'] == 'CLOSED'
    assert trade['real_net_pnl_lamports'] == '5000000'
    assert trade['fees_lamports'] == '10000'  # already included in cash deltas


def test_buy_rejected_before_send(flow):
    event = flow.buy()
    assert flow.store.feedback(flow.data(event, 'REJECTED'))
    assert flow.trade(event)['state'] == 'REJECTED'


def test_sell_rejected_preserves_real_position_and_emergency_state(flow):
    event = flow.open()
    sell = flow.store.prepare_sell(event['trade_id'])
    assert flow.store.feedback(flow.data(sell, 'REJECTED'))
    assert flow.trade(sell)['state'] == 'EMERGENCY_EXIT'
    assert flow.trade(sell)['remaining_raw'] == '1000'
    assert not flow.store.control('resume_buys')


@pytest.mark.parametrize('status', ['TIMEOUT', 'FAILURE', 'UNKNOWN_EXECUTION', 'REJECTED', 'DEFERRED'])
def test_uncertain_sent_order_blocks_new_trades(flow, status):
    event = flow.claim(flow.buy())
    assert flow.store.feedback(flow.data(event, status))
    assert flow.store.status()['kill_reason']
    assert flow.store.prepare_buy(flow.candidate(mint='other')) is None
    assert not flow.store.control('reset_session')


def test_api_unavailable_engages_kill_switch(flow):
    for _ in range(flow.store.cfg['max_api_failures']):
        flow.store.api_failure('fixture-unavailable')
    assert flow.store.status()['kill_reason'] == 'api_failure_limit'


def test_timeout_obligation_and_late_fill_after_stop(flow):
    event = flow.claim(flow.buy())
    flow.now += timedelta(seconds=31)
    assert flow.store.status()['kill_reason'] == 'execution_timeout'
    flow.store.control('stop')
    assert flow.store.feedback(*flow.fill(event))
    assert flow.trade(event)['remaining_raw'] == '1000'
    assert flow.reconcile()
    assert flow.store.status()['kill_reason'] is None
    assert not flow.store.status()['new_buys_enabled']  # stop survives reconciliation


def test_duplicate_api_response_and_duplicate_signature_are_idempotent(flow):
    event = flow.claim(flow.buy())
    data, proof = flow.fill(event)
    assert flow.store.feedback(data, proof)
    assert flow.store.feedback(data, proof)
    repeated = dict(data, feedback_id=data['feedback_id'] + ':again')
    assert flow.store.feedback(repeated, proof)
    assert flow.trade(event)['buy_raw'] == '1000'
    with flow.store.transaction() as c:
        assert c.execute('SELECT count(*) FROM receipts').fetchone()[0] == 1


def test_conflicting_duplicate_feedback_persists_kill(flow):
    event = flow.claim(flow.buy())
    data = flow.data(event, 'ACK')
    assert flow.store.feedback(data)
    assert not flow.store.feedback(dict(data, order_id='different'))
    assert flow.store.status()['kill_reason'] == 'feedback_identity_conflict'


def test_duplicate_buy_candidate_different_mints_and_atomic_claim(flow):
    event = flow.buy()
    assert flow.candidate() == event['trade_id']
    assert flow.store.prepare_buy(event['trade_id']) is None
    assert flow.candidate(mint='another') != event['trade_id']
    def attempt(_):
        try:
            flow.claim(event)
            return True
        except StateError:
            return False
    with ThreadPoolExecutor(max_workers=2) as executor:
        assert sorted(executor.map(attempt, range(2))) == [False, True]


def test_duplicate_sell_and_sell_without_buy(flow):
    candidate = flow.candidate(mint='unbought')
    assert flow.store.prepare_sell(candidate) is None
    buy = flow.open()
    sell = flow.store.prepare_sell(buy['trade_id'])
    assert flow.store.prepare_sell(buy['trade_id']) == sell
    flow.claim(sell)
    with pytest.raises(StateError, match='already_claimed'):
        flow.claim(sell)


def test_restart_open_position_requires_reconciliation(flow):
    buy = flow.open()
    restarted = Ledger(flow.store.path, clock=flow.clock)
    restarted.recover()
    assert restarted.status()['open_positions'] == 1
    assert not restarted.status()['new_buys_enabled']
    assert not restarted.control('reset_session')
    flow.store = restarted
    assert flow.reconcile()
    assert flow.trade(buy)['state'] == 'POSITION_OPEN'


def test_restart_between_commit_and_external_response_never_resends(flow):
    event = flow.claim(flow.buy())
    restarted = Ledger(flow.store.path, clock=flow.clock)
    restarted.recover()
    assert restarted.pending() is None
    with pytest.raises(StateError, match='already_claimed'):
        restarted.claim(event['event_id'], flow.quote(event))
    assert restarted.status()['kill_reason'] == 'restart_with_execution_obligation'


@pytest.mark.parametrize('difference', ['local', 'chain', 'executor', 'balance', 'unknown_position'])
def test_reconciliation_disagreements_block(flow, difference):
    buy = flow.open()
    changes = {}
    if difference == 'local':
        with flow.store.transaction() as c:
            trade = flow.store.get(c, 'trades', buy['trade_id'])
            # Exercise the central transition; a direct legacy projection write
            # is now rejected and cannot bypass the execution lifecycle.
            flow.store.transition(c,trade,'RECONCILIATION_REQUIRED','isolated_fixture_balance_check')
            assert trade['execution_state']=='RECONCILING'
        assert flow.reconcile()
        assert flow.trade(buy)['state'] == 'POSITION_OPEN'
        return
    if difference == 'chain': changes['chain_positions'] = {'mint': '999'}
    if difference == 'executor': changes['executor_positions'] = {}
    if difference == 'balance': changes['balance_lamports'] = '1'
    if difference == 'unknown_position': changes['chain_positions'] = {'mint': '1000', 'unknown': '2'}
    assert not flow.reconcile(**changes)
    assert flow.store.status()['kill_reason']
    assert not flow.store.control('resume_buys')


@pytest.mark.parametrize('field,value,error', [
    ('slippage_bps', 151, 'slippage'), ('price_impact_bps', 151, 'impact'),
    ('liquidity_usd', '10', 'liquidity'), ('price_deviation_bps', 151, 'deviation'),
    ('quoted_at', '2000-01-01T00:00:00+00:00', 'stale_quote'),
    ('input_raw', '999999999', 'capital_limit')])
def test_execution_guards(flow, field, value, error):
    event = flow.buy()
    with pytest.raises(StateError, match=error):
        flow.claim(event, **{field: value})
    assert flow.trade(event)['state'] == 'APPROVED'


def test_stale_signal_blocks_including_already_queued(flow):
    event = flow.buy()
    flow.now += timedelta(seconds=5)
    assert flow.store.pending() is None
    assert flow.trade(event)['state'] == 'REJECTED'
    old = (flow.now - timedelta(seconds=10)).isoformat()
    assert flow.store.prepare_buy(flow.candidate(mint='old', signal_at=old)) is None


def test_unverified_fill_never_creates_real_position(flow):
    event = flow.claim(flow.buy())
    data, _ = flow.fill(event)
    data['actual_output_raw'] = '999999999999999'
    assert not flow.store.feedback(data)
    assert flow.trade(event)['remaining_raw'] == '0'
    assert flow.trade(event)['state'] == 'UNKNOWN_EXECUTION'


def test_kill_blocks_already_queued_buy(flow):
    flow.buy()
    flow.store.block('fixture-kill')
    assert flow.store.pending() is None


def test_emergency_exit_uses_exact_confirmed_quantity(flow):
    buy = flow.open()
    assert flow.store.control('emergency_exit')
    sell = flow.store.pending()
    assert sell['side'] == 'SELL' and sell['amount_raw'] == '1000'
    assert flow.trade(buy)['state'] == 'EMERGENCY_EXIT'
    assert flow.claim(sell)['state'] == 'SENT'


def test_recovery_after_verified_late_fill(flow):
    buy = flow.claim(flow.buy())
    flow.store.feedback(flow.data(buy, 'TIMEOUT'))
    assert not flow.reconcile()
    assert flow.store.feedback(*flow.fill(buy))
    assert flow.reconcile()
    assert flow.store.status()['kill_reason'] is None


def test_corrupt_sqlite_and_corrupt_position_fail_closed(flow, tmp_path):
    broken = tmp_path / 'broken.db'
    broken.write_bytes(b'not a SQLite database')
    with pytest.raises(sqlite3.DatabaseError):
        Ledger(broken)
    buy = flow.open()
    with flow.store.transaction() as c:
        with pytest.raises(sqlite3.IntegrityError,match='SCHEMA2_TRADE_REQUIRED'):
            c.execute('UPDATE trades SET doc=? WHERE id=?', ('{}', buy['trade_id']))
        # A structurally guarded but incomplete document still tests the
        # reader's corruption rejection after the SQL guard rejects legacy IO.
        c.execute('UPDATE trades SET doc=? WHERE id=?', ('{"execution_state":"POSITION_OPEN","revision":1}', buy['trade_id']))
    with pytest.raises(StateError, match='corrupt_trade_schema'):
        flow.store.status()


def test_sell_wrong_quantity_and_precision(flow):
    event = flow.claim(flow.buy())
    quantity = 2**53 + 17
    assert flow.store.feedback(*flow.fill(event, delta=quantity))
    assert flow.reconcile()
    sell = flow.store.prepare_sell(event['trade_id'])
    assert sell['amount_raw'] == str(quantity)
    with pytest.raises(StateError, match='sell_amount_mismatch'):
        flow.claim(sell, input_raw=str(quantity + 1))
    with pytest.raises(StateError, match='invalid_raw_amount'):
        raw(float(quantity))


def test_partial_sell_preserves_remaining_and_blocks_retry(flow):
    buy = flow.open()
    sell = flow.claim(flow.store.prepare_sell(buy['trade_id']))
    assert flow.store.feedback(*flow.fill(sell, status='PARTIAL', delta=-400, cash=20_000_000))
    assert flow.trade(sell)['remaining_raw'] == '600'
    assert flow.trade(sell)['state'] == 'RECONCILIATION_REQUIRED'
    assert not flow.reconcile()
    with pytest.raises(StateError, match='already_claimed'):
        flow.claim(sell)


def test_partial_buy_is_not_a_completed_order(flow):
    event = flow.claim(flow.buy())
    assert flow.store.feedback(*flow.fill(event, status='PARTIAL', delta=500, swap_input=25_000_000))
    assert flow.trade(event)['remaining_raw'] == '500'
    assert flow.trade(event)['state'] == 'RECONCILIATION_REQUIRED'
    assert not flow.reconcile()


def test_partial_buy_later_completes_without_double_booking(flow):
    event = flow.claim(flow.buy())
    assert flow.store.feedback(*flow.fill(event, status='PARTIAL', delta=500, cash=-25_000_000, swap_input=25_000_000))
    assert flow.store.feedback(*flow.fill(event, status='FILLED', delta=500, cash=-25_000_000,
                                         swap_input=25_000_000, signature='c' * 88, suffix=':second'))
    assert flow.trade(event)['buy_raw'] == '1000'
    assert flow.reconcile()
    assert flow.trade(event)['state'] == 'POSITION_OPEN'


def test_partial_sell_later_completes_and_same_receipt_terminal_update(flow):
    buy = flow.open()
    sell = flow.claim(flow.store.prepare_sell(buy['trade_id']))
    assert flow.store.feedback(*flow.fill(sell, status='PARTIAL', delta=-400, cash=20_000_000))
    assert flow.store.feedback(*flow.fill(sell, status='FILLED', delta=-600, cash=35_000_000,
                                         signature='z' * 88, suffix=':second'))
    assert flow.reconcile()
    assert flow.trade(sell)['state'] == 'CLOSED'


def test_terminal_report_for_existing_partial_receipt_does_not_book_twice(flow):
    event = flow.claim(flow.buy())
    data, proof = flow.fill(event, status='PARTIAL')
    assert flow.store.feedback(data, proof)
    assert flow.trade(event)['state'] == 'RECONCILIATION_REQUIRED'
    final_data = dict(data, feedback_id='terminal-order-observation', status='FILLED')
    final_proof = dict(proof, order_terminal=True)
    assert flow.store.feedback(final_data, final_proof)
    assert flow.trade(event)['buy_raw'] == '1000'
    assert flow.reconcile()


def test_partial_receipt_terminalizes_after_timeout_and_restart(flow):
    event = flow.claim(flow.buy())
    data, proof = flow.fill(event, status='PARTIAL')
    assert flow.store.feedback(data, proof)
    flow.now += timedelta(seconds=31)
    assert flow.store.status()['events'][0]['state'] == 'UNKNOWN'
    flow.store = Ledger(flow.store.path, clock=flow.clock)
    flow.store.recover()
    final_data = dict(data, feedback_id='after-timeout-terminal', status='FILLED')
    final_proof = dict(proof, order_terminal=True)
    assert flow.store.feedback(final_data, final_proof)
    assert flow.trade(event)['buy_raw'] == '1000'
    assert flow.reconcile()
    trade = flow.trade(event)
    assert trade['execution_state'] == 'POSITION_OPEN'
    assert trade['state'] == 'EMERGENCY_EXIT' and trade['exit_requested']
    assert flow.store.pending()['exit_reason'] == 'recovery_exit'
    assert flow.store.pending()['amount_raw'] == trade['remaining_raw'] == '1000'


def test_permanent_kill_cannot_be_overwritten_by_api_failures(flow):
    flow.store.block('legacy_state_requires_reconciliation')
    for _ in range(3):
        flow.store.api_failure('fixture')
    assert not flow.reconcile()
    assert flow.store.status()['kill_reasons'] == ['legacy_state_requires_reconciliation']
    assert not flow.store.control('resume_buys')


@pytest.mark.parametrize('status', ['DEFERRED', 'TIMEOUT', 'REJECTED', 'FAILURE'])
def test_stale_feedback_never_regresses_terminal_state(flow, status):
    buy = flow.open()
    assert flow.store.feedback(flow.data(buy, status, ':late'))
    assert flow.trade(buy)['state'] == 'POSITION_OPEN'
    assert flow.store.status()['kill_reason'] is None


def test_foreign_order_and_foreign_quote_are_rejected(flow):
    event = flow.buy()
    with pytest.raises(StateError, match='quote_identity_mismatch'):
        flow.claim(event, output_token='unrelated')
    event = flow.claim(event)
    assert not flow.store.feedback(dict(flow.data(event, 'ACK'), order_id='foreign'))
    response = {'order_id':'order:' + event['event_id'], 'hash':'other', 'status':'confirmed',
                'report':{'input_token':SOL, 'output_token':'mint', 'input_amount':'50000000', 'output_amount':'1000'}}
    bound = dict(event, order_id='order:' + event['event_id'])
    with pytest.raises(StateError, match='order_signature_not_confirmed'):
        GMGNOrderEvidence(load_config(), query=lambda _:response).verify(bound, 'b' * 88)


def test_gmgn_report_success_requires_complete_matching_contract(flow):
    event = flow.claim(flow.buy())
    event['order_id'] = 'order:' + event['event_id']
    response = {'order_id':event['order_id'], 'hash':'b' * 88, 'status':'successful', 'state':30,
                'report':{'input_token':SOL, 'output_token':'mint', 'input_amount':'50000000', 'output_amount':'1000'}}
    reader = GMGNOrderEvidence(load_config(), query=lambda _:response)
    assert reader.verify(event, 'b' * 88)['swap_input_raw'] == '50000000'
    response['report']['output_token'] = 'foreign'
    with pytest.raises(StateError, match='token_pair_mismatch'):
        reader.verify(event, 'b' * 88)


def test_signal_only_remains_passive_without_wallet_reconciliation(flow):
    flow.store.recover()
    event = flow.buy()
    assert event and event['execution'] is False
    assert event['risk_status'] == 'PENDING_EXECUTION_CHECKS'
    assert flow.store.pending()['event_id'] == event['event_id']
    with pytest.raises(StateError, match='buys_blocked'):
        flow.claim(event)


def test_exit_generation_and_signature_reuse_conflicts(flow):
    buy = flow.open()
    assert flow.store.prepare_sell(buy['trade_id'], generation='different') is None
    assert flow.store.status()['kill_reason'] == 'exit_candidate_generation_mismatch'


def test_shadow_never_becomes_live_performance(flow):
    buy = flow.open()
    flow.store.shadow(buy['trade_id'], {'net_pnl_lamports': '6000000',
        'evidence_class': 'SIMULATION', 'source': 'isolated-fixture:simulator'})
    assert flow.trade(buy)['comparison'] is None
    sell = flow.claim(flow.store.prepare_sell(buy['trade_id']))
    assert flow.store.feedback(*flow.fill(sell))
    assert flow.reconcile()
    assert flow.trade(sell)['comparison']['pnl_delta_lamports'] == '-1000000'


def test_legacy_positions_are_preserved_not_migrated(tmp_path):
    legacy = tmp_path / 'state.json'
    original = b'{"buy_filled":{"mint":{"actual_output_raw":"1000"}}}'
    legacy.write_bytes(original)
    store = Ledger(tmp_path / 'ledger.db')
    store.bootstrap(datetime.now(timezone.utc).isoformat(), legacy)
    assert store.status()['kill_reason'] == 'legacy_state_requires_reconciliation'
    assert legacy.read_bytes() == original


def test_bridge_sell_has_raw_quantity_and_no_percent(flow):
    buy = flow.open()
    sell = flow.store.prepare_sell(buy['trade_id'])
    packet = bridge.translate(sell, wallet_reader=lambda: ('wallet', 1_000_000_000))
    assert packet['order']['amount_raw'] == '1000'
    assert 'percent' not in packet['order']
    assert packet['execution'] is False


def test_projection_failure_does_not_rollback_committed_order(flow, monkeypatch):
    event = flow.claim(flow.buy())
    monkeypatch.setattr(gateway, 'STATUS', Path('invalid:/status.json'))
    with pytest.raises(OSError):
        gateway.write_status(flow.store)
    assert flow.trade(event)['state'] == 'BUY_SENT'
    with pytest.raises(StateError, match='already_claimed'):
        flow.claim(event)


def test_rpc_reads_finalized_deltas_ignoring_client_amounts():
    event = {'wallet': 'wallet', 'mint': 'mint', 'side': 'BUY'}
    signature = 'b' * 88
    tx = {'slot': 42, 'blockTime': 1000, 'transaction': {'signatures': [signature],
          'message': {'accountKeys': [{'pubkey': 'wallet', 'signer': True}]}},
          'meta': {'err': None, 'fee': 5000, 'preBalances': [1000000000], 'postBalances': [950000000],
                   'preTokenBalances': [], 'postTokenBalances': [{'mint': 'mint', 'owner': 'wallet',
                      'uiTokenAmount': {'amount': '9007199254741009'}}]}}
    calls = []
    def transport(method, params):
        calls.append((method, params))
        return tx
    reader = SolanaEvidence(load_config(), transport)
    proof = reader.fill(event, signature)
    assert proof['token_delta_raw'] == '9007199254741009'
    assert proof['wallet_delta_lamports'] == '-50000000'
    assert calls[0][1][1]['commitment'] == 'finalized'
    tx['meta']['err'] = {'InstructionError': 'fixture'}
    with pytest.raises(StateError, match='transaction_failed'):
        reader.fill(event, signature)


def test_launcher_does_not_automatically_reset_execution_session():
    text = (BRIDGE.parents[2] / 'LAUNCHERS/START_IMC_SIGNAL_ONLY.ps1').read_text(encoding='utf-8-sig')
    assert "action='reset_session'" not in text


def test_http_auth_body_limits_claim_and_untrusted_snapshot(flow, monkeypatch):
    """Ephemeral fixture server, never a production launcher/port/database."""
    monkeypatch.setattr(gateway, '_LEDGER', flow.store)
    monkeypatch.setattr(gateway, 'STATUS', flow.store.path.with_name('gateway-status.json'))
    monkeypatch.setattr(gateway, '_MONITOR', None)
    monkeypatch.setenv('IMC_EXECUTION_TOKEN', 'isolated-test-token')
    def unavailable(*_):
        raise StateError('isolated-missing-access')
    monkeypatch.setattr(ReconciliationReader, 'snapshot', unavailable)
    server = gateway.ThreadingHTTPServer(('127.0.0.1', 0), gateway.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = 'http://127.0.0.1:' + str(server.server_port)
    def post(path, payload, token=True):
        headers = {'Content-Type': 'application/json'}
        if token:
            headers['Authorization'] = 'Bearer isolated-test-token'
        request = urllib.request.Request(base + path, data=payload, headers=headers, method='POST')
        try:
            with urllib.request.urlopen(request, timeout=2) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read())
    try:
        assert post('/control', b'{"action":"stop"}', token=False)[0] == 403
        monkeypatch.setenv('IMC_EXECUTION_TOKEN','unicode-\u00e4')
        assert post('/control', b'{}')[0] == 403
        monkeypatch.setenv('IMC_EXECUTION_TOKEN','isolated-test-token')
        assert post('/control', b'[]')[0] == 400
        assert post('/control', b'{"value":NaN}')[0] == 400
        assert post('/control', b'x' * 65537)[0] == 413
        assert post('/claim', b'{}')[1]['error'] == 'live_execution_disabled'
        assert post('/reconcile', b'{}')[1]['error'] == 'reconciliation_unavailable'
        assert post('/reconcile', b'{"balance_lamports":"1000"}')[1]['error'] == 'caller_balances_not_accepted'
        assert post('/control', b'{"action":"stop"}')[0] == 200
        with urllib.request.urlopen(base + '/health', timeout=2) as response:
            assert json.loads(response.read())['broadcast'] is False
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_state_transition_and_receipt_rollback_together(flow, monkeypatch):
    event = flow.claim(flow.buy())
    original = flow.store.put
    def fail_receipt(c, table, key, doc):
        if table == 'receipts':
            raise sqlite3.OperationalError('isolated commit fixture')
        return original(c, table, key, doc)
    monkeypatch.setattr(flow.store, 'put', fail_receipt)
    with pytest.raises(sqlite3.OperationalError):
        flow.store.feedback(*flow.fill(event))
    assert flow.trade(event)['state'] == 'BUY_SENT'
    assert flow.trade(event)['buy_raw'] == '0'
    monkeypatch.setattr(flow.store, 'put', original)
    assert flow.store.feedback(*flow.fill(event))


def test_three_isolated_functional_buy_sell_cycles(tmp_path):
    """3/3 functional fixtures are not the requested micro-live pilot evidence."""
    clock = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    store = Ledger(tmp_path / 'cycles.db', clock=lambda: clock)
    store.bootstrap(clock)
    balance = 1_000_000_000
    orders = {}
    def reconcile(positions):
        return store.reconcile({'wallet':'wallet', 'observed_at':clock, 'chain_positions':positions,
            'executor_positions':positions, 'orders':orders, 'balance_lamports':str(balance)})
    assert reconcile({})
    for index in range(3):
        mint = 'mint' + str(index)
        tid = store.candidate({'source':'isolated-fixture', 'variant':'SAFE100', 'mint':mint,
            'signal_at':clock, 'safe100_approved':True, 'reference_liquidity_usd':'200000'})
        buy = store.prepare_buy(tid)
        for side in ('BUY', 'SELL'):
            event = buy if side == 'BUY' else store.prepare_sell(tid)
            event = store.claim(event['event_id'], {'wallet':'wallet', 'quoted_at':clock,
                'slippage_bps':100, 'price_impact_bps':100, 'price_deviation_bps':100,
                'liquidity_usd':'200000', 'input_raw':'50000000' if side == 'BUY' else '1000',
                'minimum_output_raw':'900', 'event_id':event['event_id'],
                'input_token':SOL if side == 'BUY' else mint, 'output_token':mint if side == 'BUY' else SOL})
            order_id = 'order:' + event['event_id']
            assert store.bind_order(event['event_id'], order_id)
            signature = str(index) + side
            cash = -50_000_000 if side == 'BUY' else 51_000_000
            data = {'feedback_id':event['event_id'], 'event_id':event['event_id'], 'trade_id':tid,
                'mint':mint, 'side':side, 'status':'FILLED', 'order_id':order_id, 'signature':signature}
            proof = {'signature':signature, 'wallet':'wallet', 'mint':mint, 'side':side, 'slot':index,
                'block_time':clock, 'token_delta_raw':'1000' if side == 'BUY' else '-1000',
                'wallet_delta_lamports':str(cash), 'fees_lamports':'5000', 'event_id':event['event_id'],
                'order_id':order_id, 'swap_input_raw':'50000000' if side == 'BUY' else '1000',
                'swap_output_raw':'1000' if side == 'BUY' else str(cash), 'order_terminal':True,
                'commitment':'finalized','source':'isolated-fixture:cycles'}
            assert store.feedback(data, proof)
            balance += cash
            orders[event['event_id']] = {'state':'FILLED', 'order_id':order_id}
            assert reconcile({mint:'1000'} if side == 'BUY' else {})
    assert store.status()['sell_filled'] == 3
    assert store.prepare_buy(store.candidate({'source':'isolated-fixture', 'variant':'SAFE100',
        'mint':'fourth', 'signal_at':clock, 'safe100_approved':True, 'reference_liquidity_usd':'200000'})) is None


def test_reconciliation_reader_recovers_missed_fill_using_independent_order(flow):
    event = flow.claim(flow.buy())
    data, proof = flow.fill(event)
    order = {'order_id':data['order_id'], 'hash':data['signature'], 'status':'confirmed', 'state':30,
             'report':{'input_token':SOL, 'output_token':'mint', 'input_amount':'50000000', 'output_amount':'1000'}}
    class FakeRPC:
        def wallet_snapshot(self, wallet):
            return {'mint':'1000'}, {'mint':3}, '950000000', 42
        def fill(self, ev, signature):
            assert ev['event_id'] == event['event_id'] and signature == data['signature']
            return copy.deepcopy(proof)
    calls = []
    def cli(args):
        calls.append(args)
        if args[:2] == ['portfolio','info']:
            return {'wallets':[{'chain':'sol','address':'wallet'}]}
        if args[:2] == ['portfolio','holdings']:
            return {'list':[{'token':{'token_address':'mint'},'balance':'1.000'}], 'next':''}
        if args[:2] == ['order','get']:
            return order
        raise AssertionError('Unexpected command')
    reader = ReconciliationReader(load_config(), cli=cli, rpc=FakeRPC(), clock=flow.clock)
    assert flow.store.reconcile(reader.snapshot(flow.store))
    assert flow.trade(event)['state'] == 'POSITION_OPEN'
    assert flow.trade(event)['remaining_raw'] == '1000'
    assert all(args[:2] in [['portfolio','info'],['portfolio','holdings'],['order','get']] for args in calls)
    assert '--hide-airdrop' in calls[1] and 'false' in calls[1]
    assert flow.store.reconcile(reader.snapshot(flow.store))
    assert flow.trade(event)['buy_raw'] == '1000'


@pytest.mark.parametrize('failure', ['missing_list','cursor_loop','unknown_holding','precision_loss'])
def test_snapshot_reader_rejects_incomplete_or_ambiguous_inventory(flow, failure):
    class FakeRPC:
        def wallet_snapshot(self, wallet):
            return {'mint':'1000'}, {'mint':3}, '1000000000', 42
    def cli(args):
        if args[:2] == ['portfolio','info']:
            return {'wallets':[{'chain':'sol','address':'wallet'}]}
        if failure == 'missing_list': return {}
        if failure == 'cursor_loop': return {'list':[], 'next':'repeated'}
        mint = 'foreign' if failure == 'unknown_holding' else 'mint'
        return {'list':[{'token':{'token_address':mint},'balance':'0.0001'}], 'next':''}
    with pytest.raises(StateError):
        ReconciliationReader(load_config(), cli=cli, rpc=FakeRPC(), clock=flow.clock).snapshot(flow.store)


def test_rpc_snapshot_requires_same_finalized_bank_and_reads_both_programs():
    calls = []
    varying = False
    def transport(method, params):
        calls.append((method, params))
        if method == 'getBalance': return {'context':{'slot':43 if varying else 42}, 'value':1000}
        return {'context':{'slot':42}, 'value':[]}
    reader = SolanaEvidence(load_config(), transport)
    assert reader.wallet_snapshot('wallet')[0] == {}
    programs = [params[1]['programId'] for method,params in calls if method == 'getTokenAccountsByOwner']
    assert len(set(programs)) == 2
    varying = True
    with pytest.raises(StateError, match='inconsistent_chain_snapshot_slots'):
        reader.wallet_snapshot('wallet')


def test_gmgn_reader_preserves_numeric_precision_before_decimal_conversion(monkeypatch):
    class Result:
        returncode = 0
        stdout = '{"list":[{"balance":9007199254741009.001}]}'
    import execution_evidence
    monkeypatch.setattr(execution_evidence.subprocess, 'run', lambda *a, **kw:Result())
    reader = ReconciliationReader(load_config())
    result = reader.request(['portfolio','holdings'])
    assert str(result['list'][0]['balance']) == '9007199254741009.001'


def test_realized_loss_limit_blocks_scaling_not_invested_capital(flow):
    buy = flow.open()
    assert flow.store.status()['kill_reason'] is None
    sell = flow.claim(flow.store.prepare_sell(buy['trade_id']))
    assert flow.store.feedback(*flow.fill(sell, cash=39_000_000))
    assert not flow.reconcile()
    assert flow.store.status()['kill_reason'] == 'session_loss_limit'
    assert flow.trade(sell)['state'] == 'CLOSED'


@pytest.mark.parametrize('damage',['empty','missing_table','missing_version','wrong_columns','missing_session'])
def test_existing_damaged_ledger_is_never_reinitialized(flow,tmp_path,damage):
    path = flow.store.path
    if damage == 'empty':
        path = tmp_path/'empty.db'
        path.write_bytes(b'')
    else:
        with flow.store.transaction() as c:
            if damage == 'missing_table': c.execute('DROP TABLE receipts')
            if damage == 'missing_version': c.execute("DELETE FROM meta WHERE key='schema_version'")
            if damage == 'missing_session': c.execute("DELETE FROM meta WHERE key='session'")
            if damage == 'wrong_columns': c.execute('ALTER TABLE events ADD COLUMN unexpected TEXT')
    original = path.read_bytes()
    with pytest.raises(StateError):
        Ledger(path,clock=flow.clock)
    assert path.read_bytes() == original


@pytest.mark.parametrize('key,value',[
    ('port',True), ('max_snapshot_pages',1.5), ('dashboard_max_response_bytes',0),
    ('dashboard_control_timeout_seconds',float('inf')), ('dashboard_start_poll_seconds',False),
    ('execution_authority_pct',1), ('signing',True), ('max_http_body_bytes',2**40)])
def test_shared_execution_config_rejects_unsafe_values(tmp_path,key,value):
    cfg = load_config()
    cfg[key] = value
    path = tmp_path/'config.json'
    path.write_text(json.dumps(cfg))
    with pytest.raises(StateError):
        load_config(path)


def test_concurrent_ledger_initialization_preserves_session(tmp_path):
    path = tmp_path/'concurrent.db'
    with ThreadPoolExecutor(max_workers=2) as executor:
        stores = list(executor.map(lambda _:Ledger(path), range(2)))
    with stores[0].transaction() as c:
        first = stores[0].meta(c,'session')
    with stores[1].transaction() as c:
        assert stores[1].meta(c,'session') == first
