"""Observed Cabal risks for virtual execution only, never address innocence.

No keys/authentication/payment methods exist. Every admission reparses the
persisted bytes and checks provider compute time, not just HTTP receipt time.
"""
import base64
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import hashlib
import http.client
import json
import math
from pathlib import Path
import time
from urllib.parse import urlencode

MODE = 'LIVE_PARITY_NO_BROADCAST'
HOST = 'api.cabal-hunter.com'
PATH = '/api/scan-cabal'
SCOPE = 'CABAL_OBSERVED_VIRTUAL_RISK_NOT_MALICIOUS_ADDRESS_CLEARANCE'
LIMIT = 1048576
ROOT = Path(__file__).resolve().parents[2]


def authority_off(root=ROOT):
    """Re-read real and virtual gates before every virtual admission/leg."""
    state = json.loads((root / 'RUNTIME/current-system-state.json').read_bytes(), object_pairs_hook=unique)
    execution = json.loads((root / 'config/imc-execution.json').read_bytes(), object_pairs_hook=unique)
    owner = json.loads((root / 'config/owner-broadcast-gate.json').read_bytes(), object_pairs_hook=unique)
    if (state.get('MODE') != MODE or state.get('EXECUTION_PROVIDER') != 'JUPITER'
            or state.get('WALLET') != 'OWN_SOLANA_WALLET'
            or state.get('GMGN_EXECUTION') != 'DISABLED' or state.get('GMGN_BROADCAST') != 'DISABLED'
            or state.get('GMGN_RUNTIME_DEPENDENCY') != 'NONE' or state.get('OWNER_GATE') != 'OFF'
            or owner.get('OWNER_BROADCAST_GATE') != 'OFF'
            or type(execution.get('execution_authority_pct')) is not int
            or execution['execution_authority_pct'] != 0
            or any(state.get(k) is not False for k in ('LIVE_TRADING', 'SIGNING_ENABLED',
                'BROADCAST_ENABLED', 'PRIVATE_KEY_ACCESS', 'REAL_WALLET_MOVEMENT'))
            or any(execution.get(k) is not False for k in ('signing', 'broadcast_enabled',
                'live_trading_enabled', 'wallet_access'))):
        raise ValueError('virtual_authority_rejected_stop_required')
    return execution


def unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate_security_field')
        result[key] = value
    return result


def public_address(value):
    alphabet = '123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz'
    if not isinstance(value, str) or not 32 <= len(value) <= 44:
        raise ValueError('security_address_invalid')
    number = 0
    for char in value:
        if char not in alphabet:
            raise ValueError('security_address_invalid')
        number = number * 58 + alphabet.index(char)
    if (number.bit_length() + 7)//8 + len(value) - len(value.lstrip('1')) != 32:
        raise ValueError('security_address_invalid')
    return value


def endpoint(mint):
    return 'https://' + HOST + PATH + '?' + urlencode({'mintAddress': public_address(mint)})


def evaluate(record, mint, expected_owner, observed_at, *, max_age=10, mode=MODE):
    """PASS means no defined veto in a complete, fresh *virtual* observation.

    Original deployer is provider-attributed; current_owner is bound separately
    to the caller's independently attributed owner. Transfer itself is neutral.
    Sampled history cannot be promoted to complete history.
    """
    result = {'status': 'UNKNOWN', 'scope': SCOPE, 'source': 'CABAL_HUNTER',
              'reasons': [], 'token': mint, 'malicious_creator': None,
              'original_deployer': None, 'current_owner': None,
              'coverage': {}, 'execution_eligible': False}
    def reject(reason, status='UNKNOWN'):
        result.update(status=status, reasons=[reason])
        return result
    try:
        if mode != MODE:
            return reject('virtual_mode_required')
        public_address(mint); public_address(expected_owner)
        if record.get('source') != endpoint(mint) or record.get('http_status') != 200:
            return reject('provider_unavailable_or_unbound')
        if type(max_age) not in (int, float) or not 0 < max_age <= 10:
            return reject('security_freshness_limit_invalid')
        if type(observed_at) not in (int, float) or not math.isfinite(observed_at):
            return reject('security_clock_invalid')
        raw = base64.b64decode(record['raw_body_base64'], validate=True)
        if len(raw) > LIMIT or hashlib.sha256(raw).hexdigest() != record['response_sha256']:
            return reject('security_response_hash_mismatch')
        body = json.loads(raw, object_pairs_hook=unique)
        if not isinstance(body, dict) or body.get('mint') != mint:
            return reject('security_token_mismatch')
        requested = datetime.fromisoformat(record['requested_at'].replace('Z', '+00:00'))
        received = datetime.fromisoformat(record['received_at'].replace('Z', '+00:00'))
        if requested.utcoffset() is None or received.utcoffset() is None:
            return reject('security_observation_timezone_unknown')
        computed = body.get('computed_at')
        if (type(computed) not in (int, float) or not math.isfinite(computed)
                or not requested.timestamp() <= received.timestamp() <= observed_at
                or not 0 <= observed_at - received.timestamp() <= max_age
                or not 0 <= observed_at - computed <= max_age):
            return reject('security_computed_or_received_stale')
        result.update(computed_at=computed, received_at=received.timestamp(),
                      response_sha256=record['response_sha256'])
        if body.get('scan_complete') is not True or body.get('degraded') is not False:
            return reject('security_scan_incomplete_or_degraded')
        if body.get('source') not in ('pre_indexed', 'real_time'):
            return reject('security_source_unknown')
        deployer = body.get('deployer')
        if not isinstance(deployer, dict):
            return reject('deployer_schema_unknown')
        original = public_address(deployer.get('creator'))
        owner = public_address(deployer.get('current_owner'))
        if (deployer.get('from_index') is not True
                or type(deployer.get('owner_transferred')) is not bool
                or deployer['owner_transferred'] != (original != owner)
                or owner != expected_owner):
            return reject('deployer_owner_attribution_conflict_or_unknown')
        result.update(original_deployer=original, current_owner=owner,
                      owner_transferred=deployer['owner_transferred'])
        if (type(body.get('cabal_score')) is not int or not 0 <= body['cabal_score'] <= 100
                or body.get('risk_level') not in ('LOW_SIGNAL', 'ELEVATED', 'HIGH')
                or body.get('recommendation') not in ('SAFE', 'REVIEW', 'AVOID')):
            return reject('risk_schema_unknown')
        boolean_fields = ('is_controlled','time_sync','coordinated_exit','exit_liquidity_risk',
                          'mint_authority_revoked','freeze_authority_revoked')
        if (any(k in body and type(body[k]) is not bool for k in boolean_fields)
                or 'honeypot_risk' in body and body['honeypot_risk'] not in ('LOW', 'HIGH')
                or 'token2022_risks' in body and (not isinstance(body['token2022_risks'], list)
                    or any(not isinstance(v, str) or not v for v in body['token2022_risks']))):
            return reject('risk_schema_unknown')
        # Independent documented vetoes. AVOID is never masked by a low score.
        if (body.get('risk_level') == 'HIGH' or body.get('recommendation') == 'AVOID'
                or type(body.get('cabal_score')) is int and body['cabal_score'] >= 65
                or body.get('honeypot_risk') == 'HIGH'
                or body.get('mint_authority_revoked') is False
                or body.get('freeze_authority_revoked') is False
                or body.get('coordinated_exit') is True
                or body.get('exit_liquidity_risk') is True
                or isinstance(body.get('token2022_risks'), list) and body['token2022_risks']):
            return reject('documented_hard_risk', 'FAIL')
        required = {'risk_level': 'LOW_SIGNAL', 'risk': 'CLEAN', 'recommendation': 'SAFE',
                    'is_controlled': False, 'confidence': 'HIGH', 'time_sync': False,
                    'coordinated_exit': False, 'exit_liquidity_risk': False,
                    'honeypot_risk': 'LOW', 'mint_authority_revoked': True,
                    'freeze_authority_revoked': True}
        if any(type(body.get(k)) is not type(v) or body[k] != v for k, v in required.items()):
            return reject('risk_fields_missing_conflicting_or_caution')
        if (type(body.get('cabal_score')) is not int or not 0 <= body['cabal_score'] < 35
                or type(body.get('wallets_checked')) is not int or body['wallets_checked'] < 5
                or body.get('token2022_risks') != []
                or type(body.get('token2022_risks')) is not list
                or not isinstance(body.get('coordinated_clusters'), list)
                or body['coordinated_clusters']):
            return reject('trace_coverage_unknown_or_coordination_detected')
        counts = {k: deployer.get(k) for k in ('tokens_launched', 'sampled', 'dead', 'pump_and_dumps')}
        if any(type(v) is not int or v < 0 for v in counts.values()):
            return reject('deployer_history_schema_unknown')
        if (counts['sampled'] > counts['tokens_launched'] or counts['dead'] > counts['sampled']
                or counts['pump_and_dumps'] > counts['sampled']):
            return reject('deployer_history_counts_conflict')
        if counts['dead'] or counts['pump_and_dumps']:
            # Dead launches are outcomes, not a documented fraud finding.
            return reject('deployer_adverse_history_requires_review')
        result['coverage'] = {'wallets_checked': body['wallets_checked'], **counts}
        if counts['sampled'] != counts['tokens_launched']:
            return reject('deployer_history_sampled_not_complete')
        if deployer.get('verdict') not in ('FIRST_LAUNCH', 'NORMAL'):
            return reject('deployer_history_unknown_or_caution')
        return reject('complete_fresh_observed_virtual_checks_no_veto', 'PASS')
    except (KeyError, ValueError, TypeError, OverflowError):
        return reject('security_schema_or_encoding_unknown')


def bind_holders(holders, creator, security, mint, observed_at):
    """Bind the existing finalized full-enumeration adapter to ORIGINAL deployer.

    An owner balance, a stale report, or an unrelated numeric percentage cannot
    stand in for original-deployer concentration after an ownership transfer.
    """
    result = {'status': 'UNKNOWN', 'reason': 'original_deployer_holder_binding_missing'}
    try:
        if (not isinstance(holders, dict) or creator['status'] != 'PASS'
                or holders.get('source') != 'SOLANA_RPC_FINALIZED_FULL_ACCOUNT_ENUMERATION'
                or holders.get('mint') != mint
                or holders.get('queried_owner') != creator['original_deployer']
                or holders.get('concentration_complete') is not True
                or holders.get('supply_reconciliation') != 'PASS'
                or type(holders.get('slot')) is not int or holders['slot'] <= 0
                or not isinstance(holders.get('evidence_sha256'), str)
                or len(holders['evidence_sha256']) != 64
                or any(v not in '0123456789abcdef' for v in holders['evidence_sha256'])):
            return result
        times = [holders[k] for k in ('requested_at', 'received_at')]
        if (any(type(v) not in (int, float) or not math.isfinite(v) for v in times)
                or not times[0] <= times[1] <= observed_at
                or not 0 <= observed_at - times[0] <= 5):
            return result
        for expected, actual in (('top10_holder_pct','top10_token_account_owner_pct'),
                                 ('creator_holder_pct','queried_owner_pct')):
            value = holders.get(actual)
            if (type(value) not in (int, float) or not math.isfinite(value)
                    or not 0 <= value <= 100 or security.get(expected) != value):
                return result
        return {'status': 'PASS', 'reason': 'fresh_complete_original_deployer_supply_bound',
                'mint': mint, 'original_deployer': creator['original_deployer'],
                'slot': holders['slot'], 'source': holders['source'],
                'requested_at': times[0], 'response_sha256': holders['evidence_sha256']}
    except (TypeError, KeyError, ValueError):
        return result


class CabalReadOnly:
    """One GET; 402/429/5xx remain UNKNOWN. No auth, payment or retry path."""
    def __init__(self, *, budget, clock=time.time, connection_factory=http.client.HTTPSConnection):
        self.budget, self.clock, self.connection_factory = budget, clock, connection_factory

    def observe(self, mint):
        url = endpoint(mint)
        record = {'source': url, 'requested_at': datetime.fromtimestamp(self.clock(), timezone.utc).isoformat(),
                  'http_status': None, 'attempts': 0}
        connection = None
        try:
            if self.budget.reserve('cabal_public', 1.1, wait=False, timeout=.05):
                raise ValueError('security_rate_budget_unavailable')
            connection = self.connection_factory(HOST, timeout=30)
            record['attempts'] = 1
            connection.request('GET', url.split(HOST, 1)[1], headers={'Accept': 'application/json'})
            response = connection.getresponse()
            raw = response.read(LIMIT + 1)
            record.update(http_status=response.status,
                          received_at=datetime.fromtimestamp(self.clock(), timezone.utc).isoformat(),
                          response_sha256=hashlib.sha256(raw).hexdigest(),
                          raw_body_base64=base64.b64encode(raw).decode('ascii'))
            if response.status == 429:
                value = response.getheader('Retry-After')
                try: seconds = float(value)
                except (ValueError, TypeError):
                    seconds = parsedate_to_datetime(value).timestamp() - self.clock() if value else 30
                self.budget.backoff('cabal_public', max(0, seconds), 'cabal_429', timeout=.05)
            if len(raw) > LIMIT:
                raise ValueError('security_response_size_limit')
        except (OSError, ValueError, TypeError, http.client.HTTPException) as error:
            record['error'] = type(error).__name__ + ':' + str(error)
        finally:
            if connection is not None: connection.close()
        return record
