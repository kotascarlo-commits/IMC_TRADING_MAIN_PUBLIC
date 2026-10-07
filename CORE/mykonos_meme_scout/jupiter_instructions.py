"""Closed auxiliary instruction policy for unsigned native/legacy-token probes.

This verifies wallet setup/cleanup, not arbitrary Jupiter route/CPI semantics.
Unknown variants and Token2022 are deliberately unsupported by this policy.
"""
import base64
import hashlib

from .jupiter_simulation import SOL, TOKEN, ATA, associated, raw, u64

SYSTEM = "11111111111111111111111111111111"
BUDGET = "ComputeBudget111111111111111111111111111111"
JUPITER = "JUP6LkbZbjS1jKKwapdHNy74zcZ3tLUZoi5QNyVTaV4"


def encoded(value):
    if not isinstance(value, dict) or set(value) != {"programId", "accounts", "data"}:
        raise ValueError("auxiliary_instruction_schema_unknown")
    if not isinstance(value["accounts"], list):
        raise ValueError("auxiliary_accounts_unknown")
    for account in value["accounts"]:
        if (not isinstance(account, dict) or set(account) != {"pubkey", "isSigner", "isWritable"}
                or type(account["isSigner"]) is not bool or type(account["isWritable"]) is not bool):
            raise ValueError("auxiliary_accounts_unknown")
    return base64.b64decode(value["data"], validate=True)


def accounts(value, expected):
    actual = [(v["pubkey"], v["isSigner"], v["isWritable"]) for v in value["accounts"]]
    if actual != expected:
        raise ValueError("auxiliary_account_binding_mismatch")


def validate_auxiliary(build, query):
    wallet = query["taker"]
    input_mint, output_mint = query["inputMint"], query["outputMint"]
    if (input_mint == output_mint or SOL not in (input_mint, output_mint) or
            build.get("otherInstructions", []) != [] or build.get("tipInstruction")):
        raise ValueError("auxiliary_route_unsupported")
    atas = {m: associated(wallet, m, TOKEN) for m in (input_mint, output_mint)}
    prices = build.get("computeBudgetInstructions", [])
    if not isinstance(prices, list) or len(prices) != 1:
        raise ValueError("auxiliary_compute_price_required")
    price = prices[0]; data = encoded(price)
    if price["programId"] != BUDGET or price["accounts"] or len(data) != 9 or data[0] != 3:
        raise ValueError("auxiliary_compute_instruction_unknown")
    micro_price = u64(int.from_bytes(data[1:], "little"))
    setup = build.get("setupInstructions", [])
    if not isinstance(setup, list) or len(setup) > 6:
        raise ValueError("auxiliary_setup_unknown")
    created = set(); transfer = False; synced = False
    for value in setup:
        data = encoded(value); program = value["programId"]
        if program == ATA:
            if len(value["accounts"]) != 6 or data != b"\x01":
                raise ValueError("auxiliary_ata_variant_unknown")
            mint = value["accounts"][3]["pubkey"]
            if mint not in atas or mint in created:
                raise ValueError("auxiliary_ata_mint_unknown")
            accounts(value, [(wallet, True, True), (atas[mint], False, True),
                (wallet, False, False), (mint, False, False), (SYSTEM, False, False), (TOKEN, False, False)])
            created.add(mint)
        elif program == SYSTEM:
            if (input_mint != SOL or SOL not in created or transfer or synced or
                    len(data) != 12 or int.from_bytes(data[:4], "little") != 2 or
                    int.from_bytes(data[4:], "little") != raw(query["amount"])):
                raise ValueError("auxiliary_native_transfer_unknown")
            accounts(value, [(wallet, True, True), (atas[SOL], False, True)])
            transfer = True
        elif program == TOKEN:
            if data != b"\x11" or not transfer or synced:
                raise ValueError("auxiliary_token_instruction_unknown")
            accounts(value, [(atas[SOL], False, True)])
            synced = True
        else:
            raise ValueError("auxiliary_program_unknown")
    if input_mint == SOL and not (transfer and synced):
        raise ValueError("auxiliary_native_input_not_bound")
    swap = build["swapInstruction"]; data = encoded(swap)
    direct = hashlib.sha256(b"global:route_v2").digest()[:8]
    shared = hashlib.sha256(b"global:shared_accounts_route_v2").digest()[:8]
    if (swap["programId"] != JUPITER or data[:8] not in (direct, shared)
            or len(swap["accounts"]) < (10 if data[:8] == direct else 12)):
        raise ValueError("auxiliary_swap_variant_unsupported")
    # Account positions confirmed from finalized program-owned Anchor IDL at
    # C88XWfp26heEmDkmfSzeXP7Fd7GQJ2j9dDTUsyiZbUTa, slot 453646944.
    indexes = [0, 1, 2, 3, 4, 5, 6] if data[:8] == direct else [1, 2, 5, 6, 7, 8, 9]
    expected = [wallet, atas[input_mint], atas[output_mint], input_mint, output_mint, TOKEN, TOKEN]
    if [swap["accounts"][i]["pubkey"] for i in indexes] != expected:
        raise ValueError("auxiliary_swap_recipient_mismatch")
    signer, source, destination = indexes[:3]
    if (not swap["accounts"][signer]["isSigner"] or
            any(a["isSigner"] for i, a in enumerate(swap["accounts"]) if i != signer) or
            not all(swap["accounts"][i]["isWritable"] for i in (source, destination))):
        raise ValueError("auxiliary_swap_signer_unknown")
    if data[:8] == direct and swap["accounts"][7]["pubkey"] not in (JUPITER, atas[output_mint]):
        raise ValueError("auxiliary_optional_destination_unknown")
    cleanup = build.get("cleanupInstruction")
    if not cleanup or cleanup["programId"] != TOKEN or encoded(cleanup) != b"\x09":
        raise ValueError("auxiliary_wsol_cleanup_required")
    accounts(cleanup, [(atas[SOL], False, True), (wallet, False, True), (wallet, True, False)])
    return {"auxiliary_instruction_policy_verified": True, "swap_associated_accounts_bound": True,
            "compute_unit_price_micro_lamports": micro_price, "full_route_semantics_verified": False}
