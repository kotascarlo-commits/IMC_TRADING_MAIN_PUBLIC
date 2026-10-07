"""Controlled lifecycle fixtures only. Never network, signing or forward proof."""
import importlib.util
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace

import pytest
from solders.pubkey import Pubkey

from mykonos_meme_scout import paper_forward as paper
from mykonos_meme_scout import jupiter_forward as forward
from mykonos_meme_scout import jupiter_read as read


@pytest.fixture
def book(tmp_path):
    clock=[1000.0]
    result=forward.JupiterForwardBook(tmp_path/"cohort.db",forward.load_contract(),paper.SOL,
        1_000_000_000,0,clock=lambda:clock[0],frozen_hash="controlled-fixture-only")
    yield result,clock
    result.conn.close()


def snapshot(index,clock):
    return {"address":str(Pubkey.from_bytes(index.to_bytes(32,"big"))),
        "captured_at":__import__("datetime").datetime.fromtimestamp(clock[0],__import__("datetime").timezone.utc).isoformat(),
        "price_usd":1,"security":{"checked_at":__import__("datetime").datetime.fromtimestamp(
            clock[0],__import__("datetime").timezone.utc).isoformat()}}


def quote(book,clock,input_token,output_token,raw,output):
    return {"input_token":input_token,"output_token":output_token,"input_amount":str(raw),
        "output_amount":str(output),"min_output_amount":str(output),"slippage":1,
        "requested_at":clock[0],"received_at":clock[0],"source":read.SOURCE,
        "provider":"JUPITER_V2","is_fill":False,"execution_eligible":False,
        "transaction_built":False,"route_steps":1,"price_impact_bps":"0.1"}


def roundtrip(book,clock,index):
    trade=book.admit(snapshot(index,clock),index)
    buy=quote(book,clock,paper.SOL,trade["mint"],trade["input_lamports"],100000)
    amount=book.net_output(buy)
    sell=quote(book,clock,trade["mint"],paper.SOL,amount,trade["input_lamports"]+20_000_000)
    book.open(trade,buy,sell)
    book.request_exit(trade,"MAX_HOLD")
    book.close(trade,sell)
    return trade


def test_provider_evidence_never_becomes_chain_or_gmgn(book):
    value,clock=book
    trade=roundtrip(value,clock,1)
    report=value.report()
    assert trade["state"]=="CLOSED" and report["closed"]==1
    assert trade["evidence_class"]==forward.EVIDENCE and report["evidence_class"]==forward.EVIDENCE
    assert report["onchain_closed"]==0 and report["live_forward_ready"] is False
    audits=[json.loads(r[0]) for r in value.conn.execute("SELECT doc FROM audit")]
    assert all("GMGN_QUOTE_PAPER" not in json.dumps(a) for a in audits)


def test_exact_100_closed_stops_admission_and_runner_discovery(book,monkeypatch):
    value,clock=book
    for index in range(1,101):
        roundtrip(value,clock,index)
    assert value.report()["cohort_complete"] is True
    assert value.admit(snapshot(101,clock),101) is None
    reader=SimpleNamespace(quote=lambda *a:pytest.fail("unexpected quote after target"))
    runner=forward.JupiterForwardRunner(value,reader)
    monkeypatch.setattr(runner,"discover",lambda:pytest.fail("unexpected discovery after target"))
    runner.tick()
    assert len(value.intents())==100


def test_failed_attempts_cannot_be_erased_or_counted_as_closed(book):
    value,clock=book
    for index in range(1,201):
        trade=value.admit(snapshot(index,clock),index)
        value.fail(trade,"controlled_no_route")
    assert value.admit(snapshot(201,clock),201) is None
    report=value.report()
    assert report["admitted"]==200 and report["failed"]==200 and report["closed"]==0
    assert report["cohort_complete"] is False and report["goals_passed"] is False
    assert report["halt"]=="attempt_limit_before_100_closed"


def test_pending_exit_survives_restart_and_unknown_quote(book):
    value,clock=book
    trade=value.admit(snapshot(1,clock),1)
    buy=quote(value,clock,paper.SOL,trade["mint"],trade["input_lamports"],100000)
    sell=quote(value,clock,trade["mint"],paper.SOL,value.net_output(buy),51_000_000)
    value.open(trade,buy,sell)
    value.request_exit(trade,"HARD_STOP")
    value.unknown(trade,"controlled_timeout")
    restarted=forward.JupiterForwardBook(value.path,forward.load_contract(),paper.SOL,
        1_000_000_000,999,clock=lambda:clock[0],frozen_hash="controlled-fixture-only")
    try:
        assert restarted.active()["state"]=="EXITING" and restarted.meta("checkpoint")==0
        assert restarted.report()["api_failures"]==1
        recovered=restarted.active()
        restarted.close(recovered,sell)
        assert restarted.report()["closed"]==1
    finally:
        restarted.conn.close()


@pytest.mark.parametrize("change",[{"source":"GMGN_ORDER_QUOTE"},{"is_fill":True},
    {"provider":"UNKNOWN"},{"transaction_built":True},{"execution_eligible":True},
    {"price_impact_bps":None},{"price_impact_bps":"NaN"},{"price_impact_bps":"151"},
    {"route_steps":False},{"route_steps":0}])
def test_invalid_market_evidence_cannot_open_position(book,change):
    value,clock=book
    trade=value.admit(snapshot(1,clock),1)
    buy=quote(value,clock,paper.SOL,trade["mint"],trade["input_lamports"],100000)|change
    sell=quote(value,clock,trade["mint"],paper.SOL,99000,51_000_000)
    cash=value.meta("cash")
    with pytest.raises(ValueError):
        value.open(trade,buy,sell)
    assert value.meta("cash")==cash and value.active()["state"]=="ENTERING"


def test_policy_change_never_resets_existing_cohort(book):
    value,clock=book
    roundtrip(value,clock,1)
    with pytest.raises(ValueError,match="frozen_cohort"):
        forward.JupiterForwardBook(value.path,forward.load_contract(),paper.SOL,1_000_000_000,0,
            clock=lambda:clock[0],frozen_hash="different-code")
    assert value.report()["closed"]==1


@pytest.mark.parametrize("change",[{"target_closed":99},{"signing":True},{"target_closed":True},
    {"provider":"GMGN"},{"database":"../outside.db"},{"max_attempts":201}])
def test_contract_cannot_change_count_provider_or_authority(tmp_path,change):
    path=tmp_path/"config.json"
    path.write_text(json.dumps(forward.load_contract()|change))
    with pytest.raises(ValueError,match="invalid_jupiter"):
        forward.load_contract(path)


def test_rate_limit_persists_across_new_connections(tmp_path):
    path=tmp_path/"rate.db"
    clock=[1000.0]
    sleeps=[]
    def sleep(seconds):
        sleeps.append(seconds);clock[0]+=seconds
    with read.rate_reservation(path,clock=lambda:clock[0],sleep=sleep):
        pass
    with read.rate_reservation(path,clock=lambda:clock[0],sleep=sleep):
        pass
    assert len(sleeps)==1 and sleeps[0]==pytest.approx(2.2)
    with pytest.raises(read.JupiterReadError,match="RATE_LIMITED"):
        with read.rate_reservation(path,clock=lambda:clock[0],sleep=sleep):
            raise read.JupiterReadError("RATE_LIMITED")
    with sqlite3.connect(path) as c:
        assert c.execute("SELECT next_at FROM rate").fetchone()[0]==pytest.approx(clock[0]+60)


def test_rate_clock_rollback_blocks_without_network(tmp_path):
    path=tmp_path/"rate.db"
    with read.rate_reservation(path,clock=lambda:1000):
        pass
    with pytest.raises(read.JupiterReadError,match="RATE_STATE_UNKNOWN"):
        with read.rate_reservation(path,clock=lambda:999):
            pytest.fail("reserved after clock rollback")


def test_rate_lock_covers_committed_reservation_and_network_work(tmp_path):
    import threading
    path=tmp_path/"rate.db";results=[]
    def contender():
        try:
            with read.rate_reservation(path,deadline=read.time.monotonic()+.15):
                results.append("unexpected_network_permission")
        except read.JupiterReadError as error:results.append(error.code)
    with read.rate_reservation(path):
        # COMMIT has occurred; the external lock must still exclude a new reader.
        thread=threading.Thread(target=contender);thread.start();thread.join(2)
        assert not thread.is_alive() and results==["RATE_WAIT"]
    assert path.with_name("rate.db.init.lock").exists()


@pytest.mark.parametrize("value",[float("nan"),float("inf"),float("-inf")])
def test_nonfinite_rate_clock_never_allows_io(tmp_path,value):
    with pytest.raises(read.JupiterReadError):
        with read.rate_reservation(tmp_path/"rate.db",clock=lambda:value):
            pytest.fail("nonfinite clock allowed read")


def test_expired_rate_deadline_never_acquires_or_reads(tmp_path):
    with pytest.raises(read.JupiterReadError,match="RATE_WAIT"):
        with read.rate_reservation(tmp_path/"rate.db",deadline=read.time.monotonic()-1):
            pytest.fail("expired budget allowed read")


@pytest.mark.parametrize("actual",[0,25,100,101,None,100.0,float("nan"),True])
def test_reader_preserves_slippage_cap_and_actual(monkeypatch,actual):
    from contextlib import nullcontext
    monkeypatch.setattr(read,"public_wallet",lambda *a:paper.SOL)
    monkeypatch.setattr(read,"rate_reservation",lambda *a,**kw:nullcontext())
    valid=type(actual) is int and 0<=actual<=100
    threshold=10000*(10000-actual)//10000 if valid else 10000
    value={"provider":"JUPITER_V2","status":"QUOTE_OBSERVED","diagnostic_only":True,
        "execution_eligible":False,"wallet_bound":False,"transaction_built":False,
        "input_mint":paper.SOL,"output_mint":TOKEN2022,"input_raw":"1000","output_raw":"10000",
        "minimum_output_raw":str(threshold),"requested_slippage_bps":100,"slippage_bps":actual,
        "route_steps":1,"price_impact_bps":"0.1"}
    monkeypatch.setattr(read.subprocess,"run",lambda *a,**kw:SimpleNamespace(returncode=0,stdout=json.dumps(value)))
    reader=read.JupiterReader(forward.paper_config(forward.load_contract()))
    if valid:
        result=reader.quote(paper.SOL,paper.SOL,TOKEN2022,1000)
        assert result["slippage"]==actual/100 and result["requested_slippage_bps"]==100
        value["minimum_output_raw"]=str(threshold-1)
        with pytest.raises(read.JupiterReadError,match="INVALID_THRESHOLD"):
            reader.quote(paper.SOL,paper.SOL,TOKEN2022,1000)
    else:
        with pytest.raises(read.JupiterReadError,match="QUOTE_BINDING_MISMATCH"):
            reader.quote(paper.SOL,paper.SOL,TOKEN2022,1000)


def test_local_wallet_verifier_does_not_import_network_or_legacy_secret_exporter():
    source=(paper.ROOT/"TOOLS/imc_local_wallet_verify.py").read_text()
    assert "CryptUnprotectData" in source and "LocalFree" in source
    assert "decrypt_wallet.ps1" not in source and "sign_message" not in source
    assert "import urllib" not in source and "import socket" not in source
    assert "ctypes.memset(output.data,0,output.size)" in source


def test_existing_paper_contract_remains_200():
    assert paper.load_config()["target_intents"]==200
    with pytest.raises(ValueError,match="frozen_200"):
        paper.validate_config(paper.load_config()|{"target_intents":100})


def test_actual_small_wallet_cannot_pass_capital_and_cost_checks():
    cfg=forward.paper_config(forward.load_contract())
    check=forward.capital_check(16_557_069,cfg)
    assert check["status"]=="UNKNOWN" and check["shortfall_lamports"]=="270784"
    assert cfg["network_fee_lamports_per_side"]==3_000_000
    assert not forward.entry_cost_pass(cfg,int(check["position_input_lamports"]),810_000)
    assert paper.load_config()["network_fee_lamports_per_side"]==100_000


def test_cost_guard_in_writer_transaction_uses_entry_fee_and_rolls_cash_back(book):
    value,clock=book
    trade=value.admit(snapshot(1,clock),1)
    buy=quote(value,clock,paper.SOL,trade["mint"],trade["input_lamports"],100000)
    sell=quote(value,clock,trade["mint"],paper.SOL,value.net_output(buy),49_000_000)
    cash=value.meta("cash")
    with pytest.raises(ValueError,match="roundtrip_cost_exceeds"):
        value.open(trade,buy,sell)
    assert value.meta("cash")==cash and value.active()["state"]=="ENTERING"
    assert value.conn.execute("SELECT count(*) FROM equity").fetchone()[0]==0


def test_runner_economic_rejections_never_create_api_kill_or_cooldown(book):
    value,clock=book
    def observed(wallet,input_token,output_token,amount):
        output=100000 if input_token==paper.SOL else 49_000_000
        return quote(value,clock,input_token,output_token,amount,output)
    runner=forward.JupiterForwardRunner(value,SimpleNamespace(quote=observed))
    initial=value.meta("cash")
    for index in range(1,7):
        value.admit(snapshot(index,clock),index)
        clock[0]+=value.cfg["execution_delay_seconds"]
        runner.tick()
        assert value.active() is None and value.meta("cash")==initial
    report=value.report()
    assert report["failed"]==6 and report["api_failures"]==0 and report["halt"] is None
    reasons=[json.loads(r[0]) for r in value.conn.execute("SELECT doc FROM intents")]
    assert all(t["failure"]=="jupiter_roundtrip_cost_exceeds_stop_budget" for t in reasons)


def runtime_fixture(monkeypatch):
    spec=importlib.util.spec_from_file_location("jupiter_runtime_fixture",paper.ROOT/"RUNTIME/jupiter_forward_runtime.py")
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    monkeypatch.setattr(module,"owner_off",lambda:True)
    monkeypatch.setattr(module,"wallet_verification",lambda:{"status":"PUBLIC_KEY_VERIFIED","wallet":paper.SOL})
    monkeypatch.setattr(module,"source_status",lambda cfg:{"status":"PASS"})
    monkeypatch.setattr(module,"write_json_projection",lambda *a:None)
    monkeypatch.setattr(module,"policy_hash",lambda *a:"controlled-fixture")
    return module


class ControlledReader:
    def __init__(self,balance=1_200_000_000):
        self.balance=balance;self.last_wallet_snapshot={};self.requests=[]
    def wallet(self):return paper.SOL,self.balance
    def quote(self,wallet,input_token,output_token,amount):
        self.requests.append((wallet,input_token,output_token,amount))
        return quote(None,[paper.now()],input_token,output_token,amount,amount)


def test_preflight_binds_requested_pair_sizing_haircut_and_consistent_fee_policy(monkeypatch):
    module=runtime_fixture(monkeypatch);reader=ControlledReader()
    result=module.preflight(forward.load_contract(),reader=reader)
    assert result["quote_forward_startable"] is True and result["chain_forward_startable"] is False
    assert reader.requests[0][3]==60_000_000 and reader.requests[1][3]==59_400_000
    assert result["checks"]["roundtrip_cost"]["invested_with_entry_fee"]=="63000000"


def test_preflight_small_wallet_blocks_without_importing_any_trades(monkeypatch):
    module=runtime_fixture(monkeypatch)
    result=module.preflight(forward.load_contract(),reader=ControlledReader(16_557_069))
    assert result["quote_forward_startable"] is False
    assert result["checks"]["capital"]["status"]=="UNKNOWN" and result["checks"]["roundtrip_cost"]["status"]=="UNKNOWN"


def test_preflight_cannot_self_validate_tampered_quote_fields(monkeypatch):
    module=runtime_fixture(monkeypatch);reader=ControlledReader();original=reader.quote
    reader.quote=lambda *args:original(*args)|{"input_token":"wrong"}
    result=module.preflight(forward.load_contract(),reader=reader)
    assert result["quote_forward_startable"] is False and "two_way_quote" not in result["checks"]


def test_run_does_not_bypass_failed_preflight(monkeypatch,tmp_path):
    module=runtime_fixture(monkeypatch)
    monkeypatch.setattr(module,"preflight",lambda cfg:{"quote_forward_startable":False})
    monkeypatch.setattr(module,"open_book",lambda *a:pytest.fail("cohort opened despite failed preflight"))
    with pytest.raises(ValueError,match="preflight_blocked"):
        module.run(forward.load_contract(),tmp_path/"stop",1)


TOKEN2022 = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"
LEGACY_TOKEN = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"


def inventory(program=TOKEN2022):
    return {"context":{"slot":100},"value":[{"pubkey":str(Pubkey.new_unique()),
        "account":{"owner":program,"executable":False,"data":{"parsed":{"type":"account",
        "info":{"owner":paper.SOL,"mint":paper.SOL,"state":"initialized",
        "extensions":[{"extension":"immutableOwner"}],"tokenAmount":{"amount":"123","decimals":9}}}}}}]}


def test_jupiter_inventory_immutable_owner_read_preserves_original_and_global_parser():
    value=inventory()
    original=json.dumps(value,sort_keys=True)
    positions,decimals,slot,_=read.JupiterSolanaEvidence._token_inventory(value,paper.SOL,TOKEN2022)
    assert positions=={paper.SOL:"123"} and decimals=={paper.SOL:9} and slot==100
    assert json.dumps(value,sort_keys=True)==original
    with pytest.raises(ValueError,match="unsupported_token_extensions"):
        read.SolanaEvidence._token_inventory(value,paper.SOL,TOKEN2022)


@pytest.mark.parametrize("extensions",[[{"extension":"immutableOwner","extra":True}],
    [{"extension":"immutableOwner"}]*2,[{"extension":"transferFeeAmount"}],
    [{"extension":"immutableOwner"},{"extension":"transferHookAccount"}]])
def test_jupiter_inventory_unknown_extensions_fail_closed(extensions):
    value=inventory()
    value["value"][0]["account"]["data"]["parsed"]["info"]["extensions"]=extensions
    with pytest.raises(ValueError,match="unsupported_token_extensions"):
        read.JupiterSolanaEvidence._token_inventory(value,paper.SOL,TOKEN2022)


@pytest.mark.parametrize("program",[TOKEN2022,LEGACY_TOKEN])
@pytest.mark.parametrize("state",["frozen","uninitialized",None])
def test_jupiter_inventory_requires_initialized_for_all_accounts(program,state):
    value=inventory(program)
    info=value["value"][0]["account"]["data"]["parsed"]["info"]
    info.pop("extensions");info["state"]=state
    with pytest.raises(ValueError,match="not_initialized"):
        read.JupiterSolanaEvidence._token_inventory(value,paper.SOL,program)


@pytest.mark.parametrize("change",["owner","program","mint","executable","legacy_extension"])
def test_jupiter_inventory_keeps_identity_checks(change):
    program=LEGACY_TOKEN if change=="legacy_extension" else TOKEN2022
    value=inventory(program);account=value["value"][0]["account"]
    info=account["data"]["parsed"]["info"]
    if change=="owner":info["owner"]="wrong"
    if change=="program":account["owner"]="wrong"
    if change=="mint":info["mint"]="not_a_pubkey"
    if change=="executable":account["executable"]=True
    with pytest.raises(ValueError):
        read.JupiterSolanaEvidence._token_inventory(value,paper.SOL,program)
