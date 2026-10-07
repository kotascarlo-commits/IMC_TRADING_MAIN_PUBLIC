"""Unsigned wallet-bound Jupiter build and simulation. Never signs or submits.

This prepares technical evidence; it is NOT an accepted live send contract.
Transaction and instruction contents stay inside the owned diagnostic process.
"""
import base64
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time

from solders.address_lookup_table_account import AddressLookupTableAccount
from solders.compute_budget import set_compute_unit_limit, set_compute_unit_price
from solders.hash import Hash
from solders.instruction import AccountMeta, Instruction
from solders.message import MessageV0, to_bytes_versioned
from solders.pubkey import Pubkey
from solders.signature import Signature
from solders.transaction import VersionedTransaction

from .jupiter_read import ROOT, RATE_DB, public_wallet, rate_reservation, unique, JupiterReadError

WORKER=ROOT/"TOOLS/imc_jupiter_prepare_worker.py"
JUPITER="JUP6LkbZbjS1jKKwapdHNy74zcZ3tLUZoi5QNyVTaV4"
LOOKUP="AddressLookupTab1e1111111111111111111111111"
USDC="EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
SOL="So11111111111111111111111111111111111111112"


def worker(message,deadline):
    remaining=deadline-time.monotonic()
    if remaining<=0:
        raise ValueError("prepare_deadline")
    result=subprocess.run([str(Path(sys._base_executable).resolve(strict=True)),str(WORKER)],
        input=json.dumps(message),capture_output=True,text=True,timeout=min(8,remaining),
        creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0))
    if len(result.stdout)>1049600 or time.monotonic()>=deadline:
        raise ValueError("prepare_read_unavailable")
    value=json.loads(result.stdout,object_pairs_hook=unique)
    if result.returncode:
        if value.get("error_class")=="HTTP_STATUS" and value.get("http_status") in (401,403,429):
            raise JupiterReadError({401:"AUTH_REQUIRED",403:"ACCESS_DENIED",429:"RATE_LIMITED"}[value["http_status"]])
        raise ValueError("prepare_read_unavailable")
    if set(value)!={"result"}:
        raise ValueError("prepare_read_unavailable")
    return value["result"]


def rpc(method,params,deadline):
    return worker({"operation":"rpc","payload":{"jsonrpc":"2.0","id":1,"method":method,"params":params}},deadline)["result"]


def instruction(value,wallet):
    if not isinstance(value,dict) or set(value)!={"programId","accounts","data"}:
        raise ValueError("instruction_schema_unknown")
    program=Pubkey.from_string(value["programId"])
    accounts=value["accounts"]
    if not isinstance(accounts,list) or len(accounts)>128:
        raise ValueError("instruction_accounts_unknown")
    parsed=[]
    for account in accounts:
        if (set(account)!={"pubkey","isSigner","isWritable"} or type(account["isSigner"]) is not bool or
                type(account["isWritable"]) is not bool or account["isSigner"] and account["pubkey"]!=wallet):
            raise ValueError("unexpected_signer")
        parsed.append(AccountMeta(Pubkey.from_string(account["pubkey"]),account["isSigner"],account["isWritable"]))
    data=base64.b64decode(value["data"],validate=True)
    if len(data)>4096:
        raise ValueError("instruction_too_large")
    return Instruction(program,data,parsed)


def build_instructions(build,query,*,compute_unit_limit=1_400_000):
    if type(compute_unit_limit) is not int or not 0<compute_unit_limit<=1_400_000:
        raise ValueError("compute_unit_limit_unknown")
    for key in ("inputMint","outputMint"):
        if build.get(key)!=query[key]:
            raise ValueError("build_mint_mismatch")
    if (build.get("inAmount")!=query["amount"] or build.get("swapMode")!="ExactIn" or
            type(build.get("slippageBps")) is not int or build["slippageBps"]!=query["slippageBps"]):
        raise ValueError("build_binding_mismatch")
    output,minimum=build.get("outAmount"),build.get("otherAmountThreshold")
    if (not isinstance(output,str) or not output.isdigit() or not isinstance(minimum,str) or not minimum.isdigit() or
            not 0<int(minimum)<=int(output) or int(minimum)<int(output)*(10000-query["slippageBps"])//10000):
        raise ValueError("build_minimum_unknown")
    if not isinstance(build.get("routePlan"),list) or not build["routePlan"]:
        raise ValueError("build_route_unknown")
    impact=Decimal(str(build.get("priceImpactPct")))
    if not impact.is_finite() or abs(impact)*10000>150:
        raise ValueError("build_impact_unknown_or_exceeded")
    wallet=query["taker"]
    swap=instruction(build["swapInstruction"],wallet)
    if str(swap.program_id)!=JUPITER:
        raise ValueError("unexpected_swap_program")
    # Known Anchor ExactIn route variants carry inAmount/outAmount/slippage/fee
    # at the end. Unknown versions are diagnostic failures, never approvals.
    valid_discriminators={hashlib.sha256(("global:"+name).encode()).digest()[:8]
                          for name in ("route","shared_accounts_route")}
    discriminator=bytes(swap.data[:8])
    v2=hashlib.sha256(b"global:route_v2").digest()[:8]
    shared_v2=hashlib.sha256(b"global:shared_accounts_route_v2").digest()[:8]
    if discriminator in (v2,shared_v2):
        # Confirmed against finalized, program-owned Anchor IDL on 2026-10-05.
        # V2 moves fixed fields before its variable route vector.
        offset=9 if discriminator==shared_v2 else 8
        data=bytes(swap.data)
        if len(data)<offset+31 or len(data)>4096:
            raise ValueError("swap_instruction_version_unknown")
        header=data[offset:offset+22]
        count=int.from_bytes(data[offset+22:offset+26],"little")
        if not 1<=count<=64 or len(data[offset+26:])<count*5:
            raise ValueError("swap_route_vector_unknown")
        if (int.from_bytes(header[:8],"little")!=int(query["amount"]) or
            int.from_bytes(header[8:16],"little")!=int(output) or
            int.from_bytes(header[16:18],"little")!=query["slippageBps"] or
            int.from_bytes(header[18:20],"little")!=0 or int.from_bytes(header[20:22],"little")!=0):
            raise ValueError("swap_instruction_amount_or_fee_mismatch")
        signer,mint_in,mint_out,token_in,token_out=(1,6,7,8,9) if discriminator==shared_v2 else (0,3,4,5,6)
        token_programs={"TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA","TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"}
        if (len(swap.accounts)<(12 if discriminator==shared_v2 else 10) or
            str(swap.accounts[signer].pubkey)!=wallet or not swap.accounts[signer].is_signer or
            str(swap.accounts[mint_in].pubkey)!=query["inputMint"] or
            str(swap.accounts[mint_out].pubkey)!=query["outputMint"] or
            str(swap.accounts[token_in].pubkey) not in token_programs or
            str(swap.accounts[token_out].pubkey) not in token_programs):
            raise ValueError("swap_instruction_identity_unknown")
    elif len(swap.data)<27 or discriminator not in valid_discriminators:
        raise ValueError("swap_instruction_version_unknown")
    else:
        tail=bytes(swap.data[-19:])
        if (int.from_bytes(tail[:8],"little")!=int(query["amount"]) or
            int.from_bytes(tail[8:16],"little")!=int(output) or
            int.from_bytes(tail[16:18],"little")!=query["slippageBps"] or tail[18]!=0):
            raise ValueError("swap_instruction_amount_or_fee_mismatch")
    instructions=[set_compute_unit_limit(compute_unit_limit)]
    for field in ("computeBudgetInstructions","setupInstructions","otherInstructions"):
        values=build.get(field,[])
        if not isinstance(values,list) or len(values)>12:
            raise ValueError("build_instruction_count")
        instructions.extend(instruction(v,wallet) for v in values)
    instructions.append(swap)
    if build.get("cleanupInstruction"):
        instructions.append(instruction(build["cleanupInstruction"],wallet))
    if build.get("tipInstruction"):
        raise ValueError("unexpected_tip_instruction")
    return instructions


def compile_unsigned(wallet,instructions,tables,blockhash):
    message=MessageV0.try_compile(Pubkey.from_string(wallet),instructions,tables,Hash.from_string(blockhash))
    if message.header.num_required_signatures!=1 or str(message.account_keys[0])!=wallet:
        raise ValueError("fee_payer_or_signer_unknown")
    tx=VersionedTransaction.populate(message,[Signature.default()])
    if len(bytes(tx))>1232:
        raise ValueError("transaction_too_large")
    return tx


def assemble(build,query,tables,blockhash,*,compute_unit_limit=1_400_000):
    return compile_unsigned(query["taker"],build_instructions(build,query,compute_unit_limit=compute_unit_limit),tables,blockhash)


def load_tables(build,deadline,*,rpc_reader=None):
    read=rpc if rpc_reader is None else rpc_reader
    lookup=build.get("addressesByLookupTableAddress") or {}
    if not isinstance(lookup,dict) or len(lookup)>6:
        raise ValueError("lookup_table_schema_unknown")
    tables=[]
    for address,expected in lookup.items():
        if not isinstance(expected,list) or len(expected)>256:
            raise ValueError("lookup_table_schema_unknown")
        account=read("getAccountInfo",[address,{"encoding":"base64","commitment":"finalized"}],deadline)["value"]
        if not account or account.get("owner")!=LOOKUP or account.get("executable") is not False:
            raise ValueError("lookup_table_owner_unknown")
        raw=base64.b64decode(account["data"][0],validate=True)
        if len(raw)<56 or (len(raw)-56)%32 or int.from_bytes(raw[:4],"little")!=1:
            raise ValueError("lookup_table_layout_unknown")
        if int.from_bytes(raw[4:12],"little")!=18446744073709551615:
            raise ValueError("lookup_table_deactivated")
        actual=[str(Pubkey.from_bytes(raw[i:i+32])) for i in range(56,len(raw),32)]
        if actual!=expected:
            raise ValueError("lookup_table_binding_mismatch")
        tables.append(AddressLookupTableAccount(Pubkey.from_string(address),[Pubkey.from_string(a) for a in actual]))
    return tables


def two_phase_simulation(assembler,deadline,*,rpc_reader=None):
    read=rpc if rpc_reader is None else rpc_reader
    latest=read("getLatestBlockhash",[{"commitment":"finalized"}],deadline)["value"]
    tx=assembler(latest["blockhash"],1_400_000)
    message_b64=base64.b64encode(to_bytes_versioned(tx.message)).decode()
    fee=read("getFeeForMessage",[message_b64,{"commitment":"finalized"}],deadline)["value"]
    if type(fee) is not int or not 0<fee<=3_000_000:
        raise ValueError("network_fee_unknown_or_exceeded")
    options={"encoding":"base64","sigVerify":False,"replaceRecentBlockhash":False,"commitment":"finalized"}
    simulation=read("simulateTransaction",[base64.b64encode(bytes(tx)).decode(),options],deadline)
    value=simulation["value"]
    if (type(simulation.get("context",{}).get("slot")) is not int or simulation["context"]["slot"]<1 or
        "err" not in value or type(value.get("unitsConsumed")) is not int or value["unitsConsumed"]<0 or
        value.get("replacementBlockhash") is not None):
        raise ValueError("simulation_evidence_unknown")
    passed=value["err"] is None and 0<value["unitsConsumed"]<=1_400_000
    initial_units=value["unitsConsumed"];initial_fee=fee;limit=1_400_000
    if passed:
        # Preserve Jupiter's price. Pay for the measured budget plus headroom,
        # then independently simulate the exact new message; never extrapolate.
        limit=min(1_400_000,(initial_units*120+99)//100+10_000)
        latest=read("getLatestBlockhash",[{"commitment":"finalized"}],deadline)["value"]
        tx=assembler(latest["blockhash"],limit)
        message_b64=base64.b64encode(to_bytes_versioned(tx.message)).decode()
        fee=read("getFeeForMessage",[message_b64,{"commitment":"finalized"}],deadline)["value"]
        if type(fee) is not int or not 0<fee<=3_000_000:
            raise ValueError("network_fee_unknown_or_exceeded")
        simulation=read("simulateTransaction",[base64.b64encode(bytes(tx)).decode(),options],deadline)
        value=simulation["value"]
        if (type(simulation.get("context",{}).get("slot")) is not int or simulation["context"]["slot"]<1 or
                "err" not in value or type(value.get("unitsConsumed")) is not int or value["unitsConsumed"]<0 or
                value.get("replacementBlockhash") is not None):
            raise ValueError("simulation_evidence_unknown")
        passed=value["err"] is None and 0<value["unitsConsumed"]<=limit
    return tx,simulation,fee,latest,{"passed":passed,"initial_units":initial_units,
        "initial_fee":initial_fee,"limit":limit}


def prepare(input_mint=SOL,output_mint=USDC,amount=1_000_000,*,deadline=None):
    started=time.time();deadline=min(deadline or time.monotonic()+60,time.monotonic()+60)
    wallet=public_wallet()
    query={"inputMint":input_mint,"outputMint":output_mint,"amount":str(amount),
           "taker":wallet,"slippageBps":100,"mode":"fast"}
    with rate_reservation(RATE_DB,deadline=deadline):
        build=worker({"operation":"build","query":query},deadline)
    from .jupiter_instructions import validate_auxiliary
    auxiliary=validate_auxiliary(build,query)
    tables=load_tables(build,deadline)
    tx,simulation,fee,latest,measurement=two_phase_simulation(
        lambda blockhash,limit:assemble(build,query,tables,blockhash,compute_unit_limit=limit),deadline)
    passed=measurement["passed"];value=simulation["value"]
    from .jupiter_simulation import message_keys
    resolved,_=message_keys(tx,tables)
    # Fee, native and token pre/post balances must refer to this one simulation
    # bank and this exact message. Never join independent RPC snapshots by age.
    cost_evidence={"status":"UNKNOWN","evidence_class":"UNSIGNED_SAME_BANK_SIMULATION"}
    if passed:
        from .jupiter_simulation import account_simulation
        try:
            cost_evidence=account_simulation(tx,tables,query,build,simulation,fee)
        except (ValueError,TypeError,KeyError,IndexError):
            # A simulation can succeed while its accounting evidence is absent.
            # That never grants execution permission or renews an old cost cache.
            pass
    return {"schema_version":1,"provider":"JUPITER_V2_BUILD","wallet":wallet,
        "input_mint":input_mint,"output_mint":output_mint,"input_raw":str(amount),
        "observed_at":started,"status":"UNSIGNED_SIMULATION_PASS" if passed else "UNSIGNED_SIMULATION_FAILED",
        "completed_at":time.time(),"simulation_slot":simulation["context"]["slot"],"commitment":"finalized",
        "message_sha256":hashlib.sha256(to_bytes_versioned(tx.message)).hexdigest(),
        "account_keys_sha256":hashlib.sha256(json.dumps(resolved,separators=(",", ":")).encode()).hexdigest(),
        "transaction_sha256":hashlib.sha256(bytes(tx)).hexdigest(),"transaction_bytes":len(bytes(tx)),
        "wallet_and_fee_payer_bound":True,"swap_amounts_bound":True,"lookup_tables_chain_verified":True,
        "network_fee_lamports":str(fee),"units_consumed":value.get("unitsConsumed"),
        "simulation_error":value.get("err"),
        "initial_network_fee_lamports":str(measurement["initial_fee"]),"initial_units_consumed":measurement["initial_units"],
        "compute_unit_limit":measurement["limit"],"compute_unit_price_micro_lamports":auxiliary["compute_unit_price_micro_lamports"],
        "auxiliary_instruction_policy_verified":True,"swap_associated_accounts_bound":True,
        "blockhash_replacement":False,
        "cost_evidence":cost_evidence,
        "last_valid_block_height":latest["lastValidBlockHeight"],"lookup_tables":len(tables),
        "route_liquidity_usd":None,"total_fee_and_rent_lamports":None,"full_instruction_policy_verified":False,
        "recipient_binding_verified":False,"full_route_semantics_verified":False,
        "sig_verify":False,"signing":False,"broadcast":False,"execution_eligible":False}


def prepare_roundtrip(amount,*,middle_mint=USDC,deadline=None):
    """Atomic unsigned BUY/SELL probe, never separate-leg execution evidence.

    The real wallet need not own the intermediate token. Both swaps run inside
    one hypothetical transaction; remaining tokens and native cash are retained
    in the report. This cannot verify a standalone exit or a two-send recovery.
    """
    from .jupiter_instructions import validate_auxiliary
    from .jupiter_simulation import (raw as token_raw, u64, message_keys, token_states,
                                    associated, TOKEN)
    started=time.time();deadline=min(deadline or time.monotonic()+60,time.monotonic()+60)
    wallet=public_wallet()
    buy_query={"inputMint":SOL,"outputMint":middle_mint,"amount":str(amount),
               "taker":wallet,"slippageBps":100,"mode":"fast"}
    with rate_reservation(RATE_DB,deadline=deadline):
        buy=worker({"operation":"build","query":buy_query},deadline)
    buy_policy=validate_auxiliary(buy,buy_query)
    # The SELL cannot consume more than the contractual BUY minimum, even if
    # the first swap realizes less output than its indicative quote.
    sell_amount=token_raw(buy["otherAmountThreshold"])
    sell_query={"inputMint":middle_mint,"outputMint":SOL,"amount":str(sell_amount),
                "taker":wallet,"slippageBps":100,"mode":"fast"}
    with rate_reservation(RATE_DB,deadline=deadline):
        sell=worker({"operation":"build","query":sell_query},deadline)
    sell_policy=validate_auxiliary(sell,sell_query)
    tables_by_key={}
    for build in (buy,sell):
        for table in load_tables(build,deadline):
            key=str(table.key)
            if key in tables_by_key and tables_by_key[key]!=table:
                raise ValueError("roundtrip_lookup_changed")
            tables_by_key[key]=table
    tables=list(tables_by_key.values())
    if len(tables)>6:raise ValueError("roundtrip_lookup_limit")
    # Validate both instruction amount/minimum contracts before dropping their
    # separate budget headers. Preserve setup -> swap -> cleanup for EACH leg.
    buy_instructions=build_instructions(buy,buy_query)
    sell_instructions=build_instructions(sell,sell_query)
    price=max(buy_policy["compute_unit_price_micro_lamports"],sell_policy["compute_unit_price_micro_lamports"])
    def assembler(blockhash,limit):
        instructions=[set_compute_unit_limit(limit),set_compute_unit_price(price)]
        instructions.extend(buy_instructions[2:]);instructions.extend(sell_instructions[2:])
        return compile_unsigned(wallet,instructions,tables,blockhash)
    tx,simulation,fee,latest,measurement=two_phase_simulation(assembler,deadline)
    report={"schema_version":1,"observed_at":started,"completed_at":time.time(),
        "status":"UNSIGNED_ATOMIC_ROUNDTRIP_PASS" if measurement["passed"] else "UNSIGNED_ATOMIC_ROUNDTRIP_FAILED",
        "evidence_class":"UNSIGNED_ATOMIC_SIMULATION_NOT_SEPARATE_FILLS","wallet":wallet,
        "input_raw":str(amount),"middle_mint":middle_mint,"sell_input_raw":str(sell_amount),
        "sell_minimum_output_raw":sell["otherAmountThreshold"],"simulation_slot":simulation["context"]["slot"],
        "message_sha256":hashlib.sha256(to_bytes_versioned(tx.message)).hexdigest(),
        "transaction_bytes":len(bytes(tx)),"network_fee_lamports":str(fee),
        "compute_unit_limit":measurement["limit"],"units_consumed":simulation["value"]["unitsConsumed"],
        "initial_network_fee_lamports":str(measurement["initial_fee"]),
        "blockhash_replacement":False,"commitment":"finalized","signing":False,"broadcast":False,
        "separate_exit_verified":False,"full_route_semantics_verified":False,
        "execution_eligible":False,"chain_forward_startable":False,"accounting_status":"UNKNOWN"}
    if not measurement["passed"]:return report
    keys,loaded=message_keys(tx,tables);value=simulation["value"]
    if value.get("loadedAddresses")!=loaded or u64(value.get("fee"))!=fee:
        raise ValueError("roundtrip_message_accounting_mismatch")
    pre,post=value.get("preBalances"),value.get("postBalances")
    if not isinstance(pre,list) or not isinstance(post,list) or len(pre)!=len(keys) or len(post)!=len(keys):
        raise ValueError("roundtrip_native_balances_missing")
    pre,post=[u64(v) for v in pre],[u64(v) for v in post]
    if sum(post)-sum(pre)!=-fee:raise ValueError("roundtrip_native_conservation_unknown")
    before=token_states(value.get("preTokenBalances"),keys)
    after=token_states(value.get("postTokenBalances"),keys)
    token_delta=0;rent=0
    for index in set(before)|set(after):
        old,new=before.get(index),after.get(index)
        if old and new and any(old[k]!=new[k] for k in ("mint","owner","program","decimals")):
            raise ValueError("roundtrip_token_identity_changed")
        state=new or old
        if state["owner"]!=wallet:continue
        if (state["mint"]!=middle_mint or state["program"]!=TOKEN or
            keys[index]!=associated(wallet,middle_mint,TOKEN)):
            raise ValueError("roundtrip_wallet_asset_unknown")
        if (not old and pre[index]!=0) or (not new and post[index]!=0):
            raise ValueError("roundtrip_token_state_missing")
        token_delta+=(new["amount"] if new else 0)-(old["amount"] if old else 0)
        rent+=post[index]-pre[index]
    wsol=associated(wallet,SOL,TOKEN)
    if wsol not in keys or pre[keys.index(wsol)]!=0 or post[keys.index(wsol)]!=0 or token_delta<0:
        raise ValueError("roundtrip_existing_wsol_or_inventory_debit")
    cash_delta=post[0]-pre[0]
    if cash_delta<token_raw(sell["otherAmountThreshold"])-token_raw(str(amount))-fee-rent:
        raise ValueError("roundtrip_cash_below_bound_minimum")
    # These message fees use the measured COMBINED budget. They are separate
    # hypothetical message prices, not standalone simulation/landing evidence.
    message_fees={}
    for name,build,query in (("buy",buy,buy_query),("sell",sell,sell_query)):
        separate=assemble(build,query,tables,latest["blockhash"],compute_unit_limit=measurement["limit"])
        price_result=rpc("getFeeForMessage",[base64.b64encode(to_bytes_versioned(separate.message)).decode(),
            {"commitment":"finalized"}],deadline)["value"]
        if not 0<u64(price_result)<=3_000_000:raise ValueError("roundtrip_separate_fee_unknown")
        message_fees[name]=str(price_result)
    report.update(accounting_status="SIMULATED_NATIVE_AND_TOKEN_DELTAS_OBSERVED",
        wallet_before_lamports=str(pre[0]),wallet_after_lamports=str(post[0]),
        wallet_cash_delta_lamports=str(cash_delta),net_bound_account_lamports=str(rent),
        remaining_middle_token_delta_raw=str(token_delta),
        separate_message_fees_at_combined_budget=message_fees,
        separate_message_fees_are_execution_costs=False)
    return report
