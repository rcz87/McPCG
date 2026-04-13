"""Formatting helpers, error envelope decorator, and scan formatters.

Extracted from server.py — all formatting/presentation logic lives here.
Data values are NEVER modified — only presentation changes.
"""

from __future__ import annotations

import asyncio
import functools
import json
from datetime import datetime, timezone, timedelta
from typing import Any

from .client import APIError, FetchResult, PlanLimitError, RateLimitError
from .config import make_envelope

WIB = timezone(timedelta(hours=7))


# ─── Plan Access Fallback Map ───────────────────────────────────────────────

PLAN_FALLBACK_MAP: dict[str, dict[str, str]] = {
    "coinglass_footprint": {
        "required_plan": "standard+",
        "fallback_tool": "coinglass_orderbook",
    },
    "coinglass_liquidation_map": {
        "required_plan": "professional+",
        "fallback_tool": "coinglass_liquidation_cat",
    },
    "coinglass_orderbook_heatmap": {
        "required_plan": "standard+",
        "fallback_tool": "coinglass_orderbook",
    },
}

_GENERIC_PLAN_ACCESS = {"required_plan": "standard+", "fallback_tool": "data_binance"}


def _tool_envelope(source: str = "coinglass"):
    """Decorator: catch PlanLimitError, RateLimitError, timeouts, APIError
    and return proper enveloped error with access metadata / fallback."""

    def decorator(func):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            try:
                return await func(*args, **kwargs)
            except PlanLimitError as e:
                access = PLAN_FALLBACK_MAP.get(func.__name__, _GENERIC_PLAN_ACCESS)
                return make_envelope(
                    "failed", source, f"**Plan Error:** {e}",
                    access=access,
                    fallback_suggestion=f"Try {access['fallback_tool']}")
            except RateLimitError as e:
                return make_envelope(
                    "failed", source, f"**Rate Limited:** {e}",
                    fallback_suggestion="Rate limited — wait 30s or try data_binance")
            except (TimeoutError, asyncio.TimeoutError) as e:
                return make_envelope(
                    "failed", source,
                    f"**Timeout:** Request timed out ({e})",
                    fallback_suggestion="Try data_binance for Binance-only scan")
            except APIError as e:
                return make_envelope(
                    "failed", source, f"**API Error:** {e}")
        return wrapper

    return decorator


def _err(msg: str, source: str = "coinglass", fallback: str = "",
         access: dict[str, str] | None = None) -> str:
    """Wrap an error message in standard envelope."""
    return make_envelope("failed", source, msg,
                         fallback_suggestion=fallback, access=access)


# ─── Formatting Helpers ──────────────────────────────────────────────────────


def _age_banner(result: FetchResult) -> str:
    """Generate age/staleness banner for a FetchResult."""
    src = "CACHED" if result.is_cached else "LIVE"
    ts = datetime.fromtimestamp(result.fetched_at, tz=WIB).strftime(
        "%H:%M:%S WIB"
    )
    age = result.age_seconds

    if result.is_expired:
        return (
            f"**DATA EXPIRED ({age:.0f}s old) — DO NOT USE FOR ENTRY** | "
            f"Source: {src} | Fetched: {ts}\n\n"
        )
    elif result.is_stale:
        return (
            f"**WARNING: DATA STALE ({age:.0f}s old)** | "
            f"Source: {src} | Fetched: {ts}\n\n"
        )
    else:
        return f"Data: {result.age_label} | Source: {src} | {ts}\n\n"


def _fmt_cell(val) -> str:
    """Format a cell value for auto-table."""
    if val is None:
        return "—"
    if isinstance(val, bool):
        return str(val)
    if isinstance(val, (int, float)):
        v = float(val)
        av = abs(v)
        if av >= 1e9:
            return f"{v / 1e9:,.2f}B"
        if av >= 1e6:
            return f"{v / 1e6:,.2f}M"
        if av >= 1e3:
            return f"{v / 1e3:,.1f}K"
        if av >= 1:
            return f"{v:,.2f}"
        if av >= 0.0001:
            return f"{v:.4f}"
        if av > 0:
            return f"{v:.8f}"
        return "0"
    s = str(val)
    return s[:20] if len(s) > 20 else s


def _auto_table(data: list) -> str:
    """Auto-generate a markdown table from a list of dicts."""
    if not data or not isinstance(data[0], dict):
        return json.dumps(data[:20], indent=2, default=str)

    # Collect keys from first few items
    all_keys = []
    seen = set()
    for d in data[:5]:
        for k in d.keys():
            if k not in seen:
                all_keys.append(k)
                seen.add(k)

    # Filter out nested values, limit columns to 8
    simple_keys = [k for k in all_keys
                   if not isinstance(data[0].get(k), (list, dict))]
    if not simple_keys:
        return json.dumps(data[:20], indent=2, default=str)

    if len(simple_keys) > 8:
        prio, rest = [], []
        for k in simple_keys:
            kl = k.lower()
            if kl in ("symbol", "exchange", "exchangename", "pair", "name",
                       "side", "action", "classification"):
                prio.append(k)
            elif any(t in kl for t in ("time", "date", "timestamp")):
                prio.append(k)
            else:
                rest.append(k)
        simple_keys = (prio + rest)[:8]

    show = data[-20:] if len(data) > 20 else data
    prefix = f"*(showing last {len(show)} of {len(data)})*\n\n" if len(data) > 20 else ""

    # Calculate column widths
    widths = {}
    for k in simple_keys:
        hlen = len(str(k))
        vlen = max((len(_fmt_cell(d.get(k))) for d in show[:10]), default=0)
        widths[k] = max(hlen, min(vlen, 18))

    table = prefix + "```\n"
    table += " | ".join(f"{k:>{widths[k]}}" for k in simple_keys) + "\n"
    table += " | ".join(f"{'─' * widths[k]}" for k in simple_keys) + "\n"
    for d in show:
        table += " | ".join(f"{_fmt_cell(d.get(k)):>{widths[k]}}" for k in simple_keys) + "\n"
    table += "```\n"
    return table


def fmt(result: FetchResult, title: str = "", source: str = "coinglass",
        extra_warnings: list[str] | None = None) -> str:
    """Format API response with envelope — smart auto-table for dicts/lists."""
    data = result.data
    warnings = list(extra_warnings or [])
    header = ""
    if title:
        header = f"## {title}\n\n"

    # Staleness warnings
    if result.is_expired:
        warnings.append(f"DATA EXPIRED ({result.age_seconds:.0f}s old) — DO NOT USE FOR ENTRY")
    elif result.is_stale:
        warnings.append(f"DATA STALE ({result.age_seconds:.0f}s old)")

    if data is None:
        return make_envelope("failed", source,
                             f"{header}**ERROR: No data returned.** The symbol may not exist.",
                             data_age_seconds=result.age_seconds, warnings=warnings,
                             fallback_suggestion="Try data_binance or coinglass_price_ohlc")

    if isinstance(data, list):
        if len(data) == 0:
            return make_envelope("failed", source,
                                 f"{header}**WARNING: Empty dataset.** No data points returned.",
                                 data_age_seconds=result.age_seconds, warnings=warnings)
        # String list (supported coins/exchanges)
        if isinstance(data[0], str):
            content = header + ", ".join(data)
        # List of dicts → auto-table
        elif isinstance(data[0], dict):
            content = header + _auto_table(data)
        else:
            # List of lists (kline arrays, heatmap) → compact JSON
            show = data[-20:] if len(data) > 20 else data
            prefix = f"*(showing last {len(show)} of {len(data)})*\n\n" if len(data) > 20 else ""
            content = header + prefix + json.dumps(show, indent=1, default=str)
    elif isinstance(data, dict):
        # Single dict → key-value format
        lines = []
        for k, v in data.items():
            if isinstance(v, (list, dict)):
                continue
            lines.append(f"- **{k}:** {_fmt_cell(v)}")
        if lines:
            content = header + "\n".join(lines) + "\n"
        else:
            content = header + json.dumps(data, indent=2, default=str)
    else:
        content = header + str(data)

    status = "failed" if result.is_expired else "success"
    return make_envelope(status, source, content,
                         data_age_seconds=result.age_seconds, warnings=warnings)


def fmt_parsed(result: FetchResult, title: str, formatter,
               source: str = "coinglass",
               extra_warnings: list[str] | None = None) -> str:
    """Format API response using a parsed formatter instead of raw JSON.
    formatter(data) should return a formatted string."""
    warnings = list(extra_warnings or [])
    header = ""
    if title:
        header = f"## {title}\n\n"

    # Staleness warnings
    if result.is_expired:
        warnings.append(f"DATA EXPIRED ({result.age_seconds:.0f}s old) — DO NOT USE FOR ENTRY")
    elif result.is_stale:
        warnings.append(f"DATA STALE ({result.age_seconds:.0f}s old)")

    data = result.data
    if data is None:
        return make_envelope("failed", source,
                             f"{header}**ERROR: No data returned.** The symbol may not exist.",
                             data_age_seconds=result.age_seconds, warnings=warnings,
                             fallback_suggestion="Try data_binance or coinglass_price_ohlc")
    if isinstance(data, list) and len(data) == 0:
        return make_envelope("failed", source,
                             f"{header}**WARNING: Empty dataset.** No data points returned.",
                             data_age_seconds=result.age_seconds, warnings=warnings)
    if isinstance(data, (list, dict)) and data:
        content = header + formatter(data)
    else:
        content = header + str(data)

    status = "failed" if result.is_expired else "success"
    return make_envelope(status, source, content,
                         data_age_seconds=result.age_seconds, warnings=warnings)


def _format_fr_compact(coins: list, symbol: str) -> list:
    """Extract compact FR data for a specific coin from exchange-list response."""
    result = []
    for coin in coins:
        for margin_list_key in ("stablecoinMarginList", "stablecoin_margin_list"):
            exchanges = coin.get(margin_list_key, [])
            if exchanges:
                break
        for ex in exchanges:
            fr = ex.get("fundingRate", ex.get("funding_rate"))
            if fr is not None:
                result.append({
                    "exchange": ex.get("exchangeName", ex.get("exchange", "?")),
                    "pair": ex.get("pair", f"{symbol}USDT"),
                    "funding_rate": fr,
                    "interval_h": ex.get("fundingRateInterval", ex.get("funding_rate_interval", 8)),
                    "next_funding": ex.get("nextFundingTime", ex.get("next_funding_time")),
                })
    return result


# ─── Full Scan Readable Formatters ───────────────────────────────────────────
# Rules:
# 1. Data values are NEVER modified — only presentation changes
# 2. Timestamps converted to WIB for readability
# 3. Large numbers formatted ($1.23M, $456K) but remain accurate
# 4. Summary line = factual observation, not opinion/verdict


def _ts_wib(t: Any) -> str:
    """Convert unix timestamp (seconds or milliseconds) to HH:MM WIB."""
    if t is None or t == 0:
        return "??:??"
    try:
        ts = float(t)
        if ts > 1e12:  # milliseconds
            ts = ts / 1000
        return datetime.fromtimestamp(ts, tz=WIB).strftime("%H:%M")
    except (ValueError, OSError):
        return "??:??"


def _fmt_num(v: float, prefix: str = "$", signed: bool = False) -> str:
    """Format number as readable string. $1.23M, $456K, $1,234.
    signed=True adds +/- prefix for delta values."""
    if v is None:
        return "N/A"
    v = float(v)
    av = abs(v)
    sign = "-" if v < 0 else ("+" if signed else "")
    if av >= 1e9:
        return f"{sign}{prefix}{av / 1e9:,.2f}B"
    elif av >= 1e6:
        return f"{sign}{prefix}{av / 1e6:,.2f}M"
    elif av >= 1e3:
        return f"{sign}{prefix}{av / 1e3:,.1f}K"
    else:
        return f"{sign}{prefix}{av:,.2f}"


def _get(d: dict, *keys, default=None):
    """Get first matching key from dict (handles camelCase/snake_case variants)."""
    for k in keys:
        if k in d:
            return d[k]
    return default


def _check_all_zero(label: str, data: list) -> bool:
    """Check if data has rows but all key values are zero (false positive OK status).
    Returns True if data is effectively empty/$0."""
    if not data or not isinstance(data[0], dict):
        return False
    # Define which fields to check per panel
    checks = {
        "Orderbook Delta": ("aggregated_bids_usd", "bids_usd"),
        "Taker Buy/Sell": ("aggregated_buy_volume_usd", "taker_buy_volume_usd"),
        "Long/Short Ratio": ("global_account_long_percent", "longAccount"),
    }
    fields = checks.get(label)
    if not fields:
        return False
    for row in data[-5:]:  # check last 5 rows
        for f in fields:
            v = row.get(f)
            if v is not None and float(v) != 0:
                return False  # found non-zero data
    return True  # all checked rows were zero or missing


def _fmt_scan_cvd(data: list, label: str) -> str:
    """Format CVD time-series as readable table. Data unchanged."""
    if not data:
        return "**Empty dataset**\n\n"

    show = data[-15:]
    total = len(data)
    out = f"*(last {len(show)} of {total})*\n\n"
    out += "```\n"
    out += f"{'Time':>6} | {'CVD':>14} | {'Delta':>12}\n"
    out += f"{'─'*6} | {'─'*14} | {'─'*12}\n"

    pos_deltas = 0
    prev_cvd = None
    first_cvd = None
    last_cvd = None

    for row in show:
        if not isinstance(row, dict):
            continue
        t = _get(row, "t", "time", "timestamp", "createTime", default=0)
        # CVD value — try multiple field names
        cvd_val = _get(row, "cvd", "cum_vol_delta", "v", "value", "vol", default=None)
        if cvd_val is None:
            continue
        cvd_val = float(cvd_val)

        if first_cvd is None:
            first_cvd = cvd_val
        last_cvd = cvd_val

        delta = cvd_val - prev_cvd if prev_cvd is not None else 0
        if delta > 0:
            pos_deltas += 1
        prev_cvd = cvd_val

        out += f"{_ts_wib(t):>6} | {cvd_val:>+14,.0f} | {delta:>+12,.0f}\n"

    out += "```\n\n"

    # Factual summary
    n = len(show) - 1  # first row has no delta
    if n > 0 and first_cvd is not None and last_cvd is not None:
        net = last_cvd - first_cvd
        direction = "rising" if net > 0 else "falling" if net < 0 else "flat"
        out += (
            f"**Summary:** {pos_deltas}/{n} positive delta, "
            f"net change: {_fmt_num(net, signed=True)}, direction: {direction}\n\n"
        )

    return out


def _fmt_scan_oi(data: list) -> str:
    """Format Open Interest time-series as readable table."""
    if not data:
        return "**Empty dataset**\n\n"

    show = data[-15:]
    total = len(data)
    out = f"*(last {len(show)} of {total})*\n\n"

    # Detect if OHLC format or single value
    sample = show[0] if isinstance(show[0], dict) else {}
    has_ohlc = any(k in sample for k in ("o", "h", "l", "c", "open", "high", "low", "close"))

    if has_ohlc:
        out += "```\n"
        out += f"{'Time':>6} | {'Open':>12} | {'High':>12} | {'Low':>12} | {'Close':>12}\n"
        out += f"{'─'*6} | {'─'*12} | {'─'*12} | {'─'*12} | {'─'*12}\n"
        first_close = None
        last_close = None
        for row in show:
            if not isinstance(row, dict):
                continue
            t = _get(row, "t", "time", "timestamp", "createTime", default=0)
            o = float(_get(row, "o", "open", default=0))
            h = float(_get(row, "h", "high", default=0))
            l = float(_get(row, "l", "low", default=0))
            c = float(_get(row, "c", "close", default=0))
            if first_close is None:
                first_close = c
            last_close = c
            out += f"{_ts_wib(t):>6} | {_fmt_num(o):>12} | {_fmt_num(h):>12} | {_fmt_num(l):>12} | {_fmt_num(c):>12}\n"
        out += "```\n\n"
        if first_close and last_close:
            change = last_close - first_close
            out += f"**Summary:** OI {_fmt_num(first_close)} -> {_fmt_num(last_close)} (change: {_fmt_num(change, signed=True)})\n\n"
    else:
        out += "```\n"
        out += f"{'Time':>6} | {'OI':>14} | {'Change':>12}\n"
        out += f"{'─'*6} | {'─'*14} | {'─'*12}\n"
        prev_val = None
        first_val = None
        last_val = None
        for row in show:
            if not isinstance(row, dict):
                continue
            t = _get(row, "t", "time", "timestamp", "createTime", default=0)
            v = float(_get(row, "v", "value", "openInterest", "open_interest", default=0))
            if first_val is None:
                first_val = v
            last_val = v
            chg = v - prev_val if prev_val is not None else 0
            prev_val = v
            out += f"{_ts_wib(t):>6} | {_fmt_num(v):>14} | {_fmt_num(chg, signed=True):>12}\n"
        out += "```\n\n"
        if first_val is not None and last_val is not None:
            net = last_val - first_val
            direction = "rising" if net > 0 else "falling" if net < 0 else "flat"
            out += f"**Summary:** OI {direction}, net change: {_fmt_num(net, signed=True)}\n\n"

    return out


def _fmt_scan_fr(data: list) -> str:
    """Format Funding Rate (already compacted) as readable table."""
    if not data:
        return "**No funding rate data**\n\n"

    # If already compacted list of dicts with 'exchange' key
    if isinstance(data[0], dict) and "exchange" in data[0]:
        out = "```\n"
        out += f"{'Exchange':<12} | {'FR':>10} | {'Interval':>8} | {'Next Funding':>14}\n"
        out += f"{'─'*12} | {'─'*10} | {'─'*8} | {'─'*14}\n"

        fr_values = []
        for row in data:
            ex = row.get("exchange", "?")
            fr = row.get("funding_rate", 0)
            interval = row.get("interval_h", 8)
            nf = row.get("next_funding")
            nf_str = _ts_wib(nf) if nf else "N/A"
            if fr is not None:
                fr_values.append(float(fr))
            fr_pct = float(fr) if fr else 0  # CoinGlass already returns %
            out += f"{ex:<12} | {fr_pct:>+10.4f}% | {interval:>7}h | {nf_str:>14}\n"

        out += "```\n\n"

        if fr_values:
            avg_fr = sum(fr_values) / len(fr_values)
            out += f"**Summary:** Avg FR: {avg_fr:+.4f}% across {len(fr_values)} exchanges\n\n"
        return out
    else:
        # Fallback for unexpected format
        return json.dumps(data, indent=2, default=str) + "\n\n"


def _fmt_scan_ob_delta(data: list) -> str:
    """Format Orderbook Delta time-series as readable table."""
    if not data:
        return "**Empty dataset**\n\n"

    show = data[-15:]
    total = len(data)
    out = f"*(last {len(show)} of {total})*\n\n"
    out += "```\n"
    out += f"{'Time':>6} | {'Bids':>12} | {'Asks':>12} | {'Delta':>12} | {'Dominant':>8}\n"
    out += f"{'─'*6} | {'─'*12} | {'─'*12} | {'─'*12} | {'─'*8}\n"

    bid_dominant = 0
    n = 0

    for row in show:
        if not isinstance(row, dict):
            continue
        t = _get(row, "t", "time", "timestamp", "createTime", default=0)
        bids = float(_get(row, "aggregated_bids_usd", "bids_usd", "bids", "bidVol", "bid", default=0))
        asks = float(_get(row, "aggregated_asks_usd", "asks_usd", "asks", "askVol", "ask", default=0))
        delta = bids - asks
        dominant = "BIDS" if delta > 0 else "ASKS"
        if delta > 0:
            bid_dominant += 1
        n += 1
        out += f"{_ts_wib(t):>6} | {_fmt_num(bids):>12} | {_fmt_num(asks):>12} | {_fmt_num(delta, signed=True):>12} | {dominant:>8}\n"

    out += "```\n\n"

    if n > 0:
        out += f"**Summary:** {bid_dominant}/{n} bid-dominant candles\n\n"

    return out


def _fmt_scan_price(data: list) -> str:
    """Format Price OHLC time-series as readable table."""
    if not data:
        return "**Empty dataset**\n\n"

    show = data[-15:]
    total = len(data)
    out = f"*(last {len(show)} of {total})*\n\n"
    out += "```\n"
    out += f"{'Time':>6} | {'Open':>12} | {'High':>12} | {'Low':>12} | {'Close':>12} | {'Vol':>10}\n"
    out += f"{'─'*6} | {'─'*12} | {'─'*12} | {'─'*12} | {'─'*12} | {'─'*10}\n"

    highs = []
    lows = []
    last_close = None

    for row in show:
        if not isinstance(row, dict):
            continue
        t = _get(row, "t", "time", "timestamp", "createTime", default=0)
        o = float(_get(row, "o", "open", default=0))
        h = float(_get(row, "h", "high", default=0))
        l = float(_get(row, "l", "low", default=0))
        c = float(_get(row, "c", "close", default=0))
        v = float(_get(row, "v", "vol", "volume", "quoteVolume", default=0))
        highs.append(h)
        lows.append(l)
        last_close = c
        out += f"{_ts_wib(t):>6} | {_fmt_num(o, '$'):>12} | {_fmt_num(h, '$'):>12} | {_fmt_num(l, '$'):>12} | {_fmt_num(c, '$'):>12} | {_fmt_num(v, '$'):>10}\n"

    out += "```\n\n"

    if highs and lows and last_close:
        range_h = max(highs)
        range_l = min(lows)
        range_pct = (range_h - range_l) / range_l * 100 if range_l else 0
        out += (
            f"**Summary:** Range {_fmt_num(range_l, '$')}-{_fmt_num(range_h, '$')} "
            f"({range_pct:.2f}%), last close {_fmt_num(last_close, '$')}\n\n"
        )

    return out


def _fmt_scan_taker(data: list) -> str:
    """Format Taker Buy/Sell time-series as readable table."""
    if not data:
        return "**Empty dataset**\n\n"

    show = data[-15:]
    total = len(data)
    out = f"*(last {len(show)} of {total})*\n\n"
    out += "```\n"
    out += f"{'Time':>6} | {'Buy Vol':>12} | {'Sell Vol':>12} | {'Net':>12} | {'Aggressor':>9}\n"
    out += f"{'─'*6} | {'─'*12} | {'─'*12} | {'─'*12} | {'─'*9}\n"

    buy_dominant = 0
    total_net = 0
    n = 0

    for row in show:
        if not isinstance(row, dict):
            continue
        t = _get(row, "t", "time", "timestamp", "createTime", default=0)
        buy = float(_get(row, "aggregated_buy_volume_usd", "taker_buy_volume_usd", "buyVol", "buy_vol", "buy", "takerBuyVol", default=0))
        sell = float(_get(row, "aggregated_sell_volume_usd", "taker_sell_volume_usd", "sellVol", "sell_vol", "sell", "takerSellVol", default=0))
        net = buy - sell
        total_net += net
        aggressor = "BUY" if net > 0 else "SELL"
        if net > 0:
            buy_dominant += 1
        n += 1
        out += f"{_ts_wib(t):>6} | {_fmt_num(buy):>12} | {_fmt_num(sell):>12} | {_fmt_num(net, signed=True):>12} | {aggressor:>9}\n"

    out += "```\n\n"

    if n > 0:
        out += (
            f"**Summary:** {buy_dominant}/{n} buy-dominant, "
            f"total net: {_fmt_num(total_net, signed=True)}\n\n"
        )

    return out


def _fmt_scan_ls_ratio(data: list) -> str:
    """Format Long/Short Ratio time-series as readable table."""
    if not data:
        return "**Empty dataset**\n\n"

    show = data[-15:]
    total = len(data)
    out = f"*(last {len(show)} of {total})*\n\n"
    out += "```\n"
    out += f"{'Time':>6} | {'Long %':>8} | {'Short %':>8} | {'Ratio':>8}\n"
    out += f"{'─'*6} | {'─'*8} | {'─'*8} | {'─'*8}\n"

    ratios = []

    for row in show:
        if not isinstance(row, dict):
            continue
        t = _get(row, "t", "time", "timestamp", "createTime", default=0)
        long_pct = _get(row, "global_account_long_percent", "longAccount", "long_account", "longRate", "long_rate", default=None)
        short_pct = _get(row, "global_account_short_percent", "shortAccount", "short_account", "shortRate", "short_rate", default=None)
        ratio = _get(row, "global_account_long_short_ratio", "longShortRatio", "long_short_ratio", "ratio", default=None)

        # Convert ratio formats
        if long_pct is not None:
            long_pct = float(long_pct)
            if long_pct <= 1:  # API returns as decimal 0.52
                long_pct = long_pct * 100
        if short_pct is not None:
            short_pct = float(short_pct)
            if short_pct <= 1:
                short_pct = short_pct * 100
        if ratio is not None:
            ratio = float(ratio)
            ratios.append(ratio)

        l_str = f"{long_pct:.1f}%" if long_pct is not None else "N/A"
        s_str = f"{short_pct:.1f}%" if short_pct is not None else "N/A"
        r_str = f"{ratio:.3f}" if ratio is not None else "N/A"

        out += f"{_ts_wib(t):>6} | {l_str:>8} | {s_str:>8} | {r_str:>8}\n"

    out += "```\n\n"

    if ratios:
        avg_r = sum(ratios) / len(ratios)
        min_r = min(ratios)
        max_r = max(ratios)
        out += (
            f"**Summary:** Avg ratio {avg_r:.3f}, "
            f"range {min_r:.3f}-{max_r:.3f}\n\n"
        )

    return out


def _fmt_scan_liq_heatmap(data: Any) -> str:
    """Format Liquidation Heatmap data (keep compact, structure varies)."""
    if not data:
        return "**Empty dataset**\n\n"
    # Heatmap data is complex/nested — show compact JSON but limited
    if isinstance(data, list) and len(data) > 5:
        return f"*(showing last 5 of {len(data)})*\n" + json.dumps(data[-5:], indent=2, default=str) + "\n\n"
    return json.dumps(data, indent=2, default=str) + "\n\n"


def _fmt_scan_liq_orders(data: list) -> str:
    """Format Liquidation Orders as readable table — factual only, no interpretation."""
    if not data:
        return "**No liquidation order data**\n\n"

    # Sort by USD amount descending to show biggest clusters first
    rows = []
    for row in data:
        if not isinstance(row, dict):
            continue
        price = float(row.get("price", 0))
        amount = float(row.get("usd_value", row.get("vol_usd", row.get("amount_usd", 0))))
        side_raw = row.get("side", "")
        t = row.get("time", 0)
        # side: 1=Buy (long liq = short closes), 2=Sell (short liq = long closes)
        if side_raw == 1 or str(side_raw) == "1":
            side = "LONG"   # buy-side liq = long position liquidated
        elif side_raw == 2 or str(side_raw) == "2":
            side = "SHORT"  # sell-side liq = short position liquidated
        elif isinstance(side_raw, str):
            side = side_raw.upper()
        else:
            side = "?"
        rows.append({"price": price, "amount": amount, "side": side, "time": t})

    if not rows:
        return "**No liquidation order data**\n\n"

    rows.sort(key=lambda r: r["amount"], reverse=True)
    show = rows[:15]  # top 15 by size

    out = "```\n"
    out += f"{'Price':>12} | {'Side':>6} | {'Amount':>10} | {'Time':>6}\n"
    out += f"{'─'*12} | {'─'*6} | {'─'*10} | {'─'*6}\n"

    total_long = 0.0
    total_short = 0.0

    for r in show:
        if r["side"] == "LONG":
            total_long += r["amount"]
        else:
            total_short += r["amount"]
        out += (
            f"{_fmt_num(r['price'], '$'):>12} | {r['side']:>6} | "
            f"{_fmt_num(r['amount']):>10} | {_ts_wib(r['time']):>6}\n"
        )

    out += "```\n\n"

    # Factual stats only — no interpretation
    biggest = rows[0]
    out += (
        f"**Stats:** Largest: {_fmt_num(biggest['price'], '$')} "
        f"({biggest['side']} {_fmt_num(biggest['amount'])})\n"
        f"Total LONG liq: {_fmt_num(total_long)} | "
        f"Total SHORT liq: {_fmt_num(total_short)}\n\n"
    )

    return out


def _fmt_p2_hyperliquid_ls(data: list) -> str:
    """Format Hyperliquid L/S ratio as table — factual only."""
    if not data:
        return "**No data**\n\n"
    out = "```\n"
    out += f"{'Time':>6} | {'Long %':>8} | {'Short %':>8} | {'Ratio':>8} | {'Accts':>7}\n"
    out += f"{'─'*6} | {'─'*8} | {'─'*8} | {'─'*8} | {'─'*7}\n"
    for row in data:
        if not isinstance(row, dict):
            continue
        t = _get(row, "t", "time", "timestamp", default=0)
        long_pct = float(_get(row, "global_account_long_percent", "longPercent", "long_percent", default=0))
        short_pct = float(_get(row, "global_account_short_percent", "shortPercent", "short_percent", default=0))
        ratio = float(_get(row, "global_account_long_short_ratio", "longShortRatio", "ratio", default=0))
        total = int(float(_get(row, "global_account_total_count", "totalCount", default=0)))
        out += f"{_ts_wib(t):>6} | {long_pct:>7.1f}% | {short_pct:>7.1f}% | {ratio:>8.3f} | {total:>7}\n"
    out += "```\n\n"
    return out


def _fmt_p2_top_position_ls(data: list) -> str:
    """Format Top Trader Position L/S ratio as table — factual only."""
    if not data:
        return "**No data**\n\n"
    out = "```\n"
    out += f"{'Time':>6} | {'Long %':>8} | {'Short %':>8} | {'Ratio':>8}\n"
    out += f"{'─'*6} | {'─'*8} | {'─'*8} | {'─'*8}\n"
    for row in data:
        if not isinstance(row, dict):
            continue
        t = _get(row, "t", "time", "timestamp", default=0)
        long_pct = _get(row, "top_position_long_percent", "longAccount", "longRate", default=None)
        short_pct = _get(row, "top_position_short_percent", "shortAccount", "shortRate", default=None)
        ratio = _get(row, "top_position_long_short_ratio", "longShortRatio", "ratio", default=None)
        if long_pct is not None:
            long_pct = float(long_pct)
            if 0 < long_pct <= 1:
                long_pct *= 100
        if short_pct is not None:
            short_pct = float(short_pct)
            if 0 < short_pct <= 1:
                short_pct *= 100
        if ratio is not None:
            ratio = float(ratio)
        l_str = f"{long_pct:.1f}%" if long_pct is not None else "N/A"
        s_str = f"{short_pct:.1f}%" if short_pct is not None else "N/A"
        r_str = f"{ratio:.3f}" if ratio is not None else "N/A"
        out += f"{_ts_wib(t):>6} | {l_str:>8} | {s_str:>8} | {r_str:>8}\n"
    out += "```\n\n"
    return out


def _fmt_p2_spot_large_orders(data: list) -> str:
    """Format Spot Large Orders as table — factual only."""
    if not data:
        return "**No large orders**\n\n"
    # Sort by USD amount descending
    rows = []
    for row in data:
        if not isinstance(row, dict):
            continue
        price = float(_get(row, "limit_price", "price", default=0))
        amount_usd = float(_get(row, "current_usd_value", "start_usd_value", "amountUsd", default=0))
        side_raw = _get(row, "order_side", "orderSide", "side", "posSide", default="")
        # order_side: 1=BID(buy), 2=ASK(sell)
        if side_raw in (1, "1"):
            side = "BID"
        elif side_raw in (2, "2"):
            side = "ASK"
        elif isinstance(side_raw, str):
            side = side_raw.upper()
            if side in ("BUY",):
                side = "BID"
            elif side in ("SELL",):
                side = "ASK"
        else:
            side = "?"
        if amount_usd > 0:
            rows.append({"price": price, "amount_usd": amount_usd, "side": side})
    if not rows:
        return "**No large orders**\n\n"
    rows.sort(key=lambda r: r["amount_usd"], reverse=True)
    show = rows[:10]
    out = "```\n"
    out += f"{'Price':>12} | {'Side':>5} | {'Amount':>10}\n"
    out += f"{'─'*12} | {'─'*5} | {'─'*10}\n"
    total_bid = 0.0
    total_ask = 0.0
    for r in show:
        if r["side"] == "BID":
            total_bid += r["amount_usd"]
        else:
            total_ask += r["amount_usd"]
        out += f"{_fmt_num(r['price'], '$'):>12} | {r['side']:>5} | {_fmt_num(r['amount_usd']):>10}\n"
    out += "```\n\n"
    bid_count = len([r for r in show if r["side"] == "BID"])
    ask_count = len([r for r in show if r["side"] == "ASK"])
    out += (
        f"**Stats:** {bid_count} bids / {ask_count} asks | "
        f"Total BID: {_fmt_num(total_bid)} | Total ASK: {_fmt_num(total_ask)}\n\n"
    )
    return out


def _fmt_spot_netflow(data) -> str:
    """Format Spot Net Flow multi-timeframe as readable table — factual only."""
    if not data:
        return "**No data**\n\n"
    d = data if isinstance(data, dict) else (data[0] if isinstance(data, list) and data else {})
    if not d:
        return "**No data**\n\n"
    timeframes = ["5m", "15m", "30m", "1h", "4h", "12h", "24h"]
    out = "```\n"
    out += f"{'TF':>5} | {'Buy':>12} | {'Sell':>12} | {'Net':>12} | {'Chg%':>8}\n"
    out += f"{'─'*5} | {'─'*12} | {'─'*12} | {'─'*12} | {'─'*8}\n"
    for tf in timeframes:
        buy = float(d.get(f"taker_buy_volume_usd_{tf}", 0))
        sell = float(d.get(f"taker_sell_volume_usd_{tf}", 0))
        net = float(d.get(f"net_flow_usd_{tf}", 0))
        chg = d.get(f"net_flow_usd_change_percent_{tf}")
        chg_str = f"{float(chg):+.1f}%" if chg is not None else "N/A"
        out += f"{tf:>5} | {_fmt_num(buy):>12} | {_fmt_num(sell):>12} | {_fmt_num(net, signed=True):>12} | {chg_str:>8}\n"
    out += "```\n\n"
    return out


def _fmt_nansen_flows(data: dict, symbol: str) -> str:
    """Format Nansen token flow intelligence — factual only, no interpretation."""
    if not data:
        return "**No flow data**\n\n"

    segments = [
        ("Whale", "whale"),
        ("Smart Trader", "smart_trader"),
        ("Top PnL", "top_pnl"),
        ("Public Figure", "public_figure"),
        ("Exchange", "exchange"),
        ("Fresh Wallets", "fresh_wallets"),
    ]

    out = ""
    for label, prefix in segments:
        net = data.get(f"{prefix}_net_flow_usd")
        avg = data.get(f"{prefix}_avg_flow_usd")
        count = data.get(f"{prefix}_wallet_count", 0)

        if net is None:
            out += f"- {label:<16}: No data\n"
            continue

        net = float(net)
        direction = "inflow" if net >= 0 else "outflow"
        ratio = abs(net / avg) if avg and avg != 0 else 0
        count_str = f", {count} wallets" if count else ""

        out += (
            f"- {label:<16}: net {direction} {_fmt_num(abs(net))} "
            f"({ratio:.1f}x avg{count_str})\n"
        )

    out += "\n"
    return out


def _fmt_whale_alert(data: list) -> str:
    """Format Hyperliquid whale alerts as readable table — factual only."""
    if not data:
        return "**No whale alerts**\n\n"
    out = "```\n"
    out += f"{'Time':>6} | {'Coin':>6} | {'Action':>6} | {'Side':>6} | {'Size':>12} | {'Entry':>12}\n"
    out += f"{'─'*6} | {'─'*6} | {'─'*6} | {'─'*6} | {'─'*12} | {'─'*12}\n"
    total_long_open = 0.0
    total_short_open = 0.0
    for w in data[:20]:  # show top 20
        if not isinstance(w, dict):
            continue
        sym = w.get("symbol", "?")
        pos_size = float(w.get("position_size", 0))
        pos_value = float(w.get("position_value_usd", 0))
        entry = float(w.get("entry_price", 0))
        action_raw = w.get("position_action", 0)
        t = w.get("create_time", w.get("time", 0))
        direction = "LONG" if pos_size > 0 else "SHORT"
        action = "OPEN" if action_raw == 1 else "CLOSE"
        if action == "OPEN" and direction == "LONG":
            total_long_open += pos_value
        elif action == "OPEN" and direction == "SHORT":
            total_short_open += pos_value
        out += (
            f"{_ts_wib(t):>6} | {sym:>6} | {action:>6} | {direction:>6} | "
            f"{_fmt_num(pos_value):>12} | {_fmt_num(entry, '$'):>12}\n"
        )
    out += "```\n\n"
    out += (
        f"**Stats:** Open LONG: {_fmt_num(total_long_open)} | "
        f"Open SHORT: {_fmt_num(total_short_open)}\n\n"
    )
    return out


def _fmt_liq_history(data: list) -> str:
    """Format liquidation history time-series as readable table — factual only."""
    if not data:
        return "**Empty dataset**\n\n"
    show = data[-15:]
    total = len(data)
    out = f"*(last {len(show)} of {total})*\n\n"
    out += "```\n"
    out += f"{'Time':>6} | {'Long Liq':>12} | {'Short Liq':>12} | {'Total':>12}\n"
    out += f"{'─'*6} | {'─'*12} | {'─'*12} | {'─'*12}\n"
    total_long = 0.0
    total_short = 0.0
    for row in show:
        if not isinstance(row, dict):
            continue
        t = _get(row, "t", "time", "timestamp", "createTime", default=0)
        long_liq = float(_get(row, "longLiquidationUsd", "long_liquidation_usd",
                               "longVolUsd", "buyVolUsd", default=0))
        short_liq = float(_get(row, "shortLiquidationUsd", "short_liquidation_usd",
                                "shortVolUsd", "sellVolUsd", default=0))
        total_row = long_liq + short_liq
        total_long += long_liq
        total_short += short_liq
        out += f"{_ts_wib(t):>6} | {_fmt_num(long_liq):>12} | {_fmt_num(short_liq):>12} | {_fmt_num(total_row):>12}\n"
    out += "```\n\n"
    out += (
        f"**Stats:** Total LONG liq: {_fmt_num(total_long)} | "
        f"Total SHORT liq: {_fmt_num(total_short)}\n\n"
    )
    return out


# Map full_scan labels to their formatters
_SCAN_FORMATTERS = {
    "Spot CVD": lambda data, label: _fmt_scan_cvd(data, label),
    "Futures CVD": lambda data, label: _fmt_scan_cvd(data, label),
    "Open Interest": lambda data, label: _fmt_scan_oi(data),
    "Funding Rate": lambda data, label: _fmt_scan_fr(data),
    "Orderbook Delta": lambda data, label: _fmt_scan_ob_delta(data),
    "Price OHLC": lambda data, label: _fmt_scan_price(data),
    "Taker Buy/Sell": lambda data, label: _fmt_scan_taker(data),
    "Long/Short Ratio": lambda data, label: _fmt_scan_ls_ratio(data),
    "Liquidation Heatmap": lambda data, label: _fmt_scan_liq_heatmap(data),
}


def _fmt_fr_exchange_list(result: FetchResult, label: str,
                          extra_warnings: list[str] | None = None) -> str:
    """Format FR exchange-list with compact per-exchange breakdown."""
    warnings = list(extra_warnings or [])
    header = f"## Funding Rate — {label}\n\n"

    if result.is_expired:
        warnings.append(f"DATA EXPIRED ({result.age_seconds:.0f}s old) — DO NOT USE FOR ENTRY")
    elif result.is_stale:
        warnings.append(f"DATA STALE ({result.age_seconds:.0f}s old)")

    data = result.data
    if not data:
        return make_envelope("failed", "coinglass",
                             f"{header}**No funding rate data found.**",
                             data_age_seconds=result.age_seconds, warnings=warnings)
    # If already compacted (list of dicts with 'exchange' key)
    if isinstance(data, list) and len(data) > 0 and isinstance(data[0], dict):
        if "exchange" in data[0]:
            content = header + _fmt_scan_fr(data)
            status = "failed" if result.is_expired else "success"
            return make_envelope(status, "coinglass", content,
                                 data_age_seconds=result.age_seconds, warnings=warnings)
    # Raw format — compact it
    compacted = _format_fr_compact(data, label)
    if not compacted:
        return make_envelope("failed", "coinglass",
                             f"{header}**No active funding rate entries.**",
                             data_age_seconds=result.age_seconds, warnings=warnings)
    content = header + _fmt_scan_fr(compacted)
    status = "failed" if result.is_expired else "success"
    return make_envelope(status, "coinglass", content,
                         data_age_seconds=result.age_seconds, warnings=warnings)


def _fmt_funding_rate_all(data: list) -> str:
    """Format funding rate list for ALL coins (standalone tool)."""
    if not data:
        return "**Empty dataset**\n\n"
    out = "```\n"
    out += f" {'Symbol':<8} | {'Exchange':<12} | {'FR':>10} | {'Interval':>8} | {'Next':>6}\n"
    out += f" {'─' * 8} | {'─' * 12} | {'─' * 10} | {'─' * 8} | {'─' * 6}\n"
    for row in data:
        sym = row.get("symbol", "?")
        ex = row.get("exchange", "?")
        fr = row.get("funding_rate", 0)
        interval = row.get("interval_h", 8)
        nf = row.get("next_funding")
        nf_str = _ts_wib(nf) if nf else "N/A"
        fr_pct = float(fr) if fr else 0  # CoinGlass already returns %
        out += f" {sym:<8} | {ex:<12} | {fr_pct:>+9.4f}% | {interval:>7}h | {nf_str:>6}\n"
    out += "```\n\n"
    return out


def _fmt_fr_arbitrage(data: list) -> str:
    """Format FR arbitrage opportunities."""
    if not data:
        return "**Empty dataset**\n\n"
    out = "```\n"
    out += f" {'Symbol':<10} | {'FR':>10} | {'APR':>8} | {'Est.Income':>12} | {'Exchange':>10}\n"
    out += f" {'─' * 10} | {'─' * 10} | {'─' * 8} | {'─' * 12} | {'─' * 10}\n"
    for row in data:
        sym = _get(row, "symbol", "coin", default="?")
        fr = float(_get(row, "fundingRate", "funding_rate", "fr", default=0))
        apr = float(_get(row, "apr", "annualRate", default=0))
        income = float(_get(row, "income", "estimatedIncome", "est_income", default=0))
        ex = _get(row, "exchangeName", "exchange", default="?")
        out += f" {sym:<10} | {fr:>+9.4f}% | {apr:>+7.2f}% | {_fmt_num(income):>12} | {ex:>10}\n"
    out += "```\n\n"
    return out


def _fmt_fear_greed(data: list) -> str:
    """Format Fear & Greed Index history."""
    if not data:
        return "**Empty dataset**\n\n"
    show = data[-20:] if len(data) > 20 else data
    out = ""
    if len(data) > 20:
        out += f"*(showing last {len(show)} of {len(data)})*\n\n"
    out += "```\n"
    out += f" {'Date':>12} | {'Value':>6} | Classification\n"
    out += f" {'─' * 12} | {'─' * 6} | ──────────────\n"
    for row in show:
        t = _get(row, "t", "time", "timestamp", "date", default=0)
        val = _get(row, "value", "v", default=0)
        cls = _get(row, "classification", "valueClassification",
                   "value_classification", default="—")
        if t:
            try:
                ts = float(t)
                if ts > 1e12:
                    ts /= 1000
                date_str = datetime.fromtimestamp(ts, tz=WIB).strftime("%Y-%m-%d")
            except (ValueError, OSError):
                date_str = str(t)[:12]
        else:
            date_str = "—"
        out += f" {date_str:>12} | {int(float(val)):>6} | {cls}\n"
    out += "```\n\n"
    # Latest value summary
    if show:
        latest = show[-1]
        v = int(float(_get(latest, "value", "v", default=0)))
        c = _get(latest, "classification", "valueClassification",
                 "value_classification", default="?")
        out += f"**Current:** {v} — {c}\n\n"
    return out


def _fmt_coins_markets(data: list) -> str:
    """Format coins-markets overview (futures or spot market data).

    Handles both futures fields (open_interest_usd, avg_funding_rate_by_oi)
    and spot fields (volume_usd_24h, volume_flow_usd_24h).
    """
    if not data:
        return "**Empty dataset**\n\n"

    # Detect if this is futures data (has OI fields) or spot data
    sample = data[0] if isinstance(data[0], dict) else {}
    is_futures = "open_interest_usd" in sample

    if is_futures:
        out = "```\n"
        out += (f" {'Symbol':<8} | {'Price':>12} | {'24h %':>8} | {'OI':>12}"
                f" | {'OI Δ24h':>8} | {'FR':>9} | {'L/S 24h':>8} | {'Liq 24h':>10}\n")
        out += (f" {'─' * 8} | {'─' * 12} | {'─' * 8} | {'─' * 12}"
                f" | {'─' * 8} | {'─' * 9} | {'─' * 8} | {'─' * 10}\n")
        for row in data:
            sym = _get(row, "symbol", default="?")
            price = float(_get(row, "current_price", "price", "lastPrice", default=0))
            pct = float(_get(row, "price_change_percent_24h", "priceChangePercent", default=0))
            oi = float(_get(row, "open_interest_usd", "openInterest", default=0))
            oi_chg = float(_get(row, "open_interest_change_percent_24h", default=0))
            fr = float(_get(row, "avg_funding_rate_by_oi", "fundingRate", default=0))
            ls = float(_get(row, "long_short_ratio_24h", default=0))
            liq = float(_get(row, "liquidation_usd_24h", default=0))
            out += (f" {sym:<8} | {_fmt_num(price):>12} | {pct:>+7.2f}% | {_fmt_num(oi):>12}"
                    f" | {oi_chg:>+7.2f}% | {fr:>+8.4f}% | {ls:>8.4f} | {_fmt_num(liq):>10}\n")
        out += "```\n\n"
    else:
        # Spot market data
        out = "```\n"
        out += (f" {'Symbol':<8} | {'Price':>12} | {'24h %':>8} | {'Vol 24h':>12}"
                f" | {'Buy Vol':>12} | {'Sell Vol':>12} | {'Net Flow':>12}\n")
        out += (f" {'─' * 8} | {'─' * 12} | {'─' * 8} | {'─' * 12}"
                f" | {'─' * 12} | {'─' * 12} | {'─' * 12}\n")
        for row in data:
            sym = _get(row, "symbol", default="?")
            price = float(_get(row, "current_price", "price", default=0))
            pct = float(_get(row, "price_change_percent_24h", default=0))
            vol = float(_get(row, "volume_usd_24h", "vol24h", default=0))
            buy_vol = float(_get(row, "buy_volume_usd_24h", default=0))
            sell_vol = float(_get(row, "sell_volume_usd_24h", default=0))
            flow = float(_get(row, "volume_flow_usd_24h", default=0))
            out += (f" {sym:<8} | {_fmt_num(price):>12} | {pct:>+7.2f}% | {_fmt_num(vol):>12}"
                    f" | {_fmt_num(buy_vol):>12} | {_fmt_num(sell_vol):>12} | {_fmt_num(flow):>12}\n")
        out += "```\n\n"

    return out


def _fmt_indicator_ts(data: list) -> str:
    """Format indicator time-series (RSI, MA, EMA, MACD, ATR, Whale Index)."""
    if not data:
        return "**Empty dataset**\n\n"
    show = data[-20:] if len(data) > 20 else data
    out = ""
    if len(data) > 20:
        out += f"*(showing last {len(show)} of {len(data)})*\n\n"
    # Detect fields from first row
    sample = show[0] if isinstance(show[0], dict) else {}
    val_keys = [k for k in sample.keys()
                if k not in ("t", "time", "timestamp", "createTime")
                and not isinstance(sample[k], (list, dict))][:4]
    out += "```\n"
    header = f" {'Time':>6}"
    for k in val_keys:
        header += f" | {k:>12}"
    out += header + "\n"
    sep = f" {'─' * 6}"
    for k in val_keys:
        sep += f" | {'─' * 12}"
    out += sep + "\n"
    for row in show:
        if not isinstance(row, dict):
            continue
        t = _get(row, "t", "time", "timestamp", "createTime", default=0)
        line = f" {_ts_wib(t):>6}"
        for k in val_keys:
            v = row.get(k)
            if v is not None:
                line += f" | {_fmt_num(float(v)):>12}"
            else:
                line += f" | {'—':>12}"
        out += line + "\n"
    out += "```\n\n"
    return out


def _fmt_news(data: list) -> str:
    """Format crypto news articles."""
    if not data:
        return "**No news articles.**\n\n"
    out = ""
    for i, article in enumerate(data[:20], 1):
        title = _get(article, "title", "headline", default="Untitled")
        source = _get(article, "source", "sourceName", default="")
        t = _get(article, "createTime", "time", "timestamp", "publishedAt", default=0)
        date_str = ""
        if t:
            try:
                ts = float(t)
                if ts > 1e12:
                    ts /= 1000
                date_str = datetime.fromtimestamp(ts, tz=WIB).strftime("%m-%d %H:%M")
            except (ValueError, OSError):
                date_str = ""
        out += f"**{i}.** {title}\n"
        if source or date_str:
            out += f"   _{source}_ | {date_str}\n"
        out += "\n"
    return out
