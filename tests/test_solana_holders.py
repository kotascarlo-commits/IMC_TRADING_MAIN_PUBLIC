"""Complete-holder negatives, real SPL layouts in isolated local fixtures."""
import base64
import copy
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
import json
import time

import pytest
from solders.pubkey import Pubkey
from mykonos_meme_scout import solana_holders as h


def account(data, program):
    return {'owner': program, 'executable': False, 'data': [base64.b64encode(data).decode(), 'base64']}


def fixture(program=h.TOKEN):
    mint, owner = str(Pubkey.new_unique()), str(Pubkey.new_unique())
    body = bytearray(82); body[36:44] = (100).to_bytes(8, 'little'); body[44:46] = bytes([6, 1])
    m = {'context': {'slot': 100}, 'value': account(body, program)}
    rows = []
    for amount in (30, 70):
        data = bytearray(165); data[:32] = bytes(Pubkey.from_string(mint))
        data[32:64] = bytes(Pubkey.from_string(owner)); data[64:72] = amount.to_bytes(8, 'little'); data[108] = 1
        if program == h.TOKEN2022:
            data += bytes([2, 7, 0, 0, 0])  # Account type + ImmutableOwner TLV.
        rows.append({'pubkey': str(Pubkey.new_unique()), 'account': account(data, program)})
    return mint, owner, m, {'context': {'slot': 100}, 'value': rows}


@pytest.mark.parametrize('program', [h.TOKEN, h.TOKEN2022])
def test_complete_raw_supply_and_owner_aggregation_not_creator_permission(program):
    mint, owner, m, rows = fixture(program)
    result = h.concentration(mint, m, rows, queried_owner=owner)
    assert result['token_accounts'] == 2 and result['token_account_owners'] == 1
    assert result['supply_raw'] == '100' and result['queried_owner_raw'] == '100'
    assert result['top10_token_account_owner_pct'] == 100
    assert result['creator_identity_proven'] is False and result['execution_eligible'] is False


@pytest.mark.parametrize('violation', ['slot', 'duplicate', 'supply', 'mint', 'program', 'state', 'sliced', 'empty', 'boolean_slot'])
def test_partial_or_mismatched_observation_is_unknown(violation):
    mint, owner, m, rows = fixture()
    row = rows['value'][0]
    data = bytearray(base64.b64decode(row['account']['data'][0]))
    if violation == 'slot': m['context']['slot'] = 101
    elif violation == 'duplicate': rows['value'].append(copy.deepcopy(row))
    elif violation == 'supply': rows['value'].pop()
    elif violation == 'mint': data[:32] = bytes(Pubkey.new_unique()); row['account'] = account(data, h.TOKEN)
    elif violation == 'program': row['account']['owner'] = h.TOKEN2022
    elif violation == 'state': data[108] = 0; row['account'] = account(data, h.TOKEN)
    elif violation == 'sliced': row['account'] = account(data[32:72], h.TOKEN)
    elif violation == 'empty': rows['value'] = []
    else: rows['context']['slot'] = True
    with pytest.raises(ValueError): h.concentration(mint, m, rows)


@pytest.mark.parametrize('extension', [bytes([2, 0, 8, 0]) + (5).to_bytes(8, 'little'),
    bytes([5, 0, 0, 0]), bytes([7, 0, 1, 0, 0]), bytes([7, 0, 0, 0, 7, 0, 0, 0]), bytes([7, 0, 2, 0])])
def test_unresolved_token2022_amounts_unknown_extensions_and_bad_tlv_rejected(extension):
    mint, owner, m, rows = fixture(h.TOKEN2022)
    row = rows['value'][0]; data = base64.b64decode(row['account']['data'][0])[:166] + extension
    row['account'] = account(data, h.TOKEN2022)
    with pytest.raises(ValueError): h.concentration(mint, m, rows)


def test_read_methods_are_strict_allowlist_before_network():
    for method in ('sendTransaction', 'simulateTransaction', 'getTokenLargestAccounts'):
        with pytest.raises(ValueError, match='read_method_required'): h.rpc(method, [], time.monotonic()+1)


def test_observer_correct_program_full_accounts_same_slot_and_fresh_cache(monkeypatch):
    mint, owner, m, rows = fixture(h.TOKEN2022); calls = []
    monkeypatch.setattr(h, '_CACHE', {})
    def read(method, params, deadline):
        calls.append((method, copy.deepcopy(params)))
        return rows if method == 'getProgramAccounts' else m
    result = h.observe(mint, queried_owner=owner, reader=read)
    assert result['supply_reconciliation'] == 'PASS'
    assert len(calls) == 3
    assert calls[1][1][0] == h.TOKEN2022
    assert 'dataSize' not in str(calls[1][1]) and 'dataSlice' not in str(calls[1][1])
    result['supply_raw'] = 'MUTATED_CALLER'
    assert h.observe(mint, queried_owner=owner, reader=read)['supply_raw'] == '100'
    assert len(calls) == 3


def test_observation_deduplicates_outstanding_work():
    assert h._LOCK.acquire(blocking=False)
    try:
        with pytest.raises(ValueError, match='pending'): h.observe(str(Pubkey.new_unique()))
    finally: h._LOCK.release()


@pytest.mark.parametrize('value,minimum', [('120', 120), ('nan', 60), (None, 60), ('-1', .21)])
def test_retry_after_never_reduces_server_wait(value, minimum):
    assert h.retry_seconds(value) >= minimum


def test_http_date_retry_after_is_respected():
    date = format_datetime(datetime.now(timezone.utc) + timedelta(seconds=120), usegmt=True)
    assert 118 <= h.retry_seconds(date) <= 120


def test_ambiguous_json_fields_fail_closed():
    with pytest.raises(ValueError, match='duplicate_json_field'):
        json.loads('{"result":{},"result":null}', object_pairs_hook=h.unique)


def test_expired_cache_does_not_refresh_its_timestamp(monkeypatch):
    mint, owner, m, rows = fixture()
    monkeypatch.setattr(h, '_CACHE', {})
    calls = []
    def read(method, params, deadline):
        calls.append(method); return rows if method == 'getProgramAccounts' else m
    first = h.observe(mint, reader=read)
    key = next(iter(h._CACHE)); mono, wall, cached = h._CACHE[key]
    h._CACHE[key] = (mono-6, wall-6, cached)
    second = h.observe(mint, reader=read)
    assert len(calls) == 6 and second['requested_at'] >= first['requested_at']


def test_http_429_sets_shared_retry_after_backoff_without_retry(monkeypatch):
    calls = []
    class Budget:
        def reserve(self, *args, **kwargs): return 0
        def backoff(self, *args, **kwargs): calls.append(args)
    class Response:
        status = 429
        def getheader(self, name): return '120'
    class Connection:
        def __init__(self, *args, **kwargs): pass
        def request(self, *args): calls.append('request')
        def getresponse(self): return Response()
        def close(self): calls.append('close')
    monkeypatch.setattr(h, 'budget', lambda: Budget())
    monkeypatch.setattr(h.http.client, 'HTTPSConnection', Connection)
    with pytest.raises(ValueError, match='http_429'):
        h.rpc('getAccountInfo', [], time.monotonic()+3)
    assert calls == ['request', ('solana_holder_public', 120, 'HTTP_429'), 'close']
