"""Credential-free Jupiter observations and the existing public local wallet.

Only quote GETs and finalized RPC reads. Nothing in this module signs or sends.
All Jupiter quote consumers share a durable rate reservation across restarts.
"""
from __future__ import annotations

from contextlib import contextmanager
import copy
import hashlib
import json
import math
import re
from pathlib import Path
import sqlite3
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
QUOTE_TOOL = ROOT / "TOOLS/imc_quote_diagnostic.py"
PUBLIC_WALLET = ROOT / "RUNTIME/EXECUTOR_GATEWAY/wallet_public.json"
RATE_DB = ROOT / "RUNTIME/jupiter-read.db"
SOURCE = "JUPITER_V2_ORDER_QUOTE_OBSERVATION"
BRIDGE = ROOT / "RUNTIME/EXECUTOR_GATEWAY/GMGN_BRIDGE"
if str(BRIDGE) not in sys.path:
    sys.path.insert(0, str(BRIDGE))
from execution_evidence import SolanaEvidence
from execution_state import StateError
from execution_lock import initialization_lock


class JupiterSolanaEvidence(SolanaEvidence):
    """Inventory READ only: ImmutableOwner is not transfer permission or sellability."""
    snapshot_operation='wallet_snapshot_batch'
    @staticmethod
    def _token_inventory(result, wallet, program):
        checked = copy.deepcopy(result)
        if not isinstance(checked.get("value"), list):
            raise StateError("rpc_token_inventory_list_required")
        from solders.pubkey import Pubkey
        for account in checked["value"]:
            info = account["account"]["data"]["parsed"]["info"]
            if info.get("state") != "initialized":
                raise StateError("jupiter_token_account_not_initialized")
            try:
                mint = info["mint"]
                if str(Pubkey.from_string(mint)) != mint:
                    raise ValueError()
            except (ValueError, TypeError):
                raise StateError("rpc_account_mint_unknown") from None
            extensions = info.get("extensions")
            if extensions:
                if (program != "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb" or
                        extensions != [{"extension": "immutableOwner"}]):
                    raise StateError("unsupported_token_extensions")
                del info["extensions"]
        return SolanaEvidence._token_inventory(checked, wallet, program)
KNOWN_ERRORS = {"INVALID_MINT", "INVALID_AMOUNT", "INVALID_SLIPPAGE", "IDENTICAL_MINTS",
    "INVALID_RESPONSE", "QUOTE_BINDING_MISMATCH", "PROVIDER_ERROR", "UNEXPECTED_TRANSACTION",
    "INVALID_THRESHOLD", "MISSING_ROUTE", "INVALID_ROUTE", "DISCONNECTED_ROUTE", "INVALID_IMPACT",
    "AUTH_REQUIRED", "ACCESS_DENIED", "RATE_LIMITED", "REDIRECT_BLOCKED", "HTTP_ERROR",
    "INVALID_CONTENT_TYPE", "ENCODING_BLOCKED", "TIMEOUT", "RESPONSE_TOO_LARGE", "INVALID_JSON",
    "AMBIGUOUS_RESPONSE", "WORKER_FAILED", "TRANSPORT_ERROR", "INVALID_REQUEST"}


class JupiterReadError(ValueError):
    def __init__(self, code):
        self.code = code if code in KNOWN_ERRORS | {"RATE_WAIT", "RATE_STATE_UNKNOWN", "WALLET_UNKNOWN"} else "TRANSPORT_ERROR"
        super().__init__("jupiter_read:" + self.code)


def unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_field")
        result[key] = value
    return result


def public_wallet(path=PUBLIC_WALLET):
    with Path(path).open("rb") as stream:
        raw = stream.read(4097)
    if len(raw) > 4096:
        raise JupiterReadError("WALLET_UNKNOWN")
    value = json.loads(raw.decode("utf-8-sig"), object_pairs_hook=unique)
    from solders.pubkey import Pubkey
    address = value.get("address")
    if value.get("protection") != "Windows DPAPI CurrentUser" or str(Pubkey.from_string(address)) != address:
        raise JupiterReadError("WALLET_UNKNOWN")
    return address


@contextmanager
def rate_reservation(path=RATE_DB, *, clock=time.time, sleep=time.sleep, deadline=None):
    path=Path(path).resolve()
    path.parent.mkdir(parents=True,exist_ok=True)
    deadline=deadline if deadline is not None else time.monotonic()+12
    if not math.isfinite(deadline) or deadline<=time.monotonic():
        raise JupiterReadError("RATE_WAIT")
    try:
        # Existing durable lock inode, held across both SQLite commits and I/O.
        with initialization_lock(path,deadline-time.monotonic()):
            if time.monotonic()>=deadline:raise JupiterReadError("RATE_WAIT")
            with _rate_journal(path,clock=clock,sleep=sleep,deadline=deadline) as connection:
                yield connection
    except TimeoutError:
        raise JupiterReadError("RATE_WAIT") from None


@contextmanager
def _rate_journal(path, *, clock, sleep, deadline):
    """Serialize all keyless quotes; reserve before I/O, including failed reads."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=max(0,min(2,deadline-time.monotonic())), isolation_level=None)
    try:
        connection.execute("PRAGMA synchronous=FULL")
        connection.execute("CREATE TABLE IF NOT EXISTS rate(id INTEGER PRIMARY KEY CHECK(id=1), next_at REAL NOT NULL, last_at REAL NOT NULL)")
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute("SELECT next_at,last_at FROM rate WHERE id=1").fetchone()
        current = clock()
        if (type(current) not in (int,float) or not math.isfinite(current) or
            row and any(type(v) not in (int,float) or not math.isfinite(v) for v in row)):
            raise JupiterReadError("RATE_STATE_UNKNOWN")
        if row and (current < row[1] or row[0] - current > 61):
            raise JupiterReadError("RATE_STATE_UNKNOWN")
        delay = max(0, row[0]-current) if row else 0
        if deadline is not None and time.monotonic()+delay+1 >= deadline:
            raise JupiterReadError("RATE_WAIT")
        if delay:
            sleep(delay)
        current = clock()
        if type(current) not in (int,float) or not math.isfinite(current) or row and current < row[0]:
            raise JupiterReadError("RATE_WAIT")
        if time.monotonic()>=deadline:raise JupiterReadError("RATE_WAIT")
        connection.execute("INSERT INTO rate VALUES(1,?,?) ON CONFLICT(id) DO UPDATE SET next_at=excluded.next_at,last_at=excluded.last_at",
                           (current+2.2, current))
        connection.execute("COMMIT")
        # Keep a separate transaction for mutual exclusion during the read.
        # The already committed timestamp survives parent death.
        connection.execute("BEGIN IMMEDIATE")
        try:
            yield connection
            observed=clock()
            if type(observed) not in (int,float) or not math.isfinite(observed) or observed<current:
                raise JupiterReadError("RATE_STATE_UNKNOWN")
        except JupiterReadError as error:
            if error.code in {"RATE_LIMITED", "AUTH_REQUIRED", "ACCESS_DENIED"}:
                observed=clock()
                if type(observed) not in (int,float) or not math.isfinite(observed):observed=current
                connection.execute("UPDATE rate SET next_at=? WHERE id=1", (max(current,observed)+60,))
            raise
        finally:
            connection.execute("COMMIT")
    finally:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        connection.close()


class JupiterReader:
    def __init__(self, cfg, *, rate_path=RATE_DB, wallet_path=PUBLIC_WALLET, rpc=None):
        self.cfg, self.rate_path, self.wallet_path, self.rpc = cfg, Path(rate_path), Path(wallet_path), rpc
        self.last_wallet_snapshot = None

    def wallet(self):
        address = public_wallet(self.wallet_path)
        if self.rpc is None:
            self.rpc = JupiterSolanaEvidence({"rpc_url": "https://api.mainnet-beta.solana.com",
                "rpc_timeout_seconds": 5, "reconciliation_max_age_seconds": 15})
        started = time.time()
        positions, decimals, balance, slot = self.rpc.wallet_snapshot(address)
        if type(slot) is not int or slot < 1 or int(balance) <= self.cfg["reserve_lamports"]:
            raise JupiterReadError("WALLET_UNKNOWN")
        self.last_wallet_snapshot = {"wallet": address, "balance_lamports": str(balance),
            "positions": positions, "decimals": decimals, "slot": slot, "commitment": "finalized",
            "observed_at": started, "source": "LOCAL_PUBLIC_WALLET_AND_FINALIZED_RPC"}
        return address, int(balance)

    def quote(self, wallet, input_token, output_token, amount):
        if wallet != public_wallet(self.wallet_path) or type(amount) is not int or amount <= 0:
            raise JupiterReadError("WALLET_UNKNOWN")
        outer = time.monotonic()
        deadline = outer + self.cfg["quote_max_latency_seconds"]
        with rate_reservation(self.rate_path, deadline=deadline):
            network_start = time.monotonic()
            requested = time.time()
            command = [str(Path(sys._base_executable).resolve(strict=True)), str(QUOTE_TOOL),
                "--worker",
                "--input-mint", input_token, "--output-mint", output_token,
                "--amount", str(amount), "--slippage-bps", "100"]
            try:
                remaining=deadline-time.monotonic()
                if remaining<=0:raise JupiterReadError("TIMEOUT")
                result = subprocess.run(command, stdin=subprocess.DEVNULL, capture_output=True, text=True,
                    timeout=remaining,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            except subprocess.TimeoutExpired:
                raise JupiterReadError("TIMEOUT") from None
            received = time.time()
            if (len(result.stdout) > 8192 or result.returncode not in (0, 1) or
                    time.monotonic() >= deadline or received < requested):
                raise JupiterReadError("TRANSPORT_ERROR")
            value = json.loads(result.stdout, object_pairs_hook=unique)
            if result.returncode:
                raise JupiterReadError(value.get("code", "TRANSPORT_ERROR"))
            required = {"provider": "JUPITER_V2", "status": "QUOTE_OBSERVED", "diagnostic_only": True,
                        "execution_eligible": False, "wallet_bound": False, "transaction_built": False}
            if (any(type(value.get(k)) is not type(v) or value[k] != v for k,v in required.items()) or
                    value.get("input_mint") != input_token or value.get("output_mint") != output_token or
                    value.get("input_raw") != str(amount) or type(value.get("requested_slippage_bps")) is not int or
                    value["requested_slippage_bps"] != 100 or type(value.get("slippage_bps")) is not int or
                    not 0<=value["slippage_bps"]<=100):
                raise JupiterReadError("QUOTE_BINDING_MISMATCH")
            if any(not isinstance(value.get(k),str) or not re.fullmatch(r"[1-9][0-9]{0,19}",value[k]) or
                   int(value[k])>=2**64 for k in ("output_raw","minimum_output_raw")):
                raise JupiterReadError("INVALID_THRESHOLD")
            output,minimum=int(value["output_raw"]),int(value["minimum_output_raw"])
            if not output*(10000-value["slippage_bps"])//10000<=minimum<=output:
                raise JupiterReadError("INVALID_THRESHOLD")
            if abs((received-requested)-(time.monotonic()-network_start)) > .5:
                raise JupiterReadError("TRANSPORT_ERROR")
            return {"input_token": input_token, "output_token": output_token,
                "input_amount": str(amount), "output_amount": value["output_raw"],
                "min_output_amount": value["minimum_output_raw"], "slippage": value["slippage_bps"]/100,
                "requested_slippage_bps": 100,
                "requested_at": requested, "received_at": received, "source": SOURCE, "is_fill": False,
                "provider": "JUPITER_V2", "route_steps": value["route_steps"],
                "price_impact_bps": value["price_impact_bps"], "liquidity_usd": None,
                "total_execution_fee_lamports": None, "transaction_built": False,
                "execution_eligible": False,
                "transport_sha256": hashlib.sha256(QUOTE_TOOL.read_bytes()).hexdigest()}
