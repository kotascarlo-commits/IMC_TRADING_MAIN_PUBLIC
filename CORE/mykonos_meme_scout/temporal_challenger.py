from __future__ import annotations

import hashlib
import json
import math
import random
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

CHALLENGER_SCHEMA_VERSION = 1
CHALLENGER_LINEAGE = "0.8.11-horizon-aware-gap-aware-real-shadow"
HORIZONS = (10, 30, 60, 180, 300, 600, 900)
OUTPUT_NAMES = ("p5", "p10", "p25", "p_rug", "p_stagnation")
LABEL_COLUMNS = {
    "p5": "hit_5",
    "p10": "hit_10",
    "p25": "hit_25",
    "p_rug": "rug_or_liquidity_collapse",
    "p_stagnation": "stagnant",
}
FEATURE_NAMES = (
    "price_return_window",
    "price_return_10s",
    "liquidity_return_window",
    "market_cap_return_window",
    "volume_delta_to_liquidity_bps",
    "transaction_rate_per_second",
    "buy_sell_imbalance",
    "price_log_return_volatility",
    "max_runup_window",
    "max_drawdown_window",
    "observation_coverage",
    "max_gap_seconds",
    "mean_gap_seconds",
    "age_minutes",
    "price_change_5m_pct",
    "social_availability_ratio",
    "smart_wallet_availability_ratio",
    "hard_risk_seen",
)


def _sigmoid(x: float) -> float:
    if x >= 0:
        z = math.exp(-min(x, 40.0))
        return 1.0 / (1.0 + z)
    z = math.exp(max(x, -40.0))
    return z / (1.0 + z)


def _safe_return(a: float, b: float) -> float:
    return b / a - 1.0 if a > 0 and b > 0 else 0.0


def _mean(xs: Sequence[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def _std(xs: Sequence[float]) -> float:
    if len(xs) < 2:
        return 0.0
    m = _mean(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))


def extract_gap_aware_features(rows: Sequence[sqlite3.Row], *, observed_second: int, window_seconds: int = 60) -> list[float]:
    """Summarize only actually observed ticks. Missing seconds are encoded, never filled."""
    if not rows:
        raise ValueError("challenger window cannot be empty")
    rows = [r for r in rows if observed_second - window_seconds + 1 <= int(r["monotonic_second"]) <= observed_second]
    if not rows or int(rows[-1]["monotonic_second"]) != observed_second:
        raise ValueError("challenger requires an exact observed endpoint")
    if any(bool(r["market_data_stale"]) or not bool(r["feed_complete"]) for r in rows):
        raise ValueError("challenger does not consume stale/incomplete returned ticks")

    secs = [int(r["monotonic_second"]) for r in rows]
    gaps = [b - a for a, b in zip(secs, secs[1:])]
    coverage = len(set(secs)) / float(window_seconds)
    max_gap = max(gaps, default=1)
    mean_gap = _mean([float(g) for g in gaps]) if gaps else 1.0
    first, last = rows[0], rows[-1]

    cutoff10 = observed_second - 10
    r10 = rows[0]
    for r in rows:
        if int(r["monotonic_second"]) >= cutoff10:
            r10 = r
            break

    prices = [float(r["price_usd"]) for r in rows]
    log_returns = []
    for a, b in zip(prices, prices[1:]):
        if a > 0 and b > 0:
            log_returns.append(math.log(b / a))
    base_price = max(prices[0], 1e-12)
    runup = max((p / base_price - 1.0 for p in prices), default=0.0)
    drawdown = min((p / base_price - 1.0 for p in prices), default=0.0)

    vol_delta = max(0.0, float(last["volume_5m_usd"]) - float(first["volume_5m_usd"]))
    buy_delta = max(0, int(last["buys_5m"]) - int(first["buys_5m"]))
    sell_delta = max(0, int(last["sells_5m"]) - int(first["sells_5m"]))
    tx = buy_delta + sell_delta
    elapsed = max(1, secs[-1] - secs[0])
    liq = max(float(last["liquidity_usd"]), 1e-9)

    return [
        _safe_return(float(first["price_usd"]), float(last["price_usd"])),
        _safe_return(float(r10["price_usd"]), float(last["price_usd"])),
        _safe_return(float(first["liquidity_usd"]), float(last["liquidity_usd"])),
        _safe_return(float(first["market_cap_usd"]), float(last["market_cap_usd"])),
        vol_delta / liq * 10_000.0,
        tx / elapsed,
        (buy_delta - sell_delta) / max(1, tx),
        _std(log_returns),
        runup,
        drawdown,
        coverage,
        float(max_gap),
        mean_gap,
        float(last["age_seconds"] or 0.0) / 60.0,
        float(last["price_change_5m_pct"] or 0.0),
        _mean([float(bool(r["social_data_available"])) for r in rows]),
        _mean([float(bool(r["smart_wallet_data_available"])) for r in rows]),
        float(any(bool(r["hard_risk_seen"]) for r in rows)),
    ]


@dataclass(slots=True)
class LogisticHead:
    weights: list[float]
    bias: float
    prevalence: float
    positive_count: int
    negative_count: int

    def predict(self, x: Sequence[float]) -> float:
        return _sigmoid(self.bias + sum(w * v for w, v in zip(self.weights, x)))


@dataclass(slots=True)
class HorizonAwareChallengerBundle:
    trained_at: str
    source_database_sha256: str
    window_seconds: int
    minimum_window_coverage: float
    means: list[float]
    stds: list[float]
    heads: dict[str, dict[str, LogisticHead]]
    metrics: dict[str, Any]
    training_config: dict[str, Any]

    def _standardize(self, raw: Sequence[float]) -> list[float]:
        if len(raw) != len(FEATURE_NAMES):
            raise ValueError("challenger feature width mismatch")
        return [max(-8.0, min(8.0, (float(v) - self.means[i]) / self.stds[i])) for i, v in enumerate(raw)]

    def predict(self, rows: Sequence[sqlite3.Row], *, observed_second: int, horizon_seconds: int) -> dict[str, float]:
        key = str(int(horizon_seconds))
        if key not in self.heads:
            raise ValueError(f"challenger horizon unavailable: {horizon_seconds}")
        raw = extract_gap_aware_features(rows, observed_second=observed_second, window_seconds=self.window_seconds)
        if raw[FEATURE_NAMES.index("observation_coverage")] < self.minimum_window_coverage:
            raise ValueError("challenger window coverage below training policy")
        x = self._standardize(raw)
        return {name: self.heads[key][name].predict(x) for name in OUTPUT_NAMES}

    def as_dict(self) -> dict[str, Any]:
        return {
            "challenger_schema_version": CHALLENGER_SCHEMA_VERSION,
            "lineage_version": CHALLENGER_LINEAGE,
            "model_family": "horizon_specific_gap_aware_logistic_shadow",
            "trained_at": self.trained_at,
            "source_database_sha256": self.source_database_sha256,
            "window_seconds": self.window_seconds,
            "minimum_window_coverage": self.minimum_window_coverage,
            "feature_names": list(FEATURE_NAMES),
            "output_names": list(OUTPUT_NAMES),
            "horizons_seconds": sorted(int(k) for k in self.heads),
            "means": self.means,
            "stds": self.stds,
            "heads": {h: {n: {"weights": head.weights, "bias": head.bias, "prevalence": head.prevalence,
                                "positive_count": head.positive_count, "negative_count": head.negative_count}
                           for n, head in outputs.items()} for h, outputs in self.heads.items()},
            "metrics": self.metrics,
            "training_config": self.training_config,
            "shadow_only": True,
            "runtime_decision_input": False,
            "eligible_for_real_champion": False,
            "execution_authority_pct": 0,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "HorizonAwareChallengerBundle":
        if raw.get("lineage_version") != CHALLENGER_LINEAGE:
            raise ValueError("unsupported challenger lineage")
        if tuple(raw.get("feature_names") or ()) != FEATURE_NAMES:
            raise ValueError("challenger feature schema mismatch")
        if float(raw.get("execution_authority_pct", -1)) != 0.0 or not bool(raw.get("shadow_only")):
            raise ValueError("challenger safety freeze violated")
        if bool(raw.get("runtime_decision_input")) or bool(raw.get("eligible_for_real_champion")):
            raise ValueError("challenger cannot acquire decision/promotion authority")
        heads = {}
        for h, outputs in raw["heads"].items():
            heads[str(h)] = {n: LogisticHead(weights=[float(v) for v in d["weights"]], bias=float(d["bias"]),
                                             prevalence=float(d["prevalence"]), positive_count=int(d["positive_count"]),
                                             negative_count=int(d["negative_count"])) for n, d in outputs.items()}
        return cls(trained_at=str(raw["trained_at"]), source_database_sha256=str(raw["source_database_sha256"]),
                   window_seconds=int(raw["window_seconds"]), minimum_window_coverage=float(raw["minimum_window_coverage"]),
                   means=[float(v) for v in raw["means"]], stds=[float(v) for v in raw["stds"]], heads=heads,
                   metrics=dict(raw.get("metrics") or {}), training_config=dict(raw.get("training_config") or {}))

    def save(self, path: Path, *, overwrite: bool = False) -> None:
        path = Path(path).resolve()
        if path.exists() and not overwrite:
            raise FileExistsError(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.as_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> "HorizonAwareChallengerBundle":
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _fit_head(xs: list[list[float]], ys: list[int], *, epochs: int, learning_rate: float, l2: float, seed: int) -> LogisticHead:
    n = len(xs[0])
    pos, neg = sum(ys), len(ys) - sum(ys)
    prevalence = (pos + 1.0) / (len(ys) + 2.0)
    w = [0.0] * n
    bias = math.log(prevalence / (1.0 - prevalence))
    rng = random.Random(seed)
    order = list(range(len(xs)))
    for _ in range(epochs):
        rng.shuffle(order)
        for i in order:
            p = _sigmoid(bias + sum(a*b for a,b in zip(w, xs[i])))
            err = p - ys[i]
            for j in range(n):
                w[j] -= learning_rate * (err * xs[i][j] + l2 * w[j])
            bias -= learning_rate * err
    return LogisticHead(w, bias, prevalence, pos, neg)


def _brier(bundle: HorizonAwareChallengerBundle, examples: list[dict[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for h in sorted({e["horizon"] for e in examples}):
        vals = [e for e in examples if e["horizon"] == h]
        if not vals:
            continue
        per = {}
        for name in OUTPUT_NAMES:
            errs=[]
            for e in vals:
                x=bundle._standardize(e["features"])
                p=bundle.heads[str(h)][name].predict(x)
                errs.append((p-e["labels"][name])**2)
            per[name]=_mean(errs)
        weights={"p5":1/6,"p10":1/6,"p25":1/6,"p_rug":1/4,"p_stagnation":1/4}
        out[str(h)]={"rows":len(vals),"balanced_brier":sum(per[n]*weights[n] for n in OUTPUT_NAMES),"brier_by_output":per}
    return out


def train_real_challenger(database: Path, model_path: Path, *, window_seconds: int = 60,
                          minimum_window_coverage: float = 0.85, minimum_examples_per_horizon: int = 20,
                          epochs: int = 30, learning_rate: float = 0.01, l2: float = 0.001,
                          overwrite: bool = False) -> HorizonAwareChallengerBundle:
    database = Path(database).resolve()
    conn = sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        meta = conn.execute("SELECT * FROM real_temporal_metadata WHERE id=1").fetchone()
        if meta is None or str(meta["data_domain"]) != "REAL_MARKET_PAPER_SHADOW":
            raise ValueError("challenger training requires a real-market paper-shadow database")
        if int(meta["execution_authority_pct"]) != 0 or int(meta["runtime_decision_input"]) != 0 or int(meta["eligible_for_real_champion"]) != 0:
            raise ValueError("challenger source database violates zero-authority safety gates")
        # One outcome path per session/observation/horizon, irrespective of which old shadow model created it.
        keys = conn.execute("""
            SELECT p.session_id,p.address,p.observed_second,p.horizon_seconds,
                   MIN(o.prediction_id) any_prediction_id,
                   MAX(o.hit_5) hit_5,MAX(o.hit_10) hit_10,MAX(o.hit_25) hit_25,
                   MAX(o.rug_or_liquidity_collapse) rug_or_liquidity_collapse,MAX(o.stagnant) stagnant,
                   MIN(s.started_at) started_at
            FROM real_shadow_predictions p
            JOIN real_shadow_outcomes o ON o.prediction_id=p.prediction_id
            JOIN real_temporal_sessions s ON s.session_id=p.session_id
            GROUP BY p.session_id,p.address,p.observed_second,p.horizon_seconds
            ORDER BY started_at,p.session_id,p.observed_second,p.horizon_seconds
        """).fetchall()
        examples=[]
        for k in keys:
            obs=int(k["observed_second"])
            rows=conn.execute("""SELECT * FROM real_temporal_ticks WHERE session_id=? AND monotonic_second BETWEEN ? AND ?
                                ORDER BY monotonic_second""",
                              (k["session_id"], obs-window_seconds+1, obs)).fetchall()
            try:
                features=extract_gap_aware_features(rows, observed_second=obs, window_seconds=window_seconds)
            except ValueError:
                continue
            if features[FEATURE_NAMES.index("observation_coverage")] < minimum_window_coverage:
                continue
            examples.append({"session_id":str(k["session_id"]),"address":str(k["address"]),"started_at":str(k["started_at"]),
                             "observed_second":obs,"horizon":int(k["horizon_seconds"]),"features":features,
                             "labels":{"p5":int(k["hit_5"]),"p10":int(k["hit_10"]),"p25":int(k["hit_25"]),
                                       "p_rug":int(k["rug_or_liquidity_collapse"]),"p_stagnation":int(k["stagnant"])}})
        if not examples:
            raise ValueError("no eligible settled real examples for challenger training")
        groups=[]
        seen=set()
        for e in examples:
            g=(e["address"],e["session_id"])
            if g not in seen:
                groups.append(g); seen.add(g)
        if len(groups) < 4:
            raise ValueError("challenger requires at least four chronological token sessions")
        cut=max(1,int(len(groups)*0.75))
        fit_groups=set(groups[:cut]); hold_groups=set(groups[cut:])
        fit=[e for e in examples if (e["address"],e["session_id"]) in fit_groups]
        hold=[e for e in examples if (e["address"],e["session_id"]) in hold_groups]
        cols=list(zip(*(e["features"] for e in fit)))
        means=[_mean(list(c)) for c in cols]
        stds=[max(_std(list(c)),1e-6) for c in cols]
        def z(raw): return [max(-8.0,min(8.0,(v-means[i])/stds[i])) for i,v in enumerate(raw)]
        heads={}
        trained_horizons=[]
        for h in HORIZONS:
            vals=[e for e in fit if e["horizon"]==h]
            if len(vals) < minimum_examples_per_horizon:
                continue
            xs=[z(e["features"]) for e in vals]
            heads[str(h)]={}
            for oi,name in enumerate(OUTPUT_NAMES):
                ys=[e["labels"][name] for e in vals]
                heads[str(h)][name]=_fit_head(xs,ys,epochs=epochs,learning_rate=learning_rate,l2=l2,seed=20260904+h*10+oi)
            trained_horizons.append(h)
        if not heads:
            raise ValueError("no horizon has enough eligible examples")
        bundle=HorizonAwareChallengerBundle(
            trained_at=datetime.now(timezone.utc).isoformat(),source_database_sha256=_sha256_file(database),
            window_seconds=window_seconds,minimum_window_coverage=minimum_window_coverage,means=means,stds=stds,heads=heads,
            metrics={},training_config={"chronological_session_disjoint_split":"75/25","fit_sessions":len(fit_groups),
                "holdout_sessions":len(hold_groups),"fit_examples":len(fit),"holdout_examples":len(hold),
                "minimum_examples_per_horizon":minimum_examples_per_horizon,"epochs":epochs,"learning_rate":learning_rate,
                "l2":l2,"no_interpolation":True,"automatic_promotion":False,"execution_authority_pct":0})
        bundle.metrics={"fit":_brier(bundle,[e for e in fit if str(e["horizon"]) in heads]),
                        "forward_holdout":_brier(bundle,[e for e in hold if str(e["horizon"]) in heads]),
                        "market_proof":False,"trained_horizons":trained_horizons}
        bundle.save(model_path,overwrite=overwrite)
        return bundle
    finally:
        conn.close()


def challenger_status(model_path: Path) -> dict[str, Any]:
    path=Path(model_path).resolve(); bundle=HorizonAwareChallengerBundle.load(path)
    return {"version":"0.8.11","model_path":str(path),"model_sha256":_sha256_file(path),"lineage":CHALLENGER_LINEAGE,
            "horizons_seconds":sorted(int(h) for h in bundle.heads),"feature_count":len(FEATURE_NAMES),
            "window_seconds":bundle.window_seconds,"minimum_window_coverage":bundle.minimum_window_coverage,
            "metrics":bundle.metrics,"shadow_only":True,"runtime_decision_input":False,
            "eligible_for_real_champion":False,"execution_authority_pct":0}
