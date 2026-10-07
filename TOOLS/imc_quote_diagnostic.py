"""Isolated keyless Jupiter quote diagnosis. Never feeds IMC execution evidence.

One pinned GET without taker, credentials, transaction building or retries.
The CLI bounds the entire worker lifetime, including DNS and response reads.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import http.client
import json
from pathlib import Path
import re
import subprocess
import sys
import time
from urllib.parse import urlencode

HOST = "api.jup.ag"
ENDPOINT = "/swap/v2/order"
SOL = "So11111111111111111111111111111111111111112"
USDC = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
MAX_BYTES = 262144
TIMEOUT = 12
ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


class DiagnosticError(ValueError):
    """Closed error codes only; never include raw provider text."""


def mint(value):
    if not isinstance(value, str) or not 32 <= len(value) <= 44:
        raise DiagnosticError("INVALID_MINT")
    number = 0
    for char in value:
        if char not in ALPHABET:
            raise DiagnosticError("INVALID_MINT")
        number = number * 58 + ALPHABET.index(char)
    length = (number.bit_length() + 7) // 8 + len(value) - len(value.lstrip("1"))
    if length != 32:
        raise DiagnosticError("INVALID_MINT")
    return value


def amount(value):
    if not isinstance(value, str) or not re.fullmatch(r"[1-9][0-9]{0,19}", value):
        raise DiagnosticError("INVALID_AMOUNT")
    if int(value) > 18446744073709551615:
        raise DiagnosticError("INVALID_AMOUNT")
    return value


def parameters(input_mint, output_mint, raw_amount, slippage_bps):
    if type(slippage_bps) is not int or not 0 <= slippage_bps <= 150:
        raise DiagnosticError("INVALID_SLIPPAGE")
    if mint(input_mint) == mint(output_mint):
        raise DiagnosticError("IDENTICAL_MINTS")
    return {"inputMint": input_mint, "outputMint": output_mint,
            "amount": amount(raw_amount), "swapMode": "ExactIn",
            "slippageBps": slippage_bps}


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise DiagnosticError("AMBIGUOUS_RESPONSE")
        result[key] = value
    return result


def normalize(body, request, requested_at, latency):
    if not isinstance(body, dict):
        raise DiagnosticError("INVALID_RESPONSE")
    error_code = body.get("errorCode")
    if (body.get("error") not in (None, "") or body.get("errorMessage") not in (None, "") or
            (error_code is not None and (type(error_code) is not int or error_code != 0))):
        raise DiagnosticError("PROVIDER_ERROR")
    if body.get("transaction") not in (None, "") or body.get("taker"):
        raise DiagnosticError("UNEXPECTED_TRANSACTION")
    for key in ("inputMint", "outputMint"):
        if body.get(key) != request[key]:
            raise DiagnosticError("QUOTE_BINDING_MISMATCH")
    if (body.get("inAmount") != request["amount"] or body.get("swapMode") != "ExactIn" or
            type(body.get("slippageBps")) is not int or
            not 0 <= body["slippageBps"] <= request["slippageBps"]):
        raise DiagnosticError("QUOTE_BINDING_MISMATCH")
    output = amount(body.get("outAmount"))
    minimum = amount(body.get("otherAmountThreshold"))
    if (int(minimum) > int(output) or
            int(minimum) < int(output) * (10000-body["slippageBps"]) // 10000):
        raise DiagnosticError("INVALID_THRESHOLD")
    route = body.get("routePlan")
    if not isinstance(route, list) or not 1 <= len(route) <= 64:
        raise DiagnosticError("MISSING_ROUTE")
    edges = []
    for step in route:
        info = step.get("swapInfo") if isinstance(step, dict) else None
        if not isinstance(info, dict):
            raise DiagnosticError("INVALID_ROUTE")
        try:
            edges.append((mint(info.get("inputMint")), mint(info.get("outputMint"))))
            mint(info.get("ammKey"))
            amount(info.get("inAmount"))
            amount(info.get("outAmount"))
        except DiagnosticError:
            raise DiagnosticError("INVALID_ROUTE") from None
    forward, backward = {request["inputMint"]}, {request["outputMint"]}
    for _ in edges:
        forward.update(target for source, target in edges if source in forward)
        backward.update(source for source, target in edges if target in backward)
    if (request["outputMint"] not in forward or
            any(source not in forward or target not in backward for source, target in edges)):
        raise DiagnosticError("DISCONNECTED_ROUTE")
    impact = body.get("priceImpact")
    impact_bps = None
    if impact is not None:
        if isinstance(impact, bool) or not isinstance(impact, (int, float, Decimal)):
            raise DiagnosticError("INVALID_IMPACT")
        try:
            value = Decimal(str(impact))
            if not value.is_finite() or abs(value) > 100:
                raise DiagnosticError("INVALID_IMPACT")
            # Jupiter v2 priceImpact is percentage points, NOT the deprecated ratio.
            impact_bps = str(value * 100)
        except InvalidOperation:
            raise DiagnosticError("INVALID_IMPACT") from None
    return {"schema_version": 1, "provider": "JUPITER_V2", "status": "QUOTE_OBSERVED",
            "diagnostic_only": True, "execution_eligible": False,
            "endpoint": "https://" + HOST + ENDPOINT, "auth": "keyless",
            "requested_at": requested_at, "latency_seconds": round(latency, 3),
            "input_mint": request["inputMint"], "output_mint": request["outputMint"],
            "input_raw": request["amount"], "output_raw": output,
            "minimum_output_raw": minimum, "slippage_bps": body["slippageBps"],
            "requested_slippage_bps": request["slippageBps"],
            "route_steps": len(route), "price_impact_bps": impact_bps,
            "liquidity_usd": None, "total_execution_fee_lamports": None,
            "wallet_bound": False, "transaction_built": False,
            "market_data_readiness": "UNKNOWN"}


def fetch(request):
    """Single request; CLI worker adds a hard wall-clock timeout around this."""
    if not isinstance(request, dict) or set(request) != {
            "inputMint", "outputMint", "amount", "swapMode", "slippageBps"}:
        raise DiagnosticError("INVALID_REQUEST")
    checked = parameters(request["inputMint"], request["outputMint"],
                         request["amount"], request["slippageBps"])
    if request != checked:
        raise DiagnosticError("INVALID_REQUEST")
    request = checked
    started = time.monotonic()
    requested_at = datetime.now(timezone.utc).isoformat()
    connection = http.client.HTTPSConnection(HOST, timeout=TIMEOUT-1)
    try:
        connection.request("GET", ENDPOINT + "?" + urlencode(request),
                           headers={"Accept": "application/json", "User-Agent": "IMC-Quote-Diagnostic/1"})
        response = connection.getresponse()
        if response.status != 200:
            status = response.status
            code = {401: "AUTH_REQUIRED", 403: "ACCESS_DENIED", 429: "RATE_LIMITED"}.get(status)
            if code is None:
                code = "REDIRECT_BLOCKED" if 300 <= status < 400 else "HTTP_ERROR"
            raise DiagnosticError(code)
        if response.getheader("Content-Type", "").split(";", 1)[0].strip().lower() != "application/json":
            raise DiagnosticError("INVALID_CONTENT_TYPE")
        if response.getheader("Content-Encoding", "identity").lower() != "identity":
            raise DiagnosticError("ENCODING_BLOCKED")
        data = bytearray()
        while True:
            remaining = TIMEOUT - 1 - (time.monotonic() - started)
            if remaining <= 0:
                raise DiagnosticError("TIMEOUT")
            if connection.sock is not None:
                connection.sock.settimeout(remaining)
            chunk = response.read1(min(16384, MAX_BYTES + 1 - len(data)))
            data.extend(chunk)
            if len(data) > MAX_BYTES:
                raise DiagnosticError("RESPONSE_TOO_LARGE")
            if not chunk:
                break
        try:
            body = json.loads(data.decode("utf-8"), object_pairs_hook=unique_object,
                              parse_float=Decimal)
        except (UnicodeError, json.JSONDecodeError, RecursionError):
            raise DiagnosticError("INVALID_JSON") from None
        return normalize(body, request, requested_at, time.monotonic() - started)
    finally:
        connection.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-mint", default=SOL)
    parser.add_argument("--output-mint", default=USDC)
    parser.add_argument("--amount", default="10000000")
    parser.add_argument("--slippage-bps", type=int, default=50)
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    try:
        request = parameters(args.input_mint, args.output_mint, args.amount, args.slippage_bps)
        if args.worker:
            result = fetch(request)
        else:
            # The Windows venv launcher creates another interpreter process.
            # Kill the actual stdlib worker, rather than its redirector.
            interpreter = str(Path(getattr(sys, "_base_executable", sys.executable)).resolve(strict=True))
            command = [interpreter, str(Path(__file__).resolve()), "--worker",
                       "--input-mint", request["inputMint"], "--output-mint", request["outputMint"],
                       "--amount", request["amount"], "--slippage-bps", str(request["slippageBps"])]
            child = subprocess.run(command, capture_output=True, text=True, timeout=TIMEOUT,
                                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            if child.returncode not in (0, 1):
                raise DiagnosticError("WORKER_FAILED")
            result = json.loads(child.stdout)
        print(json.dumps(result, separators=(",", ":")))
        return 0 if result.get("status") == "QUOTE_OBSERVED" else 1
    except (DiagnosticError, subprocess.TimeoutExpired, OSError, ValueError, RecursionError) as error:
        code = str(error) if isinstance(error, DiagnosticError) else (
            "TIMEOUT" if isinstance(error, subprocess.TimeoutExpired) else "TRANSPORT_ERROR")
        print(json.dumps({"schema_version": 1, "provider": "JUPITER_V2", "status": "UNAVAILABLE",
                          "diagnostic_only": True, "execution_eligible": False, "code": code}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
