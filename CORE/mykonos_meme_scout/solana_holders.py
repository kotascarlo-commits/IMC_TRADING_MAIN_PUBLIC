"""Bounded read-only holder observations; never token/security/entry permission.

Concentration is by token-account owner, including pools, not beneficial owner.
Mint and complete account enumeration must share one finalized context slot.
Unknown extensions, partial responses or supply mismatch are rejected.
"""
import base64
import copy
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import hashlib
import http.client
import importlib.util
import json
import math
from pathlib import Path
import threading
import time

from solders.pubkey import Pubkey

TOKEN = 'TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA'
TOKEN2022 = 'TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb'
HOST = 'api.mainnet-beta.solana.com'
_LOCK = threading.Lock()
_CACHE = {}
_BUDGET = None


def address(value):
    if not isinstance(value, str) or str(Pubkey.from_string(value)) != value:
        raise ValueError('holder_address_unknown')
    return value


def unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('holder_duplicate_json_field')
        result[key] = value
    return result


def account_data(value, program):
    data = value.get('data') if isinstance(value, dict) else None
    if (not isinstance(value, dict) or value.get('owner') != program or value.get('executable') is not False
            or not isinstance(data, list) or len(data) != 2 or data[1] != 'base64' or not isinstance(data[0], str)):
        raise ValueError('holder_account_schema_unknown')
    return base64.b64decode(data[0], validate=True)


def extensions(data, program, kind):
    base = 82 if kind == 1 else 165
    if len(data) == base:
        return
    if (program != TOKEN2022 or len(data) < 166 or data[165] != kind
            or (kind == 1 and any(data[82:165]))):
        raise ValueError('holder_extension_schema_unknown')
    offset, seen = 166, set()
    while offset < len(data):
        rest = data[offset:]
        if not any(rest):
            break
        if len(rest) < 4:
            raise ValueError('holder_extension_truncated')
        tag, length = int.from_bytes(rest[:2], 'little'), int.from_bytes(rest[2:4], 'little')
        if tag in seen or len(rest) < 4 + length:
            raise ValueError('holder_extension_ambiguous')
        payload = rest[4:4+length]
        # Only data-only metadata / immutable owner. Zero withheld fees may be
        # decoded, but unresolved/nonzero amounts must never disappear from sum.
        allowed = ((kind == 1 and ((tag == 18 and length == 64) or (tag == 19 and length >= 80)))
                   or (kind == 2 and ((tag == 7 and length == 0) or (tag == 2 and length == 8 and not any(payload)))))
        if not allowed:
            raise ValueError('holder_extension_unsupported:' + str(tag))
        seen.add(tag); offset += 4 + length


def context(result):
    slot = result.get('context', {}).get('slot') if isinstance(result, dict) else None
    if type(slot) is not int or slot <= 0 or 'value' not in result:
        raise ValueError('holder_context_unknown')
    return slot


def concentration(mint, mint_result, holders_result, *, queried_owner=None):
    address(mint)
    slot = context(holders_result)
    if context(mint_result) != slot:
        raise ValueError('holder_snapshot_slots_differ')
    raw_mint = mint_result['value']
    program = raw_mint.get('owner') if isinstance(raw_mint, dict) else None
    if program not in (TOKEN, TOKEN2022):
        raise ValueError('holder_token_program_unknown')
    body = account_data(raw_mint, program)
    if len(body) < 82 or body[45] != 1 or int.from_bytes(body[:4], 'little') not in (0, 1) or int.from_bytes(body[46:50], 'little') not in (0, 1):
        raise ValueError('holder_mint_unknown')
    extensions(body, program, 1)
    supply = int.from_bytes(body[36:44], 'little')
    if supply <= 0:
        raise ValueError('holder_supply_unknown')
    rows = holders_result['value']
    if not isinstance(rows, list) or not rows or len(rows) > 4000:
        raise ValueError('holder_enumeration_empty_or_unbounded')
    amounts, seen = {}, set()
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError('holder_record_unknown')
        key = address(row.get('pubkey'))
        if key in seen:
            raise ValueError('holder_duplicate_account')
        seen.add(key)
        data = account_data(row.get('account'), program)
        if (len(data) < 165 or str(Pubkey.from_bytes(data[:32])) != mint or data[108] not in (1, 2)
                or any(int.from_bytes(data[a:a+4], 'little') not in (0, 1) for a in (72, 109, 129))):
            raise ValueError('holder_token_account_unknown')
        extensions(data, program, 2)
        owner = str(Pubkey.from_bytes(data[32:64]))
        amounts[owner] = amounts.get(owner, 0) + int.from_bytes(data[64:72], 'little')
    if sum(amounts.values()) != supply:
        raise ValueError('holder_supply_reconciliation_failed')
    queried_owner = address(queried_owner) if queried_owner is not None else None
    return {'mint': mint, 'program': program, 'slot': slot, 'supply_raw': str(supply), 'decimals': body[44],
            'token_accounts': len(rows), 'token_account_owners': len(amounts),
            'top10_token_account_owner_pct': 100 * sum(sorted(amounts.values(), reverse=True)[:10]) / supply,
            'queried_owner': queried_owner, 'queried_owner_raw': str(amounts.get(queried_owner, 0)) if queried_owner else None,
            'queried_owner_pct': 100 * amounts.get(queried_owner, 0) / supply if queried_owner else None,
            'supply_reconciliation': 'PASS', 'concentration_complete': True,
            'beneficial_ownership_proven': False, 'creator_identity_proven': False,
            'security_complete': False, 'execution_eligible': False}


def budget():
    global _BUDGET
    if _BUDGET is None:
        path = Path(__file__).resolve().parents[2] / 'RUNTIME/imc_resilience.py'
        spec = importlib.util.spec_from_file_location('imc_holder_budget', path)
        _BUDGET = importlib.util.module_from_spec(spec); spec.loader.exec_module(_BUDGET)
    return _BUDGET


def retry_seconds(value):
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        try:
            seconds = (parsedate_to_datetime(value) - datetime.now(timezone.utc)).total_seconds()
        except (TypeError, ValueError, OverflowError):
            return 60.0
    return max(.21, seconds) if math.isfinite(seconds) else 60.0


def rpc(method, params, deadline):
    if method not in ('getAccountInfo', 'getProgramAccounts'):
        raise ValueError('holder_read_method_required')
    remaining = deadline - time.monotonic()
    if remaining <= 0 or budget().reserve('solana_holder_public', .21, max_wait=min(.65, remaining), timeout=.05):
        raise ValueError('holder_rate_budget_unavailable')
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise ValueError('holder_deadline_exceeded')
    request = {'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params}
    connection = http.client.HTTPSConnection(HOST, timeout=min(3, remaining))
    try:
        connection.request('POST', '/', json.dumps(request), {'Content-Type': 'application/json'})
        response = connection.getresponse()
        if response.status == 429:
            budget().backoff('solana_holder_public', retry_seconds(response.getheader('Retry-After')), 'HTTP_429', timeout=.05)
            raise ValueError('holder_http_429')
        raw = response.read(2097153)
        if response.status != 200 or len(raw) > 2097152 or time.monotonic() >= deadline:
            raise ValueError('holder_response_unavailable_or_late')
        value = json.loads(raw, object_pairs_hook=unique)
        if (not isinstance(value, dict) or value.get('jsonrpc') != '2.0' or type(value.get('id')) is not int
                or value['id'] != 1 or 'error' in value or 'result' not in value):
            raise ValueError('holder_rpc_result_unknown')
        return value['result']
    finally:
        connection.close()


def observe(mint, *, queried_owner=None, reader=rpc, hard_deadline=4):
    address(mint)
    if queried_owner is not None:
        address(queried_owner)
    if type(hard_deadline) not in (int, float) or not math.isfinite(hard_deadline) or not 0 < hard_deadline <= 30:
        raise ValueError('holder_deadline_invalid')
    key = (mint, queried_owner, 'finalized')
    # One outstanding observation; identical calls reuse only independently fresh
    # same-program evidence. No background workers or unreserved retries.
    if not _LOCK.acquire(blocking=False):
        raise ValueError('holder_observation_pending')
    try:
        cached = _CACHE.get(key); now = time.monotonic()
        if cached and 0 <= now-cached[0] <= 5 and abs(time.time()-cached[1]-(now-cached[0])) <= .5:
            return copy.deepcopy(cached[2])
        started, wall = time.monotonic(), time.time(); deadline = started + hard_deadline
        options = {'commitment': 'finalized', 'encoding': 'base64'}
        initial = reader('getAccountInfo', [mint, options], deadline)
        context(initial)
        program = (initial['value'] or {}).get('owner')
        if program not in (TOKEN, TOKEN2022):
            raise ValueError('holder_token_program_unknown')
        holders = reader('getProgramAccounts', [program, {**options, 'withContext': True,
            'minContextSlot': context(initial), 'filters': [{'memcmp': {'offset': 0, 'bytes': mint}}]}], deadline)
        current = reader('getAccountInfo', [mint, {**options, 'minContextSlot': context(holders)}], deadline)
        result = concentration(mint, current, holders, queried_owner=queried_owner)
        if result['program'] != program:
            raise ValueError('holder_program_changed')
        elapsed = time.monotonic()-started
        if elapsed < 0 or time.monotonic() >= deadline or abs(time.time()-wall-elapsed) > .5:
            raise ValueError('holder_observation_late_or_clock_changed')
        result.update(requested_at=wall, received_at=time.time(), source='SOLANA_RPC_FINALIZED_FULL_ACCOUNT_ENUMERATION')
        result['evidence_sha256'] = hashlib.sha256(json.dumps([current, holders], sort_keys=True).encode()).hexdigest()
        if len(_CACHE) >= 128:
            _CACHE.clear()
        _CACHE[key] = (started, wall, copy.deepcopy(result))
        return result
    finally:
        _LOCK.release()
