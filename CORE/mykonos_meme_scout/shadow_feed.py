from __future__ import annotations

import threading
import time
import urllib.parse
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass
from typing import Iterable, Sequence

from .models import TokenSnapshot
from .providers import DexScreenerProvider, GeckoTerminalProvider, ProviderError, _get_json


@dataclass(frozen=True)
class FeedHealth:
    source: str
    complete: bool
    errors: tuple[str, ...] = ()
    dex_requests: int = 0
    gecko_requests: int = 0
    dex_backoff_seconds: float = 0.0

    def as_dict(self) -> dict:
        return {
            "source": self.source,
            "complete": self.complete,
            "errors": list(self.errors),
            "dex_requests": self.dex_requests,
            "gecko_requests": self.gecko_requests,
            "dex_backoff_seconds": round(self.dex_backoff_seconds, 3),
        }


class ShadowBatchWatchProvider:
    """Rate-limit-aware watch feed used only by the real temporal shadow runner.

    The frozen execution provider remains untouched. DEX Screener watch reads are
    batched by token mint (up to 30 mints per public endpoint call) and then
    filtered back to the exact pair pinned at session admission. GeckoTerminal is
    used only as a delayed exact-pool hedge for missing targets.
    """

    dex_base = "https://api.dexscreener.com"

    def __init__(self, gecko: GeckoTerminalProvider | None = None):
        self.gecko = gecko or GeckoTerminalProvider()
        self._lock = threading.Lock()
        self._dex_backoff_until = 0.0
        self._dex_failures = 0

    @staticmethod
    def _normalize(targets: Iterable[tuple[str, str]]) -> list[tuple[str, str]]:
        out: list[tuple[str, str]] = []
        seen: set[str] = set()
        for address, pair in targets:
            address, pair = str(address or ""), str(pair or "")
            if address and address not in seen:
                seen.add(address)
                out.append((address, pair))
        return out

    def _remaining_backoff(self) -> float:
        with self._lock:
            return max(0.0, self._dex_backoff_until - time.monotonic())

    def _record_dex_failure(self, exc: Exception) -> None:
        text = str(exc)
        with self._lock:
            self._dex_failures = min(self._dex_failures + 1, 8)
            if "429" in text:
                delay = min(120.0, 10.0 * (2 ** (self._dex_failures - 1)))
            else:
                delay = min(30.0, 2.0 * self._dex_failures)
            self._dex_backoff_until = max(self._dex_backoff_until, time.monotonic() + delay)

    def _record_dex_success(self) -> None:
        with self._lock:
            self._dex_failures = 0
            self._dex_backoff_until = 0.0

    @staticmethod
    def _select_exact_pairs(
        pairs: Sequence[dict], wanted: Sequence[tuple[str, str]]
    ) -> list[TokenSnapshot]:
        wanted_map = {address: pair for address, pair in wanted}
        exact_rows: list[dict] = []
        fallback_rows: dict[str, list[dict]] = {}
        for row in pairs:
            if row.get("chainId") != "solana":
                continue
            address = str((row.get("baseToken") or {}).get("address") or "")
            if address not in wanted_map:
                continue
            pair = str(row.get("pairAddress") or "")
            pinned = wanted_map[address]
            if pinned:
                if pair == pinned:
                    exact_rows.append(row)
            else:
                fallback_rows.setdefault(address, []).append(row)
        # Missing pair IDs are allowed only at admission edge-cases; choose the
        # most liquid market exactly as the frozen provider does.
        for address, rows in fallback_rows.items():
            if rows:
                exact_rows.extend(rows)
        return DexScreenerProvider._parse_pairs(exact_rows, {}, {})

    def _dex_batch(
        self, wanted: Sequence[tuple[str, str]], timeout: float
    ) -> tuple[list[TokenSnapshot], int]:
        if self._remaining_backoff() > 0:
            raise ProviderError("DEX Screener shadow feed in adaptive backoff")
        addresses = [address for address, _ in wanted]
        rows: list[dict] = []
        chunks = [addresses[start:start + 30] for start in range(0, len(addresses), 30)]
        requests = len(chunks)

        def fetch_chunk(chunk: list[str]):
            return _get_json(
                f"{self.dex_base}/tokens/v1/solana/{','.join(chunk)}",
                timeout=timeout,
            )

        try:
            # v0.8.9: preserve the single-request path (including deterministic
            # 429/backoff semantics). Only fan out when >30 active tokens need
            # more than one HTTP request.
            if len(chunks) == 1:
                payloads = [fetch_chunk(chunks[0])]
            else:
                with ThreadPoolExecutor(max_workers=min(4, len(chunks)), thread_name_prefix="mykonos-dex-batch") as pool:
                    futures = [pool.submit(fetch_chunk, chunk) for chunk in chunks]
                    payloads = [future.result() for future in futures]
            for payload in payloads:
                if isinstance(payload, list):
                    rows.extend(payload)
                elif isinstance(payload, dict):
                    rows.extend(payload.get("pairs") or [])
        except Exception as exc:
            self._record_dex_failure(exc)
            if isinstance(exc, ProviderError):
                raise
            raise ProviderError(str(exc)) from exc
        self._record_dex_success()
        return self._select_exact_pairs(rows, wanted), requests

    def fetch_watch_targets(
        self,
        targets: Iterable[tuple[str, str]],
        *,
        dex_timeout: float = 1.25,
        gecko_timeout: float = 1.0,
        hedge_delay: float = 0.65,
        hard_deadline: float = 1.8,
    ) -> tuple[list[TokenSnapshot], dict]:
        wanted = self._normalize(targets)
        if not wanted:
            return [], FeedHealth("none", True).as_dict()
        wanted_addresses = {address for address, _ in wanted}
        cancel_fallback = threading.Event()
        errors: list[str] = []
        partial: dict[str, TokenSnapshot] = {}
        counts = {"dex": 0, "gecko": 0}

        def primary() -> tuple[str, list[TokenSnapshot], int]:
            items, reqs = self._dex_batch(wanted, dex_timeout)
            return "dexscreener_batch", items, reqs

        def fallback() -> tuple[str, list[TokenSnapshot], int]:
            if cancel_fallback.wait(max(0.0, hedge_delay)):
                return "geckoterminal_cancelled", [], 0
            counts["gecko"] += 1
            return "geckoterminal", self.gecko.fetch_watch_targets(wanted, timeout=gecko_timeout), 1

        executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="mykonos-shadow-feed")
        futures = {executor.submit(primary), executor.submit(fallback)}
        started = time.monotonic()
        try:
            pending = set(futures)
            while pending:
                remaining = hard_deadline - (time.monotonic() - started)
                if remaining <= 0:
                    errors.append("shadow_watch_hard_deadline_exceeded")
                    break
                done, pending = wait(pending, timeout=remaining, return_when=FIRST_COMPLETED)
                if not done:
                    errors.append("shadow_watch_hard_deadline_exceeded")
                    break
                for future in done:
                    try:
                        source, items, reqs = future.result()
                    except Exception as exc:
                        errors.append(str(exc))
                        continue
                    if source == "geckoterminal_cancelled":
                        continue
                    if source == "dexscreener_batch":
                        counts["dex"] += reqs
                    for item in items:
                        if item.address in wanted_addresses and item.price_usd > 0 and not item.market_data_stale:
                            partial[item.address] = item
                    if wanted_addresses.issubset(partial):
                        cancel_fallback.set()
                        return [partial[a] for a in wanted_addresses], FeedHealth(
                            source=source,
                            complete=True,
                            errors=tuple(errors),
                            dex_requests=counts["dex"],
                            gecko_requests=counts["gecko"],
                            dex_backoff_seconds=self._remaining_backoff(),
                        ).as_dict()
            cancel_fallback.set()
            return list(partial.values()), FeedHealth(
                source="partial" if partial else "unavailable",
                complete=wanted_addresses.issubset(partial),
                errors=tuple(errors),
                dex_requests=counts["dex"],
                gecko_requests=counts["gecko"],
                dex_backoff_seconds=self._remaining_backoff(),
            ).as_dict()
        finally:
            executor.shutdown(wait=False, cancel_futures=True)


class ShadowDiscoveryCoordinator:
    """Rolling discovery with cache and adaptive DexScreener backoff.

    GeckoTerminal remains the primary fresh-pool source. DEX Screener augments it
    when available but stops hammering its public profile/boost endpoints after
    a 429. Cached candidates can bridge temporary discovery outages.
    """

    def __init__(self, gecko: GeckoTerminalProvider, dex: DexScreenerProvider):
        self.gecko = gecko
        self.dex = dex
        self._dex_backoff_until = 0.0
        self._dex_failures = 0
        self._cache: dict[str, TokenSnapshot] = {}

    def discover(
        self,
        maximum: int,
        max_age_minutes: float,
        *,
        request_timeout: float,
        hard_deadline: float,
    ) -> tuple[list[TokenSnapshot], dict]:
        errors: list[str] = []
        now = time.monotonic()
        candidates: dict[str, TokenSnapshot] = {}
        try:
            for item in self.gecko.discover(
                maximum,
                max_age_minutes,
                request_timeout=request_timeout,
                hard_deadline=hard_deadline,
            ):
                candidates[item.address] = item
        except ProviderError as exc:
            errors.append(f"gecko:{exc}")

        dex_attempted = False
        if now >= self._dex_backoff_until:
            dex_attempted = True
            try:
                for item in self.dex.discover(
                    maximum,
                    request_timeout=request_timeout,
                    hard_deadline=hard_deadline,
                ):
                    if item.age_minutes <= max_age_minutes:
                        candidates[item.address] = item
                self._dex_failures = 0
                self._dex_backoff_until = 0.0
            except ProviderError as exc:
                text = str(exc)
                errors.append(f"dex:{text}")
                self._dex_failures = min(self._dex_failures + 1, 8)
                delay = min(180.0, 15.0 * (2 ** (self._dex_failures - 1))) if "429" in text else 20.0
                self._dex_backoff_until = now + delay

        # Retain only genuinely fresh cached candidates. Cached snapshots are
        # admission hints; the first watch tick still has to arrive from a real feed.
        for address, item in list(self._cache.items()):
            if item.age_minutes <= max_age_minutes:
                candidates.setdefault(address, item)
            else:
                self._cache.pop(address, None)
        self._cache.update(candidates)
        ordered = sorted(candidates.values(), key=lambda x: (x.age_minutes, -x.liquidity_usd))[:maximum]
        return ordered, {
            "errors": errors,
            "dex_attempted": dex_attempted,
            "dex_backoff_seconds": round(max(0.0, self._dex_backoff_until - time.monotonic()), 3),
            "candidates": len(ordered),
        }
