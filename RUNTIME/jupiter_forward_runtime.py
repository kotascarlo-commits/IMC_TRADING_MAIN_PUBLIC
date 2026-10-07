"""Canonical Jupiter quote-forward entrypoint. Contains no live sender."""
import argparse
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import time

from mykonos_meme_scout import paper_forward as paper
from mykonos_meme_scout.jupiter_forward import (ROOT, load_contract, policy_hash, source_status,
    open_book, read_report, JupiterForwardRunner, paper_config, capital_check, entry_cost_pass)
from mykonos_meme_scout.jupiter_read import JupiterReader
from mykonos_meme_scout.jupiter_read import JupiterReadError, unique
from mykonos_meme_scout.projection_io import write_json_projection

FEED_STATUS = ROOT/"RUNTIME/jupiter-feed-status.json"
READINESS = ROOT/"RUNTIME/jupiter-forward-readiness.json"
PREPARATION = ROOT/"RUNTIME/jupiter-preparation.json"


def owner_off():
    bridge = ROOT/"RUNTIME/EXECUTOR_GATEWAY/GMGN_BRIDGE"
    if str(bridge) not in sys.path:
        sys.path.insert(0,str(bridge))
    from execution_policy import owner_gate
    from execution_state import load_config
    gate, cfg = owner_gate(),load_config()
    return bool(gate == {"value":"OFF","valid":True} and all(cfg[k] is False for k in
        ("broadcast_enabled","live_trading_enabled","signing","wallet_access")) and cfg["execution_authority_pct"] == 0)


def wallet_verification():
    try:
        interpreter=Path(sys._base_executable).resolve(strict=True)
        if not interpreter.is_absolute() or interpreter.parent!=Path(sys.base_prefix).resolve():
            raise ValueError("wallet_verification_failed")
        command = [str(interpreter),"-I","-S",str(ROOT/"TOOLS/imc_local_wallet_verify.py")]
        result = subprocess.run(command,stdin=subprocess.DEVNULL,capture_output=True,text=True,timeout=10,
            creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0))
        if result.returncode or len(result.stdout)>4096:
            raise ValueError("wallet_verification_failed")
        value=json.loads(result.stdout, object_pairs_hook=unique)
        if (value.get("status") != "PUBLIC_KEY_VERIFIED" or value.get("signing") is not False or
                value.get("broadcast") is not False or value.get("secret_exported") is not False):
            raise ValueError("wallet_verification_failed")
        return value
    except (ValueError,OSError,subprocess.SubprocessError):
        return {"status":"WALLET_UNAVAILABLE","signing":False,"broadcast":False}


def preflight(contract, *, reader=None):
    checks = {"authority_off": {"status":"PASS" if owner_off() else "UNKNOWN"},
              "source": source_status(contract), "local_wallet":wallet_verification()}
    cfg=paper_config(contract)
    reader = reader or JupiterReader(cfg)
    try:
        wallet,balance = reader.wallet()
        checks["capital"]=capital_check(balance,cfg)
        checks["rpc_inventory"]={"status":"PASS",**reader.last_wallet_snapshot}
        checks["wallet_identity"]={"status":"PASS" if checks["local_wallet"].get("wallet")==wallet else "UNKNOWN"}
        amount=int(checks["capital"]["position_input_lamports"])
        mint="EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
        buy=reader.quote(wallet,paper.SOL,mint,amount)
        # Preflight applies the identical observation policy without creating a cohort.
        from mykonos_meme_scout.jupiter_forward import JupiterForwardBook
        validator=JupiterForwardBook.__new__(JupiterForwardBook)
        validator.cfg,validator.contract,validator.clock=cfg,contract,paper.now
        validator.validate_observation(buy,paper.SOL,mint,amount)
        sell_amount=validator.net_output(buy)
        sell=reader.quote(wallet,mint,paper.SOL,sell_amount)
        validator.validate_observation(sell,mint,paper.SOL,sell_amount)
        # Recheck BUY's original time after SELL waited for the shared rate bucket.
        validator.validate_observation(buy,paper.SOL,mint,amount)
        checks["two_way_quote"]={"status":"PASS","buy":buy,"sell":sell}
        sell_net=validator.net_output(sell)
        checks["roundtrip_cost"]={"status":"PASS" if entry_cost_pass(cfg,amount,sell_net) else "UNKNOWN",
            "input_lamports":str(amount),"invested_with_entry_fee":str(amount+cfg["network_fee_lamports_per_side"]),
            "net_liquidation_after_exit_fee":str(max(0,sell_net-cfg["network_fee_lamports_per_side"])),
            "hard_stop_pct":cfg["hard_stop_pct"],"cost_evidence":"MODEL_ALLOWANCE_NOT_CHAIN_TOTAL"}
    except (ValueError,OSError,subprocess.SubprocessError) as error:
        checks["market_read"]={"status":"UNKNOWN","error_class":type(error).__name__,
                              "code":error.code if isinstance(error,JupiterReadError) else "READ_UNAVAILABLE"}
    # Source may have aged while wallet/quote reads ran. Never renew it from a cache.
    checks["source"]=source_status(contract)
    required=("authority_off","source","rpc_inventory","wallet_identity","two_way_quote","capital","roundtrip_cost")
    ready=all(checks.get(key,{}).get("status")=="PASS" for key in required)
    report={"schema_version":1,"observed_at":paper.now(),"mode":"JUPITER_QUOTE_FORWARD_100",
        "quote_forward_startable":ready,"chain_forward_startable":False,
        "broadcast_enabled":False,"signing":False,"target_closed":100,
        "evidence_class":"JUPITER_LIVE_MARKET_QUOTE_PAPER_NOT_CHAIN_FILLS",
        "policy_hash":policy_hash(contract),"checks":checks,
        "quote_forward_blockers":[key.upper()+"_NOT_VERIFIED" for key in required if checks.get(key,{}).get("status")!="PASS"],
        "chain_blockers":["PROVIDER_SPECIFIC_SEND_AND_FINALIZED_RECOVERY_NOT_ACCEPTED",
                          "ROUTE_LIQUIDITY_AND_TOTAL_FEES_NOT_VERIFIED",
                          "BOUNDED_CHAIN_TRIAL_AND_OWNER_LIVE_AUTHORIZATION_REQUIRED"]}
    write_json_projection(READINESS,report)
    return report


def feed(contract, stop, ticks):
    from mykonos_meme_scout.cli import scan_once
    from mykonos_meme_scout.config import Config
    cfg=Config()
    index=0
    while not stop.exists() and (ticks is None or index<ticks):
        if not owner_off():
            raise ValueError("forward_requires_money_authority_off")
        started=time.monotonic()
        try:
            phase={}
            result=scan_once(cfg,ROOT/contract["source_database"],30,
                manage_open_positions=False,watcher_priority=True,timing_out=phase)
            observation=source_status(contract)
            report={"schema_version":1,"updated_at":paper.now(),"status":observation["status"],
                "pid":__import__("os").getpid(),"source":observation,"scanned":len(result),"phases":phase,
                "broadcast_enabled":False,"signing":False}
        except Exception as error:
            report={"schema_version":1,"updated_at":paper.now(),"status":"UNKNOWN",
                    "error_class":type(error).__name__,"broadcast_enabled":False,"signing":False}
        write_json_projection(FEED_STATUS,report)
        index+=1
        time.sleep(max(.1,contract["feed_interval_seconds"]-(time.monotonic()-started)))


def unsigned_preparation():
    deadline=time.monotonic()+60
    if not owner_off():
        raise ValueError("prepare_requires_money_authority_off")
    from mykonos_meme_scout.jupiter_prepare import prepare,prepare_roundtrip
    cfg=paper_config(load_contract())
    _,balance=JupiterReader(cfg).wallet()
    amount=int(capital_check(balance,cfg)["position_input_lamports"])
    report=prepare(amount=amount,deadline=deadline)
    try:
        report["atomic_roundtrip"]=prepare_roundtrip(amount,deadline=deadline)
    except (ValueError,TypeError,KeyError,OSError,subprocess.SubprocessError) as error:
        report["atomic_roundtrip"]={"status":"UNKNOWN","error_class":type(error).__name__,
            "execution_eligible":False,"separate_exit_verified":False}
    if not owner_off():
        raise ValueError("prepare_requires_money_authority_off")
    write_json_projection(PREPARATION,report)
    return report


def run(contract,stop,ticks):
    if not owner_off():
        raise ValueError("forward_requires_money_authority_off")
    check=preflight(contract)
    if not check["quote_forward_startable"]:
        raise ValueError("jupiter_forward_preflight_blocked")
    book,reader=open_book(contract)
    runner=JupiterForwardRunner(book,reader)
    index=0
    try:
        while not stop.exists() and (ticks is None or index<ticks):
            if not owner_off() or policy_hash(contract)!=book.contract_hash:
                with book.transaction():
                    book.set_meta("halt","authority_or_frozen_sources_changed")
                    book.event("runtime_stopped",{"reason":"authority_or_frozen_sources_changed"})
                break
            delay=contract["poll_seconds"]
            try:
                # Never admit stale source data; existing exit obligations still run.
                if book.active() is not None or source_status(contract)["status"]=="PASS":
                    runner.tick()
            except (ValueError,OSError,sqlite3.Error,RuntimeError,subprocess.SubprocessError) as error:
                with book.transaction():
                    book.event("runner_error",{"class":type(error).__name__})
                delay=book.cfg["error_cooldown_seconds"]
            report=book.report()
            write_json_projection(ROOT/contract["status"],report)
            if report["cohort_complete"] or (book.active() is None and (report["halt"] or report["admitted"]>=200)):
                break
            index+=1
            end=time.monotonic()+delay
            while not stop.exists() and time.monotonic()<end:
                time.sleep(min(.2,max(0,end-time.monotonic())))
    finally:
        book.conn.close()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode",choices=("feed","preflight","prepare","validate","initialize-validation","run","status"),required=True)
    parser.add_argument('--maintenance-nonce')
    parser.add_argument("--stop-file",type=Path,default=ROOT/"RUNTIME/jupiter-forward.stop")
    parser.add_argument("--ticks",type=int)
    args=parser.parse_args()
    contract=load_contract()
    if args.ticks is not None and args.ticks<1:
        raise ValueError("invalid_ticks")
    if args.mode=='initialize-validation':
        if not owner_off():raise ValueError('validation_requires_all_real_gates_off')
        sys.path.insert(0,str(ROOT/'RUNTIME/EXECUTOR_GATEWAY/GMGN_BRIDGE'))
        sys.path.insert(0,str(ROOT/'scripts'))
        from maintenance_state import read as maintenance_read
        def guard():
            state=maintenance_read(ROOT/'RUNTIME/maintenance-state.json')
            if state['state']!='ACTIVE' or state['nonce']!=args.maintenance_nonce:
                raise ValueError('validation_maintenance_lease_required')
        guard()
        from execution_state import Ledger,timestamp
        from execution_validation import migrate_unexecuted_wallet,recover_prestart_read_abort,status as validation_status
        from execution_jupiter_monitor import JupiterReconciliationReader
        store=Ledger(ROOT/'RUNTIME/EXECUTOR_GATEWAY/GMGN_BRIDGE/execution-ledger.db')
        reader=JupiterReconciliationReader(store.cfg)
        first=reader.snapshot(store)
        migrate_unexecuted_wallet(store,first,maintenance_guard=guard)
        recover_prestart_read_abort(store,first,maintenance_guard=guard)
        second=reader.snapshot(store)
        if not store.reconcile(second,read_guard=guard):raise ValueError('validation_initial_reconciliation_failed')
        with store.transaction(before_commit=guard) as c:
            if store.meta(c,'validation_prestart_recovery_pending'):
                if not owner_off():raise ValueError('validation_requires_all_real_gates_off')
                store.set_meta(c,'validation_prestart_recovery_pending',False)
                store.audit(c,'validation_prestart_recovery_reconciled',{'revision':store.meta(c,'reconciliation_revision')})
        state=store.status()
        store.mark_equity({'wallet':state['wallet'],'observed_at':timestamp(),
            'balance_lamports':state['wallet_balance_lamports'],'marks':{},
            'reconciliation_revision':state['reconciliation_revision']},read_guard=guard)
        with store.transaction() as c:mode=validation_status(store,c)
        with store.transaction() as c:
            if c.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise ValueError('validation_ledger_integrity_failed')
            store.validate_links(c)
        if not owner_off() or state['open_positions']!=0:raise ValueError('validation_safety_failed')
        report={'status':'PREPARED_ONLY','mode':mode,'wallet':state['wallet'],'open_positions':state['open_positions'],
                'signing':False,'broadcast':False,'live_trading':False,'owner_gate':'OFF','completed_at':time.time()}
        write_json_projection(ROOT/'RUNTIME/three-trade-preparation.json',report)
        print(json.dumps(report,indent=2))
    elif args.mode=="validate":
        if not owner_off():raise ValueError('validation_requires_all_real_gates_off')
        import uuid
        sys.path.insert(0,str(ROOT/'RUNTIME/EXECUTOR_GATEWAY/GMGN_BRIDGE'))
        from execution_validation_run import IsolatedValidationRun
        from execution_validation import status as validation_status
        case=IsolatedValidationRun(ROOT/'.local-tools'/('three-trade-validation-'+uuid.uuid4().hex)/'execution.db')
        for _ in range(3):case.roundtrip()
        from execution_state import Ledger
        restart=Ledger(case.store.path)
        with restart.transaction() as c:mode=validation_status(restart,c)
        if (not mode['stopped'] or mode['closed_count']!=3 or restart.status()['open_positions']!=0
                or restart.status()['performance']['count']!=0 or not owner_off()):
            raise ValueError('isolated_validation_failed')
        report={'status':'ISOLATED_VALIDATION_PASS','mode':'THREE_TRADE_VALIDATION','evidence_domain':'ISOLATED',
                'closed':3,'send_calls':len(case.bank.sent),'auto_stop':True,'restart_counter':mode['closed_count'],
                'real_closed':0,'open_positions':0,'owner_gate':'OFF','signing':False,'broadcast':False,
                'live_trading':False,'ledger':str(case.store.path),'completed_at':time.time()}
        write_json_projection(ROOT/'RUNTIME/three-trade-shadow-acceptance.json',report)
        print(json.dumps(report,indent=2))
    elif args.mode=="feed":
        if not owner_off():
            raise ValueError("forward_requires_money_authority_off")
        feed(contract,args.stop_file,args.ticks)
    elif args.mode=="preflight":
        report=preflight(contract)
        print(json.dumps(report,indent=2))
        return 0 if report["quote_forward_startable"] else 1
    elif args.mode=="status":
        print(json.dumps(read_report(contract),indent=2))
    elif args.mode=="prepare":
        report=unsigned_preparation()
        print(json.dumps(report,indent=2))
        return 0 if report["status"]=="UNSIGNED_SIMULATION_PASS" else 1
    else:
        run(contract,args.stop_file,args.ticks)
    return 0


if __name__=="__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        import re
        code=str(error)
        report={"status":"UNKNOWN","error_class":type(error).__name__,"broadcast_enabled":False,
                "code":code if re.fullmatch(r'[A-Za-z0-9_:-]{1,96}',code) else 'RUNTIME_PREPARATION_FAILED'}
        if '--mode' in sys.argv and 'initialize-validation' in sys.argv:
            write_json_projection(ROOT/'RUNTIME/three-trade-preparation.json',report)
        print(json.dumps(report))
        raise SystemExit(1)
