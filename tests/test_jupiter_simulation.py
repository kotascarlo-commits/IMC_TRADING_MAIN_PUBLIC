"""Adversarial same-bank accounting fixtures; not chain execution evidence."""
import copy

import pytest
from solders.address_lookup_table_account import AddressLookupTableAccount
from solders.hash import Hash
from solders.instruction import AccountMeta, Instruction
from solders.message import MessageV0
from solders.pubkey import Pubkey
from solders.signature import Signature
from solders.transaction import VersionedTransaction

from mykonos_meme_scout import jupiter_simulation as sim
from mykonos_meme_scout.jupiter_prepare import USDC, JUPITER


def fixture(*, sell=False, table=False):
    wallet = str(Pubkey.new_unique())
    ata = sim.associated(wallet, USDC, sim.TOKEN)
    pool = str(Pubkey.new_unique())
    keys = [wallet, ata, pool]
    tables = [AddressLookupTableAccount(Pubkey.new_unique(), [Pubkey.from_string(pool)])] if table else []
    ix = Instruction(Pubkey.from_string(JUPITER), b"fixture", [
        AccountMeta(Pubkey.from_string(k), i == 0, True) for i, k in enumerate(keys)])
    msg = MessageV0.try_compile(Pubkey.from_string(wallet), [ix], tables, Hash.new_unique())
    tx = VersionedTransaction.populate(msg, [Signature.default()])
    resolved, loaded = sim.message_keys(tx, tables)
    index = resolved.index(ata)
    pre = [0] * len(resolved); pre[0] = 100_000_000
    post = pre.copy()
    query = {"taker": wallet, "amount": "1000000", "inputMint": USDC if sell else sim.SOL,
             "outputMint": sim.SOL if sell else USDC}
    def token(amount):
        return {"accountIndex": index, "mint": USDC, "owner": wallet, "programId": sim.TOKEN,
                "uiTokenAmount": {"amount": str(amount), "decimals": 6}}
    if sell:
        pre[index] = post[index] = 2_000_000
        post[0] += 10_000_000 - 5000
        pre[resolved.index(pool)] = 20_000_000
        post[resolved.index(pool)] = 10_000_000
        before, after = [token(1_100_000)], [token(100_000)]
        minimum = "9900000"
    else:
        post[0] -= 1_000_000 + 5000 + 2_000_000
        post[index] = 2_000_000
        post[resolved.index(pool)] = 1_000_000
        before, after = [], [token(100_000)]
        minimum = "99000"
    observation = {"context": {"slot": 123}, "value": {"err": None, "fee": 5000,
        "preBalances": pre, "postBalances": post, "preTokenBalances": before,
        "postTokenBalances": after, "loadedAddresses": loaded}}
    return tx, tables, query, {"otherAmountThreshold": minimum}, observation, 5000


@pytest.mark.parametrize("sell", [False, True])
@pytest.mark.parametrize("table", [False, True])
def test_exact_message_native_cash_includes_fee_and_account_deposit_once(sell, table):
    args = fixture(sell=sell, table=table)
    report = sim.account_simulation(*args)
    assert report["status"] == "SIMULATED_COST_OBSERVED"
    assert report["execution_eligible"] is False and report["chain_fill"] is False
    assert report["full_instruction_policy_verified"] is False
    assert report["total_fee_and_net_account_lamports"] == ("5000" if sell else "2005000")
    assert report["wallet_cash_delta_lamports"] == ("9995000" if sell else "-3005000")
    assert report["output_raw"] == ("10000000" if sell else "100000")


@pytest.mark.parametrize("change", ["missing_fee", "fee_mismatch", "native_length", "negative_native",
    "bool_fee", "bad_slot", "loaded_addresses", "conservation", "output_below_minimum",
    "other_owner", "duplicate_token", "missing_token", "bad_index", "float_index",
    "fake_program", "bad_amount", "bool_decimal", "absent_existing", "missing_pre",
    "missing_post", "failed", "signed"])
def test_corrupt_or_absent_evidence_cannot_be_promoted(change):
    args = list(fixture()); value = args[4]["value"]
    if change == "missing_fee": value.pop("fee")
    if change == "fee_mismatch": value["fee"] = 6000
    if change == "bool_fee": value["fee"] = True
    if change == "native_length": value["postBalances"].pop()
    if change == "negative_native": value["preBalances"][0] = -1
    if change == "bad_slot": args[4]["context"]["slot"] = True
    if change == "loaded_addresses": value["loadedAddresses"]["writable"].append(str(Pubkey.new_unique()))
    if change == "conservation": value["postBalances"][0] += 1
    if change == "output_below_minimum": value["postTokenBalances"][0]["uiTokenAmount"]["amount"] = "98000"
    if change == "other_owner": value["postTokenBalances"][0]["owner"] = str(Pubkey.new_unique())
    if change == "duplicate_token": value["postTokenBalances"] *= 2
    if change == "missing_token": value["postTokenBalances"] = []
    if change == "bad_index": value["postTokenBalances"][0]["accountIndex"] = 999
    if change == "float_index": value["postTokenBalances"][0]["accountIndex"] = 1.0
    if change == "fake_program": value["postTokenBalances"][0]["programId"] = JUPITER
    if change == "bad_amount": value["postTokenBalances"][0]["uiTokenAmount"]["amount"] = "0100000"
    if change == "bool_decimal": value["postTokenBalances"][0]["uiTokenAmount"]["decimals"] = True
    if change == "absent_existing":
        i = value["postTokenBalances"][0]["accountIndex"]
        value["preBalances"][i] = 1; value["preBalances"][0] -= 1
    if change == "missing_pre": value.pop("preBalances")
    if change == "missing_post": value.pop("postTokenBalances")
    if change == "failed": value["err"] = {"InstructionError": [0, "InvalidArgument"]}
    if change == "signed": args[0] = VersionedTransaction.populate(args[0].message, [Signature.new_unique()])
    with pytest.raises((ValueError, TypeError, KeyError)):
        sim.account_simulation(*args)


def test_sell_input_debit_and_identity_change_are_bound():
    args = fixture(sell=True)
    value = args[4]["value"]
    value["postTokenBalances"][0]["uiTokenAmount"]["amount"] = "100001"
    with pytest.raises(ValueError, match="input_debit"):
        sim.account_simulation(*args)
    value["postTokenBalances"][0]["mint"] = sim.SOL
    with pytest.raises(ValueError, match="identity_changed"):
        sim.account_simulation(*args)


def test_wrong_associated_recipient_and_unexplained_cash_debit_rejected():
    args = fixture()
    value = args[4]["value"]
    value["postTokenBalances"][0]["programId"] = sim.TOKEN2022
    with pytest.raises(ValueError, match="recipient_not_associated"):
        sim.account_simulation(*args)
    args = fixture(); value = args[4]["value"]
    value["postBalances"][0] -= 1
    keys, _ = sim.message_keys(args[0], args[1])
    # Put an extra lamport into a route account: conservation alone isn't enough.
    recipient = value["postTokenBalances"][0]["accountIndex"]
    other = next(i for i in range(1, len(keys)) if i != recipient)
    value["postBalances"][other] += 1
    with pytest.raises(ValueError, match="unexplained_wallet_debit"):
        sim.account_simulation(*args)
