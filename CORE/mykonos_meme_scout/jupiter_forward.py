"""Prospective 100 CLOSED Jupiter quote-paper cohort, never chain fills.

Reuses the frozen causal policy and durable accounting from paper_forward,
without changing any existing GMGN cohort's source or policy hash.
"""
from __future__ import annotations

from contextlib import closing
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import time

from . import paper_forward as paper
from .jupiter_read import JupiterReader, SOURCE as QUOTE_SOURCE, unique

ROOT = paper.ROOT
CONFIG = ROOT / "config/imc-jupiter-forward.json"
EVIDENCE = "JUPITER_LIVE_MARKET_QUOTE_PAPER_NOT_CHAIN_FILLS"


class JupiterEconomicRejection(ValueError):
    """A verified observation can still fail the entry's economic budget."""


def load_contract(path=CONFIG):
    cfg = json.loads(Path(path).read_text(encoding="utf-8-sig"), object_pairs_hook=unique)
    return validate_contract(cfg)


def validate_contract(cfg):
    required = {"schema_version": 1, "evidence_class": EVIDENCE, "provider": "JUPITER_V2",
        "target_closed": 100, "max_attempts": 200, "database": "RUNTIME/jupiter-forward.db",
        "status": "RUNTIME/jupiter-forward-status.json", "source_database": "CORE/data/imc-main.db",
        "wallet_public_file": "RUNTIME/EXECUTOR_GATEWAY/wallet_public.json",
        "poll_seconds": 2.2, "feed_interval_seconds": 3, "source_max_age_seconds": 30,
        "max_price_impact_bps": 150, "execution_authority_pct": 0,
        "network_fee_lamports_per_side": 3000000,
        "live_trading_enabled": False, "broadcast_enabled": False, "signing": False, "wallet_access": False}
    if set(cfg) != set(required) or any(type(cfg[k]) is not type(v) or cfg[k] != v for k,v in required.items()):
        raise ValueError("invalid_jupiter_forward_contract")
    return cfg


def paper_config(contract):
    cfg=paper.load_config()
    cfg["network_fee_lamports_per_side"]=validate_contract(contract)["network_fee_lamports_per_side"]
    return paper.validate_config(cfg)


def capital_check(balance,cfg):
    amount=int(Decimal(balance)*Decimal(cfg["position_fraction"]))
    required=amount+2*cfg["network_fee_lamports_per_side"]+cfg["reserve_lamports"]
    return {"status":"PASS" if amount>0 and balance>=required else "UNKNOWN",
        "wallet_balance_lamports":str(balance),"position_input_lamports":str(amount),
        "required_lamports_for_this_size":str(required),"shortfall_lamports":str(max(0,required-balance)),
        "fee_allowance_per_side":str(cfg["network_fee_lamports_per_side"]),
        "reserve_lamports":str(cfg["reserve_lamports"]),"cost_evidence":"MODEL_ALLOWANCE_NOT_CHAIN_TOTAL"}


def entry_cost_pass(cfg,amount,sell_net):
    fee=cfg["network_fee_lamports_per_side"]
    return Decimal(100)*(sell_net-fee)>(Decimal(100)-Decimal(str(cfg["hard_stop_pct"])))*(amount+fee)


def policy_hash(contract):
    files = [Path(__file__), Path(__file__).with_name("jupiter_read.py"),
             Path(__file__).with_name("jupiter_prepare.py"), Path(__file__).with_name("jupiter_simulation.py"),
             Path(__file__).with_name("jupiter_instructions.py"), ROOT / "TOOLS/imc_jupiter_prepare_worker.py",
             ROOT / "TOOLS/imc_quote_diagnostic.py", CONFIG, paper.CONFIG,
             ROOT / "RUNTIME/jupiter_forward_runtime.py", ROOT / "TOOLS/imc_local_wallet_verify.py",
             ROOT / "LAUNCHERS/RUN_JUPITER_FORWARD.ps1", ROOT / "LAUNCHERS/JUPITER_FORWARD.ps1",
             ROOT / "LAUNCHERS/IMC_PROCESS_COMMON.ps1", ROOT / "TOOLS/IMC_RUNTIME_JOB.ps1",
             ROOT / "TOOLS/IMC_MAINTENANCE.ps1", ROOT / "RUNTIME/EXECUTOR_GATEWAY/wallet_public.json",
             *[Path(__file__).with_name(name+".py") for name in ("cli","engine","store","shadow_feed")],
             *[ROOT/"RUNTIME/EXECUTOR_GATEWAY/GMGN_BRIDGE"/(name+".py") for name in
               ("execution_evidence","execution_jupiter_evidence","rpc_read_worker","execution_state","execution_lock","execution_machine")],
             Path(__file__).with_name("execution_config.py")]
    document = {"paper_code_hash": paper.code_hash(), "contract": contract,
        "files": {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in files}}
    return hashlib.sha256(paper.encoded(document).encode()).hexdigest()


class JupiterForwardBook(paper.PaperBook):
    def __init__(self, path, contract, wallet, cash, checkpoint, *, clock=paper.now, frozen_hash=None):
        self.contract = dict(validate_contract(contract))
        self.contract_hash = frozen_hash or policy_hash(contract)
        super().__init__(path, paper_config(contract), wallet, cash, checkpoint, clock=clock,
                         policy_hash=self.contract_hash)
        try:
            with self.transaction():
                if self.meta("jupiter_contract") is None:
                    if self.intents() or self.meta("provider") is not None:
                        raise ValueError("unbound_jupiter_cohort")
                    self.set_meta("jupiter_contract", contract)
                    self.set_meta("provider", "JUPITER_V2")
                    self.event("jupiter_contract_frozen", {"contract": contract,
                               "policy_hash": self.contract_hash, "evidence_class": EVIDENCE})
                elif self.meta("jupiter_contract") != contract or self.meta("provider") != "JUPITER_V2":
                    raise ValueError("frozen_jupiter_contract_changed")
        except BaseException:
            self.conn.close()
            raise

    def validate_observation(self, quote, input_token, output_token, amount):
        if (quote.get("source") != QUOTE_SOURCE or quote.get("provider") != "JUPITER_V2" or
                quote.get("is_fill") is not False or quote.get("execution_eligible") is not False or
                quote.get("transaction_built") is not False):
            raise ValueError("unverified_jupiter_paper_observation")
        paper.validate_quote(quote, input_token, output_token, amount,
                             quote["requested_at"], quote["received_at"], self.cfg)
        if not 0 <= self.clock()-quote["requested_at"] <= self.cfg["quote_max_latency_seconds"]:
            raise ValueError("stale_jupiter_paper_observation")
        try:
            impact = Decimal(str(quote.get("price_impact_bps")))
        except InvalidOperation:
            raise ValueError("jupiter_impact_unknown_or_exceeded") from None
        if not impact.is_finite() or abs(impact) > self.contract["max_price_impact_bps"]:
            raise ValueError("jupiter_impact_unknown_or_exceeded")
        if type(quote.get("route_steps")) is not int or not 1 <= quote["route_steps"] <= 64:
            raise ValueError("jupiter_route_unknown")

    def _admit(self, snapshot, snapshot_id, selection=None):
        trades = self.intents()
        if self.meta("halt") or self.active() or sum(t["state"] == "CLOSED" for t in trades) >= 100:
            return None
        if len(trades) >= 200:
            self.set_meta("halt", "attempt_limit_before_100_closed")
            return None
        if any(t["mint"] == snapshot["address"] for t in trades):
            return None
        amount = int(Decimal(self.meta("initial_cash"))*Decimal(self.cfg["position_fraction"]))
        fee = self.cfg["network_fee_lamports_per_side"]
        if amount <= 0 or self.meta("cash") < amount+fee*2+self.cfg["reserve_lamports"]:
            self.set_meta("halt", "capital_reserve")
            return None
        candidate = hashlib.sha256(paper.encoded([self.contract["source_database"],
            snapshot["address"], snapshot_id, self.policy]).encode()).hexdigest()
        trade = {"candidate_id": candidate, "version": 1, "mint": snapshot["address"],
            "snapshot_id": snapshot_id, "signal_at": paper.epoch(snapshot["captured_at"]),
            "admitted_at": self.clock(), "due_at": self.clock()+self.cfg["execution_delay_seconds"],
            "state": "ENTERING", "input_lamports": amount, "reference_price": snapshot["price_usd"],
            "features": snapshot, "selection_evidence": selection, "last_mark_at": None,
            "best_mark": 0, "exit_reason": None, "net_pnl_lamports": None,
            "shadow_pnl_lamports": None, "evidence_class": EVIDENCE}
        self.conn.execute("INSERT INTO intents(candidate_id,doc) VALUES(?,?)", (candidate,paper.encoded(trade)))
        self.event("admitted_before_quote", trade)
        return trade

    def _mark(self,trade,quote,*,initial=False):
        # Base open invokes this inside its writer transaction, after validating
        # both observations and their original deadlines. Failure rolls cash back.
        if initial:
            if self.meta("cash")-self.cfg["network_fee_lamports_per_side"]<self.cfg["reserve_lamports"]:
                raise JupiterEconomicRejection("jupiter_capital_reserve")
            if not entry_cost_pass(self.cfg,trade["input_lamports"],self.net_output(quote)):
                raise JupiterEconomicRejection("jupiter_roundtrip_cost_exceeds_stop_budget")
        return super()._mark(trade,quote,initial=initial)

    def report(self):
        report = super().report()
        if any(t.get("evidence_class") != EVIDENCE for t in self.intents()):
            raise ValueError("mixed_jupiter_cohort_evidence")
        report.update(version="imc-jupiter-forward-1", evidence_class=EVIDENCE,
            provider="JUPITER_V2", target_closed=100, max_attempts=200,
            failed=report["states"].get("FAILED",0), onchain_closed=0, live_forward_ready=False,
            cohort_complete=report["closed"] == 100 and self.active() is None,
            cost_model="Jupiter minimum output; fee haircut plus 3000000 lamports per side model allowance. Simulated, not observed chain total including rent.")
        goals = report["goals"]
        report["goals_passed"] = bool(report["cohort_complete"] and report["valuation_known"] and
            report["evidence_complete"] and report["max_drawdown_pct"] is not None and
            report["win_rate_pct"] is not None and report["win_rate_pct"] >= goals["win_rate_pct"] and
            report["profit_factor"] is not None and report["profit_factor"] >= goals["profit_factor"] and
            report["max_drawdown_pct"] <= goals["max_drawdown_pct"] and
            report["top3_net_profit_share_pct"] is not None and
            report["top3_net_profit_share_pct"] <= goals["top3_net_profit_share_pct"])
        report["rejection_counts"] = self.meta("rejection_counts") or {}
        return report


class JupiterForwardRunner(paper.ForwardRunner):
    def tick(self):
        if self.book.active() is None and (self.book.report()["closed"] >= 100 or len(self.book.intents()) >= 200):
            if self.book.report()["closed"] < 100 and not self.book.meta("halt"):
                with self.book.transaction():
                    self.book.set_meta("halt", "attempt_limit_before_100_closed")
            return
        book=self.book
        trade=book.active()
        if trade is None or trade["state"]!="ENTERING":
            return super().tick()
        if book.clock()<trade["due_at"]:
            return
        try:
            if book.clock()-trade["signal_at"]>book.cfg["signal_max_age_seconds"]:
                return book.fail(trade,"stale_before_entry_quote")
            wallet=book.meta("wallet")
            buy=self.reader.quote(wallet,paper.SOL,trade["mint"],trade["input_lamports"])
            sell=self.reader.quote(wallet,trade["mint"],paper.SOL,book.net_output(buy))
            if book.clock()-trade["signal_at"]>book.cfg["signal_max_age_seconds"]:
                return book.fail(trade,"stale_after_roundtrip_probe")
            # The authoritative cost check runs inside open's writer transaction.
            # Economic rejection is not an HTTP failure and receives no cooldown.
            return book.open(trade,buy,sell)
        except JupiterEconomicRejection as error:
            return book.fail(trade,str(error))
        except (RuntimeError,ValueError,OSError,subprocess.SubprocessError):
            book.fail(trade,"entry_quote_unavailable",api_failure=True)
            raise


def source_status(contract, *, clock=paper.now):
    path = ROOT / contract["source_database"]
    with closing(sqlite3.connect(path.resolve().as_uri()+"?mode=ro", uri=True, timeout=2)) as c:
        c.execute("PRAGMA query_only=ON")
        deadline = time.monotonic()+2
        c.set_progress_handler(lambda: int(time.monotonic() >= deadline),1000)
        row = c.execute("SELECT id,captured_at FROM snapshots ORDER BY id DESC LIMIT 1").fetchone()
    age = clock()-paper.epoch(row[1]) if row else None
    return {"status": "PASS" if age is not None and 0 <= age <= contract["source_max_age_seconds"] else "UNKNOWN",
            "checkpoint": row[0] if row else 0, "captured_at": row[1] if row else None,
            "age_seconds": age, "source": contract["source_database"]+":snapshots"}


def open_book(contract, reader=None):
    reader = reader or JupiterReader(paper_config(contract))
    source = source_status(contract)
    if source["status"] != "PASS":
        raise ValueError("forward_source_not_fresh")
    wallet, balance = reader.wallet()
    return JupiterForwardBook(ROOT/contract["database"], contract, wallet, balance, source["checkpoint"]), reader


def read_report(contract):
    path = ROOT/contract["database"]
    with closing(sqlite3.connect(path.resolve().as_uri()+"?mode=ro", uri=True, timeout=2)) as c:
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA query_only=ON")
        book = JupiterForwardBook.__new__(JupiterForwardBook)
        book.path, book.conn, book.clock, book.contract = path,c,paper.now,contract
        book.cfg = paper.validate_config(book.meta("policy")["config"])
        if book.meta("jupiter_contract") != contract:
            raise ValueError("frozen_jupiter_contract_changed")
        return book.report()
