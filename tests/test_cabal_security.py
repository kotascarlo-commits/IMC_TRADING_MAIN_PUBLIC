"""Offline synthetic contract/adversarial fixtures; never provider qualification."""
import base64
import copy
from datetime import datetime, timezone
import hashlib
import json
import socket

import pytest
from mykonos_meme_scout import cabal_security as cabal
from mykonos_meme_scout import paper_forward as paper

MINT = 'FNgMfPgP6pHwxkqYYJT2jTzfzQgoSiCtEXZhGB13pump'
OWNER = 'AwknxcVV9u7Yox1LKNQzTkDMrTuJUpXbyc1ff5zPevWM'
DEPLOYER = 'FVZRwUp6E4m9jV4VumF8q7m8q3mF9fpikRrJSCCfFAdP'


@pytest.fixture(autouse=True)
def offline_network_barrier(monkeypatch):
    def blocked(*args, **kwargs): raise AssertionError('UNIT_TEST_NETWORK_ACCESS_FORBIDDEN')
    monkeypatch.setattr(socket, 'create_connection', blocked)


def payload():
    return {'mint': MINT, 'computed_at': 1000., 'source': 'real_time',
        'scan_complete': True, 'degraded': False, 'risk_level': 'LOW_SIGNAL',
        'risk': 'CLEAN', 'recommendation': 'SAFE', 'cabal_score': 0,
        'is_controlled': False, 'confidence': 'HIGH', 'wallets_checked': 5,
        'time_sync': False, 'coordinated_exit': False, 'exit_liquidity_risk': False,
        'honeypot_risk': 'LOW', 'mint_authority_revoked': True, 'freeze_authority_revoked': True,
        'token2022_risks': [], 'coordinated_clusters': [],
        'deployer': {'creator': DEPLOYER, 'current_owner': OWNER, 'owner_transferred': True,
            'from_index': True, 'tokens_launched': 0, 'sampled': 0, 'dead': 0,
            'pump_and_dumps': 0, 'verdict': 'FIRST_LAUNCH'}}


def record(body=None, *, http=200, raw=None):
    raw = raw if raw is not None else json.dumps(body or payload()).encode()
    return {'source': cabal.endpoint(MINT), 'http_status': http,
        'requested_at': datetime.fromtimestamp(999.9, timezone.utc).isoformat(),
        'received_at': datetime.fromtimestamp(1000.1, timezone.utc).isoformat(),
        'raw_body_base64': base64.b64encode(raw).decode(),
        'response_sha256': hashlib.sha256(raw).hexdigest()}


def evaluate(body):
    return cabal.evaluate(record(body), MINT, OWNER, 1000.2)


def test_pass_is_only_fresh_complete_virtual_observation_not_malicious_false():
    result = evaluate(payload())
    assert result['status'] == 'PASS'
    assert result['original_deployer'] == DEPLOYER and result['current_owner'] == OWNER
    assert result['owner_transferred'] is True
    assert result['malicious_creator'] is None and result['execution_eligible'] is False
    assert 'NOT_MALICIOUS_ADDRESS' in result['scope']


@pytest.mark.parametrize('field,value', [
    ('scan_complete', False), ('degraded', True), ('computed_at', 990.),
    ('computed_at', 1001.), ('computed_at', None), ('scan_complete', 'true'),
    ('degraded', 'false'), ('cabal_score', '0'), ('cabal_score', 101),
    ('risk_level', 'SAFE'), ('recommendation', 'REVIEW'), ('risk', 'MEDIUM'),
    ('honeypot_risk', None), ('token2022_risks', 'none'), ('token2022_risks', [None]),
    ('source', 'unknown'), ('confidence', 'LOW'), ('wallets_checked', 4),
    ('exit_liquidity_risk', None), ('time_sync', 'false'),
])
def test_incomplete_stale_malformed_or_caution_remains_unknown(field, value):
    body = payload(); body[field] = value
    assert evaluate(body)['status'] == 'UNKNOWN'


@pytest.mark.parametrize('field,value', [
    ('risk_level', 'HIGH'), ('recommendation', 'AVOID'), ('cabal_score', 65),
    ('honeypot_risk', 'HIGH'), ('mint_authority_revoked', False),
    ('freeze_authority_revoked', False), ('coordinated_exit', True),
    ('exit_liquidity_risk', True), ('token2022_risks', ['permanent_delegate']),
])
def test_each_documented_hard_veto_blocks_independently(field, value):
    body = payload(); body[field] = value
    assert evaluate(body)['status'] == 'FAIL'


@pytest.mark.parametrize('field,value', [
    ('creator', OWNER), ('current_owner', DEPLOYER), ('owner_transferred', False),
    ('creator', None), ('current_owner', None), ('from_index', False),
    ('sampled', 1), ('tokens_launched', 1), ('verdict', 'UNKNOWN'),
    ('verdict', 'SERIAL_LAUNCHER'), ('sampled', True),
])
def test_deployer_owner_and_history_attribution_fail_closed(field, value):
    body = payload(); body['deployer'][field] = value
    assert evaluate(body)['status'] == 'UNKNOWN'


@pytest.mark.parametrize('field', ['dead', 'pump_and_dumps'])
def test_adverse_outcomes_are_review_not_undocumented_fraud(field):
    body = payload(); body['deployer'].update(tokens_launched=1, sampled=1)
    body['deployer'][field] = 1
    assert evaluate(body)['status'] == 'UNKNOWN'


def test_ownership_transfer_is_neutral_but_cannot_hide_deployer():
    body = payload(); body['deployer'].update(creator=OWNER, owner_transferred=False)
    assert evaluate(body)['status'] == 'PASS'
    body['deployer']['owner_transferred'] = True
    assert evaluate(body)['status'] == 'UNKNOWN'


@pytest.mark.parametrize('http', [401, 402, 403, 429, 500, 503, None])
def test_provider_outage_or_payment_response_never_passes(http):
    assert cabal.evaluate(record(http=http), MINT, OWNER, 1000.2)['status'] == 'UNKNOWN'


def test_hash_duplicate_mint_receipt_and_mode_forgery_are_rejected():
    forged = record(); forged['response_sha256'] = '0' * 64
    assert cabal.evaluate(forged, MINT, OWNER, 1000.2)['status'] == 'UNKNOWN'
    body = payload(); body['mint'] = OWNER
    assert evaluate(body)['status'] == 'UNKNOWN'
    duplicate = record(raw=b'{"mint":"x","mint":"y"}')
    assert cabal.evaluate(duplicate, MINT, OWNER, 1000.2)['status'] == 'UNKNOWN'
    fresh_receipt_old_compute = record(); body = payload(); body['computed_at'] = 900
    assert evaluate(body)['status'] == 'UNKNOWN'
    assert cabal.evaluate(fresh_receipt_old_compute, MINT, OWNER, 1000.2, mode='LIVE')['status'] == 'UNKNOWN'


class Budget:
    def __init__(self): self.backoffs = []
    def reserve(self, *args, **kwargs): return 0
    def backoff(self, *args, **kwargs): self.backoffs.append(args)


@pytest.mark.parametrize('status', [200, 402, 429])
def test_read_only_client_does_one_mock_get_and_never_retries_or_pays(status):
    calls = []; budget = Budget()
    class Connection:
        def __init__(self, host, timeout): assert host == cabal.HOST
        def request(self, method, path, headers): calls.append((method, path, headers))
        def getresponse(self): return self
        def read(self, limit): return json.dumps(payload()).encode()
        def getheader(self, name): return '60'
        def close(self): pass
    Connection.status = status
    client = cabal.CabalReadOnly(budget=budget, clock=lambda: 1000.1, connection_factory=Connection)
    result = client.observe(MINT)
    assert len(calls) == 1 and calls[0][0] == 'GET' and result['attempts'] == 1
    assert set(calls[0][2]) == {'Accept'}
    assert bool(budget.backoffs) == (status == 429)


def test_virtual_brain_preserves_known_malicious_veto_and_never_aliases_none():
    cfg = paper.load_config()
    sec = {'checked': True, 'checked_at': datetime.fromtimestamp(1000., timezone.utc).isoformat(),
           'mint_authority_disabled': True, 'freeze_authority_disabled': True,
           'non_transferable': False, 'transfer_hook_present': False, 'malicious_creator': None,
           'top10_holder_pct': 1., 'creator_holder_pct': 0.}
    current = {'address': MINT, 'market_data_stale': False, 'security': sec}
    evidence = {}
    reasons = paper.brain(current, None, cfg, 1000.2, evidence=evidence,
                          cabal_observation=record(), cabal_owner=OWNER)
    assert 'security:creator_risk_status' not in reasons
    assert 'security:original_deployer_holder_binding' in reasons
    assert sec['malicious_creator'] is None
    assert evidence['security_checks']['creator_risk_status']['status'] == 'PASS'
    assert 'security:malicious_creator' in paper.brain(current, None, cfg, 1000.2)
    sec['malicious_creator'] = True
    assert 'security:malicious_creator' in paper.brain(current, None, cfg, 1000.2,
                          cabal_observation=record(), cabal_owner=OWNER)
