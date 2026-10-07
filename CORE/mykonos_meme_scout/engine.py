from __future__ import annotations

from dataclasses import replace
from datetime import datetime

from .config import Config
from .learning import LearningLab
from .models import Assessment, MicroMetrics, TokenSnapshot
from .scoring import assess, derive_micro_metrics, early_probe_ok, entry_momentum_ok
from .store import Store


class PaperEngine:
    def __init__(self, store: Store, config: Config):
        self.store = store
        self.config = config

    def equity(self) -> float:
        account = self.store.account()
        total = float(account["cash_usd"])
        for pos in self.store.open_positions():
            latest = self.store.conn.execute(
                "SELECT price_usd FROM snapshots WHERE address=? ORDER BY id DESC LIMIT 1", (pos["address"],)
            ).fetchone()
            price = float(latest["price_usd"]) if latest else float(pos["entry_price"])
            total += float(pos["remaining_quantity"]) * price
        return total

    def risk_kill_reason(self) -> str | None:
        equity = self.equity()
        self.store.update_equity_state(equity)
        account = self.store.account()
        if account["kill_reason"]:
            return str(account["kill_reason"])
        checks = [
            (equity <= account["day_start_equity"] * (1 - self.config.daily_loss_kill_pct / 100), "daily_loss_limit"),
            (equity <= account["week_start_equity"] * (1 - self.config.weekly_loss_kill_pct / 100), "weekly_loss_limit"),
            (equity <= account["high_water_equity"] * (1 - self.config.account_drawdown_kill_pct / 100), "drawdown_limit"),
        ]
        for triggered, reason in checks:
            if triggered:
                self.store.update_equity_state(equity, reason)
                return reason
        return None

    def process(
        self, snapshot: TokenSnapshot, manage_existing: bool = True, settle_learning: bool = True,
        entry_authority: bool = True,
    ) -> dict:
        previous = self.store.latest_snapshot(snapshot.address)
        micro = derive_micro_metrics(snapshot, previous)
        self.store.record_snapshot(snapshot)

        learning_lab = LearningLab(
            self.store,
            prediction_interval_seconds=self.config.learning_prediction_interval_seconds,
            maximum_age_minutes=self.config.maximum_entry_age_minutes,
        )
        shadow_predictions = learning_lab.observe(snapshot, micro)
        result = assess(snapshot, self.config, micro)
        momentum_ok, momentum_reasons = entry_momentum_ok(snapshot, self.config, micro)
        probe_ok, probe_reasons = early_probe_ok(snapshot, self.config, micro, result)
        if manage_existing:
            self._manage_existing(snapshot, result, micro)
        kill = self.risk_kill_reason()
        extra: list[str] = []
        opened = False
        entry_lane: str | None = None
        decision_override: str | None = None
        probe_notional_cap_pct: float | None = None
        probe_security_tier: str | None = None

        standard_ready = result.eligible and momentum_ok
        no_open_position = self.store.open_position(snapshot.address) is None
        reentry_block_reason = self.store.risk_exit_reentry_block_reason(snapshot.address) if no_open_position else None
        if reentry_block_reason:
            # Exact mint-address lockout after a forced risk exit. This is not an
            # optimization filter: it prevents the same already-rejected launch
            # from being churned back into the PAPER book.
            extra.extend(["rug_reentry_blocked", f"prior_risk_exit:{reentry_block_reason}"])
            if not momentum_ok:
                extra.extend(momentum_reasons)
            if self.config.paper_probe_enabled and not probe_ok:
                extra.extend(probe_reasons)
        elif standard_ready and not kill and no_open_position and entry_authority and self._pf2_entry_guard(snapshot):
            confirmed = True
            if self.config.require_second_confirmation:
                prior = self.store.conn.execute(
                    "SELECT decision FROM decisions WHERE address=? ORDER BY id DESC LIMIT 1", (snapshot.address,)
                ).fetchone()
                confirmed = bool(prior and prior["decision"] == "PASS" and micro.has_previous)
            if confirmed:
                entry_lane = "STANDARD"
                opened = self._try_open(snapshot, entry_lane)
                if not opened:
                    extra.append("portfolio_capacity_or_cash_limit")
            else:
                extra.append("awaiting_fast_second_confirmation")
        elif probe_ok and not kill and no_open_position and entry_authority:
            # EARLY_PROBE remains fully classified and learned, but after the
            # corrected net-cost audit showed negative lane expectancy it no
            # longer receives PAPER execution authority. This preserves the
            # research signal without allowing it to damage the forward sample.
            probe_notional_cap_pct = self._probe_notional_cap_pct(snapshot)
            probe_security_tier = (
                "confirmed" if self._probe_security_confirmed(snapshot) else "unverified_or_partial"
            )
            extra.extend([
                f"probe_size_cap_pct:{probe_notional_cap_pct}",
                f"probe_security_tier:{probe_security_tier}",
            ])
            if self.config.paper_probe_execution_enabled:
                entry_lane = "EARLY_PROBE"
                decision_override = "PROBE"
                opened = self._try_open(snapshot, entry_lane)
                if not opened:
                    extra.append("probe_portfolio_capacity_or_cash_limit")
            else:
                entry_lane = "EARLY_PROBE_SHADOW"
                decision_override = "PROBE_SHADOW"
                extra.append("probe_shadow_only_no_execution")
        else:
            if not entry_authority and no_open_position and not kill and (standard_ready or probe_ok):
                extra.append("watch_feed_degraded_entry_pause")
            if not momentum_ok:
                extra.extend(momentum_reasons)
            if self.config.paper_probe_enabled and not probe_ok:
                extra.extend(probe_reasons)
        if kill:
            extra.append(f"kill_switch:{kill}")

        self.store.record_decision(snapshot, result, extra, decision_override=decision_override)
        settled = learning_lab.settle() if settle_learning else 0
        families = len({p.family for p in shadow_predictions}) if shadow_predictions else 0
        final_decision = decision_override or result.decision
        return {
            "symbol": snapshot.symbol,
            "age_minutes": round(snapshot.age_minutes, 2),
            "source": snapshot.discovery_source,
            "score": result.score,
            "confidence": result.confidence,
            "decision": final_decision,
            "entry_lane": entry_lane,
            "opened": opened,
            "probe_notional_cap_pct": probe_notional_cap_pct,
            "probe_security_tier": probe_security_tier,
            "reentry_blocked": bool(reentry_block_reason),
            "reentry_block_reason": reentry_block_reason,
            "volume_per_minute": round(snapshot.volume_rate_per_minute, 2),
            "transactions_per_minute": round(snapshot.transaction_rate_per_minute, 2),
            "buy_ratio": round(snapshot.buy_ratio, 3),
            "micro_price_velocity_pct_per_minute": (
                None if micro.price_velocity_pct_per_minute is None
                else round(micro.price_velocity_pct_per_minute, 2)
            ),
            "hard_failures": result.hard_failures,
            "reasons": result.hard_failures + extra + result.warnings,
            "shadow_models_observed": families,
            "shadow_predictions_written": len(shadow_predictions),
            "learning_outcomes_settled": settled,
        }

    def manage_position_snapshot(self, snapshot: TokenSnapshot) -> dict:
        """Manage an already-open PAPER position without running entry/learning logic.

        Used by the rapid position watcher. Last known security data is carried
        forward when the fast market refresh has no security payload of its own.
        """
        pos_before = self.store.open_position(snapshot.address)
        if pos_before is None or snapshot.market_data_stale or snapshot.price_usd <= 0:
            return {"address": snapshot.address, "symbol": snapshot.symbol, "managed": False, "closed": False}
        previous = self.store.latest_snapshot(snapshot.address)
        if previous is not None and not snapshot.security.checked and previous.security.checked:
            snapshot = replace(snapshot, security=previous.security)
        micro = derive_micro_metrics(snapshot, previous)
        self.store.record_snapshot(snapshot)
        result = assess(snapshot, self.config, micro)
        self._manage_existing(snapshot, result, micro)
        pos_after = self.store.open_position(snapshot.address)
        trade = None
        if pos_after is None:
            row = self.store.conn.execute(
                "SELECT reason,price,occurred_at FROM trades WHERE position_id=? AND side='SELL' ORDER BY id DESC LIMIT 1",
                (pos_before["id"],),
            ).fetchone()
            if row:
                trade = dict(row)
        return {
            "address": snapshot.address, "symbol": snapshot.symbol, "managed": True,
            "closed": pos_after is None, "last_exit": trade,
            "micro_price_velocity_pct_per_minute": micro.price_velocity_pct_per_minute,
            "micro_liquidity_velocity_pct_per_minute": micro.liquidity_velocity_pct_per_minute,
        }


    @staticmethod
    def _probe_security_confirmed(snapshot: TokenSnapshot) -> bool:
        """True only when the critical launch-control checks are positively known safe.

        This does not create an entry permission or a size-up permission.
        In v0.4.1 EARLY_PROBE is shadow-only; this helper is retained for research because API
        security completeness cannot rule out launch-gap/rug risk.
        """
        sec = snapshot.security
        return bool(
            sec.checked
            and sec.mint_authority_disabled is True
            and sec.freeze_authority_disabled is True
            and sec.honeypot is not True
            and sec.non_transferable is not True
            and sec.transfer_hook_present is not True
            and sec.malicious_creator is not True
            and (sec.top10_holder_pct is None or sec.top10_holder_pct < 45)
            and (sec.creator_holder_pct is None or sec.creator_holder_pct <= 5)
        )

    def _probe_notional_cap_pct(self, snapshot: TokenSnapshot) -> float:
        # Security is still recorded as a feature/tier, but never sizes up a probe.
        return min(self.config.probe_maximum_notional_pct, self.config.probe_unverified_maximum_notional_pct)


    def _pf2_entry_guard(self, snapshot: TokenSnapshot) -> bool:
        """PF2 paper challenger: veto only; never creates a trade."""
        try:
            rows = self.store.conn.execute(
                """SELECT model_family,p_rug,expected_utility,entry_liquidity
                   FROM learning_predictions
                   WHERE address=?
                   ORDER BY id DESC LIMIT 120""",
                (snapshot.address,),
            ).fetchall()
        except Exception:
            rows = []

        try:
            prior = self.store.conn.execute(
                "SELECT decision FROM decisions WHERE address=? ORDER BY id DESC LIMIT 3",
                (snapshot.address,),
            ).fetchall()
        except Exception:
            prior = []

        # current PASS + two previous PASS observations
        if len(prior) < 2 or any(str(r["decision"]).upper() != "PASS" for r in prior[:2]):
            return False

        if not rows:
            return True

        by_family = {}
        for r in rows:
            fam = str(r["model_family"])
            bucket = by_family.setdefault(fam, [])
            if len(bucket) < 5:
                bucket.append(r)

        def med(vals):
            vals=sorted(float(x) for x in vals if x is not None)
            if not vals:
                return None
            n=len(vals)
            return vals[n//2] if n%2 else (vals[n//2-1]+vals[n//2])/2.0

        neural=[]
        legacy=[]

        for fam,bucket in by_family.items():
            item={
                "rug":med([r["p_rug"] for r in bucket]),
                "util":med([r["expected_utility"] for r in bucket]),
                "liq":med([r["entry_liquidity"] for r in bucket]),
            }

            if fam in {"neural_champion_v041","neural_adaptive_v041"}:
                neural.append(item)
            elif fam in {"balanced","flow_rocket","security_first","ai_learning_family"}:
                legacy.append(item)

        nr=med([x["rug"] for x in neural])
        nu=med([x["util"] for x in neural])
        lr=med([x["rug"] for x in legacy])
        lu=med([x["util"] for x in legacy])
        liq=med([x["liq"] for x in neural+legacy])

        # Hard veto only when independent model groups agree.
        catastrophic=(
            nr is not None and lr is not None and
            nu is not None and lu is not None and
            nr >= 0.50 and lr >= 0.50 and
            nu < 0.0 and lu < 0.0
        )

        if catastrophic:
            return False

        # Uncertain/risky cases require one extra PASS observation.
        elevated=(
            (liq is not None and liq <= 0.0) or
            (nr is not None and nr >= 0.12) or
            (lr is not None and lr >= 0.50) or
            (nu is not None and nu <= 0.0) or
            (lu is not None and lu <= 0.0)
        )

        if elevated:
            if len(prior) < 3 or any(str(r["decision"]).upper() != "PASS" for r in prior[:3]):
                return False

        return True

    def _try_open(self, snapshot: TokenSnapshot, entry_lane: str = "STANDARD") -> bool:
        positions = self.store.open_positions()
        if len(positions) >= self.config.maximum_open_positions:
            return False
        equity = self.equity()
        account = self.store.account()
        current_exposure = sum(float(p["remaining_quantity"]) * float(p["entry_price"]) for p in positions)
        exposure_cap = equity * self.config.maximum_early_exposure_pct / 100
        available_exposure = max(0.0, exposure_cap - current_exposure)
        stop_fraction = self.config.hard_stop_pct / 100
        risk_budget = equity * self.config.risk_per_trade_pct / 100
        desired_notional = risk_budget / stop_fraction if stop_fraction > 0 else 0
        gross = min(desired_notional, available_exposure, float(account["cash_usd"]))
        if entry_lane == "EARLY_PROBE":
            probe_cap_pct = self._probe_notional_cap_pct(snapshot)
            gross = min(gross, equity * probe_cap_pct / 100)
        minimum_gross = 1.0 if entry_lane == "EARLY_PROBE" else 5.0
        if gross < minimum_gross or snapshot.price_usd <= 0:
            return False
        cost = gross * self.config.estimated_cost_per_side_pct / 100
        reason = "early_probe_entry" if entry_lane == "EARLY_PROBE" else "first15_qualified_entry"
        self.store.execute_buy(snapshot, gross, cost, reason=reason)
        return True

    def _manage_existing(self, snapshot: TokenSnapshot, result: Assessment, micro: MicroMetrics) -> None:
        pos = self.store.open_position(snapshot.address)
        if pos is None or snapshot.price_usd <= 0:
            return
        high = max(float(pos["high_price"]), snapshot.price_usd)
        self.store.update_position_flags(pos["id"], high_price=high)
        pos = self.store.open_position(snapshot.address)
        change_pct = (snapshot.price_usd / float(pos["entry_price"]) - 1) * 100

        # Fast rug/crash shield. This acts only on an already-open PAPER position.
        # It is intentionally based on abrupt *changes* rather than relaxed entry
        # thresholds, so it cannot create additional trades.
        liq_velocity = micro.liquidity_velocity_pct_per_minute
        price_velocity = micro.price_velocity_pct_per_minute
        if liq_velocity is not None and liq_velocity <= self.config.rug_liquidity_velocity_exit_pct_per_minute:
            self.store.execute_sell(pos["id"], snapshot, pos["remaining_quantity"],
                                    self.config.estimated_cost_per_side_pct, "rug_shield_liquidity_collapse")
            return
        if price_velocity is not None and price_velocity <= self.config.rug_price_velocity_exit_pct_per_minute \
                and snapshot.buy_ratio < self.config.rug_flow_buy_ratio_exit:
            self.store.execute_sell(pos["id"], snapshot, pos["remaining_quantity"],
                                    self.config.estimated_cost_per_side_pct, "rug_shield_price_flow_collapse")
            return
        hard_security_or_liquidity = any(reason in result.hard_failures for reason in {
            "low_liquidity", "thin_liquidity_ratio", "mint_authority_active",
            "freeze_authority_active", "honeypot_signal", "top10_concentration_high",
            "non_transferable_token", "transfer_hook_present", "malicious_creator",
        })
        if hard_security_or_liquidity:
            self.store.execute_sell(pos["id"], snapshot, pos["remaining_quantity"],
                                    self.config.estimated_cost_per_side_pct, "safety_gate_failed")
            return
        if change_pct <= -self.config.hard_stop_pct:
            self.store.execute_sell(pos["id"], snapshot, pos["remaining_quantity"],
                                    self.config.estimated_cost_per_side_pct, "fast_hard_stop")
            return

        opened = datetime.fromisoformat(str(pos["opened_at"]).replace("Z", "+00:00"))
        captured = datetime.fromisoformat(snapshot.captured_at.replace("Z", "+00:00"))
        held_minutes = (captured - opened).total_seconds() / 60

        if held_minutes >= self.config.maximum_hold_minutes:
            self.store.execute_sell(pos["id"], snapshot, pos["remaining_quantity"],
                                    self.config.estimated_cost_per_side_pct, "first15_max_hold")
            return
        if held_minutes >= self.config.no_progress_exit_minutes and change_pct < self.config.no_progress_min_return_pct:
            self.store.execute_sell(pos["id"], snapshot, pos["remaining_quantity"],
                                    self.config.estimated_cost_per_side_pct, "no_progress_fast_exit")
            return
        if held_minutes >= 1 and snapshot.buy_ratio < .45:
            velocity = micro.price_velocity_pct_per_minute
            if velocity is not None and velocity < 0:
                self.store.execute_sell(pos["id"], snapshot, pos["remaining_quantity"],
                                        self.config.estimated_cost_per_side_pct, "flow_reversal_exit")
                return

        if change_pct >= self.config.first_take_profit_pct and not pos["partial_1_done"]:
            self.store.execute_sell(pos["id"], snapshot, pos["quantity"] * 0.35,
                                    self.config.estimated_cost_per_side_pct, "fast_take_profit_1")
            self.store.update_position_flags(pos["id"], partial_1=True)
            pos = self.store.open_position(snapshot.address)
            if pos is None:
                return
        if change_pct >= self.config.second_take_profit_pct and not pos["partial_2_done"]:
            self.store.execute_sell(pos["id"], snapshot, pos["quantity"] * 0.35,
                                    self.config.estimated_cost_per_side_pct, "fast_take_profit_2")
            self.store.update_position_flags(pos["id"], partial_2=True)
            pos = self.store.open_position(snapshot.address)
            if pos is None:
                return

        trail_price = high * (1 - self.config.trailing_stop_pct / 100)
        if high >= float(pos["entry_price"]) * (1 + self.config.trailing_activation_pct / 100) \
                and snapshot.price_usd <= trail_price:
            self.store.execute_sell(pos["id"], snapshot, pos["remaining_quantity"],
                                    self.config.estimated_cost_per_side_pct, "fast_trailing_stop")
