from __future__ import annotations

import json
import math
import os
import time
import threading
import http.client
import socket
import importlib.util
from pathlib import Path
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, as_completed, wait
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any, Iterable

from .models import SecuritySignals, TokenSnapshot


USER_AGENT = "Mykonos-Meme-Scout/0.4.1 (paper-only continual-neural research)"


class ProviderError(RuntimeError):
    pass


def _get_json(url: str, headers: dict[str, str] | None = None, timeout: float = 4.0,
              *, deadline: float | None = None, max_attempts: int = 3,
              on_http_error=None) -> Any:
    """GET JSON with bounded retries for transient transport/provider failures.

    Permanent 4xx responses fail immediately. 429/408 and common 5xx responses
    retry with Retry-After awareness so a short upstream outage cannot silently
    remove market data from IMC.
    """
    merged = {"Accept": "application/json", "User-Agent": USER_AGENT, "Connection": "close"}
    merged.update(headers or {})
    last: Exception | None = None
    for attempt in range(max_attempts):
        remaining = None if deadline is None else deadline - time.monotonic()
        if remaining is not None and remaining <= 0:
            raise ProviderError('GET observation deadline exceeded')
        if url.startswith('https://api.dexscreener.com/'):
            # Shared ceiling below the public token endpoint's 300/minute rate.
            delay = GoPlusProvider._rate_budget().reserve('dex_public', .21, wait=True,
                max_wait=min(.65, max(0, remaining or .65)), timeout=.05)
            if delay:
                raise ProviderError('DEX public rate budget unavailable')
            remaining = None if deadline is None else deadline-time.monotonic()
            if remaining is not None and remaining <= 0:
                raise ProviderError('GET observation deadline exceeded')
        req = urllib.request.Request(url, headers=merged)
        try:
            with urllib.request.urlopen(req, timeout=timeout if remaining is None else min(timeout, remaining)) as response:
                value = json.loads(response.read().decode("utf-8"))
                if deadline is not None and time.monotonic() >= deadline:
                    raise ProviderError('GET observation deadline exceeded')
                return value
        except urllib.error.HTTPError as exc:
            last = exc
            if on_http_error is not None:
                on_http_error(exc)
            if exc.code not in (408, 429, 500, 502, 503, 504) or attempt >= max_attempts - 1:
                break
            retry_after = exc.headers.get("Retry-After") if exc.headers else None
            try:
                delay = min(4.0, max(0.2, float(retry_after)))
            except (TypeError, ValueError):
                delay = (1.0 if exc.code == 429 else 0.25) * (attempt + 1)
            if deadline is not None:
                delay = min(delay, max(0, deadline-time.monotonic()))
            time.sleep(delay)
        except (urllib.error.URLError, TimeoutError, socket.timeout,
                ConnectionResetError, BrokenPipeError,
                http.client.RemoteDisconnected) as exc:
            last = exc
            if attempt >= max_attempts - 1:
                break
            delay = 0.15 * (attempt + 1)
            if deadline is not None:
                delay = min(delay, max(0, deadline-time.monotonic()))
            time.sleep(delay)
        except json.JSONDecodeError as exc:
            raise ProviderError(f"Invalid JSON from {url}: {exc}") from exc
    raise ProviderError(f"GET failed after retries for {url}: {last}") from last


def _as_list(value: Any) -> list:
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        return [value]
    return []


def _number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _integer(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _flag(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if value in (1, "1", "true", "True", "yes", "YES"):
        return True
    if value in (0, "0", "false", "False", "no", "NO"):
        return False
    return None


def _status_flag(value: Any) -> bool | None:
    if isinstance(value, dict):
        return _flag(value.get("status"))
    return _flag(value)


def _fraction_to_pct(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    parsed = _number(value, -1)
    if not math.isfinite(parsed) or not 0 <= parsed <= 100:
        return None
    return parsed * 100 if parsed <= 1 else parsed


def _iso_to_ms(value: Any) -> int:
    if isinstance(value, (int, float)):
        # tolerate seconds and milliseconds
        return int(value if value > 10_000_000_000 else value * 1000)
    if not value:
        return 0
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return int(dt.timestamp() * 1000)
    except ValueError:
        return 0


class GeckoTerminalProvider:
    """Fresh-pool discovery feed with process-wide throttling/backoff.

    The free public endpoint is not polled every scan cycle. Fresh-pool discovery
    only needs periodic refresh; tracked addresses are refreshed through DEX
    Screener between those calls. A cached pool list also keeps the scout working
    through temporary HTTP 429 responses.
    """

    base = "https://api.geckoterminal.com/api/v2"
    wrapped_sol = "So11111111111111111111111111111111111111112"
    usdc = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
    core_quotes = {wrapped_sol, usdc}
    minimum_refresh_seconds = 10.0
    _cached: list[TokenSnapshot] = []
    _last_attempt_monotonic: float = 0.0
    _last_success_monotonic: float = 0.0
    _empty_verified = False
    _backoff_until_monotonic: float = 0.0
    # When GeckoTerminal is actively protecting an open position as the
    # independent watcher fallback, discovery network calls are temporarily
    # suppressed so the public 30-calls/minute budget is reserved for safety.
    _watch_fallback_active_until_monotonic: float = 0.0
    _discovery_slots = threading.BoundedSemaphore(1)

    @classmethod
    def _cached_fresh(cls, maximum: int, max_age_minutes: float) -> list[TokenSnapshot]:
        now_iso = datetime.now(timezone.utc).isoformat()
        updated = [replace(s, captured_at=now_iso, market_data_stale=True) for s in cls._cached]
        items = [s for s in updated if s.age_minutes <= max_age_minutes]
        items.sort(key=lambda s: s.pair_created_at_ms, reverse=True)
        return items[:maximum]

    def discover(
        self, maximum: int = 30, max_age_minutes: float = 20.0, *,
        request_timeout: float = 1.5, hard_deadline: float = 2.0,
    ) -> list[TokenSnapshot]:
        now = time.monotonic()
        self.last_observation = {'state': 'UNKNOWN', 'network_success': False}
        if now < self._watch_fallback_active_until_monotonic:
            self.last_observation['state'] = 'WATCH_PRIORITY_CACHE'
            # Safety traffic has priority over new-pool discovery. Do not spend
            # another Gecko public-API request while the watcher is degraded.
            return self._cached_fresh(maximum, max_age_minutes) if self._cached else []
        if now < self._backoff_until_monotonic:
            self.last_observation['state'] = 'BACKOFF_CACHE'
            return self._cached_fresh(maximum, max_age_minutes) if self._cached else []
        if self._last_success_monotonic and 0 <= now-self._last_success_monotonic < self.minimum_refresh_seconds:
            self.last_observation['state'] = 'CACHE'
            if not self._cached and self._empty_verified:
                self.last_observation.update(state='CACHED_EMPTY',empty_verified=True,
                    empty_age_seconds=now-self._last_success_monotonic)
            return self._cached_fresh(maximum, max_age_minutes)

        type(self)._last_attempt_monotonic = now
        if not self._discovery_slots.acquire(blocking=False):
            if self._cached:
                return self._cached_fresh(maximum, max_age_minutes)
            raise ProviderError('Gecko outstanding discovery limit')
        executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="mykonos-gecko-discovery")
        try:
            future = executor.submit(
                _get_json,
                f"{self.base}/networks/solana/new_pools?page=1&include=base_token,quote_token,dex",
                None, request_timeout, deadline=now+max(0.05, hard_deadline),
            )
        except Exception:
            self._discovery_slots.release()
            executor.shutdown(wait=False, cancel_futures=True)
            raise
        future.add_done_callback(lambda done, slots=self._discovery_slots: slots.release())
        payload = None
        failure: Exception | None = None
        try:
            done, _ = wait({future}, timeout=max(0.05, hard_deadline))
            if not done:
                failure = ProviderError("GeckoTerminal discovery hard deadline exceeded")
            else:
                try:
                    payload = future.result()
                except Exception as exc:
                    failure = exc
        finally:
            # A TLS handshake can outlive urllib's timeout on Windows. Never let
            # that worker extend the scanner cycle; it may finish in background.
            executor.shutdown(wait=False, cancel_futures=True)

        if failure is not None:
            type(self)._backoff_until_monotonic = now + (90.0 if "429" in str(failure) else 20.0)
            if self._cached:
                return self._cached_fresh(maximum, max_age_minutes)
            if isinstance(failure, ProviderError):
                raise failure
            raise ProviderError(f"GeckoTerminal discovery failed: {failure}") from failure

        if not isinstance(payload, dict) or not isinstance(payload.get('data'), list):
            raise ProviderError('Gecko invalid discovery schema')
        snapshots = self._parse_new_pools(payload)
        parsed_empty_unverified = bool(payload['data']) and not snapshots
        self.last_observation = {'state': 'DEGRADED' if parsed_empty_unverified else 'FRESH',
                                 'network_success': not parsed_empty_unverified,
                                 'raw_count': len(payload['data'])}
        type(self)._cached = snapshots
        type(self)._empty_verified = not payload['data']
        type(self)._last_success_monotonic = time.monotonic()
        type(self)._backoff_until_monotonic = 0.0
        # A just-fetched Gecko response is current market data and can stand on
        # its own when DEX Screener is unavailable. Only reused cache entries are
        # marked stale by _cached_fresh().
        items = [s for s in snapshots if s.age_minutes <= max_age_minutes]
        items.sort(key=lambda s: s.pair_created_at_ms, reverse=True)
        return items[:maximum]

    @classmethod
    def reserve_for_watch_fallback(cls, seconds: float = 10.0) -> None:
        cls._watch_fallback_active_until_monotonic = max(
            cls._watch_fallback_active_until_monotonic, time.monotonic() + max(0.0, seconds)
        )

    def fetch_watch_targets(
        self, targets: Iterable[tuple[str, str]], timeout: float = 1.0,
        *, deadline: float | None = None,
    ) -> list[TokenSnapshot]:
        """Independent exact-pool fallback for open-position monitoring.

        Uses one GeckoTerminal multi-pool request for every entry pair currently
        open. Tokens without a stored pair are intentionally not guessed here;
        exact market identity is more important than returning a wrong price.
        """
        normalized = [(str(a or ""), str(p or "")) for a, p in targets if a and p]
        if not normalized:
            return []
        pair_addresses = list(dict.fromkeys(pair for _, pair in normalized))
        self.reserve_for_watch_fallback()
        quoted = ",".join(urllib.parse.quote(pair, safe="") for pair in pair_addresses)
        payload = _get_json(
            f"{self.base}/networks/solana/pools/multi/{quoted}?include=base_token,quote_token",
            timeout=timeout,
            deadline=deadline,
        )
        parsed = self._parse_new_pools(payload if isinstance(payload, dict) else {})
        wanted = {(address, pair) for address, pair in normalized}
        return [item for item in parsed if (item.address, item.pair_address) in wanted]

    @classmethod
    def _parse_new_pools(cls, payload: dict) -> list[TokenSnapshot]:
        included = {str(item.get("id")): item for item in _as_list(payload.get("included")) if item.get("id")}
        snapshots: list[TokenSnapshot] = []
        for pool in _as_list(payload.get("data")):
            attrs = pool.get("attributes") or {}
            rel = pool.get("relationships") or {}
            base_id = str((((rel.get("base_token") or {}).get("data") or {}).get("id")) or "")
            quote_id = str((((rel.get("quote_token") or {}).get("data") or {}).get("id")) or "")
            base_addr = cls._token_address(base_id)
            quote_addr = cls._token_address(quote_id)
            if not base_addr and not quote_addr:
                continue
            if base_addr in cls.core_quotes and quote_addr and quote_addr not in cls.core_quotes:
                side, token_id, address = "quote", quote_id, quote_addr
            else:
                side, token_id, address = "base", base_id, base_addr
            if not address or address in cls.core_quotes:
                continue

            token = included.get(token_id) or {}
            token_attrs = token.get("attributes") or {}
            created_ms = _iso_to_ms(attrs.get("pool_created_at"))
            if created_ms <= 0:
                continue
            tx = attrs.get("transactions") or {}
            tx5 = tx.get("m5") or {}
            volume = attrs.get("volume_usd") or {}
            change = attrs.get("price_change_percentage") or {}
            dex_id = str((((rel.get("dex") or {}).get("data") or {}).get("id")) or "geckoterminal")
            symbol = str(token_attrs.get("symbol") or token_attrs.get("name") or "?")
            name = str(token_attrs.get("name") or symbol)
            snapshots.append(TokenSnapshot(
                address=address,
                symbol=symbol,
                name=name,
                pair_address=str(attrs.get("address") or str(pool.get("id") or "").split("_", 1)[-1]),
                dex_id=dex_id,
                price_usd=_number(attrs.get(f"{side}_token_price_usd")),
                liquidity_usd=_number(attrs.get("reserve_in_usd")),
                market_cap_usd=_number(attrs.get("market_cap_usd")) or _number(attrs.get("fdv_usd")),
                volume_5m_usd=_number(volume.get("m5")),
                volume_1h_usd=_number(volume.get("h1")),
                buys_5m=_integer(tx5.get("buys")),
                sells_5m=_integer(tx5.get("sells")),
                price_change_5m_pct=_number(change.get("m5")),
                pair_created_at_ms=created_ms,
                discovery_source="geckoterminal_new_pool",
            ))
        return snapshots

    @staticmethod
    def _token_address(token_id: str) -> str:
        return token_id.split("_", 1)[1] if token_id.startswith("solana_") else token_id


class DexScreenerProvider:
    base = "https://api.dexscreener.com"
    _discovery_slots = threading.BoundedSemaphore(4)
    _discovery_cache = None
    _discovery_cached_at = 0.0

    @classmethod
    def _submit_discovery(cls, executor, url, timeout, deadline):
        if not cls._discovery_slots.acquire(blocking=False):
            raise ProviderError('DEX outstanding discovery limit')
        try:
            future = executor.submit(_get_json, url, None, timeout, deadline=deadline)
        except Exception:
            cls._discovery_slots.release()
            raise
        future.add_done_callback(lambda done, slots=cls._discovery_slots: slots.release())
        return future

    def discover(
        self, maximum: int = 60, *, request_timeout: float = 1.5, hard_deadline: float = 2.5
    ) -> list[TokenSnapshot]:
        # Total deadline covers both profile/boost discovery and the first pair
        # snapshot. A stuck TLS worker is detached instead of extending the scan.
        started = time.monotonic()
        self.last_observation = {'state': 'UNKNOWN', 'network_success': False}
        deadline = started+max(0.05, hard_deadline)
        cached=type(self)._discovery_cache
        if cached is not None and 0<=started-type(self)._discovery_cached_at<10:
            profiles, boosts = cached
            addresses=list(dict.fromkeys([*profiles,*boosts]))[:maximum]
            if not addresses:
                self.last_observation={'state':'CACHED_EMPTY','network_success':False,
                    'empty_verified':True,'empty_age_seconds':started-type(self)._discovery_cached_at}
                return []
            rows=self.fetch_addresses(addresses,profiles,boosts,request_timeout=request_timeout,
                                      hard_deadline=hard_deadline,deadline=deadline)
            self.last_observation={'state':'DISCOVERY_CACHE_MARKET_REFRESH',
                'network_success':bool(addresses) and self.refresh_observation.get('network_success') is True}
            return rows
        urls = {
            "profiles": f"{self.base}/token-profiles/latest/v1",
            "boosts": f"{self.base}/token-boosts/latest/v1",
        }
        payloads: dict[str, list] = {"profiles": [], "boosts": []}
        errors: list[Exception] = []
        executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="mykonos-dex-discovery")
        futures = {}
        for name, url in urls.items():
            try:
                futures[self._submit_discovery(executor, url, request_timeout, deadline)] = name
            except ProviderError as exc:
                errors.append(exc)
        try:
            done, pending = wait(set(futures), timeout=max(0.05, hard_deadline))
            for future in done:
                name = futures[future]
                try:
                    raw = future.result()
                    if not isinstance(raw, list):
                        raise ProviderError('DEX invalid discovery schema')
                    if any(not isinstance(item,dict) or not item.get('chainId')
                           or not item.get('tokenAddress') for item in raw):
                        raise ProviderError('DEX invalid discovery row')
                    payloads[name] = raw
                except Exception as exc:
                    errors.append(exc)
            if pending:
                errors.append(ProviderError("DEX Screener discovery hard deadline exceeded"))
        finally:
            executor.shutdown(wait=False, cancel_futures=True)
        if len(errors) >= 2 and not payloads["profiles"] and not payloads["boosts"]:
            raise ProviderError(f"DEX Screener discovery failed: {errors[0]}")
        self.last_observation = {'state': 'DEGRADED' if errors else 'FRESH',
                                 'network_success': not errors, 'errors': len(errors)}

        profiles = payloads["profiles"]
        boosts = payloads["boosts"]
        profile_addresses = {
            str(item.get("tokenAddress")): item
            for item in profiles if item.get("chainId") == "solana" and item.get("tokenAddress")
        }
        boost_by_address: dict[str, float] = {}
        for item in boosts:
            if item.get("chainId") == "solana" and item.get("tokenAddress"):
                address = str(item["tokenAddress"])
                boost_by_address[address] = boost_by_address.get(address, 0) + _number(
                    item.get("amount", item.get("totalAmount", 0))
                )
        if not errors:
            type(self)._discovery_cache=(profile_addresses,boost_by_address)
            type(self)._discovery_cached_at=started
        addresses = list(dict.fromkeys([*profile_addresses, *boost_by_address]))[:maximum]
        remaining = max(0.05, hard_deadline - (time.monotonic() - started))
        if not addresses or remaining <= 0.05:
            return []
        return self.fetch_addresses(
            addresses, profile_addresses, boost_by_address,
            request_timeout=request_timeout, hard_deadline=remaining, deadline=deadline,
        )

    def fetch_addresses(
        self, addresses: Iterable[str], profile_addresses: dict | None = None,
        boost_by_address: dict[str, float] | None = None, *,
        request_timeout: float = 1.5, hard_deadline: float = 2.5,
        deadline: float | None = None,
    ) -> list[TokenSnapshot]:
        self.refresh_observation = {'state': 'UNKNOWN', 'network_success': False}
        unique = list(dict.fromkeys(str(a) for a in addresses if a))
        snapshots: list[TokenSnapshot] = []
        chunks = [unique[start:start + 30] for start in range(0, len(unique), 30)]
        if not chunks:
            return snapshots
        errors: list[Exception] = []
        deadline = deadline if deadline is not None else time.monotonic()+max(0.05, hard_deadline)
        executor = ThreadPoolExecutor(
            max_workers=min(4, len(chunks)), thread_name_prefix="mykonos-dex-refresh"
        )
        futures = {}
        for chunk in chunks:
            try:
                futures[self._submit_discovery(executor,
                    f"{self.base}/tokens/v1/solana/{','.join(chunk)}", request_timeout, deadline)] = chunk
            except ProviderError as exc:
                errors.append(exc)
        try:
            done, pending = wait(set(futures), timeout=max(0.0, deadline-time.monotonic()))
            for future in done:
                try:
                    payload = future.result()
                except Exception as exc:
                    errors.append(exc)
                    continue
                pairs = payload if isinstance(payload, list) else (
                    payload.get('pairs') if isinstance(payload, dict) else None)
                if not isinstance(pairs, list):
                    errors.append(ProviderError('DEX invalid market schema'))
                    continue
                snapshots.extend(self._parse_pairs(pairs, profile_addresses or {}, boost_by_address or {}))
            if pending:
                errors.append(ProviderError("DEX Screener refresh hard deadline exceeded"))
        finally:
            executor.shutdown(wait=False, cancel_futures=True)
        if errors and not snapshots:
            raise ProviderError(f"DEX Screener refresh failed: {errors[0]}")
        self.refresh_observation = {'state': 'DEGRADED' if errors else 'FRESH',
                                    'network_success': not errors, 'rows': len(snapshots)}
        return snapshots

    _watch_slots = threading.BoundedSemaphore(6)

    @classmethod
    def _watch_json(cls, url, timeout, deadline):
        if time.monotonic() >= deadline:
            raise ProviderError('watch_queue_deadline')
        if not cls._watch_slots.acquire(blocking=False):
            raise ProviderError('watch_http_capacity')
        try:
            return _get_json(url, timeout=timeout, deadline=deadline)
        finally:
            cls._watch_slots.release()

    def fetch_watch_targets(self, targets: Iterable[tuple[str, str]], timeout: float = 1.25,
                            *, deadline: float | None = None) -> list[TokenSnapshot]:
        """Low-latency refresh for already-open positions.

        Prefer the exact pair captured at entry so the watcher cannot silently
        jump to another market for the same mint. Only targets without a known
        pair fall back to the mint endpoint. No discovery/security calls occur
        on this path.
        """
        unique: list[tuple[str, str]] = []
        deadline = deadline if deadline is not None else time.monotonic()+timeout
        seen: set[str] = set()
        for address, pair_address in targets:
            address = str(address or "")
            pair_address = str(pair_address or "")
            if not address or address in seen:
                continue
            seen.add(address)
            unique.append((address, pair_address))
        if not unique:
            return []

        snapshots: list[TokenSnapshot] = []
        errors: list[Exception] = []
        paired = [(address, pair) for address, pair in unique if pair]
        missing_pair = [address for address, pair in unique if not pair]

        if paired:
            with ThreadPoolExecutor(max_workers=min(3, len(paired))) as pool:
                futures = {
                    pool.submit(
                        self._watch_json,
                        f"{self.base}/latest/dex/pairs/solana/{urllib.parse.quote(pair, safe='')}",
                        timeout,
                        deadline,
                    ): (address, pair)
                    for address, pair in paired
                }
                for future in as_completed(futures):
                    address, pair = futures[future]
                    try:
                        payload = future.result()
                    except ProviderError as exc:
                        errors.append(exc)
                        continue
                    pairs = payload if isinstance(payload, list) else (payload.get("pairs") or [])
                    exact = [
                        item for item in pairs
                        if str(item.get("pairAddress") or "") == pair
                        and str((item.get("baseToken") or {}).get("address") or "") == address
                    ]
                    snapshots.extend(self._parse_pairs(exact, {}, {}))

        if missing_pair:
            # At most three open positions are allowed, so one direct mint batch is enough.
            try:
                payload = self._watch_json(
                    f"{self.base}/tokens/v1/solana/{','.join(missing_pair)}",
                    timeout, deadline,
                )
                pairs = payload if isinstance(payload, list) else (payload.get("pairs") or [])
                parsed = self._parse_pairs(pairs, {}, {})
                wanted = set(missing_pair)
                snapshots.extend(item for item in parsed if item.address in wanted)
            except ProviderError as exc:
                errors.append(exc)

        if errors and not snapshots:
            raise ProviderError(f"DEX Screener fast watch refresh failed: {errors[0]}")
        return snapshots

    @staticmethod
    def _parse_pairs(pairs: Iterable[dict], profile_addresses: dict,
                     boost_by_address: dict[str, float]) -> list[TokenSnapshot]:
        best: dict[str, dict] = {}
        for pair in pairs:
            if pair.get("chainId") != "solana":
                continue
            address = str((pair.get("baseToken") or {}).get("address", ""))
            if not address:
                continue
            current_liq = _number((pair.get("liquidity") or {}).get("usd"))
            prior_liq = _number((best.get(address, {}).get("liquidity") or {}).get("usd"))
            if address not in best or current_liq > prior_liq:
                best[address] = pair
        snapshots: list[TokenSnapshot] = []
        for address, pair in best.items():
            base = pair.get("baseToken") or {}
            tx5 = (pair.get("txns") or {}).get("m5") or {}
            volume = pair.get("volume") or {}
            change = pair.get("priceChange") or {}
            market_cap = _number(pair.get("marketCap")) or _number(pair.get("fdv"))
            snapshots.append(TokenSnapshot(
                address=address,
                symbol=str(base.get("symbol") or "?"), name=str(base.get("name") or "Unknown"),
                pair_address=str(pair.get("pairAddress") or ""), dex_id=str(pair.get("dexId") or "unknown"),
                price_usd=_number(pair.get("priceUsd")),
                liquidity_usd=_number((pair.get("liquidity") or {}).get("usd")), market_cap_usd=market_cap,
                volume_5m_usd=_number(volume.get("m5")), volume_1h_usd=_number(volume.get("h1")),
                buys_5m=_integer(tx5.get("buys")), sells_5m=_integer(tx5.get("sells")),
                price_change_5m_pct=_number(change.get("m5")),
                pair_created_at_ms=_integer(pair.get("pairCreatedAt"), 0),
                profile_present=address in profile_addresses, boost_amount=boost_by_address.get(address, 0.0),
                discovery_source="dexscreener",
            ))
        return snapshots


class ResilientWatchProvider:
    """Hedged, deadline-bounded market feed for already-open positions.

    DEX Screener starts immediately. If it has not completed quickly, an
    independent GeckoTerminal exact-pool request is launched. The caller gets
    the first complete result without waiting for a stuck TLS handshake.
    """

    _slots = threading.BoundedSemaphore(4)

    def __init__(self, dex: DexScreenerProvider | None = None, gecko: GeckoTerminalProvider | None = None):
        self.dex = dex or DexScreenerProvider()
        self.gecko = gecko or GeckoTerminalProvider()

    @staticmethod
    def _normalize_targets(targets: Iterable[tuple[str, str]]) -> list[tuple[str, str]]:
        out: list[tuple[str, str]] = []
        seen: set[str] = set()
        for address, pair in targets:
            address, pair = str(address or ""), str(pair or "")
            if address and address not in seen:
                seen.add(address)
                out.append((address, pair))
        return out

    def fetch_watch_targets(
        self, targets: Iterable[tuple[str, str]], *, dex_timeout: float = 1.25,
        gecko_timeout: float = 1.0, hedge_delay: float = 0.65, hard_deadline: float = 1.8,
    ) -> tuple[list[TokenSnapshot], dict]:
        wanted = self._normalize_targets(targets)
        if not wanted:
            return [], {"source": "none", "complete": True, "errors": []}
        wanted_addresses = {address for address, _ in wanted}
        cancel_fallback = threading.Event()
        executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="mykonos-watch-feed")
        errors: list[str] = []
        partial: dict[str, TokenSnapshot] = {}
        started = time.monotonic()
        deadline = started+hard_deadline

        def _primary() -> tuple[str, list[TokenSnapshot]]:
            return "dexscreener", self.dex.fetch_watch_targets(wanted, timeout=dex_timeout, deadline=deadline)

        def _fallback() -> tuple[str, list[TokenSnapshot]]:
            if cancel_fallback.wait(max(0.0, hedge_delay)):
                return "geckoterminal_cancelled", []
            return "geckoterminal", self.gecko.fetch_watch_targets(wanted, timeout=gecko_timeout, deadline=deadline)

        futures = set()
        for action in (_primary, _fallback):
            if not self._slots.acquire(blocking=False):
                errors.append('watch_outstanding_capacity')
                continue
            try:
                future = executor.submit(action)
            except Exception:
                self._slots.release()
                executor.shutdown(wait=False, cancel_futures=True)
                raise
            future.add_done_callback(lambda done, slots=self._slots: slots.release())
            futures.add(future)
        try:
            pending = set(futures)
            while pending:
                remaining = hard_deadline - (time.monotonic() - started)
                if remaining <= 0:
                    errors.append("watch_hard_deadline_exceeded")
                    break
                done, pending = wait(pending, timeout=remaining, return_when=FIRST_COMPLETED)
                if not done:
                    errors.append("watch_hard_deadline_exceeded")
                    break
                for future in done:
                    try:
                        source, items = future.result()
                    except Exception as exc:
                        errors.append(str(exc))
                        continue
                    if source == "geckoterminal_cancelled":
                        continue
                    for item in items:
                        if item.address in wanted_addresses and item.price_usd > 0 and not item.market_data_stale:
                            partial[item.address] = item
                    if wanted_addresses.issubset(partial):
                        cancel_fallback.set()
                        return [partial[a] for a in wanted_addresses], {
                            "source": source, "complete": True, "errors": errors
                        }
            cancel_fallback.set()
            return list(partial.values()), {
                "source": "partial" if partial else "unavailable",
                "complete": wanted_addresses.issubset(partial),
                "errors": errors,
            }
        finally:
            # Never let a stuck urllib TLS handshake extend the watcher deadline.
            # Outstanding worker threads may finish later, but they no longer block
            # the safety loop or new-entry fail-closed state.
            executor.shutdown(wait=False, cancel_futures=True)


class GoPlusProvider:
    base = "https://api.gopluslabs.io/api/v1/solana/token_security"
    # Shared across provider instances: abandoned reads retain their slot until
    # they actually finish. A deadline rejects evidence; it cannot kill a thread.
    _slots = threading.BoundedSemaphore(4)
    _state_lock = threading.Lock()
    _cache: dict = {}
    _attempted: dict = {}
    _budget = None
    cache_seconds = 5.0

    @classmethod
    def _rate_budget(cls):
        with cls._state_lock:
            if cls._budget is None:
                path = Path(__file__).resolve().parents[2] / 'RUNTIME/imc_resilience.py'
                spec = importlib.util.spec_from_file_location('imc_provider_budget', path)
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                cls._budget = module
            return cls._budget

    @classmethod
    def _reserve(cls):
        return cls._rate_budget().reserve('goplus_public', 2.1, wait=False, timeout=.05)

    @classmethod
    def _http_error(cls, error):
        if error.code != 429:
            return
        try:
            seconds = float(error.headers.get('Retry-After', '60'))
            if not math.isfinite(seconds) or seconds < 0:
                raise ValueError('invalid_retry_after')
        except (ValueError, TypeError, AttributeError):
            seconds = 60.0
        cls._rate_budget().backoff('goplus_public', max(2.1, seconds),
                                  'HTTP_429', timeout=.05)

    @staticmethod
    def _observe(url, request_timeout, deadline):
        started = datetime.now(timezone.utc)
        begin = time.monotonic()
        if begin >= deadline:
            raise ProviderError('GoPlus queue deadline exceeded')
        if GoPlusProvider._reserve() > 0:
            raise ProviderError('GoPlus rate budget unavailable')
        if time.monotonic() >= deadline:
            raise ProviderError('GoPlus queue deadline exceeded')
        # Public Solana responses currently cover one mint, not a 20-mint batch.
        # Retries would consume unreserved HTTP starts; retry on a later scan.
        payload = _get_json(url, None, request_timeout, deadline=deadline,
                            max_attempts=1, on_http_error=GoPlusProvider._http_error)
        end = time.monotonic()
        finished = datetime.now(timezone.utc)
        elapsed = end-begin
        if end >= deadline or elapsed < 0 or abs((finished-started).total_seconds()-elapsed) > .5:
            raise ProviderError('GoPlus observation late or clock changed')
        if (not isinstance(payload, dict) or type(payload.get('code')) is not int
                or payload['code'] != 1 or not isinstance(payload.get('result'), dict)):
            raise ProviderError('GoPlus invalid success envelope')
        return payload['result'], started.isoformat(), end, begin

    def enrich(
        self, snapshots: Iterable[TokenSnapshot], *,
        request_timeout: float = 1.5, hard_deadline: float = 2.0,
        priority_addresses: Iterable[str] | None = None,
    ) -> list[TokenSnapshot]:
        deadline = time.monotonic() + max(0.05, hard_deadline)
        items = list(snapshots)
        if not items:
            return items
        by_address = {item.address: replace(item, security=SecuritySignals()) for item in items}
        self.last_observation = {'http_success': 0, 'mint_hits': 0, 'cache_hits': 0,
                                 'requested': 0, 'unknown': {}, 'cache_max_age_seconds': 0.0}
        now = datetime.now(timezone.utc)
        pending = []
        with self._state_lock:
            for item in items:
                cached = self._cache.get(item.address)
                age = None
                if cached:
                    try:
                        age = (now-datetime.fromisoformat(cached[0].checked_at)).total_seconds()
                        elapsed = time.monotonic()-cached[1]
                        valid = (0 <= age <= self.cache_seconds and 0 <= elapsed <= self.cache_seconds
                                 and abs(age-elapsed) <= .5)
                    except (ValueError, TypeError):
                        valid = False
                    if valid:
                        by_address[item.address] = replace(item, security=cached[0])
                        self.last_observation['cache_hits'] += 1
                        self.last_observation['cache_max_age_seconds'] = max(
                            self.last_observation['cache_max_age_seconds'], age)
                        continue
                    self._cache.pop(item.address, None)
                pending.append(item)
            # Input is market-prioritized by the scanner; rotating attempts prevents
            # the first address consuming every new request. No score gate changes.
            priority = set(priority_addresses or [])
            eligible = [s for s in pending if s.address in priority]
            pool = eligible or pending
            target = min(pool, key=lambda s: self._attempted.get(s.address, 0)) if pool else None
            if len(self._attempted) > 4096:
                wanted = {s.address for s in items}
                type(self)._attempted = {k:v for k,v in self._attempted.items() if k in wanted}
                type(self)._cache = {k:v for k,v in self._cache.items() if k in wanted}
        for item in pending:
            self.last_observation['unknown'][item.address] = 'security_pending_rate_budget'
        if target is None:
            return [by_address[item.address] for item in items]
        slots = self._slots
        if not slots.acquire(blocking=False):
            raise ProviderError('GoPlus outstanding read limit reached')
        executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='mykonos-goplus')
        try:
            url = f'{self.base}?contract_addresses={urllib.parse.quote(target.address, safe="")}'
            future = executor.submit(self._observe, url, request_timeout, deadline)
        except Exception:
            slots.release()
            executor.shutdown(wait=False, cancel_futures=True)
            raise
        future.add_done_callback(lambda done, pool=slots: pool.release())
        try:
            done, _ = wait({future}, timeout=max(0, deadline-time.monotonic()))
            if not done:
                raise ProviderError('GoPlus enrichment hard deadline exceeded')
            records, checked_at, completed, began = future.result()
            if completed >= deadline or time.monotonic() >= deadline:
                raise ProviderError('GoPlus validation deadline exceeded')
            self.last_observation['http_success'] = 1
            self.last_observation['requested'] = 1
            with self._state_lock:
                self._attempted[target.address] = time.monotonic()
            security = self._normalize(records.get(target.address))
            if security.checked:
                security = replace(security, checked_at=checked_at)
                by_address[target.address] = replace(target, security=security)
                self.last_observation['mint_hits'] = 1
                self.last_observation['unknown'].pop(target.address, None)
                with self._state_lock:
                    self._cache[target.address] = (security, began)
            else:
                self.last_observation['unknown'][target.address] = 'provider_mint_missing_or_empty'
                with self._state_lock:
                    self._cache.pop(target.address, None)
        except Exception as error:
            with self._state_lock:
                self._cache.pop(target.address, None)
            self.last_observation['unknown'][target.address] = 'provider_lookup_failed:' + type(error).__name__
            if self.last_observation['cache_hits']:
                # Other mints retain only their independently fresh evidence.
                return [by_address[item.address] for item in items]
            raise
        finally:
            executor.shutdown(wait=False, cancel_futures=True)
        return [by_address[item.address] for item in items]

    @staticmethod
    def _normalize(raw: dict | None) -> SecuritySignals:
        if not isinstance(raw, dict) or not raw:
            return SecuritySignals(checked=False)

        # GoPlus Solana v1.2.6+ returns mintable/freezable as objects with a
        # `status` field. Legacy flat fields are still accepted for compatibility.
        mintable = _status_flag(raw.get("mintable"))
        if mintable is None:
            mintable = _flag(raw.get("is_mintable"))
        freezable = _status_flag(raw.get("freezable"))
        if freezable is None:
            legacy = raw.get("freeze_authority")
            if legacy is not None:
                freezable = bool(str(legacy).strip())

        top10_pct = _fraction_to_pct(raw.get("top_10_holder_rate", raw.get("top10_holder_rate")))
        creator_pct = _fraction_to_pct(raw.get("creator_percent", raw.get("creator_holder_rate")))
        holders = raw.get("holders") if isinstance(raw.get("holders"), list) else []
        if top10_pct is None and holders:
            total = 0.0
            complete = len(holders) >= 10
            for holder in holders[:10]:
                if not isinstance(holder, dict):
                    complete = False
                    break
                # Concentration includes every reported holder. A free-text tag
                # or a lock flag is not proof that the tokens can be excluded.
                pct = _fraction_to_pct(holder.get("percent"))
                if pct is None:
                    complete = False
                    break
                total += pct
            if complete and total <= 100:
                top10_pct = total
        # A creator tag in a top-ten list cannot prove the creator's complete
        # holdings. Only an explicit provider aggregate is used above.

        transfer_hooks = raw.get("transfer_hook")
        transfer_hook_present = bool(transfer_hooks) if isinstance(transfer_hooks, list) else None
        creators = raw.get("creators")
        malicious_creator = None
        if isinstance(creators, list) and creators:
            flags = [_flag(c.get("malicious_address")) if isinstance(c, dict) else None for c in creators]
            if True in flags:
                malicious_creator = True
            elif all(flag is False for flag in flags):
                malicious_creator = False

        metadata_mutable = _status_flag(raw.get("metadata_mutable"))
        if metadata_mutable is None:
            metadata_mutable = _flag(raw.get("is_metadata_mutable"))

        return SecuritySignals(
            checked=True,
            mint_authority_disabled=None if mintable is None else not mintable,
            freeze_authority_disabled=None if freezable is None else not freezable,
            honeypot=_flag(raw.get("is_honeypot")),
            top10_holder_pct=top10_pct,
            creator_holder_pct=creator_pct,
            metadata_mutable=metadata_mutable,
            non_transferable=_flag(raw.get("none_transferable", raw.get("non_transferable"))),
            transfer_hook_present=transfer_hook_present,
            malicious_creator=malicious_creator,
        )


class JupiterProvider:
    base = "https://api.jup.ag/swap/v2/order"
    wrapped_sol = "So11111111111111111111111111111111111111112"

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or os.getenv("JUPITER_API_KEY")

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    def enrich(self, snapshot: TokenSnapshot, sol_lamports: int = 10_000_000) -> TokenSnapshot:
        if not self.api_key:
            return snapshot
        query = urllib.parse.urlencode({
            "inputMint": self.wrapped_sol,
            "outputMint": snapshot.address,
            "amount": sol_lamports,
        })
        raw = _get_json(f"{self.base}?{query}", {"x-api-key": self.api_key})
        impact = raw.get("priceImpactPct", raw.get("priceImpact"))
        parsed = _number(impact, -1)
        if 0 <= parsed <= 1 and str(impact).startswith("0."):
            parsed *= 100
        return replace(snapshot, jupiter_price_impact_pct=None if parsed < 0 else parsed)


def provider_health() -> dict[str, str]:
    return {
        "geckoterminal_new_pools": "configured (10s fresh discovery + stale-cache fallback)",
        "dexscreener": "configured (parallel discovery + exact-pair primary watcher)",
        "watch_fallback": "GeckoTerminal exact-pool hedged fallback + hard deadline",
        "goplus": "single-mint lookup; shared 30/minute budget; <=5s dated cache; UNKNOWN blocks",
        "jupiter_swap_v2": "configured" if os.getenv("JUPITER_API_KEY") else "optional key missing",
        "live_execution": "hard-disabled in v0.4.1",
    }
