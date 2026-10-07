import json, sqlite3, sys, time
from pathlib import Path
from datetime import datetime, timezone

ROOT = Path.home() / "Desktop" / "IMC_TRADING_MAIN" / "RUNTIME"
GW = ROOT / "EXECUTOR_GATEWAY"
CFG = json.loads((GW / "config_v480.json").read_text(encoding="utf-8-sig"))
sys.path.insert(0, str(ROOT))
import real_market_execution_v480_build_parity as v

AMOUNT = int(CFG["calibration_input_lamports"])
MAX_LOSS = float(CFG["max_calibration_roundtrip_loss"])
MAX_QUOTE_DRIFT = 0.01
WATCH = ROOT / "watch-breadth-v425-shadow.db"
c = sqlite3.connect(WATCH)
mints = [r[0] for r in c.execute(
    "select distinct address from picks where variant='CALIB' and accepted=1 "
    "order by decision_at desc limit 12"
).fetchall()]
c.close()

routes = []
for mint in mints:
    try:
        a = v.lite_roundtrip(mint, AMOUNT)
        time.sleep(0.12)
        b = v.lite_roundtrip(mint, AMOUNT)
        drift = abs(int(b[4]) - int(a[4])) / AMOUNT
        worst_loss = max(float(a[5]), float(b[5]))
        if worst_loss <= MAX_LOSS and drift <= MAX_QUOTE_DRIFT:
            routes.append((worst_loss + drift, mint, b[0], b[1], b[2], b[3], b[4], worst_loss, drift))
    except Exception:
        pass
routes.sort(key=lambda x: x[0])
if not routes:
    raise SystemExit("NO_CALIB_ROUTE_UNDER_LIMIT")
chosen = None
for score, mint, bq, sq, bi, si, back, loss, drift in routes[:4]:
    try:
        build = v.fast_build("BUY", mint, AMOUNT)
        sim = v.simulate_build(build, True, mint)
        chosen = (loss, drift, mint, bi, si, back, build, sim)
        break
    except Exception as e:
        print(json.dumps({"candidate": mint, "rejected": str(e)}))
        time.sleep(2.1)
if chosen is None:
    raise SystemExit("NO_EXECUTABLE_CALIB_CANDIDATE")

loss, drift, mint, bi, si, back, build, sim = chosen
build_out = int(build["outAmount"])
sim_out = int(sim.get("token_delta_raw") or build_out)
bal = v.balance_lamports(force=True)
rent_locked = None
if bal is not None and sim.get("wallet_post") is not None:
    rent_locked = max(0, int(bal) - int(sim["wallet_post"]) - AMOUNT - int(sim["fee"]))
trade_id = int(time.time())
candidate = {
    "version": "4.8.0",
    "trade_id": trade_id,
    "mode": "CALIB_PATH_ONLY",
    "execution_path": "JUPITER_V2_BUILD_FAST_PLUS_LOCAL_SIM",
    "prepared_at": datetime.now(timezone.utc).isoformat(),
    "mint": mint,
    "input_lamports": AMOUNT,
    "expected_buy_out_raw": str(sim_out),
    "build_buy_out_raw": str(build_out),
    "sim_fill_delta_pct": ((sim_out / build_out) - 1.0) * 100.0 if build_out else 0.0,
    "pre_buy_impact": bi,
    "pre_sell_impact": si,
    "pre_roundtrip_loss": loss,
    "quote_drift_fraction": drift,
    "expected_sell_back_lamports": int(back),
    "buy_fee_lamports": int(sim["fee"]),
    "rent_locked_lamports": rent_locked,
    "simulation_units": int(sim["units"]),
    "cu_limit": int(sim["cu_limit"]),
    "prepared_tx_b64": sim["prepared_tx_b64"],
    "blockhash_b58": sim["blockhash_b58"],
    "last_valid_block_height": int(sim["last_valid_block_height"]),
    "simulation_passed": True,
    "broadcast": False
}
(GW / "v480_calibration_candidate.json").write_text(
    json.dumps(candidate, indent=2), encoding="utf-8"
)
(GW / "_v480_unsigned_test.json").write_text(json.dumps({
    "prepared_tx_b64": sim["prepared_tx_b64"],
    "blockhash": sim["blockhash_b58"],
    "last_valid_block_height": int(sim["last_valid_block_height"]),
    "fee": int(sim["fee"]),
    "units": int(sim["units"]),
    "build_out": str(build["outAmount"]),
    "mint": mint,
    "amount": AMOUNT
}, indent=2), encoding="utf-8")
print(json.dumps({
    "state": "V480_CALIB_PREPARED_NO_BROADCAST",
    "trade_id": trade_id,
    "mint": mint,
    "input_lamports": AMOUNT,
    "expected_buy_out_raw": str(sim_out),
    "build_buy_out_raw": str(build_out),
    "sim_fill_delta_pct": round(((sim_out / build_out) - 1.0) * 100.0, 4) if build_out else 0.0,
    "roundtrip_loss_pct": round(loss * 100, 4),
    "quote_drift_pct": round(drift * 100, 4),
    "fee_lamports": int(sim["fee"]),
    "rent_locked_lamports": rent_locked,
    "units": int(sim["units"]),
    "broadcast": False
}, indent=2))
