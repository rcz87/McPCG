"""MCP tool registrations for the forex module.

10 tools:
  MyFXBook (6): myfxbook_sentiment, myfxbook_sentiment_all, myfxbook_sentiment_by_country,
                myfxbook_extreme_scanner, myfxbook_sentiment_change, myfxbook_historical_query
  CFTC COT (4): cftc_cot_snapshot, cftc_cot_extreme_scanner, cftc_cot_historical,
                cftc_cot_vs_retail_divergence

All return the standard envelope (make_envelope) used by existing coinglass tools so
response shape matches across the server. Tools are best-effort and never raise —
errors land as {status:"failed", ...} envelopes.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timezone, timedelta

from ..config import make_envelope
from .cftc_cot import SYMBOL_MAP as COT_SYMBOL_MAP
from .cftc_cot import fetch_cot_all_mapped, fetch_cot_for_symbol, percentile_of
from .formatters import (
    fmt_cot_history, fmt_cot_scanner, fmt_cot_snapshot, fmt_divergence,
    fmt_extreme_flags, fmt_historical_query, fmt_sentiment, fmt_sentiment_all,
    fmt_sentiment_change,
)
from .myfxbook_client import MyFXBookClient, MyFXBookAuthError, MyFXBookError
from .rate_limiter import RateLimitExceeded
from .storage import ForexStorage

logger = logging.getLogger("coinglass-mcp.forex")

DEFAULT_WATCHLIST = [
    "XAUUSD", "EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD", "NZDUSD", "USDCHF",
]

# Module-level singletons, initialized on first registration.
_storage: ForexStorage | None = None
_client: MyFXBookClient | None = None
_poller_task: asyncio.Task | None = None
_cot_poller_task: asyncio.Task | None = None

# COT cache TTL (24h) — official release is weekly but we poll daily to self-heal.
_COT_CACHE_TTL = 24 * 3600
_cot_last_fetch_ts: float = 0.0


def _ensure_storage() -> ForexStorage:
    global _storage
    if _storage is None:
        _storage = ForexStorage()
    return _storage


def _ensure_client() -> MyFXBookClient:
    global _client
    _ensure_storage()
    if _client is None:
        _client = MyFXBookClient(storage=_storage)
    return _client


def _fail(source: str, msg: str, fallback: str = "") -> str:
    return make_envelope("failed", source, msg, fallback_suggestion=fallback)


def _ok(source: str, content: str, age_sec: float = 0.0,
        warnings: list[str] | None = None) -> str:
    """Wrap pre-formatted markdown content in standard success envelope."""
    return make_envelope(
        "success", source, content,
        data_age_seconds=age_sec, warnings=warnings,
    )


def _handle_myfxbook_error(e: Exception) -> str:
    if isinstance(e, MyFXBookAuthError):
        return _fail("myfxbook",
                     f"Auth error: {e}. Set MYFXBOOK_EMAIL + MYFXBOOK_PASSWORD in .env.")
    if isinstance(e, RateLimitExceeded):
        return _fail("myfxbook", f"Rate limited: {e}")
    if isinstance(e, MyFXBookError):
        return _fail("myfxbook", f"API error: {e}")
    return _fail("myfxbook", f"Unexpected error: {type(e).__name__}: {e}")


# ─── Tools ──────────────────────────────────────────────────────────────────


def register_forex_tools(mcp) -> None:
    """Register all forex tools on the given FastMCP server."""

    @mcp.tool(output_schema=None)
    async def myfxbook_sentiment(symbol: str) -> str:
        """Retail sentiment for one FX/gold symbol from MyFXBook community outlook.

        Retail positioning is a classic contrarian indicator — when retail is crowded on
        one side, smart money often takes the other.

        Args:
            symbol: e.g. XAUUSD, EURUSD, GBPUSD, USDJPY

        Returns JSON with long_pct, short_pct, positions, volume, avg entry prices.
        """
        try:
            client = _ensure_client()
            row = await client.get_sentiment(symbol)
            if not row:
                return _fail("myfxbook", f"Symbol {symbol} not found in MyFXBook outlook")
            _storage.save_snapshot(row["symbol"], row)
            return _ok("myfxbook", fmt_sentiment(row))
        except Exception as e:
            return _handle_myfxbook_error(e)

    @mcp.tool(output_schema=None)
    async def myfxbook_sentiment_all() -> str:
        """Retail sentiment for ALL symbols tracked by MyFXBook (1 API call).

        Sorted by most-extreme positioning first (max of long_pct/short_pct desc).
        Use this to scan the full FX universe for crowded retail positions.
        """
        try:
            client = _ensure_client()
            rows = await client.get_community_outlook()
            for r in rows:
                _storage.save_snapshot(r["symbol"], r)
            rows.sort(key=lambda r: max(r["long_pct"], r["short_pct"]), reverse=True)
            return _ok("myfxbook", fmt_sentiment_all(rows))
        except Exception as e:
            return _handle_myfxbook_error(e)

    @mcp.tool(output_schema=None)
    async def myfxbook_sentiment_by_country(symbol: str) -> str:
        """Country-level breakdown of retail positioning for a symbol.

        NOTE: MyFXBook public API only exposes aggregate community outlook, not country
        breakdown. This tool returns a clear NOT_SUPPORTED envelope so callers can
        fall back to myfxbook_sentiment for aggregate data.

        Args:
            symbol: e.g. EURUSD
        """
        return make_envelope(
            "failed", "myfxbook",
            f"Country breakdown for {symbol} not available via MyFXBook public API. "
            f"Use myfxbook_sentiment({symbol}) for aggregate retail positioning.",
            fallback_suggestion=f"myfxbook_sentiment(symbol='{symbol}')",
        )

    @mcp.tool(output_schema=None)
    async def myfxbook_extreme_scanner(
        threshold: int = 70,
        watchlist: list[str] | None = None,
    ) -> str:
        """Scan watchlist for symbols with crowded retail positioning.

        A symbol is flagged when long_pct >= threshold OR short_pct >= threshold.
        The crowded side is the CONTRARIAN bias (retail is usually wrong at extremes).
        Avg entry price = likely stop-hunt zone.

        Args:
            threshold: Percentage cutoff for 'crowded' (default 70)
            watchlist: Symbols to scan (default: 7 majors + XAUUSD)
        """
        try:
            wl = [s.upper() for s in (watchlist or DEFAULT_WATCHLIST)]
            client = _ensure_client()
            all_rows = await client.get_community_outlook()
            idx = {r["symbol"]: r for r in all_rows}
            flagged: list[dict] = []
            for sym in wl:
                row = idx.get(sym)
                if not row:
                    continue
                _storage.save_snapshot(sym, row)
                lp, sp = row["long_pct"], row["short_pct"]
                if lp >= threshold:
                    flagged.append({
                        **row,
                        "crowded_side": "LONG", "crowded_pct": lp,
                        "contrarian_bias": "SHORT",
                        "stop_hunt_zone": row["avg_long_entry"],
                    })
                elif sp >= threshold:
                    flagged.append({
                        **row,
                        "crowded_side": "SHORT", "crowded_pct": sp,
                        "contrarian_bias": "LONG",
                        "stop_hunt_zone": row["avg_short_entry"],
                    })
            flagged.sort(key=lambda r: r["crowded_pct"], reverse=True)
            return _ok("myfxbook",
                       fmt_extreme_flags(flagged, threshold=threshold, scanned=len(wl)))
        except Exception as e:
            return _handle_myfxbook_error(e)

    @mcp.tool(output_schema=None)
    async def myfxbook_sentiment_change(symbol: str, hours: int = 6) -> str:
        """Compare current retail sentiment vs N hours ago.

        Requires historical snapshots in DB (populated by background poller or prior
        calls). Returns null deltas with a warning if insufficient history.

        Args:
            symbol: e.g. XAUUSD
            hours: Lookback window in hours (default 6)
        """
        try:
            client = _ensure_client()
            # Refresh current
            row = await client.get_sentiment(symbol)
            if not row:
                return _fail("myfxbook", f"Symbol {symbol} not found")
            _storage.save_snapshot(row["symbol"], row)

            target_ts = time.time() - hours * 3600
            tolerance = max(1800, hours * 600)  # ±10% of window, min 30min
            past = _storage.snapshot_near(row["symbol"], target_ts, tolerance)
            if not past:
                payload = {
                    "symbol": row["symbol"],
                    "current_long_pct": row["long_pct"],
                    "current_short_pct": row["short_pct"],
                    "past_long_pct": None, "past_short_pct": None,
                    "delta_long_pct": None, "delta_short_pct": None,
                    "momentum": "INSUFFICIENT_HISTORY",
                    "requested_lookback_hours": hours,
                }
                return make_envelope(
                    "partial", "myfxbook", fmt_sentiment_change(payload),
                    warnings=[f"No snapshot within {hours}h window for {symbol}. "
                              "Background poller needs time to accumulate history."],
                )

            d_long = row["long_pct"] - past["long_pct"]
            d_short = row["short_pct"] - past["short_pct"]
            if d_long > 2:
                momentum = "RETAIL_PILING_LONG"
            elif d_long < -2:
                momentum = "RETAIL_CAPITULATING_LONG"
            else:
                momentum = "FLAT"
            payload = {
                "symbol": row["symbol"],
                "current_long_pct": row["long_pct"],
                "current_short_pct": row["short_pct"],
                "past_long_pct": past["long_pct"],
                "past_short_pct": past["short_pct"],
                "delta_long_pct": round(d_long, 2),
                "delta_short_pct": round(d_short, 2),
                "momentum": momentum,
                "lookback_hours": hours,
                "past_snapshot_age_sec": int(time.time() - past["ts"]),
            }
            return _ok("myfxbook", fmt_sentiment_change(payload))
        except Exception as e:
            return _handle_myfxbook_error(e)

    @mcp.tool(output_schema=None)
    async def myfxbook_historical_query(symbol: str, days: int = 7) -> str:
        """Query historical retail sentiment snapshots from local DB.

        Returns time-series of long_pct / short_pct / positions for the last N days.
        No API call (reads from DB only) — cheap.

        Args:
            symbol: e.g. XAUUSD
            days: History window (default 7)
        """
        try:
            storage = _ensure_storage()
            since = time.time() - days * 86400
            snaps = storage.snapshots_range(symbol, since)
            payload = {
                "symbol": symbol.upper(), "days": days,
                "count": len(snaps), "snapshots": snaps,
            }
            return _ok("myfxbook", fmt_historical_query(payload))
        except Exception as e:
            return _fail("myfxbook", f"Historical query failed: {e}")

    # ─── CFTC COT tools ──────────────────────────────────────────────────────

    @mcp.tool(output_schema=None)
    async def cftc_cot_snapshot(symbol: str) -> str:
        """Latest CFTC Commitments of Traders snapshot for a symbol.

        CFTC releases weekly on Fridays ~15:30 ET. Data tracks institutional (Non-Comm
        = speculators like hedge funds) vs Commercial (hedgers) positioning.
        Cached 24h after fetch — weekly release anyway.

        Args:
            symbol: XAUUSD, GOLD, SILVER, DXY, EURUSD, GBPUSD, USDJPY, USDCHF,
                    AUDUSD, USDCAD, NZDUSD, USDMXN, WTI
        """
        try:
            sym = symbol.upper()
            if sym not in COT_SYMBOL_MAP:
                return _fail("cftc",
                             f"Unknown symbol '{symbol}'. Supported: "
                             f"{', '.join(sorted(COT_SYMBOL_MAP))}")

            storage = _ensure_storage()
            global _cot_last_fetch_ts
            age = time.time() - _cot_last_fetch_ts
            cached = storage.latest_cot(sym)

            if cached and age < _COT_CACHE_TTL:
                age_sec = time.time() - cached["fetched_at"]
                return _ok("cftc", fmt_cot_snapshot(cached), age_sec=age_sec)

            parsed = await fetch_cot_for_symbol(sym)
            if not parsed or not parsed.get("week_date"):
                if cached:
                    return make_envelope(
                        "partial", "cftc", fmt_cot_snapshot(cached),
                        warnings=["Fresh fetch failed, returning last cached record"],
                    )
                return _fail("cftc", "Could not parse COT report")
            storage.save_cot(sym, parsed["week_date"], parsed)
            _cot_last_fetch_ts = time.time()
            return _ok("cftc", fmt_cot_snapshot(parsed), age_sec=0)
        except Exception as e:
            logger.warning("cot_snapshot failed: %s", e)
            return _fail("cftc", f"Error: {e}")

    @mcp.tool(output_schema=None)
    async def cftc_cot_extreme_scanner(percentile_threshold: int = 85) -> str:
        """Scan all tracked CFTC symbols for Non-Comm net positioning at extremes.

        Flags symbols where current net position is >= percentile_threshold or
        <= (100 - threshold) over trailing history in DB. Requires at least 10 weeks
        of history — flags are gated on that.

        Args:
            percentile_threshold: Extremity cutoff (default 85 = top/bottom 15%)
        """
        try:
            storage = _ensure_storage()
            global _cot_last_fetch_ts
            if time.time() - _cot_last_fetch_ts > _COT_CACHE_TTL:
                try:
                    parsed_all = await fetch_cot_all_mapped()
                    for sym, p in parsed_all.items():
                        if p.get("week_date"):
                            storage.save_cot(sym, p["week_date"], p)
                    _cot_last_fetch_ts = time.time()
                except Exception as e:
                    logger.warning("cot refresh failed: %s", e)

            flagged: list[dict] = []
            for sym in COT_SYMBOL_MAP:
                history = storage.cot_history(sym, weeks=52)
                if len(history) < 10:
                    continue
                nets = [h["net_position"] for h in history if h["net_position"] is not None]
                if not nets:
                    continue
                current = nets[0]
                pct = percentile_of(current, nets)
                if pct >= percentile_threshold:
                    side = "EXTREME_LONG"
                elif pct <= (100 - percentile_threshold):
                    side = "EXTREME_SHORT"
                else:
                    continue
                flagged.append({
                    "symbol": sym,
                    "current_net": current,
                    "percentile": round(pct, 1),
                    "side": side,
                    "weeks_of_history": len(history),
                })
            flagged.sort(key=lambda r: abs(r["percentile"] - 50), reverse=True)
            payload = {
                "threshold": percentile_threshold,
                "flagged": len(flagged),
                "results": flagged,
            }
            return _ok(
                "cftc", fmt_cot_scanner(payload),
                warnings=None if flagged else ["No extremes or insufficient history."],
            )
        except Exception as e:
            return _fail("cftc", f"Scanner error: {e}")

    @mcp.tool(output_schema=None)
    async def cftc_cot_historical(symbol: str, weeks: int = 52) -> str:
        """Time-series CFTC Non-Comm positioning for a symbol.

        Reads from DB only (no fetch). History grows as the background poller runs.

        Args:
            symbol: e.g. XAUUSD
            weeks: Lookback window in weeks (default 52)
        """
        try:
            sym = symbol.upper()
            if sym not in COT_SYMBOL_MAP:
                return _fail("cftc", f"Unknown symbol '{symbol}'.")
            storage = _ensure_storage()
            history = storage.cot_history(sym, weeks=weeks)
            payload = {
                "symbol": sym, "weeks_requested": weeks,
                "count": len(history), "history": history,
            }
            return _ok("cftc", fmt_cot_history(payload))
        except Exception as e:
            return _fail("cftc", f"Historical error: {e}")

    @mcp.tool(output_schema=None)
    async def cftc_cot_vs_retail_divergence(symbol: str) -> str:
        """Cross-reference CFTC institutional direction vs MyFXBook retail direction.

        Killer signal — when institutions (Non-Comm net) and retail disagree, high
        conviction setup. Direction is based on net positioning sign.

        Args:
            symbol: e.g. XAUUSD, EURUSD (must exist in both CFTC + MyFXBook)
        """
        try:
            sym = symbol.upper()
            if sym not in COT_SYMBOL_MAP:
                return _fail("cftc", f"No CFTC mapping for {symbol}")

            client = _ensure_client()
            cot_row, retail_row = await asyncio.gather(
                fetch_cot_for_symbol(sym),
                client.get_sentiment(sym),
                return_exceptions=True,
            )

            if isinstance(cot_row, Exception):
                return _fail("forex", f"CFTC fetch failed: {cot_row}")
            if isinstance(retail_row, Exception):
                return _handle_myfxbook_error(retail_row)
            if not cot_row or not retail_row:
                return _fail("forex", "Missing data from one or both sources")

            cot_dir = "LONG" if cot_row["net_position"] > 0 else "SHORT"
            retail_dir = "LONG" if retail_row["long_pct"] > 50 else "SHORT"
            diverge = cot_dir != retail_dir
            # Store both
            _storage.save_snapshot(sym, retail_row)
            if cot_row.get("week_date"):
                _storage.save_cot(sym, cot_row["week_date"], cot_row)

            cot_payload = {
                "week_date": cot_row.get("week_date"),
                "net_position": cot_row["net_position"],
                "direction": cot_dir,
            }
            retail_payload = {
                "long_pct": retail_row["long_pct"],
                "short_pct": retail_row["short_pct"],
                "direction": retail_dir,
            }
            return _ok("forex", fmt_divergence(cot_payload, retail_payload, diverge, sym))
        except Exception as e:
            return _fail("forex", f"Divergence error: {e}")


# ─── Background pollers ─────────────────────────────────────────────────────


async def _poll_myfxbook_loop():
    """Hourly poll of MyFXBook community outlook to build up sentiment history.

    Skips if MYFXBOOK creds missing or rate-limit near cap.
    """
    storage = _storage or ForexStorage()
    client = _client or MyFXBookClient(storage=storage)
    # Start 60s after boot to avoid blocking startup
    await asyncio.sleep(60)
    while True:
        try:
            rows = await client.get_community_outlook()
            for r in rows:
                storage.save_snapshot(r["symbol"], r)
            logger.info("forex poll: saved %d MyFXBook snapshots", len(rows))
        except MyFXBookAuthError:
            logger.info("forex poll: MyFXBook creds missing — skipping (set MYFXBOOK_EMAIL/PASSWORD)")
        except RateLimitExceeded as e:
            logger.warning("forex poll: rate limited — %s", e)
        except Exception as e:
            logger.warning("forex poll: error %s", e)
        await asyncio.sleep(3600)


async def _poll_cot_loop():
    """Weekly poll of CFTC COT — check hourly, fetch only on Fri >=21:00 UTC or if >7d old."""
    storage = _storage or ForexStorage()
    await asyncio.sleep(120)
    while True:
        try:
            now = datetime.now(timezone.utc)
            any_row = None
            for sym in COT_SYMBOL_MAP:
                row = storage.latest_cot(sym)
                if row:
                    any_row = row
                    break
            stale = True
            if any_row:
                age_days = (time.time() - any_row["fetched_at"]) / 86400
                stale = age_days > 7
            is_release_time = now.weekday() == 4 and now.hour >= 21
            if stale or is_release_time:
                parsed_all = await fetch_cot_all_mapped()
                for sym, p in parsed_all.items():
                    if p.get("week_date"):
                        storage.save_cot(sym, p["week_date"], p)
                global _cot_last_fetch_ts
                _cot_last_fetch_ts = time.time()
                logger.info("forex cot poll: fetched %d symbols", len(parsed_all))
        except Exception as e:
            logger.warning("forex cot poll: error %s", e)
        await asyncio.sleep(3600)


def start_forex_pollers() -> None:
    """Spawn background poller tasks. Called from server lifespan."""
    global _poller_task, _cot_poller_task, _storage, _client
    if _storage is None:
        _storage = ForexStorage()
    if _client is None:
        _client = MyFXBookClient(storage=_storage)
    if _poller_task is None or _poller_task.done():
        _poller_task = asyncio.create_task(_poll_myfxbook_loop(), name="forex-mfb-poller")
    if _cot_poller_task is None or _cot_poller_task.done():
        _cot_poller_task = asyncio.create_task(_poll_cot_loop(), name="forex-cot-poller")


def stop_forex_pollers() -> None:
    """Cancel background tasks on shutdown."""
    for t in (_poller_task, _cot_poller_task):
        if t and not t.done():
            t.cancel()
