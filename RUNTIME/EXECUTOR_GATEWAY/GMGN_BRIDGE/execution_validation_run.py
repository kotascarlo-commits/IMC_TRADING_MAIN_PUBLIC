"""Bounded isolated acceptance driver on the production execution components."""
import time
from types import SimpleNamespace
from execution_state import Ledger, StateError, timestamp
from execution_policy import RiskEngine, CRITICAL, CAPABILITIES
from execution_adapter import SingleSendAdapter
from execution_validation import initialize, status as validation_status
from execution_jupiter import IsolatedJupiterTransport, JupiterTransactionBuilder, recover_observation
from execution_jupiter_evidence import JupiterChainEvidence
from execution_jupiter_read import JupiterExecutionReader
from execution_jupiter_shadow import IsolatedBank
from mykonos_meme_scout.jupiter_prepare import USDC

class IsolatedValidationRun:
    def __init__(self, path):
        self.bank = IsolatedBank(); self.store = Ledger(path, clock=timestamp)
        initialize(self.store, domain='ISOLATED'); self.store.bootstrap(timestamp())
        self.attach()
        assert self.reconcile()
        self.equity()

    def attach(self):
        self.transport = IsolatedJupiterTransport(self.store, self.bank)
        self.risk = RiskEngine(self.store, isolated_transport=self.transport)
        self.builder = JupiterTransactionBuilder(self.store, rpc_reader=self.bank.rpc)
        self.reader = JupiterExecutionReader(self.store, rpc_reader=self.bank.rpc, fetch_build=self.bank.build,dex='Whirlpool')
        self.receipts = JupiterChainEvidence(self.store.cfg, rpc=SimpleNamespace(transport=self.bank.observe_rpc))
        self.adapter = SingleSendAdapter(self.risk, self.probes, contract_transport=self.transport, jupiter_builder=self.builder)

    def probes(self, side):
        names = tuple('JUPITER_API' if name == 'GMGN_API' else name for name in CRITICAL)
        return {'heartbeats': {name: {'status': 'PASS', 'source': 'ISOLATED_ACCOUNT_BANK', 'observed_at': timestamp(),
                                    'valid_until_monotonic': time.monotonic()+2} for name in names},
                'capabilities': {name: True for name in CAPABILITIES}}

    def buy(self):
        tid = self.store.candidate({'source': 'ISOLATED_ACCOUNT_BANK', 'variant': 'SAFE100', 'mint': USDC,
            'signal_at': timestamp(), 'reference_price': '.1', 'reference_liquidity_usd': '200000', 'safe100_approved': True})
        return self.store.prepare_buy(tid)

    def reconcile(self):
        return self.store.reconcile(recover_observation(self.store, self.receipts, self.bank.snapshot(timestamp)))

    def equity(self):
        status = self.store.status(); marks = {}
        for trade in status['trades']:
            if int(trade['remaining_raw']):
                marks[trade['trade_id']] = {'wallet': self.bank.wallet, 'mint': USDC, 'source': 'JUPITER_V2_ORDER_QUOTE_OBSERVATION',
                    'remaining_raw': trade['remaining_raw'], 'quoted_at': timestamp(),
                    'minimum_output_lamports': str(int(trade['remaining_raw'])*100//12), 'exit_cost_budget_lamports': '3000000'}
        self.store.mark_equity({'wallet': self.bank.wallet, 'observed_at': timestamp(), 'balance_lamports': str(self.bank.balance),
                               'reconciliation_revision': status['reconciliation_revision'], 'marks': marks})

    def leg(self, event):
        quote = self.reader.quote(event)
        result = self.adapter.execute(event, quote)
        assert result['state'] == 'ACKNOWLEDGED', result
        status = self.store.status()
        assert next(e for e in status['events'] if e['event_id'] == event['event_id'])['state'] == 'ACK'
        assert self.reconcile()
        self.equity()
        return quote

    def roundtrip(self):
        buy = self.buy(); assert buy
        self.leg(buy)
        trade = next(t for t in self.store.status()['trades'] if t['trade_id'] == buy['trade_id'])
        assert trade['execution_state'] == 'POSITION_OPEN'
        sell = self.store.prepare_sell(buy['trade_id'], reason='strategy_exit')
        assert sell['amount_raw'] == trade['remaining_raw']
        self.leg(sell)
        assert next(t for t in self.store.status()['trades'] if t['trade_id'] == buy['trade_id'])['state'] == 'CLOSED'
        return buy, sell
