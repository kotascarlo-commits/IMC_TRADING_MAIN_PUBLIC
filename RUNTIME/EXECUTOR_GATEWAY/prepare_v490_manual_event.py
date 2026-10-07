import json, sys
from pathlib import Path
from datetime import datetime, timezone

HERE = Path(__file__).resolve().parent
RUNTIME = HERE.parent
LIVE = HERE / "LIVE_V490"
sys.path.insert(0, str(RUNTIME))
import real_market_execution_v480_build_parity as eng

EVENT_FILE = HERE / "v490_manual_event.json"
CAND = HERE / "v480_calibration_candidate.json"
STATE = LIVE / "live_signal_state_v490.json"
CFG = json.loads((LIVE / "config_v490_live.json").read_text(encoding="utf-8-sig"))

if not EVENT_FILE.exists():
    raise SystemExit("NO_MANUAL_EVENT")
ev = json.loads(EVENT_FILE.read_text(encoding="utf-8-sig"))
side = str(ev.get("side") or "").upper()
mint = str(ev.get("mint") or "")
tid = int(ev.get("trade_id") or 0)
if side not in ("BUY","SELL") or not mint or tid <= 0:
    raise SystemExit("BAD_EVENT")
state = json.loads(STATE.read_text(encoding="utf-8-sig")) if STATE.exists() else {}
if side == "BUY":
    amount = int(ev.get("input_lamports") or 0)
    if amount <= 0:
        raise SystemExit("BAD_BUY_AMOUNT")
else:
    rec = (state.get("buy_filled") or {}).get(mint) or {}
    bought = int(rec.get("actual_output_raw") or 0)
    wallet_raw = int(eng.token_balance_raw(mint) or 0)
    amount = min(bought, wallet_raw)
    if amount <= 0:
        raise SystemExit("NO_SELLABLE_FILLED_BALANCE")

build = eng.fast_build(side, mint, amount)
impact = abs(float(build.get("priceImpactPct") or 0))
limit = float(CFG["max_buy_price_impact_fraction"] if side == "BUY" else CFG["max_sell_price_impact_fraction"])
if impact > limit:
    raise SystemExit(f"PRICE_IMPACT_BLOCK {impact:.6f}>{limit:.6f}")
sim = eng.simulate_build(build, True, mint)
cand = {
    "version":"4.8.0","trade_id":tid,"side":side,"mint":mint,
    "prepared_at":datetime.now(timezone.utc).isoformat(),
    "simulation_passed":True,"broadcast":False,
    "pre_roundtrip_loss":float(ev.get("sim_roundtrip_loss") or 0.0),
    "prepared_tx_b64":sim["prepared_tx_b64"],
    "blockhash_b58":sim["blockhash_b58"],
    "last_valid_block_height":int(sim["last_valid_block_height"]),
    "simulation_units":int(sim["units"]),
    "cu_limit":int(sim["cu_limit"])
}
if side == "BUY":
    cand["input_lamports"] = amount
    cand["expected_buy_out_raw"] = str(max(0, int(sim.get("token_delta_raw") or build.get("outAmount") or 0)))
    cand["expected_token_delta_raw"] = cand["expected_buy_out_raw"]
else:
    cand["input_token_raw"] = str(amount)
    cand["expected_sell_out_lamports"] = int(build.get("outAmount") or 0)
    cand["expected_token_delta_raw"] = str(int(sim.get("token_delta_raw") or -amount))

CAND.write_text(json.dumps(cand, indent=2), encoding="utf-8")
print(json.dumps({"state":"MANUAL_ORDER_READY","side":side,"trade_id":tid,"mint":mint,
                  "amount_raw":amount,"price_impact":impact,"broadcast":False}, indent=2))
