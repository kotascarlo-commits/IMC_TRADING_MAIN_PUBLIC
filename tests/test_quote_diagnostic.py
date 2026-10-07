"""Offline tests for independent quotes; never call providers or execution."""
import importlib.util
import io
import json
from pathlib import Path
import subprocess
from urllib.parse import parse_qs, urlsplit

import pytest

PATH = Path(__file__).resolve().parents[1] / "TOOLS" / "imc_quote_diagnostic.py"
spec = importlib.util.spec_from_file_location("quote_diagnostic", PATH)
quote = importlib.util.module_from_spec(spec)
spec.loader.exec_module(quote)


def request():
    return quote.parameters(quote.SOL, quote.USDC, "10000000", 50)


def body():
    return {"inputMint": quote.SOL, "outputMint": quote.USDC, "inAmount": "10000000",
            "outAmount": "2000001", "otherAmountThreshold": "1990000",
            "swapMode": "ExactIn", "slippageBps": 50, "priceImpact": -0.1,
            "priceImpactPct": "IGNORE_DEPRECATED_VALUE", "transaction": None,
            "routePlan": [{"swapInfo": {"ammKey": quote.SOL, "inputMint": quote.SOL,
                           "outputMint": quote.USDC, "inAmount": "10000000",
                           "outAmount": "2000001", "label": "SECRET_UNTRUSTED_METADATA"}}]}


def normalized(value=None):
    return quote.normalize(body() if value is None else value, request(), "2026-10-05T00:00:00Z", .2)


def test_quote_is_independent_read_only_and_redacted():
    result = normalized()
    assert result["price_impact_bps"] == "-10.0"
    assert result["minimum_output_raw"] == "1990000"  # Provider rounds threshold down.
    assert result["execution_eligible"] is False and result["diagnostic_only"] is True
    assert result["market_data_readiness"] == "UNKNOWN"
    assert result["total_execution_fee_lamports"] is None and result["liquidity_usd"] is None
    assert "SECRET" not in json.dumps(result) and "IGNORE" not in json.dumps(result)


@pytest.mark.parametrize("actual",[0,25,50])
def test_lower_slippage_preserves_actual_and_exact_threshold(actual):
    value=body()|{"slippageBps":actual,"otherAmountThreshold":str(2000001*(10000-actual)//10000)}
    result=normalized(value)
    assert result["requested_slippage_bps"]==50 and result["slippage_bps"]==actual
    value["otherAmountThreshold"]=str(int(value["otherAmountThreshold"])-1)
    with pytest.raises(quote.DiagnosticError,match="INVALID_THRESHOLD"):normalized(value)


@pytest.mark.parametrize("change", [
    {"inputMint": quote.USDC}, {"outputMint": quote.SOL}, {"inAmount": "10000001"},
    {"slippageBps": True}, {"slippageBps": 51}, {"swapMode": "ExactOut"},
    {"outAmount": "0"}, {"outAmount": "-1"}, {"outAmount": 2000001},
    {"otherAmountThreshold": "1989999"}, {"otherAmountThreshold": "2000002"},
    {"routePlan": []}, {"routePlan": [None]}, {"routePlan": [{}]},
    {"transaction": "SIGNED_OR_UNSIGNED_TX"}, {"taker": quote.SOL},
    {"error": "SECRET"}, {"errorMessage": "SECRET"}, {"errorCode": 1},
    {"errorCode": False}, {"errorCode": "0"}, {"error": {}},
    {"priceImpact": True}, {"priceImpact": "0.1"}, {"priceImpact": float("nan")},
    {"priceImpact": float("inf")}, {"priceImpact": 101},
])
def test_untrusted_or_unbound_quote_rejected(change):
    with pytest.raises(quote.DiagnosticError) as caught:
        normalized(body() | change)
    assert "SECRET" not in str(caught.value)


def test_missing_impact_stays_unknown_and_old_ratio_is_not_used():
    value = body()
    del value["priceImpact"]
    assert normalized(value)["price_impact_bps"] is None


def test_disconnected_route_rejected():
    value = body()
    value["routePlan"][0]["swapInfo"]["outputMint"] = quote.SOL
    with pytest.raises(quote.DiagnosticError, match="DISCONNECTED_ROUTE"):
        normalized(value)


@pytest.mark.parametrize("raw", ["0", "-1", "01", "1.5", "18446744073709551616", True])
def test_invalid_input_amount_prevents_request(raw):
    with pytest.raises(quote.DiagnosticError):
        quote.parameters(quote.SOL, quote.USDC, raw, 50)


@pytest.mark.parametrize("value", ["0" * 44, "1" * 31, "1" * 33, quote.SOL + "?secret=1"])
def test_invalid_mint_prevents_request(value):
    with pytest.raises(quote.DiagnosticError):
        quote.parameters(value, quote.USDC, "10", 50)


@pytest.mark.parametrize("slippage", [-1, 151, True, "50"])
def test_unsafe_slippage_rejected(slippage):
    with pytest.raises(quote.DiagnosticError):
        quote.parameters(quote.SOL, quote.USDC, "10", slippage)


class Response:
    def __init__(self, value=None, status=200, headers=None, raw=None):
        self.status = status
        self.headers = {"Content-Type": "application/json"} | (headers or {})
        self.stream = io.BytesIO(json.dumps(body() if value is None else value).encode() if raw is None else raw)

    def getheader(self, name, default=None):
        return self.headers.get(name, default)

    def read1(self, size):
        return self.stream.read(size)


def fake_connection(monkeypatch, response):
    observed = []

    class Connection:
        sock = None

        def __init__(self, host, timeout):
            observed.append((host, timeout))

        def request(self, method, path, headers):
            observed.append((method, path, headers))

        def getresponse(self):
            return response

        def close(self):
            observed.append("closed")

    monkeypatch.setattr(quote.http.client, "HTTPSConnection", Connection)
    return observed


def test_exactly_one_pinned_get_has_no_wallet_or_credentials(monkeypatch):
    observed = fake_connection(monkeypatch, Response())
    assert quote.fetch(request())["status"] == "QUOTE_OBSERVED"
    assert observed[0][0] == "api.jup.ag" and observed[-1] == "closed"
    method, path, headers = observed[1]
    assert method == "GET" and urlsplit(path).path == "/swap/v2/order"
    assert set(parse_qs(urlsplit(path).query)) == set(request())
    assert set(headers) == {"Accept", "User-Agent"}
    assert len(observed) == 3


@pytest.mark.parametrize("change", [{"taker": quote.SOL}, {"receiver": quote.USDC},
    {"payer": quote.SOL}, {"swapMode": "ExactOut"}, {"amount": "0"}])
def test_direct_fetch_cannot_add_wallet_fields_or_change_mode(monkeypatch, change):
    observed = fake_connection(monkeypatch, Response())
    with pytest.raises(quote.DiagnosticError):
        quote.fetch(request() | change)
    assert observed == []


@pytest.mark.parametrize("status,code", [(301, "REDIRECT_BLOCKED"), (307, "REDIRECT_BLOCKED"),
    (401, "AUTH_REQUIRED"), (403, "ACCESS_DENIED"), (429, "RATE_LIMITED"), (503, "HTTP_ERROR")])
def test_provider_errors_never_follow_redirect_or_retry(monkeypatch, status, code):
    observed = fake_connection(monkeypatch, Response(status=status, raw=b"SECRET"))
    with pytest.raises(quote.DiagnosticError, match=code):
        quote.fetch(request())
    assert len(observed) == 3 and observed[-1] == "closed"


@pytest.mark.parametrize("response,code", [
    (Response(raw=b"x" * (quote.MAX_BYTES + 1)), "RESPONSE_TOO_LARGE"),
    (Response(raw=b'{"inputMint":"a","inputMint":"b"}'), "AMBIGUOUS_RESPONSE"),
    (Response(raw=b"not-json-SECRET"), "INVALID_JSON"),
    (Response(headers={"Content-Type": "text/html"}), "INVALID_CONTENT_TYPE"),
    (Response(headers={"Content-Encoding": "gzip"}), "ENCODING_BLOCKED"),
])
def test_bounded_stream_and_json(monkeypatch, response, code):
    fake_connection(monkeypatch, response)
    with pytest.raises(quote.DiagnosticError, match=code):
        quote.fetch(request())


def test_parent_enforces_entire_worker_timeout(monkeypatch, capsys):
    calls = []

    def timeout(command, **kwargs):
        calls.append((command, kwargs))
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])

    monkeypatch.setattr(quote.subprocess, "run", timeout)
    assert quote.main([]) == 1
    output = json.loads(capsys.readouterr().out)
    assert output["code"] == "TIMEOUT" and output["execution_eligible"] is False
    assert len(calls) == 1 and calls[0][1]["timeout"] == quote.TIMEOUT
    assert "--worker" in calls[0][0]
    assert calls[0][0][0] == str(Path(quote.sys._base_executable).resolve(strict=True))


def test_cli_transport_error_is_redacted(monkeypatch, capsys):
    def fail(request):
        raise OSError("SECRET credentials and provider message")

    monkeypatch.setattr(quote, "fetch", fail)
    assert quote.main(["--worker"]) == 1
    output = capsys.readouterr().out
    assert "SECRET" not in output and json.loads(output)["code"] == "TRANSPORT_ERROR"
