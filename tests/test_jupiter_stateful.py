"""Local VM control negatives; isolated fixtures, never forward performance."""
import base64

import pytest
from solders.account import Account
from solders.hash import Hash
from solders.message import MessageV0
from solders.pubkey import Pubkey
from solders.signature import Signature
from solders.system_program import transfer, TransferParams
from solders.transaction import VersionedTransaction

from mykonos_meme_scout import jupiter_stateful as module


def raw_account(lamports=1000000000, owner=module.SYSTEM, data=b''):
    return {'lamports': lamports, 'owner': owner, 'data': [base64.b64encode(data).decode(), 'base64'],
            'executable': False, 'rentEpoch': 0}


def bank():
    wallet, receiver = str(Pubkey.new_unique()), str(Pubkey.new_unique())
    result = module.StatefulJupiterBank(wallet, {wallet: raw_account(), receiver: raw_account()}, {}, 100)
    return result, wallet, receiver


def transaction(wallet, receiver, lamports=12345):
    blockhash = Hash.new_unique()
    ix = transfer(TransferParams(from_pubkey=Pubkey.from_string(wallet), to_pubkey=Pubkey.from_string(receiver), lamports=lamports))
    message = MessageV0.try_compile(Pubkey.from_string(wallet), [ix], [], blockhash)
    return VersionedTransaction.populate(message, [Signature.default()]), str(blockhash)


def test_real_local_vm_preview_hold_commit_and_duplicate_protection(monkeypatch):
    engine, wallet, receiver = bank()
    tx, blockhash = transaction(wallet, receiver)
    # The system-transfer fixture has no Jupiter fill. Test only VM state control;
    # real route/cost evidence is exercised separately by current-market probes.
    monkeypatch.setattr(module, 'account_simulation', lambda *a: {'scope': 'ISOLATED_SYSTEM_TRANSFER_NOT_A_FILL'})
    query = {'taker': wallet}
    before = engine.state_hash()
    engine.preview(tx, [], query, {}, blockhash)
    assert engine.state_hash() == before
    evidence = engine.execute(tx, [], query, {}, blockhash)
    assert engine.account(receiver).lamports == 1000000000 + 12345
    assert engine.account(wallet).lamports == 1000000000 - 12345 - 5000
    assert engine.state_hash() != before
    held = engine.state_hash()
    assert engine.checkpoint()['state_hash'] == held
    assert evidence['LIVE_PARITY_CLOSED'] is False
    assert evidence['signing'] is False and evidence['broadcast'] is False
    with pytest.raises(ValueError, match='duplicate_transaction'):
        engine.execute(tx, [], query, {}, blockhash)
    assert engine.state_hash() == held


def test_nonzero_signature_rejected_before_vm():
    engine, wallet, receiver = bank()
    tx, blockhash = transaction(wallet, receiver)
    tx = VersionedTransaction.populate(tx.message, [Signature.from_bytes(bytes([1]) * 64)])
    before = engine.state_hash()
    with pytest.raises(ValueError, match='unsigned_simulation_required'):
        engine.preview(tx, [], {'taker': wallet}, {}, blockhash)
    assert engine.state_hash() == before


@pytest.mark.parametrize('violation', ['wallet', 'blockhash', 'poisoned'])
def test_transaction_binding_and_poisoned_bank_fail_closed(violation):
    engine, wallet, receiver = bank()
    tx, blockhash = transaction(wallet, receiver)
    query = {'taker': wallet}
    if violation == 'wallet': query['taker'] = receiver
    elif violation == 'blockhash': blockhash = str(Hash.new_unique())
    else: engine.poisoned = True
    with pytest.raises(ValueError, match='binding_unknown'):
        engine.preview(tx, [], query, {}, blockhash)


def test_hold_cannot_overwrite_account_or_buy_state():
    engine, wallet, receiver = bank()
    before = engine.state_hash()
    with pytest.raises(ValueError, match='hold_mainnet_state_conflict'):
        engine.import_additional({receiver: raw_account(lamports=1)})
    assert engine.state_hash() == before


def test_token_injection_after_commit_is_atomic_and_rejected():
    engine, wallet, receiver = bank()
    engine._committed.add('ISOLATED_TEST_COMMIT')
    extra, injected = str(Pubkey.new_unique()), str(Pubkey.new_unique())
    body = bytearray(165); body[:32] = bytes(Pubkey.new_unique())
    body[32:64] = bytes(Pubkey.from_string(wallet)); body[64:72] = (999999).to_bytes(8, 'little')
    with pytest.raises(ValueError, match='token_injection_blocked'):
        engine.import_additional({extra: raw_account(), injected: raw_account(owner=module.TOKEN, data=body)})
    assert engine.account(extra) is None and engine.account(injected) is None


@pytest.mark.parametrize('owner,size', [(module.TOKEN2022, 165), (module.TOKEN2022, 170),
                                     (module.TOKEN, 170)])
def test_unsupported_token_account_import_is_atomic_and_rejected(owner, size):
    engine, wallet, receiver = bank()
    engine._committed.add('ISOLATED_TEST_COMMIT')
    extra, injected = str(Pubkey.new_unique()), str(Pubkey.new_unique())
    body = bytearray(size); body[:32] = bytes(Pubkey.new_unique())
    body[32:64] = bytes(Pubkey.from_string(wallet)); body[64:72] = (999999).to_bytes(8, 'little')
    with pytest.raises(ValueError, match='hold_token_(program|schema)_unsupported'):
        engine.import_additional({extra: raw_account(), injected: raw_account(owner=owner, data=body)})
    assert engine.account(extra) is None and engine.account(injected) is None


@pytest.mark.parametrize('cash', [True, -1, 0, '1000'])
def test_invalid_virtual_cash_rejected(cash):
    wallet = str(Pubkey.new_unique())
    with pytest.raises(ValueError, match='virtual_initial_cash_invalid'):
        module.StatefulJupiterBank(wallet, {wallet: raw_account()}, {}, 100, virtual_cash_lamports=cash)


def test_initial_virtual_funding_changes_only_sol_payer():
    wallet, external = str(Pubkey.new_unique()), str(Pubkey.new_unique())
    engine = module.StatefulJupiterBank(wallet, {wallet: raw_account(), external: raw_account()}, {}, 100,
                                     virtual_cash_lamports=2000000000)
    assert engine.initial_real_cash == 1000000000 and engine.initial_virtual_cash == 2000000000
    assert engine.account(external).lamports == 1000000000


def test_closed_account_equivalence_does_not_hide_tokens_or_foreign_accounts():
    system = Pubkey.from_string(module.SYSTEM)
    token = Pubkey.from_string(module.TOKEN)
    assert module.fingerprint(None) == module.fingerprint(Account(0, b'', system))
    assert module.fingerprint(Account(0, bytes(165), token)) is not None
    assert module.fingerprint(Account(0, b'', token)) is not None
    assert module.fingerprint(Account(1, b'', system)) is not None


@pytest.mark.parametrize('data', [None, [], [''], ['AA==', 'jsonParsed'], [1, 'base64']])
def test_malformed_snapshot_encoding_fails_closed(data):
    value = raw_account(); value['data'] = data
    with pytest.raises(ValueError, match='mainnet_account_encoding_unknown'):
        module.imported_account(value)


@pytest.mark.parametrize('field,value', [('lamports', True), ('lamports', 2**64),
                                       ('rentEpoch', -1), ('rentEpoch', True), ('owner', None)])
def test_malformed_snapshot_schema_fails_closed(field, value):
    raw = raw_account(); raw[field] = value
    with pytest.raises(ValueError, match='mainnet_account_schema_unknown'):
        module.imported_account(raw)
