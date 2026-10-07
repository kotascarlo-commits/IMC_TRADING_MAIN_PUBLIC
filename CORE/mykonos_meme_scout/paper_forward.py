"""Prospective GMGN quote-based paper cohort. Never submits transactions.

Quotes are observations, not fills. This frozen policy uses existing CORE
scoring, current security and causal microstructure. Its results cannot train
or promote a model used by the same cohort. All money is simulated in lamports.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sqlite3
import subprocess
import time
import threading
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import asdict
from collections import Counter
from contextlib import contextmanager, closing
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from types import MappingProxyType

from .config import Config
from .models import SecuritySignals, TokenSnapshot
from .scoring import assess, derive_micro_metrics
from .projection_io import write_json_projection

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / 'config/imc-paper.json'
DATABASE = ROOT / 'RUNTIME/gmgn-paper.db'
SOURCE = ROOT / 'CORE/data/imc-main.db'
STATUS = ROOT / 'RUNTIME/gmgn-paper-status.json'
SOL = 'So11111111111111111111111111111111111111112'
ADDRESS = re.compile(r'[1-9A-HJ-NP-Za-km-z]{32,44}')


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def now():
    return datetime.now(timezone.utc).timestamp()


def failure_evidence(error):
    """Persist bounded diagnostic codes, never arbitrary provider error bodies."""
    result={'class':type(error).__name__}
    message=str(error)
    if re.fullmatch(r'[a-z][a-z0-9_]{0,79}',message):result['code']=message
    if type(getattr(error,'sqlite_errorcode',None)) is int:
        result['sqlite_errorcode']=error.sqlite_errorcode
    return result


def epoch(value):
    if not isinstance(value, str):
        raise ValueError('timestamp_string_required')
    stamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if stamp.tzinfo is None:
        raise ValueError('timezone_required')
    return stamp.timestamp()


def quantity(value):
    if not isinstance(value, str) or not re.fullmatch(r'[0-9]{1,40}', value):
        raise ValueError('invalid_raw_quantity')
    return int(value)


def load_config(path=CONFIG):
    cfg = json.loads(Path(path).read_text(encoding='utf-8-sig'))
    return validate_config(cfg)


def validate_config(cfg):
    if type(cfg.get('schema_version')) is not int or cfg['schema_version'] != 1:
        raise ValueError('invalid_paper_config')
    for key in ('execution_authority_pct',):
        if type(cfg.get(key)) is not int or cfg[key] != 0:
            raise ValueError('paper_authority_required')
    for key in ('live_trading_enabled', 'broadcast_enabled', 'signing', 'wallet_access'):
        if cfg.get(key) is not False:
            raise ValueError('paper_authority_required')
    if type(cfg.get('target_intents')) is not int or cfg['target_intents'] != 200:
        raise ValueError('frozen_200_intent_contract')
    for key, value in cfg.items():
        if key in {'schema_version', 'target_intents', 'execution_authority_pct', 'goals',
                   'position_fraction', 'live_trading_enabled', 'broadcast_enabled', 'signing', 'wallet_access'}:
            continue
        if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
            raise ValueError('invalid_paper_parameter:' + key)
    fraction = Decimal(cfg['position_fraction'])
    if not fraction.is_finite() or not 0 < fraction <= Decimal('0.1'):
        raise ValueError('invalid_paper_position_fraction')
    for key in ('reserve_lamports','network_fee_lamports_per_side','extra_fee_bps_per_side','maximum_api_failures'):
        if type(cfg.get(key)) is not int:raise ValueError('integer_paper_parameter_required:'+key)
    goals = cfg.get('goals')
    if not isinstance(goals, dict) or set(goals) != {'win_rate_pct','profit_factor','max_drawdown_pct','top3_net_profit_share_pct'} or any(
        type(v) not in (int,float) or not math.isfinite(v) for v in goals.values()):
        raise ValueError('invalid_paper_goals')
    if not (70 <= goals['win_rate_pct'] <= 100 and goals['profit_factor'] >= 2 and
            0 < goals['max_drawdown_pct'] <= 15 and 0 < goals['top3_net_profit_share_pct'] <= 25):
        raise ValueError('invalid_paper_goals')
    if not 0.5 <= cfg['minimum_buy_ratio'] < 1 or cfg['extra_fee_bps_per_side'] >= 10000 or not 0 < cfg['halt_drawdown_pct'] < cfg['goals']['max_drawdown_pct'] <= 15:
        raise ValueError('invalid_paper_risk_limits')
    if cfg['maximum_momentum_pct'] < cfg['minimum_momentum_pct'] or cfg['execution_delay_seconds'] >= cfg['signal_max_age_seconds']:
        raise ValueError('invalid_paper_time_or_momentum')
    return cfg


def code_hash():
    files = [Path(__file__), Path(__file__).with_name('scoring.py'),
             Path(__file__).with_name('models.py'), Path(__file__).with_name('config.py'),
             Path(__file__).with_name('providers.py'),
             Path(__file__).with_name('projection_io.py'), Path(__file__).with_name('cli.py'),
             Path(__file__).with_name('engine.py'), Path(__file__).with_name('store.py'),
             Path(__file__).with_name('learning.py'), Path(__file__).with_name('shadow_session.py'),
             Path(__file__).parent/'brain_v1/costs.py',
             ROOT/'RUNTIME/imc_resilience.py', ROOT/'config/imc-execution.json',
             ROOT/'config/owner-broadcast-gate.json', ROOT / 'TOOLS/GMGN-ReadBroker.ps1',
             ROOT / 'TOOLS/GMGN-ReadProtocol.ps1', ROOT / 'TOOLS/Invoke-GMGN-ReadBroker.ps1']
    return hashlib.sha256(encoded({str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
                                  for p in files}).encode()).hexdigest()


class GMGNReadError(RuntimeError):
    """Only the authenticated shim's bounded error schema, never provider text."""
    def __init__(self, evidence=None):
        super().__init__('gmgn_read_unavailable')
        self.evidence = MappingProxyType(evidence) if evidence is not None else None

    @classmethod
    def from_stderr(cls, stderr):
        fields = {'origin', 'code', 'http_status', 'api_code', 'retry_after_seconds',
                  'quota_limit', 'quota_remaining', 'reset_source', 'api_error'}
        def unique(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError('duplicate_evidence_field')
                result[key] = value
            return result
        try:
            if not isinstance(stderr, str) or len(stderr) > 4096:
                raise ValueError('evidence_size')
            lines = [line[len('GMGN_READ_EVIDENCE='):] for line in stderr.splitlines()
                     if line.startswith('GMGN_READ_EVIDENCE=')]
            if len(lines) != 1:
                raise ValueError('evidence_count')
            value = json.loads(lines[0], object_pairs_hook=unique)
            if (not isinstance(value, dict) or set(value) != fields
                    or value['origin'] not in {'local_error', 'local_cooldown', 'provider_response', 'transport_error'}
                    or not isinstance(value['code'], str)
                    or not re.fullmatch(r'GMGN_[A-Z0-9_]{1,59}', value['code'])
                    or value['reset_source'] not in {'unknown', 'header', 'body', 'header_and_body'}
                    or value['api_error'] not in (None, 'RATE_LIMIT_EXCEEDED', 'RATE_LIMIT_BANNED', 'ERROR_RATE_LIMIT_BLOCKED')):
                raise ValueError('evidence_schema')
            for key, low, high in (('http_status', 100, 599), ('api_code', 0, 999999999),
                                   ('quota_limit', 0, 1000000000), ('quota_remaining', 0, 1000000000),
                                   ('retry_after_seconds', -1, 86400)):
                number = value[key]
                if number is not None and (type(number) is not int or not low <= number <= high):
                    raise ValueError('evidence_number')
            rate = value['code'].startswith('GMGN_RATE_') or value['code'] == 'GMGN_COOLDOWN_UNKNOWN'
            if ((rate and value['retry_after_seconds'] in (None, 0))
                    or (not rate and value['retry_after_seconds'] is not None)
                    or (value['origin'] == 'local_cooldown' and not rate)
                    or (value['origin'] != 'provider_response' and
                        (value['reset_source'] != 'unknown' or any(value[k] is not None for k in
                         ('http_status', 'api_code', 'quota_limit', 'quota_remaining', 'api_error'))))
                    or (value['api_error'] is not None and not rate)
                    or (value['origin'] == 'provider_response' and
                        value['http_status'] is None and value['api_code'] is None)
                    or (value['quota_limit'] is not None and value['quota_remaining'] is not None
                        and value['quota_remaining'] > value['quota_limit'])):
                raise ValueError('evidence_consistency')
            return cls(value)
        except (ValueError, TypeError, KeyError):
            return cls()


class ObservationUnavailable(ValueError):
    """Expected unavailable/stale provider evidence; never a ledger-state error."""


class GMGNReader:
    """Only the project read-only shim, never PATH fallback or credential access."""
    def __init__(self, cfg):
        self.cfg = cfg
        self.deadline = None

    def read(self, *args):
        shim = ROOT / 'TOOLS/GMGN_READ_SHIM/gmgn-cli.cmd'
        broker = json.loads((ROOT / 'config/imc-gmgn-read-broker.json').read_text())
        timeout=broker['response_timeout_seconds']+2
        previous=os.environ.get('IMC_GMGN_READ_DEADLINE_UTC')
        if self.deadline is not None:
            remaining=self.deadline-time.monotonic()
            if remaining<=0:raise ObservationUnavailable('cohort_read_deadline')
            timeout=min(timeout,remaining)
            os.environ['IMC_GMGN_READ_DEADLINE_UTC']=datetime.fromtimestamp(now()+remaining,timezone.utc).isoformat(timespec='microseconds')
        try:
            result = subprocess.run([str(shim), *args], stdin=subprocess.DEVNULL,
                                    capture_output=True, text=True,timeout=timeout)
        finally:
            if previous is None:os.environ.pop('IMC_GMGN_READ_DEADLINE_UTC',None)
            else:os.environ['IMC_GMGN_READ_DEADLINE_UTC']=previous
        if result.returncode:
            raise GMGNReadError.from_stderr(result.stderr)
        return json.loads(result.stdout)

    def wallet(self):
        wallets = [w for w in self.read('portfolio', 'info', '--raw')['wallets'] if w['chain'] == 'sol']
        if len(wallets) != 1 or not ADDRESS.fullmatch(wallets[0]['address']):
            raise ValueError('ambiguous_sol_wallet')
        balances = [b for b in wallets[0]['balances'] if b['symbol'] == 'SOL']
        if len(balances) != 1:
            raise ValueError('ambiguous_sol_balance')
        amount = Decimal(balances[0]['balance']) * 1_000_000_000
        if not amount.is_finite() or amount != amount.to_integral_value() or amount <= 0:
            raise ValueError('invalid_sol_balance')
        return wallets[0]['address'], int(amount)

    def quote(self, wallet, input_token, output_token, amount):
        monotonic_start = time.monotonic()
        requested = now()
        result = self.read('order', 'quote', '--chain', 'sol', '--from', wallet,
                           '--input-token', input_token, '--output-token', output_token,
                           '--amount', str(amount), '--slippage', '1', '--raw')
        received = now()
        elapsed = time.monotonic() - monotonic_start
        if elapsed < 0 or abs((received-requested)-elapsed) > .5 or elapsed > self.cfg['quote_max_latency_seconds']:
            raise ObservationUnavailable('quote_clock_jump_or_slow')
        try:
            return validate_quote(result, input_token, output_token, amount, requested, received, self.cfg)
        except ValueError as error:
            raise ObservationUnavailable('quote_invalid') from error


def validate_quote(result, input_token, output_token, amount, requested, received, cfg):
    if any(type(v) not in (int,float) or not math.isfinite(v) for v in (requested,received)):
        raise ValueError('quote_invalid_timestamp')
    if result.get('input_token') != input_token or result.get('output_token') != output_token or quantity(result.get('input_amount')) != amount:
        raise ValueError('quote_identity_mismatch')
    output, minimum = quantity(result.get('output_amount')), quantity(result.get('min_output_amount'))
    slippage = result.get('slippage')
    if type(slippage) not in (int, float) or not math.isfinite(slippage) or not 0 <= slippage <= 1:
        raise ValueError('quote_slippage_mismatch')
    if output <= 0 or not 0 < minimum <= output or received < requested or received - requested > cfg['quote_max_latency_seconds']:
        raise ValueError('quote_invalid_or_slow')
    return {**{k: result[k] for k in ('input_token','output_token','input_amount','output_amount','min_output_amount','slippage')},
            'requested_at': requested, 'received_at': received, 'source': 'GMGN_ORDER_QUOTE', 'is_fill': False}


def brain(current, previous, cfg, observed_at, *, evidence=None, cabal_observation=None, cabal_owner=None,
          cabal_holder_observation=None):
    """Causal frozen hypothesis: security, SAFE100, confirmation, drain and flow."""
    reasons = []
    if not ADDRESS.fullmatch(current.get('address', '')) or current.get('market_data_stale') is not False:
        reasons.append('identity_or_stale_market')
    sec = current.get('security', {})
    if not isinstance(sec, dict):
        reasons.append('invalid_security_schema')
        sec = {}
    prior_sec = previous.get('security') if isinstance(previous, dict) else None
    checks = {}
    if previous is not None and not isinstance(prior_sec, dict):
        reasons.append('invalid_previous_security_schema')
        prior_sec = {}
    # A checked flag alone cannot establish independent security freshness.
    try:
        if not 0 <= observed_at-epoch(sec['checked_at']) <= cfg['snapshot_max_age_seconds']:
            reasons.append('security_timestamp_not_fresh')
    except (KeyError,TypeError,ValueError):
        reasons.append('security_timestamp_unverified')
    required = {'checked': True, 'mint_authority_disabled': True, 'freeze_authority_disabled': True,
                'non_transferable': False, 'transfer_hook_present': False, 'malicious_creator': False}
    if cabal_observation is not None:
        # Explicit virtual observed-risk contract. Never rewrite malicious_creator.
        from .cabal_security import authority_off, evaluate, bind_holders
        authority_off()
        creator = evaluate(cabal_observation, current.get('address'), cabal_owner,
                           observed_at, max_age=min(10, cfg['snapshot_max_age_seconds']))
        del required['malicious_creator']
        checks['creator_risk_status'] = creator
        binding = bind_holders(cabal_holder_observation, creator, sec, current.get('address'), observed_at)
        checks['original_deployer_holder_binding'] = binding
        if binding['status'] != 'PASS':
            reasons.append('security:original_deployer_holder_binding')
        if creator['status'] != 'PASS':
            reasons.append('security:creator_risk_status')
        # Previously established malicious evidence remains an independent veto.
        if sec.get('malicious_creator') is True:
            reasons.append('security:malicious_creator')
            checks['malicious_creator'] = {'status': 'FAIL', 'reason': 'known_malicious_creator_veto'}
    for field, expected in required.items():
        if sec.get(field) is not expected:
            reasons.append('security:' + field)
        value = sec.get(field)
        checks[field] = {'status': 'PASS' if value is expected else (
            'FAIL' if type(value) is bool and field != 'checked' else 'UNKNOWN'),
            'reason': 'expected_value' if value is expected else 'unsafe_or_missing:' + field}
    if sec.get('honeypot') is True:
        reasons.append('security:honeypot')
    # GoPlus Solana has no guaranteed honeypot flag; fresh two-way route quotes
    # are mandatory before a position can count as quote-observed sellable.
    for field, cap in [('top10_holder_pct', cfg['maximum_top10_holder_pct']), ('creator_holder_pct', cfg['maximum_creator_holder_pct'])]:
        value = sec.get(field)
        if type(value) not in (int,float) or not math.isfinite(value) or not 0 <= value <= cap:
            reasons.append('security:' + field)
        known = type(value) in (int,float) and math.isfinite(value) and 0 <= value <= 100
        checks[field] = {'status': 'UNKNOWN' if not known else ('PASS' if value <= cap else 'FAIL'),
                         'reason': 'holder_value_missing_or_invalid' if not known else 'holder_cap_check'}
    if evidence is not None:
        evidence['security_checks'] = checks
        evidence['security_complete'] = all(v['status'] != 'UNKNOWN' for v in checks.values())
        evidence['holder_complete'] = all(checks[f]['status'] != 'UNKNOWN'
                                         for f in ('top10_holder_pct','creator_holder_pct'))
        if cabal_observation is not None:
            evidence['holder_complete'] = evidence['holder_complete'] and binding['status'] == 'PASS'
    try:
        age = observed_at - epoch(current['captured_at'])
        if not 0 <= age <= cfg['snapshot_max_age_seconds']:
            reasons.append('snapshot_not_fresh')
        if not previous or not 0 < epoch(current['captured_at']) - epoch(previous['captured_at']) <= cfg['confirmation_max_gap_seconds']:
            reasons.append('second_confirmation_missing')
        model_security = {k:v for k,v in sec.items() if k != 'checked_at'}
        snapshot = TokenSnapshot(**{**current, 'security': SecuritySignals(**model_security)})
        prior_security = {k:v for k,v in prior_sec.items() if k != 'checked_at'} if previous else None
        prior = TokenSnapshot(**{**previous, 'security': SecuritySignals(**prior_security)}) if previous else None
        micro = derive_micro_metrics(snapshot, prior)
        score = assess(snapshot, Config(), micro)
        if evidence is not None:
            evidence.update(micro_metrics=asdict(micro), score=score.score, hard_failures=score.hard_failures,
                            security_checked_at=sec.get('checked_at'))
        if score.hard_failures or score.score < cfg['minimum_score']:
            reasons.append('core_score_or_hard_gate')
        if snapshot.liquidity_usd < cfg['minimum_liquidity_usd'] or snapshot.age_minutes > cfg['maximum_entry_age_minutes']:
            reasons.append('safe100_liquidity_or_age')
        if snapshot.buy_ratio < cfg['minimum_buy_ratio'] or not cfg['minimum_momentum_pct'] <= snapshot.price_change_5m_pct <= cfg['maximum_momentum_pct'] or snapshot.volume_5m_usd / max(snapshot.liquidity_usd,1) > cfg['maximum_volume_liquidity_ratio']:
            reasons.append('safe100_flow_or_momentum')
        if prior is not None:
            if prior.liquidity_usd <= 0 or snapshot.liquidity_usd < prior.liquidity_usd * (1 - cfg['liquidity_drain_pct']/100):
                reasons.append('liquidity_drain')
            if snapshot.price_usd <= prior.price_usd or snapshot.buy_ratio < prior.buy_ratio - 0.05:
                reasons.append('microstructure_deteriorating')
        numeric = ['price_usd','liquidity_usd','market_cap_usd','volume_5m_usd','price_change_5m_pct','buys_5m','sells_5m']
        if any(type(current[k]) not in (int,float) or not math.isfinite(current[k]) or current[k] < 0 for k in numeric):
            reasons.append('invalid_market_number')
    except (KeyError, TypeError, ValueError, OverflowError):
        reasons.append('invalid_snapshot_schema')
    return sorted(set(reasons))


class PaperBook:
    def __init__(self, path, cfg, wallet, initial_cash, source_checkpoint, *, clock=now, policy_hash=None):
        cfg=validate_config(json.loads(encoded(cfg)))
        self.path, self.cfg, self.clock = Path(path), cfg, clock
        if type(initial_cash) is not int or initial_cash <= cfg['reserve_lamports']:
            raise ValueError('paper_capital_below_reserve')
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        try:
            self.conn.row_factory = sqlite3.Row
            self.conn.execute('PRAGMA journal_mode=WAL')
            self.conn.execute('PRAGMA synchronous=FULL')
            self.conn.executescript('CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,doc TEXT NOT NULL);'
                                   'CREATE TABLE IF NOT EXISTS intents(id INTEGER PRIMARY KEY, candidate_id TEXT UNIQUE NOT NULL,doc TEXT NOT NULL);'
                                   'CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY,at REAL NOT NULL,kind TEXT NOT NULL,doc TEXT NOT NULL);'
                                   'CREATE TABLE IF NOT EXISTS equity(id INTEGER PRIMARY KEY,at REAL NOT NULL,value TEXT,reason TEXT);')
            self.policy = {'config': cfg, 'code_hash': policy_hash or code_hash(), 'model': 'CORE_SCORE_CAUSAL_RULE_POLICY',
                           'training': 'NO_COHORT_TRAINING_OR_RETROSPECTIVE_SELECTION'}
            with self.transaction():
                if self.meta('policy') is None:
                    if any(self.conn.execute('SELECT count(*) FROM '+table).fetchone()[0] for table in ('meta','intents','audit','equity')):
                        raise ValueError('corrupt_paper_cohort_missing_policy')
                    self.set_meta('policy', self.policy)
                    self.set_meta('wallet', wallet)
                    self.set_meta('initial_cash', initial_cash)
                    self.set_meta('cash', initial_cash)
                    self.set_meta('peak', initial_cash)
                    self.set_meta('max_drawdown_pct', 0)
                    self.set_meta('started_at', self.clock())
                    self.set_meta('checkpoint', source_checkpoint)
                    self.set_meta('halt', None)
                    self.set_meta('evidence_complete', True)
                    self.set_meta('api_failures', 0)
                    self.event('cohort_frozen', {'policy': self.policy, 'source': str(SOURCE), 'checkpoint': source_checkpoint})
                elif self.meta('policy') != self.policy or self.meta('wallet') != wallet:
                    raise ValueError('frozen_cohort_policy_or_wallet_changed')

        except BaseException:
            self.conn.close()
            raise

    @contextmanager
    def transaction(self):
        self.conn.execute('BEGIN IMMEDIATE')
        try:
            yield
            self.conn.execute('COMMIT')
        except BaseException:
            if self.conn.in_transaction:
                self.conn.execute('ROLLBACK')
            raise

    def meta(self, key):
        row = self.conn.execute('SELECT doc FROM meta WHERE key=?', (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def set_meta(self, key, value):
        self.conn.execute('INSERT INTO meta VALUES(?,?) ON CONFLICT(key) DO UPDATE SET doc=excluded.doc', (key, encoded(value)))

    def event(self, kind, doc):
        self.conn.execute('INSERT INTO audit(at,kind,doc) VALUES(?,?,?)', (self.clock(), kind, encoded(doc)))

    def intents(self):
        return [json.loads(row[0]) for row in self.conn.execute('SELECT doc FROM intents ORDER BY id')]

    def save(self, trade):
        old_version=trade['version']
        updated={**trade,'version':old_version+1}
        changed=self.conn.execute('UPDATE intents SET doc=? WHERE candidate_id=? AND json_extract(doc,\'$.version\')=?',
                                 (encoded(updated),trade['candidate_id'],old_version)).rowcount
        if changed != 1: raise ValueError('stale_paper_intent')
        trade.update(updated)

    def require_current(self, trade, states):
        row=self.conn.execute('SELECT doc FROM intents WHERE candidate_id=?',(trade['candidate_id'],)).fetchone()
        if not row or trade.get('state') not in states or encoded(json.loads(row[0])) != encoded(trade):
            raise ValueError('stale_paper_intent')

    def validate_observation(self, quote, input_token, output_token, amount):
        if quote.get('source') != 'GMGN_ORDER_QUOTE' or quote.get('is_fill') is not False:
            raise ObservationUnavailable('unverified_paper_observation')
        try:
            validate_quote(quote,input_token,output_token,amount,quote['requested_at'],quote['received_at'],self.cfg)
        except ValueError as error:
            raise ObservationUnavailable('quote_invalid') from error
        if not 0 <= self.clock()-quote['received_at'] <= self.cfg['quote_max_latency_seconds']:
            raise ObservationUnavailable('stale_paper_observation')

    def api_failure(self):
        failures=self.meta('api_failures')+1
        self.set_meta('api_failures',failures)
        if failures >= self.cfg['maximum_api_failures']:
            self.set_meta('halt','api_failure_limit')

    def active(self):
        return next((t for t in self.intents() if t['state'] not in {'CLOSED','FAILED'}), None)

    def admit(self, snapshot, snapshot_id):
        with self.transaction():
            return self._admit(snapshot,snapshot_id)

    def consider(self,snapshot,snapshot_id,previous,previous_id,reasons,evidence):
        with self.transaction():
            if snapshot_id <= self.meta('checkpoint'): return None
            self.set_meta('checkpoint',snapshot_id)
            counts=self.meta('rejection_counts') or {}
            for reason in reasons:counts[reason]=counts.get(reason,0)+1
            self.set_meta('rejection_counts',counts)
            self.event('selection_decision',{'snapshot_id':snapshot_id,'previous_snapshot_id':previous_id,
                                            'mint':snapshot['address'],'reasons':reasons,'assessment':evidence})
            coverage=self.meta('coverage') or {'evaluated':0,'security_complete':0,'holder_complete':0,
                                               'security_fail':0,'security_unknown':0,'market_fresh':0}
            coverage['evaluated']+=1
            coverage['security_complete']+=int(evidence.get('security_complete') is True)
            coverage['holder_complete']+=int(evidence.get('holder_complete') is True)
            statuses=[v['status'] for v in evidence.get('security_checks',{}).values()]
            coverage['security_fail']+=int('FAIL' in statuses)
            coverage['security_unknown']+=int(not statuses or 'UNKNOWN' in statuses)
            coverage['market_fresh']+=int(not any(r in reasons for r in ('snapshot_not_fresh','identity_or_stale_market','invalid_snapshot_schema')))
            self.set_meta('coverage',coverage)
            if reasons:return None
            return self._admit(snapshot,snapshot_id,{'previous_snapshot_id':previous_id,
                'previous_snapshot':previous,'assessment':evidence})

    def _admit(self,snapshot,snapshot_id,selection=None):
        if self.meta('halt') or self.active() or len(self.intents()) >= self.cfg['target_intents']:
            self.set_meta('risk_blocked',int(self.meta('risk_blocked') or 0)+1)
            return None
        if self.conn.execute('SELECT 1 FROM intents WHERE json_extract(doc,\'$.mint\')=?',(snapshot['address'],)).fetchone():
            return None
        amount=int(Decimal(self.meta('initial_cash'))*Decimal(self.cfg['position_fraction']))
        fee=self.cfg['network_fee_lamports_per_side']
        if amount <= 0 or self.meta('cash') < amount+fee*2+self.cfg['reserve_lamports']:
            self.set_meta('risk_blocked',int(self.meta('risk_blocked') or 0)+1)
            self.set_meta('halt','capital_reserve');return None
        candidate=hashlib.sha256(encoded([str(SOURCE),snapshot['address'],snapshot_id,self.policy]).encode()).hexdigest()
        trade = {'candidate_id': candidate, 'version':1, 'mint': snapshot['address'], 'snapshot_id': snapshot_id,
                     'signal_at': epoch(snapshot['captured_at']), 'admitted_at': self.clock(),
                     'due_at': self.clock() + self.cfg['execution_delay_seconds'], 'state': 'ENTERING',
                     'input_lamports': amount, 'reference_price': snapshot['price_usd'],
                     'features': snapshot, 'selection_evidence':selection, 'last_mark_at': None, 'best_mark': 0, 'exit_reason': None,
                     'net_pnl_lamports': None, 'shadow_pnl_lamports': None,
                     'evidence_class': 'GMGN_QUOTE_PAPER_NOT_CHAIN_FILLS'}
        self.conn.execute('INSERT INTO intents(candidate_id,doc) VALUES(?,?)',(candidate,encoded(trade)))
        self.event('admitted_before_quote',trade)
        return trade

    def fail(self, trade, reason, *, api_failure=False):
        with self.transaction():
            self.require_current(trade,{'ENTERING'})
            if api_failure:self.api_failure()
            updated={**trade,'state':'FAILED','failure':reason,'ended_at':self.clock()}
            self.save(updated)
            self.event('intent_failed', {'candidate_id':trade['candidate_id'], 'reason':reason})
        trade.update(updated)

    def net_output(self, quote):
        return quantity(quote['min_output_amount']) * (10000-self.cfg['extra_fee_bps_per_side']) // 10000

    def open(self, trade, quote, sell_quote):
        output = self.net_output(quote)
        amount = trade['input_lamports']
        if output <= 0:
            raise ValueError('paper_zero_entry')
        with self.transaction():
            self.require_current(trade,{'ENTERING'})
            if not 0 <= self.clock()-trade['signal_at'] <= self.cfg['signal_max_age_seconds']:
                raise ObservationUnavailable('stale_paper_signal')
            self.validate_observation(quote,SOL,trade['mint'],amount)
            self.validate_observation(sell_quote,trade['mint'],SOL,output)
            try:
                security_at = epoch(trade['features']['security']['checked_at'])
            except (KeyError, TypeError, ValueError):
                raise ObservationUnavailable('unverified_paper_security') from None
            if not 0 <= self.clock()-security_at <= self.cfg['snapshot_max_age_seconds']:
                raise ObservationUnavailable('stale_paper_security')
            self.set_meta('cash', self.meta('cash') - amount - self.cfg['network_fee_lamports_per_side'])
            updated=dict(trade)
            updated.update(state='OPEN', entry_quote=quote, entry_raw=str(output), sellability_quote=sell_quote,
                         opened_at=self.clock(), due_at=self.clock(), entry_cost_lamports=self.cfg['network_fee_lamports_per_side'])
            self._mark(updated,sell_quote,initial=True)
            self.save(updated)
            self.event('simulated_entry', {'candidate_id':trade['candidate_id'],'raw':str(output),'quote':quote,'sell_quote':sell_quote})
        trade.update(updated)

    def mark(self, trade, quote):
        with self.transaction():
            self.require_current(trade,{'OPEN','EXITING'})
            self.validate_observation(quote,trade['mint'],SOL,quantity(trade['entry_raw']))
            updated=dict(trade)
            value=self._mark(updated,quote)
            self.save(updated)
        trade.update(updated)
        return value

    def _mark(self, trade, quote, *, initial=False):
        value = max(0, self.net_output(quote) - self.cfg['network_fee_lamports_per_side'])
        last=trade['last_mark_at']
        if not initial and (last is None or not 0 <= self.clock()-last <= self.cfg['valuation_max_gap_seconds']):
            self.set_meta('evidence_complete',False)
            self.event('valuation_gap',{'candidate_id':trade['candidate_id'],'seconds':None if last is None else self.clock()-last})
        trade.update(last_mark_at=self.clock(),last_mark=value,best_mark=max(trade['best_mark'],value))
        equity=self.meta('cash')+value
        peak=max(equity,self.meta('peak'))
        dd=max(0,(peak-equity)/peak*100)
        self.set_meta('peak',peak)
        self.set_meta('max_drawdown_pct',max(self.meta('max_drawdown_pct'),dd))
        if dd >= self.cfg['halt_drawdown_pct']:self.set_meta('halt','drawdown_limit')
        self.conn.execute('INSERT INTO equity(at,value,reason) VALUES(?,?,?)',(self.clock(),str(equity),None))
        return value

    def unknown(self, trade, reason):
        with self.transaction():
            self.require_current(trade,{'OPEN','EXITING'})
            self.event('quote_unavailable', {'candidate_id':trade['candidate_id'],'reason':reason})
            self.api_failure()
            if trade['last_mark_at'] is None or not 0 <= self.clock()-trade['last_mark_at'] <= self.cfg['valuation_max_gap_seconds']:
                self.set_meta('evidence_complete',False)
                self.conn.execute('INSERT INTO equity(at,value,reason) VALUES(?,NULL,?)',(self.clock(),reason))
            # Preserve the entire obligation, including pending exits, on errors.
            updated=dict(trade)
            self.save(updated)
        trade.update(updated)

    def request_exit(self,trade,reason):
        with self.transaction():
            self.require_current(trade,{'OPEN'})
            updated={**trade,'state':'EXITING','exit_reason':reason,'due_at':self.clock()+self.cfg['execution_delay_seconds']}
            self.save(updated)
            self.event('exit_intent',{'candidate_id':trade['candidate_id'],'reason':reason})
        trade.update(updated)

    def exit_reason(self, trade, value):
        invested = trade['input_lamports'] + trade['entry_cost_lamports']
        if self.clock()-trade['opened_at'] >= self.cfg['maximum_hold_seconds']:
            return 'MAX_HOLD'
        if value <= invested*(1-self.cfg['hard_stop_pct']/100):
            return 'HARD_STOP'
        if value >= invested*(1+self.cfg['take_profit_pct']/100):
            return 'TAKE_PROFIT'
        if trade['best_mark'] >= invested*(1+self.cfg['trailing_arm_pct']/100) and value <= trade['best_mark']*(1-self.cfg['trailing_stop_pct']/100):
            return 'TRAILING_STOP'
        if self.meta('halt'):
            return 'RISK_EXIT'
        return None

    def close(self, trade, quote):
        value = max(0,self.net_output(quote)-self.cfg['network_fee_lamports_per_side'])
        with self.transaction():
            self.require_current(trade,{'EXITING'})
            self.validate_observation(quote,trade['mint'],SOL,quantity(trade['entry_raw']))
            self._mark(trade.copy(),quote)
            self.set_meta('cash',self.meta('cash')+value)
            updated=dict(trade)
            updated.update(state='CLOSED', ended_at=self.clock(), exit_quote=quote,
                         net_pnl_lamports=value-trade['input_lamports']-trade['entry_cost_lamports'])
            self.save(updated)
            self.event('simulated_exit',{'candidate_id':trade['candidate_id'],'quote':quote,'net_lamports':updated['net_pnl_lamports']})
        trade.update(updated)

    def report(self):
        cfg=validate_config(self.meta('policy')['config'])
        trades = self.intents()
        values = [t['net_pnl_lamports'] for t in trades if t['state']=='CLOSED']
        positive, loss = sum(max(0,v) for v in values), -sum(min(0,v) for v in values)
        active = self.active()
        known = active is None or (active['state'] in {'OPEN','EXITING'} and active['last_mark_at'] is not None and 0 <= self.clock()-active['last_mark_at'] <= cfg['valuation_max_gap_seconds'])
        win = 100*sum(v>0 for v in values)/len(trades) if trades else None
        pf = positive/loss if loss else None
        net = sum(values)
        top = 100*sum(sorted([v for v in values if v>0],reverse=True)[:3])/net if net>0 else None
        complete = len(trades)==cfg['target_intents'] and len(values)==len(trades)
        goals = cfg['goals']
        dd = self.meta('max_drawdown_pct') if self.meta('evidence_complete') else None
        passed = bool(complete and known and dd is not None and win>=goals['win_rate_pct'] and
                      (pf is not None and pf>=goals['profit_factor']) and dd<=goals['max_drawdown_pct'] and
                      top is not None and top<=goals['top3_net_profit_share_pct'])
        return {'version':'imc-gmgn-paper','updated_at':self.clock(), 'source':str(self.path)+':intents/audit/equity',
                'evidence_class':'GMGN_QUOTE_PAPER_NOT_CHAIN_FILLS','unit':'lamports','policy':self.meta('policy'),
                'started_at':self.meta('started_at'),'target_intents':cfg['target_intents'],
                'admitted':len(trades),'closed':len(values),'states':dict(Counter(t['state'] for t in trades)),
                'cash_lamports':str(self.meta('cash')),'current_equity_lamports':str(self.meta('cash')+(active.get('last_mark',0) if active else 0)) if known else None,
                'net_pnl_lamports':str(net) if values else None,'win_rate_pct':win,'profit_factor':pf,
                'profit_factor_note':'undefined_without_losses' if not loss else None,
                'top3_net_profit_share_pct':top,'max_drawdown_pct':dd,'valuation_known':known,
                'evidence_complete':self.meta('evidence_complete'),'halt':self.meta('halt'),
                'api_failures':self.meta('api_failures'),'cohort_complete':complete,'goals':goals,
                'goals_passed':passed,'execution_authority_pct':0,'broadcast_enabled':False,
                'signing':False,'wallet_access':False, 'active_intent':active,
                'cost_model':'GMGN minimum output; additional conservative fee haircut each side; fixed network/priority allowance. Not observed chain fees.',
                **self.performance_summary(trades)}

    def performance_summary(self, trades=None):
        """Every closed cohort trade, including losses; failed intents stay visible."""
        from .brain_v1.costs import CostPolicy
        trades = self.intents() if trades is None else trades
        closed = [t for t in trades if t['state']=='CLOSED']
        values = [t['net_pnl_lamports'] for t in closed]
        wins = [v for v in values if v>0]; losses = [v for v in values if v<0]
        capital = self.meta('capital_evidence')
        unit = Decimal(capital['eur_per_lamport']) if capital else None
        convert = lambda v: float(Decimal(v)*unit) if v is not None and unit is not None else None
        ordered = sorted(closed,key=lambda t:t['net_pnl_lamports'],reverse=True)
        removed = {t['candidate_id'] for t in [t for t in ordered if t['net_pnl_lamports']>0][:3]}
        tail = [t['net_pnl_lamports'] for t in closed if t['candidate_id'] not in removed]
        pf = lambda xs: sum(max(0,v) for v in xs)/(-sum(min(0,v) for v in xs)) if any(v<0 for v in xs) else None
        known = self.active() is None
        settled = [t for t in trades if t['state'] in {'OPEN','EXITING','CLOSED'}]
        expected = self.meta('initial_cash')-sum(t['input_lamports']+t['entry_cost_lamports'] for t in settled)
        expected += sum(t['input_lamports']+t['entry_cost_lamports']+t['net_pnl_lamports'] for t in closed)
        actual = self.meta('cash')
        audit_exits = self.conn.execute("SELECT count(*) FROM audit WHERE kind='simulated_exit'").fetchone()[0]
        reconciliation = {'cash_matches':expected==actual,'expected_cash_lamports':str(expected),
            'actual_cash_lamports':str(actual),'closed_matches_exit_events':len(closed)==audit_exits,
            'open_obligations':sum(t['state'] in {'ENTERING','OPEN','EXITING'} for t in trades)}
        network = sum(t['entry_cost_lamports'] for t in settled)+len(closed)*self.cfg['network_fee_lamports_per_side']
        haircut = 0.; slippage = 0.
        for t in settled:
            buy=t['entry_quote']; sell=t['sellability_quote']
            per_raw=quantity(sell['output_amount'])/quantity(sell['input_amount'])
            haircut += (quantity(buy['min_output_amount'])-quantity(t['entry_raw']))*per_raw
            slippage += (quantity(buy['output_amount'])-quantity(buy['min_output_amount']))*per_raw
            if t['state']=='CLOSED':
                exit=t['exit_quote']
                haircut += quantity(exit['min_output_amount'])-self.net_output(exit)
                slippage += quantity(exit['output_amount'])-quantity(exit['min_output_amount'])
        coverage=self.meta('coverage') or {}
        n=coverage.get('evaluated',0)
        rows=lambda ts:[{'candidate_id':t['candidate_id'],'mint':t['mint'],'net_pnl_eur':convert(t['net_pnl_lamports']),
                         'net_pnl_lamports':str(t['net_pnl_lamports']),'exit_reason':t['exit_reason']} for t in ts]
        rejections=self.meta('rejection_counts') or {}
        category=lambda names:sum(rejections.get(name,0) for name in names)
        blocked={'stale_market_data':category(('snapshot_not_fresh','identity_or_stale_market')),
                 'security_fail':coverage.get('security_fail',0), 'security_unknown':coverage.get('security_unknown',0),
                 'missing_holder_data':n-coverage.get('holder_complete',0),
                 'liquidity':category(('safe100_liquidity_or_age','liquidity_drain')),
                 'slippage_price_impact':sum(t.get('failure')=='roundtrip_cost_exceeds_stop_budget' for t in trades),
                 'risk_engine':int(self.meta('risk_blocked') or 0),
                 'other_reasons':{k:v for k,v in rejections.items() if not k.startswith('security')
                    and k not in {'snapshot_not_fresh','identity_or_stale_market','safe100_liquidity_or_age','liquidity_drain'}}}
        return {'closed_win_rate_pct':100*len(wins)/len(closed) if closed else None,
                'winners':len(wins),'losers':len(losses),'breakeven':len(values)-len(wins)-len(losses),
                'runtime_seconds':max(0,self.clock()-self.meta('started_at')),
                'initial_capital_eur':capital['initial_eur'] if capital else None,
                'end_capital_eur':convert(actual) if known else None,
                'closed_net_pnl_eur':convert(sum(values)) if values else None,
                'average_win_eur':convert(sum(wins)/len(wins)) if wins else None,
                'average_loss_eur':convert(sum(losses)/len(losses)) if losses else None,
                'average_trade_eur':convert(sum(values)/len(values)) if values else None,
                'largest_winner_eur':convert(max(wins)) if wins else None,
                'largest_loser_eur':convert(min(losses)) if losses else None,
                'profit_factor_without_top3':pf(tail),'top20':rows(ordered[:20]),'flop20':rows(list(reversed(ordered))[:20]),
                'network_cost_eur':convert(network),'fee_haircut_estimate_eur':convert(haircut),
                'slippage_reserve_estimate_eur':convert(slippage),'total_explicit_cost_estimate_eur':convert(network+haircut+slippage),
                'cost_attribution':'Entry token charges valued by contemporaneous sellability quote; estimates. Provider fees/impact/drift embedded in quote, not separately observable.',
                'existing_cost_stress_policy':CostPolicy().as_dict(),
                'coverage':coverage,'security_coverage_pct':100*coverage.get('security_complete',0)/n if n else None,
                'holder_coverage_pct':100*coverage.get('holder_complete',0)/n if n else None,
                'rejection_counts':self.meta('rejection_counts') or {},'rejection_count_scope':'Snapshot evaluations; reasons overlap; not unique trades',
                'blocked_categories':blocked,
                'reconciliation':reconciliation,'capital_evidence':capital,
                'performance_reliable':bool(len(closed)>=30 and self.meta('evidence_complete') and known
                    and reconciliation['cash_matches'] and reconciliation['closed_matches_exit_events'])}


def eur_capital_evidence(initial_eur=1000):
    """Reference-rate accounting only; never accesses a wallet balance."""
    from .providers import DexScreenerProvider
    url='https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml'
    requested=now()
    try:
        request=urllib.request.Request(url,headers={'User-Agent':'IMC-Paper-Research/1.0'})
        with urllib.request.urlopen(request,timeout=5) as response:payload=response.read(131073)
        if len(payload)>131072:raise ValueError('fx_document_size')
        root=ET.fromstring(payload)
        dates=[e.attrib['time'] for e in root.iter() if 'time' in e.attrib]
        rates=[e.attrib['rate'] for e in root.iter() if e.attrib.get('currency')=='USD']
    except urllib.error.URLError:
        # Preserve TLS verification. This public service republishes ECB rates;
        # record the actual fallback provenance instead of inventing a rate.
        url='https://api.frankfurter.dev/v1/latest?base=EUR&symbols=USD'
        request=urllib.request.Request(url,headers={'User-Agent':'IMC-Paper-Research/1.0'})
        with urllib.request.urlopen(request,timeout=5) as response:payload=response.read(131073)
        if len(payload)>131072:raise ValueError('fx_document_size')
        doc=json.loads(payload)
        if doc.get('base')!='EUR' or doc.get('amount')!=1:
            raise ValueError('fx_schema')
        dates=[doc['date']];rates=[str(doc['rates']['USD'])]
    if len(dates)!=1 or len(rates)!=1:
        raise ValueError('fx_schema')
    day=datetime.strptime(dates[0],'%Y-%m-%d').replace(tzinfo=timezone.utc)
    if not 0 <= now()-day.timestamp() <= 5*86400:
        raise ValueError('fx_reference_stale_or_future')
    usd=Decimal(rates[0])
    snapshots=DexScreenerProvider().fetch_addresses([SOL])
    values=[s for s in snapshots if s.address==SOL and s.price_usd>0 and not s.market_data_stale
            and 0<=now()-epoch(s.captured_at)<=10]
    if len(values)!=1 or not usd.is_finite() or usd<=0:
        raise ValueError('capital_conversion_unknown')
    sol_usd=Decimal(str(values[0].price_usd))
    euros=Decimal(str(initial_eur))
    if not euros.is_finite() or euros!=1000:
        raise ValueError('virtual_eur_1000_required')
    initial_raw=int(euros*usd/sol_usd*1_000_000_000)
    # Preserve exactly EUR1000 as the accounting unit, including integer rounding.
    return initial_raw, {'initial_eur':1000.0,'eur_per_lamport':str(euros/Decimal(initial_raw)),
        'eur_usd_reference':str(usd),'fx_reference_date':dates[0],'fx_source':url,
        'sol_usd_observation':str(sol_usd),'sol_price_at':values[0].captured_at,
        'sol_price_source':'DEXSCREENER','requested_at':requested,'received_at':now(),
        'accounting':'EUR conversion fixed at cohort start; SOL/FX market exposure excluded from strategy P/L; no wallet capital used'}


def assert_closed_owner_gate():
    execution=json.loads((ROOT/'config/imc-execution.json').read_text(encoding='utf-8-sig'))
    gate=json.loads((ROOT/'config/owner-broadcast-gate.json').read_text(encoding='utf-8-sig'))
    if (any(execution.get(k) is not False for k in ('broadcast_enabled','live_trading_enabled','signing','wallet_access'))
            or type(execution.get('execution_authority_pct')) is not int
            or execution.get('execution_authority_pct')!=0 or gate.get('OWNER_BROADCAST_GATE')!='OFF'):
        raise ValueError('owner_gate_not_closed')


class ForwardRunner:
    def __init__(self, book, reader, source=SOURCE):
        self.book, self.reader, self.source = book, reader, Path(source)

    def source_connection(self):
        c = sqlite3.connect(self.source.resolve().as_uri()+'?mode=ro',uri=True,timeout=2)
        c.row_factory=sqlite3.Row
        c.execute('PRAGMA query_only=ON')
        deadline=time.monotonic()+2
        c.set_progress_handler(lambda:int(time.monotonic()>deadline),1000)
        return c

    def discover(self):
        book=self.book
        with closing(self.source_connection()) as c:
            rows=c.execute('SELECT id,address,payload_json FROM snapshots WHERE id>? ORDER BY id LIMIT 200', (book.meta('checkpoint'),)).fetchall()
            for row in rows:
                payload=json.loads(row['payload_json'])
                previous=c.execute('SELECT id,payload_json FROM snapshots WHERE address=? AND id<? ORDER BY id DESC LIMIT 1', (row['address'],row['id'])).fetchone()
                previous_payload=json.loads(previous['payload_json']) if previous else None
                evidence={}
                reasons=brain(payload,previous_payload,book.cfg,book.clock(),evidence=evidence)
                if payload.get('address') != row['address']:reasons.append('snapshot_source_identity')
                book.consider(payload,row['id'],previous_payload,previous['id'] if previous else None,reasons,evidence)
                if book.active():
                    break

    def tick(self):
        book=self.book
        trade=book.active()
        if trade is None:
            if not book.meta('halt') and len(book.intents())<book.cfg['target_intents']:
                self.discover()
            return
        if book.clock()<trade['due_at']:
            return
        try:
            wallet=book.meta('wallet')
            if trade['state']=='ENTERING':
                if book.clock()-trade['signal_at']>book.cfg['signal_max_age_seconds']:
                    return book.fail(trade,'stale_before_entry_quote')
                quote=self.reader.quote(wallet,SOL,trade['mint'],trade['input_lamports'])
                output=book.net_output(quote)
                sell=self.reader.quote(wallet,trade['mint'],SOL,output)
                if book.clock()-trade['signal_at']>book.cfg['signal_max_age_seconds']:
                    return book.fail(trade,'stale_after_roundtrip_probe')
                if book.net_output(sell)-book.cfg['network_fee_lamports_per_side'] <= trade['input_lamports']*(1-book.cfg['hard_stop_pct']/100):
                    return book.fail(trade,'roundtrip_cost_exceeds_stop_budget')
                book.open(trade,quote,sell)
            else:
                quote=self.reader.quote(wallet,trade['mint'],SOL,quantity(trade['entry_raw']))
                value=book.mark(trade,quote)
                if trade['state']=='EXITING':
                    book.close(trade,quote)
                else:
                    reason=book.exit_reason(trade,value)
                    if reason:
                        book.request_exit(trade,reason)
        except (GMGNReadError, ObservationUnavailable, OSError, subprocess.SubprocessError):
            if trade['state']=='ENTERING':
                book.fail(trade,'entry_quote_unavailable',api_failure=True)
            else:
                book.unknown(trade,'exit_quote_unavailable')
            raise


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--status',action='store_true')
    parser.add_argument('--database',type=Path,default=DATABASE)
    parser.add_argument('--probe',action='store_true')
    parser.add_argument('--status-path',type=Path,default=STATUS)
    parser.add_argument('--virtual-eur',type=int)
    parser.add_argument('--quote-wallet',help='Public quote identity only; no wallet balance or signing')
    parser.add_argument('--collect',action='store_true',help='Run the existing production scanner in signal-only mode')
    parser.add_argument('--duration-seconds',type=int,default=0)
    parser.add_argument('--drain-seconds',type=int,default=180)
    args=parser.parse_args()
    if args.status:
        with closing(sqlite3.connect(args.database.resolve().as_uri()+'?mode=ro',uri=True,timeout=2)) as c:
            c.row_factory=sqlite3.Row
            c.execute('PRAGMA query_only=ON')
            book=PaperBook.__new__(PaperBook); book.path=args.database; book.conn=c; book.clock=now
            book.cfg=validate_config(book.meta('policy')['config'])
            print(json.dumps(book.report(),indent=2))
        return
    cfg=load_config()
    if not 0<=args.duration_seconds<=43200 or not 0<=args.drain_seconds<=180:
        raise ValueError('paper_duration_invalid')
    if args.collect and (args.virtual_eur!=1000 or not args.duration_seconds):
        raise ValueError('bounded_virtual_forward_required')
    assert_closed_owner_gate()
    reader=GMGNReader(cfg)
    capital=None
    if args.virtual_eur is not None:
        if args.virtual_eur!=1000 or not ADDRESS.fullmatch(args.quote_wallet or ''):
            raise ValueError('virtual_capital_or_quote_identity_invalid')
        wallet=args.quote_wallet
        if args.database.exists():
            with closing(sqlite3.connect(args.database.resolve().as_uri()+'?mode=ro',uri=True,timeout=2)) as c:
                c.execute('PRAGMA query_only=ON')
                rows=dict(c.execute("SELECT key,doc FROM meta WHERE key IN ('initial_cash','capital_evidence')"))
            capital=json.loads(rows.get('capital_evidence','null'))
            balance=json.loads(rows.get('initial_cash','null'))
            if not capital or capital.get('initial_eur')!=1000 or type(balance) is not int:
                raise ValueError('existing_virtual_capital_unverified')
        else:
            try:
                balance,capital=eur_capital_evidence(args.virtual_eur)
            except Exception as error:
                write_status(args.status_path,{'runtime_state':'BOOTSTRAP_FAILED',
                    'stop_reason':failure_evidence(error),'pid':os.getpid(),'updated_at':now()})
                raise
    else:
        wallet,balance=reader.wallet()
    if args.probe:
        # Existing liquid SOL/USDC pair: quote only, no order or holdings change.
        quote=reader.quote(wallet,SOL,'EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v',1_000_000)
        print(encoded({'gmgn_read':True,'quote':quote,'broadcast_enabled':False}))
        return
    with closing(sqlite3.connect(SOURCE.resolve().as_uri()+'?mode=ro',uri=True,timeout=2)) as c:
        checkpoint=c.execute('SELECT coalesce(max(id),0) FROM snapshots').fetchone()[0]
    book=PaperBook(args.database,cfg,wallet,balance,checkpoint)
    if capital:
        with book.transaction():
            if book.meta('capital_evidence') is not None and book.meta('capital_evidence')!=capital:
                raise ValueError('frozen_capital_conversion_changed')
            book.set_meta('capital_evidence',capital)
            book.event('virtual_capital_initialized',capital)
    runner=ForwardRunner(book,reader)
    started=time.monotonic(); wall_started=now();stop=threading.Event();source_lock=threading.Lock()
    source={'state':'NOT_COLLECTING'};collector=None;terminal_reason=None
    def collect():
        from .cli import scan_once
        core_cfg=Config()
        try:
            while not stop.is_set():
                cycle=time.monotonic();observation={};phases={}
                scan_once(core_cfg,SOURCE,30,manage_open_positions=False,watcher_priority=True,
                          signal_only=True,timing_out=phases,observation_out=observation)
                observation.update(state='SCANNED',updated_at=now(),cycle_seconds=time.monotonic()-cycle,
                                   cycle_monotonic=time.monotonic())
                with source_lock:
                    source.clear();source.update(observation)
                write_status(args.database.parent/'source-observation.json',observation)
                stop.wait(max(0,.5-(time.monotonic()-cycle)))
        except BaseException as error:
            with source_lock:
                source.clear();source.update(state='FAILED',error=failure_evidence(error),updated_at=now())
    if args.collect:
        os.environ['IMC_SHADOW_DATA_ONLY']='1'
        collector=threading.Thread(target=collect,name='imc-production-paper-source',daemon=True)
        collector.start()
    next_integrity=0.;frozen=book.policy['code_hash'];draining=False
    try:
        while True:
            elapsed=time.monotonic()-started
            if abs((now()-wall_started)-elapsed)>3:
                raise ValueError('paper_clock_changed')
            if elapsed>=next_integrity:
                assert_closed_owner_gate()
                if code_hash()!=frozen:
                    raise ValueError('paper_frozen_code_changed')
                next_integrity=elapsed+10
            with source_lock:
                source_copy=dict(source)
            if source_copy.get('state')=='FAILED':
                raise RuntimeError('paper_source_failed')
            if args.collect and not draining and elapsed>=30 and (source_copy.get('state')!='SCANNED'
                    or time.monotonic()-source_copy.get('cycle_monotonic',0)>30):
                raise RuntimeError('paper_source_progress_stalled')
            if args.duration_seconds and elapsed>=args.duration_seconds:
                if not draining:
                    draining=True;stop.set()
                    with book.transaction():
                        book.set_meta('halt',book.meta('halt') or 'duration_elapsed')
                        book.event('cohort_window_ended',{'elapsed_seconds':elapsed})
                active=book.active()
                if active is None:
                    terminal_reason='DURATION_ELAPSED';break
                if active['state']=='ENTERING':
                    book.fail(active,'window_ended_before_paper_buy')
                    continue
                if active['state']=='OPEN':
                    book.request_exit(active,'TEST_WINDOW_END')
                if elapsed>=args.duration_seconds+args.drain_seconds:
                    terminal_reason='UNRESOLVED_POSITION_AT_DRAIN_LIMIT';break
            delay=cfg['poll_seconds']
            if args.duration_seconds:
                reader.deadline=started+args.duration_seconds+(args.drain_seconds if draining else 0)
            try:
                runner.tick()
            except (GMGNReadError, ObservationUnavailable, OSError, subprocess.SubprocessError) as error:
                doc=failure_evidence(error)
                if isinstance(error,GMGNReadError) and error.evidence:
                    doc['evidence']=dict(error.evidence)
                with book.transaction():book.event('runner_error',doc)
                delay=cfg['error_cooldown_seconds']
            report=book.report()
            report.update(runtime_state='DRAINING' if draining else 'RUNNING',
                          planned_duration_seconds=args.duration_seconds,source_observation=source_copy,
                          pid=os.getpid(),cohort_code_hash=frozen)
            write_status(args.status_path,report)
            if args.duration_seconds:
                delay=min(delay,max(.05,args.duration_seconds+(args.drain_seconds if draining else 0)-elapsed))
            stop.wait(delay) if not stop.is_set() else time.sleep(min(delay,1))
    except BaseException as error:
        terminal_reason='CRITICAL:'+type(error).__name__
        with book.transaction():book.event('critical_stop',failure_evidence(error))
        raise
    finally:
        stop.set()
        if collector:
            collector.join(timeout=10)
            if collector.is_alive():terminal_reason='SOURCE_THREAD_STOP_UNVERIFIED'
        report=book.report()
        report.update(runtime_state='SOURCE_STOP_UNVERIFIED' if collector and collector.is_alive() else 'FINISHED_PENDING_NATIVE_EXIT',stop_reason=terminal_reason,
                      planned_duration_seconds=args.duration_seconds,source_observation=dict(source),
                      pid=os.getpid(),cohort_code_hash=frozen)
        write_status(args.status_path,report)
        book.conn.close()


def write_status(path,report):
    write_json_projection(path,report)


if __name__=='__main__':
    main()
