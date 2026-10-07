"""Same-bank, message-bound simulation accounting; never chain-fill evidence.

Only standard associated accounts are understood. Missing RPC fields, token
identity conflicts and unexpected wallet asset changes fail closed. This does
not approve the programs invoked by a route, mint extensions or pool liquidity.
"""
import hashlib

from solders.message import to_bytes_versioned
from solders.pubkey import Pubkey
from solders.signature import Signature

SOL = "So11111111111111111111111111111111111111112"
TOKEN = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
TOKEN2022 = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"
ATA = "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL"


def u64(value):
    if type(value) is not int or not 0 <= value < 2**64:
        raise ValueError("simulation_integer_unknown")
    return value


def raw(value):
    if (not isinstance(value, str) or not value.isascii() or not value.isdigit()
            or len(value) > 20 or str(int(value)) != value):
        raise ValueError("simulation_amount_unknown")
    return u64(int(value))


def associated(wallet, mint, program):
    if program not in (TOKEN, TOKEN2022):
        raise ValueError("simulation_token_program_unknown")
    return str(Pubkey.find_program_address([bytes(Pubkey.from_string(wallet)),
        bytes(Pubkey.from_string(program)), bytes(Pubkey.from_string(mint))],
        Pubkey.from_string(ATA))[0])


def message_keys(tx, tables):
    if (tx.message.header.num_required_signatures != 1 or
            tx.signatures != [Signature.default()]):
        raise ValueError("unsigned_simulation_required")
    by_key = {str(table.key): table for table in tables}
    if len(by_key) != len(tables):
        raise ValueError("simulation_duplicate_lookup")
    writable, readonly = [], []
    for lookup in tx.message.address_table_lookups:
        table = by_key.get(str(lookup.account_key))
        if table is None:
            raise ValueError("simulation_lookup_unknown")
        for indexes, result in ((lookup.writable_indexes, writable),
                                (lookup.readonly_indexes, readonly)):
            for index in indexes:
                if index >= len(table.addresses):
                    raise ValueError("simulation_lookup_index_unknown")
                result.append(str(table.addresses[index]))
    keys = [str(key) for key in tx.message.account_keys] + writable + readonly
    if len(set(keys)) != len(keys):
        raise ValueError("simulation_duplicate_account")
    return keys, {"writable": writable, "readonly": readonly}


def token_states(values, keys):
    if not isinstance(values, list):
        raise ValueError("simulation_token_balances_missing")
    result = {}
    for value in values:
        index = u64(value.get("accountIndex"))
        if index >= len(keys) or index in result:
            raise ValueError("simulation_token_index_unknown")
        mint, owner, program = (value.get(k) for k in ("mint", "owner", "programId"))
        for key in (mint, owner):
            if str(Pubkey.from_string(key)) != key:
                raise ValueError("simulation_token_identity_unknown")
        if program not in (TOKEN, TOKEN2022):
            raise ValueError("simulation_token_program_unknown")
        token = value.get("uiTokenAmount", {})
        decimals = u64(token.get("decimals"))
        if decimals > 255:
            raise ValueError("simulation_token_decimals_unknown")
        result[index] = {"mint": mint, "owner": owner, "program": program,
                         "decimals": decimals, "amount": raw(token.get("amount"))}
    return result


def account_simulation(tx, tables, query, build, simulation, expected_fee):
    """Return native cash and bound-token deltas from ONE simulation bank.

    Wallet delta already includes network fees and account creation/refunds.
    Cost/rent fields are decompositions, never additional cash deductions.
    """
    keys, loaded = message_keys(tx, tables)
    wallet = query["taker"]
    if keys[0] != wallet or u64(simulation.get("context", {}).get("slot")) == 0:
        raise ValueError("simulation_message_identity_unknown")
    value = simulation["value"]
    if value.get("err", "MISSING") is not None:
        raise ValueError("simulation_execution_failed")
    if value.get("loadedAddresses") != loaded:
        raise ValueError("simulation_loaded_addresses_mismatch")
    fee = u64(value.get("fee"))
    if not 0 < fee == u64(expected_fee) <= 3_000_000:
        raise ValueError("simulation_fee_mismatch")
    pre, post = value.get("preBalances"), value.get("postBalances")
    if (not isinstance(pre, list) or not isinstance(post, list) or
            len(pre) != len(keys) or len(post) != len(keys)):
        raise ValueError("simulation_native_balances_missing")
    pre, post = [u64(v) for v in pre], [u64(v) for v in post]
    if sum(post) - sum(pre) != -fee:
        raise ValueError("simulation_native_conservation_unknown")
    before = token_states(value.get("preTokenBalances"), keys)
    after = token_states(value.get("postTokenBalances"), keys)
    deltas, rent = {}, 0
    for index in set(before) | set(after):
        old, new = before.get(index), after.get(index)
        if old and new and any(old[k] != new[k] for k in ("mint", "owner", "program", "decimals")):
            raise ValueError("simulation_token_identity_changed")
        state = new or old
        if state["owner"] != wallet:
            continue
        mint = state["mint"]
        if mint not in (query["inputMint"], query["outputMint"]) or mint in deltas:
            raise ValueError("simulation_unexpected_wallet_token")
        if keys[index] != associated(wallet, mint, state["program"]):
            raise ValueError("simulation_recipient_not_associated")
        # Absence means zero only when the same-bank native state is zero too.
        if (old is None and pre[index] != 0) or (new is None and post[index] != 0):
            raise ValueError("simulation_missing_token_state")
        if mint == SOL and (pre[index] != 0 or post[index] != 0 or old or new):
            raise ValueError("simulation_existing_wsol_or_cleanup_unknown")
        deltas[mint] = (new["amount"] if new else 0) - (old["amount"] if old else 0)
        rent += post[index] - pre[index]
    amount, minimum = raw(query["amount"]), raw(build["otherAmountThreshold"])
    cash_delta = post[0] - pre[0]
    if query["inputMint"] == SOL:
        output = deltas.get(query["outputMint"])
        if output is None or output < minimum:
            raise ValueError("simulation_output_not_received")
        total_cost = -cash_delta - amount
    elif query["outputMint"] == SOL:
        if deltas.get(query["inputMint"]) != -amount:
            raise ValueError("simulation_input_debit_mismatch")
        # Network fees and rent are already deducted/refunded in cash_delta.
        output = cash_delta + fee + rent
        if output < minimum:
            raise ValueError("simulation_native_output_below_minimum")
        total_cost = fee + rent
    else:
        raise ValueError("simulation_native_side_required")
    if total_cost != fee + rent or total_cost < 0:
        raise ValueError("simulation_unexplained_wallet_debit")
    return {"status": "SIMULATED_COST_OBSERVED", "evidence_class": "UNSIGNED_SAME_BANK_SIMULATION",
        "simulation_slot": simulation["context"]["slot"], "commitment": "finalized",
        "message_sha256": hashlib.sha256(to_bytes_versioned(tx.message)).hexdigest(),
        "wallet": wallet, "wallet_before_lamports": str(pre[0]),
        "wallet_after_lamports": str(post[0]), "wallet_cash_delta_lamports": str(cash_delta),
        "network_fee_lamports": str(fee), "net_bound_account_lamports": str(rent),
        "total_fee_and_net_account_lamports": str(total_cost),
        "output_raw": str(output), "token_deltas": {k: str(v) for k, v in deltas.items()},
        "recipient_balance_binding_verified": True, "full_instruction_policy_verified": False,
        "mint_extensions_verified": False, "route_liquidity_verified": False,
        "chain_fill": False, "execution_eligible": False}
