"""CoinGlass MCP Server — hardened for trading-critical data integrity.

AUDIT COMPLIANCE:
- Every response includes data_age + staleness warnings
- full_scan blocks analysis if critical metrics fail
- Symbol normalization prevents silent mismatches
- Rate-limited requests prevent 429 errors
- API key never appears in any output
- Interpretation hints for every metric
"""

from __future__ import annotations

import json
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone, timedelta

WIB = timezone(timedelta(hours=7))
from typing import Any

from dotenv import load_dotenv
from fastmcp import FastMCP

from .arkham import register_arkham_tools, arkham_get
from .nansen import register_nansen_tools, nansen_token_flow_intelligence, NANSEN_TOKEN_MAP
from .binance_client import (
    close_client as close_binance_client,
    binance_futures_request,
    binance_spot_request,
)
from .client import CoinGlassClient, FetchResult
from .config import (
    DEFAULT_EXCHANGE,
    STALE_EXPIRED_THRESHOLD,
    STALE_WARNING_THRESHOLD,
    Config,
    normalize_symbol,
    to_cg_symbol,
    to_pair,
)

load_dotenv()

# ─── Global State ─────────────────────────────────────────────────────────────

config = Config.from_env()
client = CoinGlassClient(config)


@asynccontextmanager
async def lifespan(app):
    """Manage shared httpx client + SQLite storage lifecycle."""
    await client.start()
    try:
        yield
    finally:
        await client.close()
        await close_binance_client()


mcp = FastMCP(
    name="coinglass-mcp",
    instructions=(
        "CoinGlass + Binance crypto derivatives analytics for order flow trading. "
        "CoinGlass: SpotCVD, FuturesCVD, Funding Rate, Open Interest, Liquidation, Orderbook. "
        "Binance: Direct market data — spot prices, futures OI, funding rate, L/S ratio, taker volume, klines, depth. "
        "Optimized for Ricoz Scalping Framework. "
        "IMPORTANT: Always check data_age in CoinGlass responses. "
        "Data older than 2 minutes has WARNING. Data older than 5 minutes must NOT be used for entries."
    ),
    lifespan=lifespan,
)

# ─── Register Arkham Intel Tools ──────────────────────────────────────────────
register_arkham_tools(mcp)

# ─── Register Nansen Smart Money Tools ────────────────────────────────────────
register_nansen_tools(mcp)

# ─── Register Chart Tool ─────────────────────────────────────────────────────
import base64

from mcp.types import ImageContent, TextContent

from .chart import get_chart


@mcp.tool(output_schema=None)
async def coinglass_chart(
    symbol: str = "BTC",
    interval: str = "5m",
    width: int = 1200,
    height: int = 600,
    theme: str = "dark",
):
    """Generate TradingView chart screenshot — EMA21 + EMA50 + VWAP + Volume.

    Returns chart IMAGE directly (Claude can see it) + metadata text.
    Setup: Ricoz chart (dark theme, EMA21 cyan, EMA50 white, VWAP yellow)
    Auto-adapts to plan limits (paid: 1200x600 + Volume, free: 800x600).
    Timezone: WIB (Asia/Jakarta)

    Args:
        symbol: Coin symbol (SOL, BTC, ETH, HYPE, AVAX, SUI, XRP, BNB, etc.)
        interval: Candle interval (1m, 5m, 15m, 30m, 1h, 4h, 1d)
        width: Chart width px (default 1200, auto-downsized on free plan)
        height: Chart height px (default 600)
        theme: dark or light (default dark)
    """
    result = await get_chart(
        symbol=symbol, interval=interval,
        width=width, height=height, theme=theme,
    )
    if "error" in result:
        return f"## Chart Error — {symbol}\n\nError: {result['error']}"

    # Download image so Claude can see it directly
    import httpx
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            img_resp = await client.get(result["url"])
            img_resp.raise_for_status()
            img_b64 = base64.b64encode(img_resp.content).decode("utf-8")
    except Exception:
        # Fallback to URL-only if download fails
        return (
            f"## TradingView Chart — {symbol} | {interval}\n\n"
            f"**Chart URL:** {result['url']}\n"
            f"*(Image download failed — open URL manually)*"
        )

    meta = (
        f"## TradingView Chart — {symbol} | {interval}\n"
        f"**Symbol:** {result['symbol_tv']} | "
        f"**Setup:** EMA21 (cyan) + EMA50 (white) + VWAP (yellow) + Volume | "
        f"**Theme:** {theme} | **Timezone:** WIB"
    )
    return [
        TextContent(type="text", text=meta),
        ImageContent(type="image", data=img_b64, mimeType="image/png"),
    ]


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


def fmt(result: FetchResult, title: str = "") -> str:
    """Format API response with age banner and staleness warnings."""
    data = result.data
    header = ""
    if title:
        header = f"## {title}\n\n"
    header += _age_banner(result)

    if data is None:
        return f"{header}**ERROR: No data returned.** The symbol may not exist."

    if isinstance(data, list):
        if len(data) == 0:
            return f"{header}**WARNING: Empty dataset.** No data points returned."
        if len(data) > 20:
            total = len(data)
            data = data[-20:]
            header += f"*(showing last 20 of {total} entries)*\n\n"
        return header + json.dumps(data, indent=2, default=str)
    elif isinstance(data, dict):
        return header + json.dumps(data, indent=2, default=str)
    else:
        return header + str(data)


def fmt_parsed(result: FetchResult, title: str, formatter) -> str:
    """Format API response using a parsed formatter instead of raw JSON.
    formatter(data) should return a formatted string."""
    header = ""
    if title:
        header = f"## {title}\n\n"
    header += _age_banner(result)
    data = result.data
    if data is None:
        return f"{header}**ERROR: No data returned.** The symbol may not exist."
    if isinstance(data, list) and len(data) == 0:
        return f"{header}**WARNING: Empty dataset.** No data points returned."
    if isinstance(data, (list, dict)) and data:
        return header + formatter(data)
    return header + str(data)


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

    show = data[-10:]
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

    show = data[-10:]
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
            fr_pct = float(fr) * 100 if fr else 0
            out += f"{ex:<12} | {fr_pct:>+10.4f}% | {interval:>7}h | {nf_str:>14}\n"

        out += "```\n\n"

        if fr_values:
            avg_fr = sum(fr_values) / len(fr_values) * 100
            out += f"**Summary:** Avg FR: {avg_fr:+.4f}% across {len(fr_values)} exchanges\n\n"
        return out
    else:
        # Fallback for unexpected format
        return json.dumps(data, indent=2, default=str) + "\n\n"


def _fmt_scan_ob_delta(data: list) -> str:
    """Format Orderbook Delta time-series as readable table."""
    if not data:
        return "**Empty dataset**\n\n"

    show = data[-10:]
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

    show = data[-10:]
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

    show = data[-10:]
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

    show = data[-10:]
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
    show = data[-10:]
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


def _fmt_fr_exchange_list(result: FetchResult, label: str) -> str:
    """Format FR exchange-list with compact per-exchange breakdown."""
    header = f"## Funding Rate — {label}\n\n"
    header += _age_banner(result)
    data = result.data
    if not data:
        return f"{header}**No funding rate data found.**"
    # If already compacted (list of dicts with 'exchange' key)
    if isinstance(data, list) and len(data) > 0 and isinstance(data[0], dict):
        if "exchange" in data[0]:
            return header + json.dumps(data, indent=2, default=str)
    # Raw format — compact it
    compacted = _format_fr_compact(data, label)
    if not compacted:
        return f"{header}**No active funding rate entries.**"
    return header + json.dumps(compacted, indent=2, default=str)



# ═══════════════════════════════════════════════════════════════════════════════
# CRITICAL TOOLS (1-6) — Ricoz Scalping Framework Core
# ═══════════════════════════════════════════════════════════════════════════════


@mcp.tool()
async def coinglass_spot_cvd(
    symbol: str = "BTC",
    interval: str = "5m",
    limit: int = 100,
    exchange: str = DEFAULT_EXCHANGE,
) -> str:
    """Get Spot CVD (Cumulative Volume Delta) — PRIMARY VETO SIGNAL.

    SpotCVD is the #1 signal in Ricoz Scalping Framework.
    - SpotCVD positive + rising = spot buyers dominant (BULLISH)
    - SpotCVD negative = DO NOT LONG regardless of other signals
    - Look at DIRECTION, not just absolute number

    Args:
        symbol: Coin symbol (BTC, ETH, SOL, HYPE, AVAX, etc.)
        interval: Candle interval (1m, 5m, 15m, 30m, 1h, 4h, 12h, 1d)
        limit: Number of data points (max 4500)
        exchange: Exchange name (Binance, OKX, Bybit, etc.)
    """
    sym = normalize_symbol(symbol)
    result = await client.get("/api/spot/aggregated-cvd/history", {
        "exchange_list": exchange,
        "symbol": sym,
        "interval": interval,
        "limit": limit,
    })
    return fmt_parsed(result, f"Spot CVD — {sym}", lambda d: _fmt_scan_cvd(d, "Spot CVD"))


@mcp.tool()
async def coinglass_futures_cvd(
    symbol: str = "BTC",
    interval: str = "5m",
    limit: int = 100,
    exchange: str = DEFAULT_EXCHANGE,
) -> str:
    """Get Futures CVD (Cumulative Volume Delta) — ENTRY FILTER signal.

    Used together with SpotCVD to confirm directional bias.
    - Both CVDs rising = strong LONG setup
    - Both CVDs falling = strong SHORT setup
    - Divergence = caution, wait for alignment

    Args:
        symbol: Coin symbol (BTC, ETH, SOL, etc.)
        interval: Candle interval (1m, 5m, 15m, 30m, 1h, 4h, 12h, 1d)
        limit: Number of data points (max 4500)
        exchange: Exchange name (Binance, OKX, Bybit, etc.)
    """
    sym = normalize_symbol(symbol)
    result = await client.get("/api/futures/aggregated-cvd/history", {
        "exchange_list": exchange,
        "symbol": sym,
        "interval": interval,
        "limit": limit,
    })
    return fmt_parsed(result, f"Futures CVD — {sym}", lambda d: _fmt_scan_cvd(d, "Futures CVD"))


@mcp.tool()
async def coinglass_funding_rate(symbol: str = "") -> str:
    """Get current Funding Rate for ALL coins across all exchanges.

    Funding Rate indicates market sentiment:
    - Positive FR = longs pay shorts (market bullish/overleveraged long)
    - Negative FR = shorts pay longs (market bearish/overleveraged short)
    - Extreme FR (>0.05%) = potential reversal zone
    - Near zero = neutral, good for directional trades

    Args:
        symbol: Ignored — always returns all coins. Accepted for compatibility.

    Returns data for all coins — only coins with active FR data shown.
    """
    result = await client.get("/api/futures/funding-rate/exchange-list")
    # Flatten to clean format: one row per coin+exchange with active FR
    if isinstance(result.data, list):
        clean = []
        for coin in result.data:
            sym = coin.get("symbol", "")
            margin_list = (coin.get("stablecoin_margin_list")
                           or coin.get("stablecoinMarginList") or [])
            for ex in margin_list:
                fr = ex.get("funding_rate", ex.get("fundingRate"))
                if fr is not None:
                    clean.append({
                        "symbol": sym,
                        "exchange": ex.get("exchange", "?"),
                        "funding_rate": fr,
                        "interval_h": ex.get("funding_rate_interval",
                                             ex.get("fundingRateInterval", 8)),
                        "next_funding": ex.get("next_funding_time",
                                               ex.get("nextFundingTime")),
                    })
        if clean:
            # Sort by extreme FR first (highest absolute value)
            clean.sort(key=lambda x: abs(x.get("funding_rate", 0)), reverse=True)
            total = len(clean)
            top50 = clean[:50]
            result = FetchResult(
                data=top50, age_seconds=result.age_seconds,
                is_cached=result.is_cached, fetched_at=result.fetched_at,
            )
    return fmt(result, f"Funding Rate — Top 50 Extreme FR (of {total} active)")


@mcp.tool()
async def coinglass_open_interest(
    symbol: str = "BTC",
    interval: str = "5m",
    limit: int = 100,
    exchange: str = DEFAULT_EXCHANGE,
) -> str:
    """Get Open Interest history (aggregated across exchanges).

    OI shows total futures positions:
    - OI rising + price rising = new longs entering (trend continuation)
    - OI rising + price falling = new shorts entering (bearish)
    - OI dropping + price rising = short squeeze
    - OI dropping + price falling = long liquidation cascade

    Args:
        symbol: Coin symbol (BTC, ETH, SOL, etc.)
        interval: Candle interval (1m, 5m, 15m, 30m, 1h, 4h, 12h, 1d)
        limit: Number of data points
        exchange: Exchange name (Binance, OKX, Bybit, etc.)
    """
    sym = normalize_symbol(symbol)
    params: dict = {
        "symbol": sym,
        "interval": interval,
        "limit": limit,
    }
    if exchange:
        params["exchange_list"] = exchange
    result = await client.get("/api/futures/open-interest/aggregated-history", params)
    return fmt_parsed(result, f"Open Interest — {sym}", _fmt_scan_oi)


@mcp.tool()
async def coinglass_liquidation_map(
    symbol: str = "BTC",
    exchange: str = DEFAULT_EXCHANGE,
    range: str = "3d",
) -> str:
    """Get Liquidation Heatmap — shows where liquidation clusters are.

    ⚠️ Requires Professional or Enterprise plan.

    Critical for identifying:
    - Magnetic zones (price tends to move toward liquidation clusters)
    - Stop loss clusters that market makers target
    - Potential reversal zones after liquidation sweeps

    Args:
        symbol: Coin symbol (BTC, ETH, SOL, etc.) — auto-converted to pair (BTCUSDT)
        exchange: Exchange name (Binance, OKX, Bybit, etc.)
        range: Time range (12h, 24h, 3d, 7d, 30d, 90d, 180d, 1y)
    """
    if not config.has_feature("liquidation_heatmap"):
        return (
            f"**ERROR:** Liquidation Heatmap requires Professional or Enterprise plan.\n"
            f"Current plan: {config.plan}"
        )
    pair = to_pair(symbol)
    result = await client.get("/api/futures/liquidation/heatmap/model1", {
        "exchange": exchange,
        "symbol": pair,
        "range": range,
    })
    return fmt(result, f"Liquidation Heatmap — {pair} ({exchange}, {range})")


@mcp.tool()
async def coinglass_orderbook(
    symbol: str = "BTC",
    exchange: str = DEFAULT_EXCHANGE,
    interval: str = "5m",
    limit: int = 100,
    range: str = "1",
) -> str:
    """Get Aggregated Orderbook Ask/Bids History — OBDelta (bid/ask imbalance).

    Shows orderbook depth imbalance over time:
    - More bids than asks = buying pressure (bullish)
    - More asks than bids = selling pressure (bearish)
    - Sudden bid wall = potential support
    - Sudden ask wall = potential resistance

    Args:
        symbol: Coin symbol (BTC, ETH, SOL, etc.)
        exchange: Exchange name or comma-separated list (Binance, OKX, Bybit or ALL)
        interval: Candle interval (1m, 5m, 15m, 1h, 4h, 1d)
        limit: Number of data points (max 1000)
        range: Depth percentage (0.25, 0.5, 0.75, 1, 2, 3, 5, 10)
    """
    sym = normalize_symbol(symbol)
    result = await client.get("/api/futures/orderbook/aggregated-ask-bids-history", {
        "exchange_list": exchange,
        "symbol": sym,
        "interval": interval,
        "limit": limit,
        "range": range,
    })
    return fmt_parsed(result, f"Orderbook Delta — {normalize_symbol(symbol)}", _fmt_scan_ob_delta)


# ═══════════════════════════════════════════════════════════════════════════════
# IMPORTANT TOOLS (7-12) — Supporting Analytics
# ═══════════════════════════════════════════════════════════════════════════════


@mcp.tool()
async def coinglass_price_ohlc(
    symbol: str = "BTC",
    interval: str = "5m",
    limit: int = 100,
    exchange: str = DEFAULT_EXCHANGE,
) -> str:
    """Get price OHLC history from futures markets.

    Args:
        symbol: Coin symbol (BTC, ETH, SOL, etc.) — auto-converted to pair (BTCUSDT)
        interval: Candle interval (1m, 5m, 15m, 30m, 1h, 4h, 12h, 1d)
        limit: Number of data points
        exchange: Exchange name (Binance, OKX, Bybit, etc.)
    """
    pair = to_pair(symbol)
    result = await client.get("/api/futures/price/history", {
        "exchange": exchange,
        "symbol": pair,
        "interval": interval,
        "limit": limit,
    })
    return fmt_parsed(result, f"Price OHLC — {normalize_symbol(symbol)} ({interval})", _fmt_scan_price)


@mcp.tool()
async def coinglass_liquidation_history(
    symbol: str = "BTC",
    interval: str = "1h",
    limit: int = 100,
    exchange: str = DEFAULT_EXCHANGE,
) -> str:
    """Get pair liquidation history — volume of liquidations over time.

    Shows when and how much was liquidated:
    - High liquidation volume = volatile period
    - Long liquidations at support = potential bottom
    - Short liquidations at resistance = potential top

    Args:
        symbol: Coin symbol (BTC, ETH, SOL, etc.) — auto-converted to pair (BTCUSDT)
        interval: Candle interval (1m, 5m, 15m, 30m, 1h, 4h, 12h, 1d)
        limit: Number of data points
        exchange: Exchange name (Binance, OKX, Bybit, etc.)
    """
    pair = to_pair(symbol)
    result = await client.get("/api/futures/liquidation/history", {
        "exchange": exchange,
        "symbol": pair,
        "interval": interval,
        "limit": limit,
    })
    return fmt_parsed(result, f"Liquidation History — {normalize_symbol(symbol)}", _fmt_liq_history)


@mcp.tool()
async def coinglass_long_short_ratio(
    symbol: str = "BTC",
    interval: str = "1h",
    limit: int = 100,
    exchange: str = DEFAULT_EXCHANGE,
) -> str:
    """Get Global Long/Short Account Ratio.

    Shows retail sentiment:
    - High L/S ratio (>1.5) = too many longs, contrarian SHORT
    - Low L/S ratio (<0.7) = too many shorts, contrarian LONG
    - Use as contrarian indicator, not primary signal

    Args:
        symbol: Coin symbol (BTC, ETH, SOL, etc.)
        interval: Candle interval (1h, 4h, 12h, 1d)
        limit: Number of data points
        exchange: Exchange name (Binance, OKX, Bybit, etc.)
    """
    pair = to_pair(symbol)
    result = await client.get("/api/futures/global-long-short-account-ratio/history", {
        "exchange": exchange,
        "symbol": pair,
        "interval": interval,
        "limit": limit,
    })
    return fmt_parsed(result, f"Long/Short Ratio — {normalize_symbol(symbol)}", _fmt_scan_ls_ratio)


@mcp.tool()
async def coinglass_taker_buysell(
    symbol: str = "BTC",
    interval: str = "5m",
    limit: int = 100,
    exchange: str = DEFAULT_EXCHANGE,
) -> str:
    """Get Taker Buy/Sell Volume — shows aggressor side.

    Taker = market orders (aggressive traders):
    - Taker buy > sell = aggressive buyers, bullish
    - Taker sell > buy = aggressive sellers, bearish
    - Supports CVD signals

    Args:
        symbol: Coin symbol (BTC, ETH, SOL, etc.) — auto-converted to pair (BTCUSDT)
        interval: Candle interval (1m, 5m, 15m, 30m, 1h, 4h, 12h, 1d)
        limit: Number of data points
        exchange: Exchange name (Binance, OKX, Bybit, etc.)
    """
    sym = normalize_symbol(symbol)
    result = await client.get("/api/futures/aggregated-taker-buy-sell-volume/history", {
        "exchange_list": exchange,
        "symbol": sym,
        "interval": interval,
        "limit": limit,
    })
    return fmt_parsed(result, f"Taker Buy/Sell — {sym}", _fmt_scan_taker)


@mcp.tool()
async def coinglass_fr_arbitrage(
    usd: int = 10000,
    exchange: str = "",
) -> str:
    """Get Funding Rate Arbitrage — find extreme funding rates across coins.

    Shows coins with highest/lowest funding rates:
    - Extreme positive FR = potential short opportunity
    - Extreme negative FR = potential long opportunity
    - Good for finding FR arbitrage trades

    Args:
        usd: Position size in USD for calculating FR income (default: 10000)
        exchange: Comma-separated exchange filter (e.g. Binance,OKX)
    """
    params: dict = {"usd": usd}
    if exchange:
        params["exchange_list"] = exchange
    result = await client.get("/api/futures/funding-rate/arbitrage", params)
    # Sort by APR descending, show top 20 best opportunities
    if isinstance(result.data, list) and result.data:
        sorted_data = sorted(result.data, key=lambda x: abs(x.get("apr", 0)), reverse=True)
        total = len(sorted_data)
        top20 = sorted_data[:20]
        result = FetchResult(
            data=top20, age_seconds=result.age_seconds,
            is_cached=result.is_cached, fetched_at=result.fetched_at,
        )
        output = fmt(result, f"Funding Rate Arbitrage — ${usd} position")
        if total > 20:
            output = output.replace(
                f"## Funding Rate Arbitrage",
                f"## Funding Rate Arbitrage (top 20 of {total} by APR)",
                1,
            )
        return output
    return fmt(result, f"Funding Rate Arbitrage — ${usd} position")


@mcp.tool()
async def coinglass_coins_markets(
    exchange: str = "",
    page: int = 1,
    per_page: int = 20,
) -> str:
    """Get market overview for all futures coins.

    Returns comprehensive market data including:
    - Price, 24h change, volume
    - Open Interest, FR, Long/Short ratio
    - Good for market scanning and finding opportunities

    Args:
        exchange: Comma-separated exchange filter (e.g. Binance,OKX)
        page: Page number (default: 1)
        per_page: Results per page (default: 20)
    """
    params: dict = {"page": page, "per_page": per_page}
    if exchange:
        params["exchange_list"] = exchange
    result = await client.get("/api/futures/coins-markets", params)
    return fmt(result, f"Futures Market Overview — page {page}")


# ═══════════════════════════════════════════════════════════════════════════════
# NICE-TO-HAVE TOOLS (13-17)
# ═══════════════════════════════════════════════════════════════════════════════


@mcp.tool()
async def coinglass_whale_alert(symbol: str = "") -> str:
    """Get Hyperliquid whale position alerts (~200 most recent, positions > $1M).

    Shows large trader positions on Hyperliquid:
    - Whale opening large long = bullish signal
    - Whale opening large short = bearish signal
    - Track whale PnL for sentiment
    - position_action: 1=open, 2=close | position_size: positive=long, negative=short

    Args:
        symbol: Ignored — returns all whales. Accepted for compatibility.
    """
    result = await client.get("/api/hyperliquid/whale-alert")
    return fmt_parsed(result, "Whale Alerts — Hyperliquid", _fmt_whale_alert)


@mcp.tool()
async def coinglass_fear_greed(symbol: str = "") -> str:
    """Get Fear & Greed Index history.

    Market sentiment indicator:
    - 0-25 = Extreme Fear (contrarian BUY zone)
    - 25-45 = Fear
    - 45-55 = Neutral
    - 55-75 = Greed
    - 75-100 = Extreme Greed (contrarian SELL zone)

    Args:
        symbol: Ignored — index is market-wide. Accepted for compatibility.
    """
    result = await client.get("/api/index/fear-greed-history")
    return fmt(result, "Fear & Greed Index")


@mcp.tool()
async def coinglass_footprint(
    symbol: str = "BTC",
    interval: str = "5m",
    limit: int = 10,
    exchange: str = DEFAULT_EXCHANGE,
) -> str:
    """Get Footprint chart — buy/sell volume at each price level.

    Shows WHERE buyers and sellers are active:
    - High buy imbalance at a level = support zone
    - High sell imbalance at a level = resistance zone
    - Total buy > sell = buyers absorbing (bullish)
    - Requires Standard plan or higher

    Args:
        symbol: Coin symbol (BTC, ETH, SOL, etc.)
        interval: Candle interval (5m, 15m, 1h, etc.)
        limit: Number of candles (default 10)
        exchange: Exchange name (Binance, OKX, Bybit, etc.)

    Response format per level:
    [price_low, price_high, buy_qty, sell_qty, buy_quote, sell_quote,
     buy_usdt, sell_usdt, buy_count, sell_count]
    """
    pair = to_pair(symbol)
    sym = normalize_symbol(symbol)
    result = await client.get("/api/futures/volume/footprint-history", {
        "exchange": exchange,
        "symbol": pair,
        "interval": interval,
        "limit": limit,
    })

    header = f"## Footprint — {sym} ({interval}, {exchange})\n\n"
    header += _age_banner(result)

    data = result.data
    if not isinstance(data, list) or len(data) == 0:
        return header + "**No footprint data available.**"

    # Filter out null candles (still forming)
    valid = [c for c in data if c is not None and isinstance(c, list) and len(c) >= 2]
    if not valid:
        return header + "**All candles still forming (null). Try larger limit.**"

    output = header
    for candle in valid[-5:]:  # Show last 5 completed candles
        ts = candle[0]
        levels = candle[1]
        if not isinstance(levels, list) or not levels:
            continue

        ts_str = datetime.fromtimestamp(ts, tz=WIB).strftime("%H:%M")
        total_buy = sum(l[6] for l in levels if len(l) > 7)
        total_sell = sum(l[7] for l in levels if len(l) > 7)
        delta = total_buy - total_sell
        dominant = "BUYERS" if delta > 0 else "SELLERS"
        buy_count = sum(l[8] for l in levels if len(l) > 8)
        sell_count = sum(l[9] for l in levels if len(l) > 9)

        output += f"### {ts_str} — **{dominant}** (delta ${delta:+,.0f})\n"
        output += f"Buy: ${total_buy:,.0f} ({buy_count} trades) | Sell: ${total_sell:,.0f} ({sell_count} trades)\n"

        # Top 3 imbalance levels
        ranked = sorted(levels, key=lambda l: abs(l[6] - l[7]) if len(l) > 7 else 0, reverse=True)
        for l in ranked[:3]:
            if len(l) > 7:
                mid = (l[0] + l[1]) / 2
                side = "BUY wall" if l[6] > l[7] else "SELL wall"
                output += f"- ${mid:,.2f}: buy ${l[6]:,.0f} vs sell ${l[7]:,.0f} → **{side}** ${abs(l[6]-l[7]):,.0f}\n"
        output += "\n"

    return output


@mcp.tool()
async def coinglass_spot_netflow(
    symbol: str = "BTC",
    exchange: str = "Binance, Bybit, OKX, Bitget, Gate",
) -> str:
    """Get spot exchange net flow — coins moving in/out of exchanges.

    Returns net flow across multiple timeframes (5m to 1y) in a single response.
    - Net inflow (positive) = more buy volume (bullish)
    - Net outflow (negative) = more sell volume (bearish)

    Args:
        symbol: Coin symbol (BTC, ETH, SOL, etc.)
        exchange: Comma-separated exchange list (default: Binance, Bybit, OKX, Bitget, Gate)
    """
    sym = normalize_symbol(symbol)
    result = await client.get("/api/spot/coin/netflow", {
        "symbol": sym,
        "exchange_list": exchange,
    })
    return fmt_parsed(result, f"Spot Net Flow — {sym}", _fmt_spot_netflow)


@mcp.tool()
async def coinglass_orderbook_heatmap(
    symbol: str = "BTC",
    exchange: str = DEFAULT_EXCHANGE,
    interval: str = "1h",
    limit: int = 100,
) -> str:
    """Get Orderbook Heatmap data — visual representation of order depth.

    Shows where large orders are placed:
    - Dense bid zones = potential support levels
    - Dense ask zones = potential resistance levels
    - Requires Standard plan or higher
    - History: 1m=3days, 5m=15days, others=150days

    Args:
        symbol: Coin symbol (BTC, ETH, SOL, etc.) — auto-converted to pair (BTCUSDT)
        exchange: Exchange name (Binance, OKX, Bybit, etc.)
        interval: Candle interval (1m, 5m, 15m, 1h, 4h, 1d)
        limit: Data points (max 100)
    """
    pair = to_pair(symbol)
    result = await client.get("/api/futures/orderbook/history", {
        "exchange": exchange,
        "symbol": pair,
        "interval": interval,
        "limit": min(limit, 100),
    })
    return fmt(result, f"Orderbook Heatmap — {normalize_symbol(symbol)}")


# ═══════════════════════════════════════════════════════════════════════════════
# CATEGORY TOOLS — All CoinGlass V4 Endpoints by Category
# ═══════════════════════════════════════════════════════════════════════════════


@mcp.tool()
async def coinglass_trading_market(
    action: str = "coins_markets",
    symbol: str = "",
    exchange: str = DEFAULT_EXCHANGE,
    interval: str = "1h",
    limit: int = 100,
    page: int = 1,
    per_page: int = 20,
) -> str:
    """Futures Trading Market data — 9 endpoints in one tool.

    Actions:
    - supported_coins: List all supported coin symbols
    - supported_exchanges: List all supported futures exchanges
    - supported_pairs: List exchange-pair combinations (optional: filter by exchange)
    - coins_markets: Market overview all coins (price, OI, volume, FR, liquidation)
    - pairs_markets: All trading pairs for a coin (requires symbol)
    - price_change: Price change % across timeframes (5m, 15m, 30m, 1h, 4h, 12h, 24h)
    - price_history: OHLC candlestick data (requires symbol, exchange, interval)
    - delisted_pairs: Retired/delisted trading pairs
    - exchange_rank: Exchange rankings by volume/OI

    Args:
        action: One of the actions listed above
        symbol: Coin symbol — needed for pairs_markets and price_history
        exchange: Exchange name — needed for price_history, optional for supported_pairs
        interval: Candle interval for price_history (1m, 5m, 15m, 1h, 4h, 1d)
        limit: Data points for price_history (max 4500)
        page: Page number for coins_markets
        per_page: Results per page for coins_markets
    """
    action = action.strip().lower()

    if action == "supported_coins":
        result = await client.get("/api/futures/supported-coins")
        return fmt(result, "Futures — Supported Coins")

    elif action == "supported_exchanges":
        result = await client.get("/api/futures/supported-exchanges")
        return fmt(result, "Futures — Supported Exchanges")

    elif action == "supported_pairs":
        params = {}
        if exchange:
            params["exchange"] = exchange
        result = await client.get("/api/futures/supported-exchange-pairs", params or None)
        return fmt(result, f"Futures — Supported Pairs ({exchange or 'All'})")

    elif action == "coins_markets":
        params = {"per_page": per_page, "page": page}
        if exchange:
            params["exchange_list"] = exchange
        result = await client.get("/api/futures/coins-markets", params)
        return fmt(result, f"Futures — Coins Markets (page {page})")

    elif action == "pairs_markets":
        if not symbol:
            return "**ERROR:** `symbol` is required for pairs_markets (e.g., BTC, ETH)"
        sym = normalize_symbol(symbol)
        result = await client.get("/api/futures/pairs-markets", {"symbol": sym})
        return fmt(result, f"Futures — Pairs Markets ({sym})")

    elif action == "price_change":
        result = await client.get("/api/futures/coins-price-change")
        return fmt(result, "Futures — Coins Price Change (5m→24h)")

    elif action == "price_history":
        if not symbol:
            return "**ERROR:** `symbol` is required for price_history (e.g., BTC, ETH)"
        pair = to_pair(symbol)
        result = await client.get("/api/futures/price/history", {
            "exchange": exchange,
            "symbol": pair,
            "interval": interval,
            "limit": limit,
        })
        return fmt(result, f"Futures — Price OHLC ({normalize_symbol(symbol)} {interval})")

    elif action == "delisted_pairs":
        result = await client.get("/api/futures/delisted-exchange-pairs")
        return fmt(result, "Futures — Delisted Pairs")

    elif action == "exchange_rank":
        result = await client.get("/api/futures/exchange-rank")
        return fmt(result, "Futures — Exchange Ranking")

    else:
        return (
            f"**ERROR:** Unknown action '{action}'. Available actions:\n"
            "supported_coins, supported_exchanges, supported_pairs, coins_markets, "
            "pairs_markets, price_change, price_history, delisted_pairs, exchange_rank"
        )


@mcp.tool()
async def coinglass_open_interest_cat(
    action: str = "aggregated_history",
    symbol: str = "BTC",
    exchange: str = DEFAULT_EXCHANGE,
    interval: str = "1h",
    limit: int = 100,
    range: str = "24h",
) -> str:
    """Open Interest data — 6 endpoints in one tool.

    Actions:
    - history: OI OHLC per pair (requires symbol as pair BTCUSDT + exchange)
    - aggregated_history: Aggregated OI OHLC across all exchanges for a coin
    - stablecoin_margin: Stablecoin-margined (USDT) OI OHLC
    - coin_margin: Coin-margined OI OHLC
    - exchange_list: OI breakdown by exchange (symbol optional, range required)
    - exchange_chart: Historical OI chart by exchange (symbol only)

    Args:
        action: One of the actions listed above
        symbol: Coin symbol (BTC, ETH, SOL) — auto-converted to pair for 'history' action
        exchange: Exchange name — needed for 'history' action
        interval: Candle interval (1m, 5m, 15m, 1h, 4h, 1d) — for history/aggregated/margin
        limit: Data points (max 4500)
        range: Time range for exchange_list only (1h, 4h, 12h, 24h)
    """
    action = action.strip().lower()
    sym = normalize_symbol(symbol)

    if action == "history":
        pair = to_pair(symbol)
        result = await client.get("/api/futures/open-interest/history", {
            "exchange": exchange,
            "symbol": pair,
            "interval": interval,
            "limit": limit,
        })
        return fmt_parsed(result, f"OI History — {pair} ({exchange})", _fmt_scan_oi)

    elif action == "aggregated_history":
        result = await client.get("/api/futures/open-interest/aggregated-history", {
            "symbol": sym,
            "interval": interval,
            "limit": limit,
        })
        return fmt_parsed(result, f"OI Aggregated — {sym}", _fmt_scan_oi)

    elif action == "stablecoin_margin":
        result = await client.get("/api/futures/open-interest/aggregated-stablecoin-history", {
            "symbol": sym,
            "interval": interval,
            "limit": limit,
        })
        return fmt_parsed(result, f"OI Stablecoin Margin — {sym}", _fmt_scan_oi)

    elif action == "coin_margin":
        result = await client.get("/api/futures/open-interest/aggregated-coin-margin-history", {
            "symbol": sym,
            "interval": interval,
            "limit": limit,
        })
        return fmt_parsed(result, f"OI Coin Margin — {sym}", _fmt_scan_oi)

    elif action == "exchange_list":
        params = {"range": range}
        if symbol:
            params["symbol"] = sym
        result = await client.get("/api/futures/open-interest/exchange-list", params)
        return fmt(result, f"OI Exchange List — {sym or 'All Coins'}")

    elif action == "exchange_chart":
        result = await client.get("/api/futures/open-interest/exchange-history-chart", {
            "symbol": sym,
        })
        return fmt(result, f"OI Exchange Chart — {sym}")

    else:
        return (
            f"**ERROR:** Unknown action '{action}'. Available actions:\n"
            "history, aggregated_history, stablecoin_margin, coin_margin, "
            "exchange_list, exchange_chart"
        )


@mcp.tool()
async def coinglass_funding_rate_cat(
    action: str = "exchange_list",
    symbol: str = "BTC",
    exchange: str = DEFAULT_EXCHANGE,
    interval: str = "1d",
    limit: int = 100,
    usd: int = 10000,
    range: str = "7d",
) -> str:
    """Funding Rate data — 6 endpoints in one tool.

    Actions:
    - history: FR OHLC per pair (requires symbol as pair BTCUSDT + exchange)
    - oi_weight: OI-weighted FR OHLC (pair + exchange) — Professional plan only
    - vol_weight: Volume-weighted FR OHLC (pair + exchange) — Professional plan only
    - exchange_list: Current FR across all exchanges (symbol optional)
    - cumulative: Accumulated/cumulative FR by exchange (range required, symbol optional)
    - arbitrage: FR arbitrage opportunities (usd = position size, exchange optional)

    Args:
        action: One of the actions listed above
        symbol: Coin symbol (BTC, ETH) — auto-converted to pair for history/oi_weight/vol_weight
        exchange: Exchange name — needed for history, oi_weight, vol_weight
        interval: Candle interval (1m, 5m, 15m, 1h, 4h, 1d) — for OHLC actions
        limit: Data points (max 4500)
        usd: Position size in USD for arbitrage FR income calc (default: 10000)
        range: Time range for cumulative (7d, 30d, 90d, 180d, 1y)
    """
    action = action.strip().lower()
    sym = normalize_symbol(symbol)

    if action == "history":
        pair = to_pair(symbol)
        result = await client.get("/api/futures/funding-rate/history", {
            "exchange": exchange,
            "symbol": pair,
            "interval": interval,
            "limit": limit,
        })
        return fmt_parsed(result, f"FR History OHLC — {pair} ({exchange})", _fmt_scan_price)

    elif action == "oi_weight":
        pair = to_pair(symbol)
        result = await client.get("/api/futures/funding-rate/oi-weight-history", {
            "exchange": exchange,
            "symbol": pair,
            "interval": interval,
            "limit": limit,
        })
        output = fmt(result, f"FR OI-Weighted — {pair} ({exchange})")
        if isinstance(result.data, list) and len(result.data) == 0:
            output += (
                "\n\n**NOTE:** Empty result. This endpoint may require "
                "Professional or Enterprise plan. Use `history` action instead."
            )
        return output

    elif action == "vol_weight":
        pair = to_pair(symbol)
        result = await client.get("/api/futures/funding-rate/vol-weight-history", {
            "exchange": exchange,
            "symbol": pair,
            "interval": interval,
            "limit": limit,
        })
        output = fmt(result, f"FR Vol-Weighted — {pair} ({exchange})")
        if isinstance(result.data, list) and len(result.data) == 0:
            output += (
                "\n\n**NOTE:** Empty result. This endpoint may require "
                "Professional or Enterprise plan. Use `history` action instead."
            )
        return output

    elif action == "exchange_list":
        result = await client.get("/api/futures/funding-rate/exchange-list")
        # Post-filter by symbol if specified (API returns all coins)
        if sym and isinstance(result.data, list):
            filtered = [c for c in result.data if c.get("symbol", "").upper() == sym]
            if not filtered:
                return f"**ERROR:** No funding rate data found for {sym}."
            result = FetchResult(
                data=filtered, age_seconds=result.age_seconds,
                is_cached=result.is_cached, fetched_at=result.fetched_at,
            )
        return _fmt_fr_exchange_list(result, sym or "All Coins")

    elif action == "cumulative":
        params: dict = {"range": range}
        if symbol:
            params["symbol"] = sym
        result = await client.get("/api/futures/funding-rate/accumulated-exchange-list", params)
        # Post-filter by symbol if specified (API returns all coins)
        if sym and isinstance(result.data, list):
            filtered = [c for c in result.data if c.get("symbol", "").upper() == sym]
            if filtered:
                result = FetchResult(
                    data=filtered, age_seconds=result.age_seconds,
                    is_cached=result.is_cached, fetched_at=result.fetched_at,
                )
        return fmt(result, f"Cumulative FR — {sym or 'All Coins'} ({range})")

    elif action == "arbitrage":
        params: dict = {"usd": usd}
        if exchange and exchange != DEFAULT_EXCHANGE:
            params["exchange_list"] = exchange
        result = await client.get("/api/futures/funding-rate/arbitrage", params)
        return fmt(result, f"FR Arbitrage — ${usd} position")

    else:
        return (
            f"**ERROR:** Unknown action '{action}'. Available actions:\n"
            "history, oi_weight, vol_weight, exchange_list, cumulative, arbitrage"
        )


@mcp.tool()
async def coinglass_long_short_cat(
    action: str = "global_account",
    symbol: str = "BTC",
    exchange: str = DEFAULT_EXCHANGE,
    interval: str = "4h",
    limit: int = 100,
    range: str = "4h",
) -> str:
    """Long/Short Ratio & Net Position data — 6 endpoints in one tool.

    Actions:
    - global_account: Global L/S account ratio history (pair + exchange)
    - top_account: Top traders L/S account ratio history (pair + exchange)
    - top_position: Top traders L/S position ratio history (pair + exchange)
    - taker_exchange: Taker buy/sell ratio per exchange (coin + range)
    - net_position: Net long/short position history (pair + exchange)
    - net_position_v2: Net position v2 with more detail (pair + exchange)

    Args:
        action: One of the actions listed above
        symbol: Coin symbol (BTC, ETH) — auto-converted to pair for ratio endpoints
        exchange: Exchange name (Binance, OKX, Bybit)
        interval: Candle interval for history actions (1m, 5m, 1h, 4h, 1d)
        limit: Data points (max 1000)
        range: Time range for taker_exchange only (5m, 15m, 30m, 1h, 4h, 12h, 24h)
    """
    action = action.strip().lower()
    sym = normalize_symbol(symbol)
    pair = to_pair(symbol)

    if action == "global_account":
        result = await client.get("/api/futures/global-long-short-account-ratio/history", {
            "exchange": exchange,
            "symbol": pair,
            "interval": interval,
            "limit": limit,
        })
        return fmt_parsed(result, f"Global L/S Account Ratio — {sym} ({exchange})", _fmt_scan_ls_ratio)

    elif action == "top_account":
        result = await client.get("/api/futures/top-long-short-account-ratio/history", {
            "exchange": exchange,
            "symbol": pair,
            "interval": interval,
            "limit": limit,
        })
        return fmt_parsed(result, f"Top Account L/S Ratio — {sym} ({exchange})", _fmt_scan_ls_ratio)

    elif action == "top_position":
        result = await client.get("/api/futures/top-long-short-position-ratio/history", {
            "exchange": exchange,
            "symbol": pair,
            "interval": interval,
            "limit": limit,
        })
        return fmt_parsed(result, f"Top Position L/S Ratio — {sym} ({exchange})", _fmt_p2_top_position_ls)

    elif action == "taker_exchange":
        result = await client.get("/api/futures/taker-buy-sell-volume/exchange-list", {
            "symbol": sym,
            "range": range,
        })
        return fmt(result, f"Taker Buy/Sell Exchange Ratio — {sym} ({range})")

    elif action == "net_position":
        result = await client.get("/api/futures/net-position/history", {
            "exchange": exchange,
            "symbol": pair,
            "interval": interval,
            "limit": limit,
        })
        return fmt(result, f"Net L/S Position — {sym} ({exchange})")

    elif action == "net_position_v2":
        result = await client.get("/api/futures/v2/net-position/history", {
            "exchange": exchange,
            "symbol": pair,
            "interval": interval,
            "limit": limit,
        })
        return fmt(result, f"Net L/S Position v2 — {sym} ({exchange})")

    else:
        return (
            f"**ERROR:** Unknown action '{action}'. Available actions:\n"
            "global_account, top_account, top_position, taker_exchange, "
            "net_position, net_position_v2"
        )


@mcp.tool()
async def coinglass_liquidation_cat(
    action: str = "coin_history",
    symbol: str = "BTC",
    exchange: str = DEFAULT_EXCHANGE,
    interval: str = "1h",
    limit: int = 100,
    range: str = "24h",
    min_amount: int = 10000,
) -> str:
    """Liquidation data — 5 endpoints in one tool.

    Actions:
    - pair_history: Liquidation history per pair (pair + exchange + interval)
    - coin_history: Aggregated liq history across exchanges (coin + exchange_list + interval)
    - coin_list: All coins liquidation on an exchange (1h/4h/12h/24h breakdown)
    - exchange_list: Liquidation by exchange for a coin (coin optional + range)
    - order: Individual liquidation orders last 7 days (coin + exchange + min_amount)

    Args:
        action: One of the actions listed above
        symbol: Coin symbol (BTC, ETH) — auto-converted to pair for pair_history
        exchange: Exchange name or comma-separated list for coin_history
        interval: Candle interval for history actions (1m, 5m, 1h, 4h, 1d)
        limit: Data points (max 1000)
        range: Time range for exchange_list (1h, 4h, 12h, 24h)
        min_amount: Minimum USD amount for order action (default 10000)
    """
    action = action.strip().lower()
    sym = normalize_symbol(symbol)

    if action == "pair_history":
        pair = to_pair(symbol)
        result = await client.get("/api/futures/liquidation/history", {
            "exchange": exchange,
            "symbol": pair,
            "interval": interval,
            "limit": limit,
        })
        return fmt_parsed(result, f"Liq Pair History — {pair} ({exchange})", _fmt_liq_history)

    elif action == "coin_history":
        result = await client.get("/api/futures/liquidation/aggregated-history", {
            "exchange_list": exchange,
            "symbol": sym,
            "interval": interval,
            "limit": limit,
        })
        return fmt_parsed(result, f"Liq Aggregated History — {sym}", _fmt_liq_history)

    elif action == "coin_list":
        result = await client.get("/api/futures/liquidation/coin-list", {
            "exchange": exchange,
        })
        return fmt(result, f"Liq Coin List — {exchange}")

    elif action == "exchange_list":
        params = {"range": range}
        if symbol:
            params["symbol"] = sym
        result = await client.get("/api/futures/liquidation/exchange-list", params)
        return fmt(result, f"Liq Exchange List — {sym or 'All Coins'} ({range})")

    elif action == "order":
        result = await client.get("/api/futures/liquidation/order", {
            "exchange": exchange,
            "symbol": sym,
            "min_liquidation_amount": str(min_amount),
        })
        return fmt_parsed(result, f"Liq Orders — {sym} ({exchange}, min ${min_amount:,})", _fmt_scan_liq_orders)

    else:
        return (
            f"**ERROR:** Unknown action '{action}'. Available actions:\n"
            "pair_history, coin_history, coin_list, exchange_list, order"
        )


@mcp.tool()
async def coinglass_orderbook_cat(
    action: str = "aggregated_bidask",
    symbol: str = "BTC",
    exchange: str = DEFAULT_EXCHANGE,
    interval: str = "1h",
    limit: int = 100,
    range: str = "1",
    state: int = 1,
) -> str:
    """Order Book (L2) data — 5 endpoints in one tool.

    Actions:
    - pair_bidask: Bid/Ask history per pair (pair + exchange + interval + depth range)
    - aggregated_bidask: Aggregated bid/ask across exchanges (coin + exchange_list + interval)
    - heatmap: Orderbook heatmap visualization (pair + exchange + interval, max 100 pts)
    - large_orders: Current large open orders (pair + exchange). BTC>=1M, ETH>=500K, Other>=50K
    - large_orders_history: Completed large orders (pair + exchange + time range + state)

    Args:
        action: One of the actions listed above
        symbol: Coin symbol (BTC, ETH) — auto-converted to pair where needed
        exchange: Exchange name or comma-separated list for aggregated_bidask
        interval: Candle interval for bidask/heatmap (1m, 5m, 15m, 1h, 4h, 1d)
        limit: Data points (max 1000 for bidask, max 100 for heatmap)
        range: Depth percentage for bidask (0.25, 0.5, 0.75, 1, 2, 3, 5, 10)
        state: For large_orders_history only — 1=Open, 2=Filled, 3=Cancelled
    """
    action = action.strip().lower()
    sym = normalize_symbol(symbol)
    pair = to_pair(symbol)

    if action == "pair_bidask":
        result = await client.get("/api/futures/orderbook/ask-bids-history", {
            "exchange": exchange,
            "symbol": pair,
            "interval": interval,
            "limit": limit,
            "range": range,
        })
        return fmt_parsed(result, f"OB Pair Bid/Ask — {pair} ({exchange}, ±{range}%)", _fmt_scan_ob_delta)

    elif action == "aggregated_bidask":
        result = await client.get("/api/futures/orderbook/aggregated-ask-bids-history", {
            "exchange_list": exchange,
            "symbol": sym,
            "interval": interval,
            "limit": limit,
            "range": range,
        })
        return fmt_parsed(result, f"OB Aggregated Bid/Ask — {sym} (±{range}%)", _fmt_scan_ob_delta)

    elif action == "heatmap":
        result = await client.get("/api/futures/orderbook/history", {
            "exchange": exchange,
            "symbol": pair,
            "interval": interval,
            "limit": min(limit, 100),
        })
        return fmt(result, f"OB Heatmap — {pair} ({exchange})")

    elif action == "large_orders":
        result = await client.get("/api/futures/orderbook/large-limit-order", {
            "exchange": exchange,
            "symbol": pair,
        })
        return fmt(result, f"Large Orders — {pair} ({exchange})")

    elif action == "large_orders_history":
        import time as _time
        # Default: last 24h
        end_ms = int(_time.time() * 1000)
        start_ms = end_ms - (24 * 3600 * 1000)
        result = await client.get("/api/futures/orderbook/large-limit-order-history", {
            "exchange": exchange,
            "symbol": pair,
            "start_time": start_ms,
            "end_time": end_ms,
            "state": state,
        })
        state_label = {1: "Open", 2: "Filled", 3: "Cancelled"}.get(state, str(state))
        return fmt(result, f"Large Orders History — {pair} ({exchange}, {state_label})")

    else:
        return (
            f"**ERROR:** Unknown action '{action}'. Available actions:\n"
            "pair_bidask, aggregated_bidask, heatmap, large_orders, large_orders_history"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# CATEGORY TOOL — Hyperliquid (3 endpoints)
# ═══════════════════════════════════════════════════════════════════════════════


@mcp.tool()
async def coinglass_hyperliquid_cat(
    action: str = "long_short_ratio",
    symbol: str = "BTC",
    interval: str = "1d",
    limit: int = 100,
    current_page: int = 1,
) -> str:
    """Hyperliquid on-chain data — 3 endpoints in one tool.

Actions:
- long_short_ratio: Global L/S account ratio history on Hyperliquid (symbol + interval)
- wallet_distribution: Wallet position distribution by tier (Shrimp→Leviathan, no params)
- positions: Individual wallet positions by coin (symbol + page)

Args:
    action: One of the actions listed above
    symbol: Coin symbol (BTC, ETH, SOL) — for long_short_ratio and positions
    interval: Time interval for long_short_ratio only (5m, 1h, 1d)
    limit: Data points for long_short_ratio (max 1000)
    current_page: Page number for positions action (paginated results)
    """
    action = action.strip().lower()
    sym = normalize_symbol(symbol)

    if action == "long_short_ratio":
        result = await client.get(
            "/api/hyperliquid/global-long-short-account-ratio/history",
            {"symbol": sym, "interval": interval, "limit": limit},
        )
        return fmt_parsed(result, f"Hyperliquid L/S Ratio — {sym} ({interval})", _fmt_p2_hyperliquid_ls)

    elif action == "wallet_distribution":
        result = await client.get(
            "/api/hyperliquid/wallet/position-distribution", {},
        )
        return fmt(result, "Hyperliquid Wallet Position Distribution (all tiers)")

    elif action == "positions":
        result = await client.get(
            "/api/hyperliquid/position",
            {"symbol": sym, "current_page": str(current_page)},
        )
        return fmt(result, f"Hyperliquid Positions — {sym} (page {current_page})")

    else:
        return (
            f"Unknown action '{action}'. Available: "
            "long_short_ratio, wallet_distribution, positions"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# CATEGORY TOOL — Futures Taker Buy/Sell & Volume (4 endpoints)
# ═══════════════════════════════════════════════════════════════════════════════


@mcp.tool()
async def coinglass_futures_taker_cat(
    action: str = "coin_taker",
    symbol: str = "BTC",
    exchange: str = DEFAULT_EXCHANGE,
    interval: str = "1h",
    limit: int = 100,
    unit: str = "usd",
) -> str:
    """Futures Taker Buy/Sell & Volume data — 4 endpoints in one tool.

Actions:
- cvd_pair: CVD per pair on single exchange (pair + exchange + interval)
- footprint: Footprint chart — buy/sell vol at each price level (pair + exchange, 90d max, Pro+)
- coin_taker: Aggregated taker buy/sell volume across exchanges (coin + exchange_list + unit)
- pair_taker: Per-pair taker buy/sell volume on single exchange (pair + exchange + interval)

Args:
    action: One of the actions listed above
    symbol: Coin symbol (BTC, ETH) — auto-converted to pair for cvd_pair/footprint/pair_taker
    exchange: Exchange name. For coin_taker: comma-separated list (Binance,OKX,Bybit)
    interval: Candle interval (1m, 5m, 15m, 30m, 1h, 4h, 8h, 12h, 1d, 1w)
    limit: Data points (max 1000)
    unit: For coin_taker only — 'usd' or 'coin'
    """
    action = action.strip().lower()
    sym = normalize_symbol(symbol)
    pair = to_pair(symbol)

    if action == "cvd_pair":
        result = await client.get(
            "/api/futures/cvd/history",
            {"exchange": exchange, "symbol": pair, "interval": interval, "limit": limit},
        )
        return fmt_parsed(result, f"Futures CVD (pair) — {pair} ({exchange}, {interval})", lambda d: _fmt_scan_cvd(d, "Futures CVD"))

    elif action == "footprint":
        result = await client.get(
            "/api/futures/volume/footprint-history",
            {"exchange": exchange, "symbol": pair, "interval": interval, "limit": limit},
        )
        return fmt(result, f"Footprint — {pair} ({exchange}, {interval})")

    elif action == "coin_taker":
        result = await client.get(
            "/api/futures/aggregated-taker-buy-sell-volume/history",
            {"exchange_list": exchange, "symbol": sym, "interval": interval,
             "limit": limit, "unit": unit},
        )
        return fmt_parsed(result, f"Aggregated Taker Buy/Sell — {sym} ({exchange}, {interval})", _fmt_scan_taker)

    elif action == "pair_taker":
        result = await client.get(
            "/api/futures/v2/taker-buy-sell-volume/history",
            {"exchange": exchange, "symbol": pair, "interval": interval, "limit": limit},
        )
        return fmt_parsed(result, f"Pair Taker Buy/Sell — {pair} ({exchange}, {interval})", _fmt_scan_taker)

    else:
        return (
            f"Unknown action '{action}'. Available: "
            "cvd_pair, footprint, coin_taker, pair_taker"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# CATEGORY TOOL — Spots Trading Market (4 endpoints)
# ═══════════════════════════════════════════════════════════════════════════════


@mcp.tool()
async def coinglass_spot_market_cat(
    action: str = "coins_markets",
    symbol: str = "BTC",
    page: int = 1,
    per_page: int = 20,
) -> str:
    """Spot Trading Market data — 4 endpoints in one tool.

Actions:
- supported_coins: List all supported spot coin symbols (no params)
- supported_pairs: List all spot exchanges and their trading pairs (no params)
- coins_markets: Spot market overview — price, volume, buy/sell, net flow across timeframes (paginated, Standard+)
- pairs_markets: All spot trading pairs for a coin with volume/flow data (requires symbol)

Args:
    action: One of the actions listed above
    symbol: Coin symbol (BTC, ETH) — needed for pairs_markets
    page: Page number for coins_markets
    per_page: Results per page for coins_markets
    """
    action = action.strip().lower()
    sym = normalize_symbol(symbol)

    if action == "supported_coins":
        result = await client.get("/api/spot/supported-coins", {})
        return fmt(result, "Spot Supported Coins")

    elif action == "supported_pairs":
        result = await client.get("/api/spot/supported-exchange-pairs", {})
        return fmt(result, "Spot Supported Exchanges & Pairs")

    elif action == "coins_markets":
        result = await client.get(
            "/api/spot/coins-markets",
            {"page": page, "per_page": per_page},
        )
        return fmt(result, f"Spot Coins Markets (page {page})")

    elif action == "pairs_markets":
        result = await client.get(
            "/api/spot/pairs-markets", {"symbol": sym},
        )
        return fmt(result, f"Spot Pairs Markets — {sym}")

    else:
        return (
            f"Unknown action '{action}'. Available: "
            "supported_coins, supported_pairs, coins_markets, pairs_markets"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# CATEGORY TOOL — Spots Order Book (5 endpoints)
# ═══════════════════════════════════════════════════════════════════════════════


@mcp.tool()
async def coinglass_spot_orderbook_cat(
    action: str = "aggregated_bidask",
    symbol: str = "BTC",
    exchange: str = DEFAULT_EXCHANGE,
    interval: str = "1h",
    limit: int = 100,
    range: str = "1",
    state: int = 1,
) -> str:
    """Spot Order Book data — 5 endpoints in one tool.

Actions:
- pair_bidask: Bid/Ask history per pair (pair + exchange + interval + depth range)
- aggregated_bidask: Aggregated bid/ask across exchanges (coin + exchange_list + interval + range)
- heatmap: Orderbook heatmap visualization (pair + exchange + interval, max 100 pts)
- large_orders: Current large open orders (pair + exchange). BTC>=350K, ETH>=250K, Other>=10K
- large_orders_history: Completed large orders (pair + exchange + state). state: 1=Open 2=Filled 3=Cancelled

Args:
    action: One of the actions listed above
    symbol: Coin symbol (BTC, ETH) — auto-converted to pair where needed
    exchange: Exchange name or comma-separated list for aggregated_bidask
    interval: Candle interval for bidask/heatmap (1m, 5m, 15m, 1h, 4h, 1d)
    limit: Data points (max 1000 for bidask, max 100 for heatmap)
    range: Depth percentage for bidask (0.25, 0.5, 0.75, 1, 2, 3, 5, 10)
    state: For large_orders_history only — 1=Open, 2=Filled, 3=Cancelled
    """
    action = action.strip().lower()
    sym = normalize_symbol(symbol)
    pair = to_pair(symbol)

    if action == "pair_bidask":
        result = await client.get(
            "/api/spot/orderbook/ask-bids-history",
            {"exchange": exchange, "symbol": pair, "interval": interval,
             "limit": limit, "range": range},
        )
        return fmt_parsed(result, f"Spot OB Pair Bid/Ask — {pair} ({exchange}, ±{range}%)", _fmt_scan_ob_delta)

    elif action == "aggregated_bidask":
        result = await client.get(
            "/api/spot/orderbook/aggregated-ask-bids-history",
            {"exchange_list": exchange, "symbol": sym, "interval": interval,
             "limit": limit, "range": range},
        )
        return fmt_parsed(result, f"Spot OB Aggregated Bid/Ask — {sym} ({exchange}, ±{range}%)", _fmt_scan_ob_delta)

    elif action == "heatmap":
        hm_limit = min(limit, 100)
        result = await client.get(
            "/api/spot/orderbook/history",
            {"exchange": exchange, "symbol": pair, "interval": interval, "limit": hm_limit},
        )
        return fmt(result, f"Spot OB Heatmap — {pair} ({exchange}, {interval})")

    elif action == "large_orders":
        result = await client.get(
            "/api/spot/orderbook/large-limit-order",
            {"exchange": exchange, "symbol": pair},
        )
        return fmt(result, f"Spot Large Orders — {pair} ({exchange})")

    elif action == "large_orders_history":
        import time as _time
        end_ms = int(_time.time() * 1000)
        start_ms = end_ms - 7 * 86400 * 1000  # last 7 days
        result = await client.get(
            "/api/spot/orderbook/large-limit-order-history",
            {"exchange": exchange, "symbol": pair,
             "start_time": str(start_ms), "end_time": str(end_ms), "state": str(state)},
        )
        state_labels = {1: "Open", 2: "Filled", 3: "Cancelled"}
        return fmt(result, f"Spot Large Orders History — {pair} ({exchange}, {state_labels.get(state, state)})")

    else:
        return (
            f"Unknown action '{action}'. Available: "
            "pair_bidask, aggregated_bidask, heatmap, large_orders, large_orders_history"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# CATEGORY TOOL — Futures Indicators (10 endpoints)
# ═══════════════════════════════════════════════════════════════════════════════


@mcp.tool()
async def coinglass_indicators_cat(
    action: str = "rsi_list",
    symbol: str = "BTC",
    exchange: str = DEFAULT_EXCHANGE,
    interval: str = "1h",
    limit: int = 100,
    window: int = 14,
    series_type: str = "close",
    fast_window: int = 12,
    slow_window: int = 26,
    signal_window: int = 9,
) -> str:
    """Futures Technical Indicators — 10 endpoints in one tool.

Actions (per-pair history — need symbol + exchange + interval):
- pair_rsi: RSI history for a trading pair (window default 14)
- pair_ma: Moving Average history (window default 10)
- pair_ema: Exponential MA history (window default 10)
- pair_macd: MACD history (fast=12, slow=26, signal=9)
- pair_atr: Average True Range history (window default 14)
- whale_index: Whale Index history for a pair

Actions (all-coins snapshot — no params needed, Standard+):
- rsi_list: RSI across all coins & timeframes
- ma_list: MA across all coins & timeframes
- ema_list: EMA across all coins & timeframes
- macd_list: MACD across all coins & timeframes

Args:
    action: One of the actions listed above
    symbol: Coin symbol (BTC, ETH) — auto-converted to pair for pair_* actions
    exchange: Exchange name (Binance, OKX, Bybit)
    interval: Candle interval (1m, 5m, 15m, 1h, 4h, 1d, 1w)
    limit: Data points (max 1000 for most, 4500 for MA/EMA/MACD/RSI)
    window: Lookback window for RSI/MA/EMA/ATR (e.g. 14 for RSI, 10 for MA)
    series_type: Price type — open, high, low, close (default close)
    fast_window: MACD fast period (default 12)
    slow_window: MACD slow period (default 26)
    signal_window: MACD signal period (default 9)
    """
    action = action.strip().lower()
    pair = to_pair(symbol)

    # --- All-coins list endpoints (no params) ---
    if action == "rsi_list":
        result = await client.get("/api/futures/rsi/list", {})
        return fmt(result, "Futures RSI List (all coins)")

    elif action == "ma_list":
        result = await client.get("/api/futures/ma/list", {})
        return fmt(result, "Futures MA List (all coins)")

    elif action == "ema_list":
        result = await client.get("/api/futures/ema/list", {})
        return fmt(result, "Futures EMA List (all coins)")

    elif action == "macd_list":
        result = await client.get("/api/futures/macd/list", {})
        return fmt(result, "Futures MACD List (all coins)")

    # --- Per-pair history endpoints ---
    elif action == "pair_rsi":
        result = await client.get(
            "/api/futures/indicators/rsi",
            {"exchange": exchange, "symbol": pair, "interval": interval,
             "limit": limit, "window": window, "series_type": series_type},
        )
        return fmt(result, f"RSI — {pair} ({exchange}, {interval}, w{window})")

    elif action == "pair_ma":
        result = await client.get(
            "/api/futures/indicators/ma",
            {"exchange": exchange, "symbol": pair, "interval": interval,
             "limit": limit, "window": window, "series_type": series_type},
        )
        return fmt(result, f"MA — {pair} ({exchange}, {interval}, w{window})")

    elif action == "pair_ema":
        result = await client.get(
            "/api/futures/indicators/ema",
            {"exchange": exchange, "symbol": pair, "interval": interval,
             "limit": limit, "window": window, "series_type": series_type},
        )
        return fmt(result, f"EMA — {pair} ({exchange}, {interval}, w{window})")

    elif action == "pair_macd":
        result = await client.get(
            "/api/futures/indicators/macd",
            {"exchange": exchange, "symbol": pair, "interval": interval,
             "limit": limit, "series_type": series_type,
             "fast_window": fast_window, "slow_window": slow_window,
             "signal_window": signal_window},
        )
        return fmt(result, f"MACD — {pair} ({exchange}, {interval}, {fast_window}/{slow_window}/{signal_window})")

    elif action == "pair_atr":
        result = await client.get(
            "/api/futures/indicators/avg-true-range",
            {"exchange": exchange, "symbol": pair, "interval": interval,
             "limit": limit, "window": window},
        )
        return fmt(result, f"ATR — {pair} ({exchange}, {interval}, w{window})")

    elif action == "whale_index":
        result = await client.get(
            "/api/futures/whale-index/history",
            {"exchange": exchange, "symbol": pair, "interval": interval, "limit": limit},
        )
        return fmt(result, f"Whale Index — {pair} ({exchange}, {interval})")

    else:
        return (
            f"Unknown action '{action}'. Available: "
            "rsi_list, ma_list, ema_list, macd_list, "
            "pair_rsi, pair_ma, pair_ema, pair_macd, pair_atr, whale_index"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# CATEGORY TOOL — Index, News & Other (3 endpoints)
# ═══════════════════════════════════════════════════════════════════════════════


@mcp.tool()
async def coinglass_index_news_cat(
    action: str = "altcoin_season",
    symbol: str = "BTC",
    exchange: str = DEFAULT_EXCHANGE,
    interval: str = "1h",
    limit: int = 100,
    language: str = "en",
    page: int = 1,
    per_page: int = 20,
) -> str:
    """Index, Volume Ratio & News — 3 endpoints in one tool.

Actions:
- altcoin_season: Altcoin Season Index history (no params needed)
- futures_spot_ratio: Futures vs Spot volume ratio (exchange_list + coin + interval)
- news: Latest crypto news articles (language: en/zh/zh-tw, paginated)

Args:
    action: One of the actions listed above
    symbol: Coin symbol for futures_spot_ratio (BTC, ETH)
    exchange: Exchange or comma-separated list for futures_spot_ratio
    interval: Candle interval for futures_spot_ratio (1m, 5m, 1h, 4h, 1d)
    limit: Data points for futures_spot_ratio (max 1000)
    language: For news — 'en', 'zh', 'zh-tw'
    page: Page number for news
    per_page: Items per page for news
    """
    action = action.strip().lower()
    sym = normalize_symbol(symbol)

    if action == "altcoin_season":
        result = await client.get("/api/index/altcoin-season", {})
        return fmt(result, "Altcoin Season Index")

    elif action == "futures_spot_ratio":
        result = await client.get(
            "/api/futures_spot_volume_ratio",
            {"exchange_list": exchange, "symbol": sym,
             "interval": interval, "limit": limit},
        )
        return fmt(result, f"Futures/Spot Volume Ratio — {sym} ({exchange}, {interval})")

    elif action == "news":
        result = await client.get(
            "/api/article/list",
            {"language": language, "page": page, "per_page": per_page},
        )
        return fmt(result, f"Crypto News ({language}, page {page})")

    else:
        return (
            f"Unknown action '{action}'. Available: "
            "altcoin_season, futures_spot_ratio, news"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# SMART SCREENER — Pump/Dump Early Detection
# ═══════════════════════════════════════════════════════════════════════════════


@mcp.tool()
async def coinglass_smart_screener(
    mode: str = "all",
    top_n: int = 15,
    min_oi_usd: float = 10_000_000,
) -> str:
    """Screen ALL coins for pump/dump signals BEFORE retail notices.

    Fetches coins-markets + whale data + FR in parallel, then scores each coin.

    PUMP signals (positive score):
    - OI rising while price flat/down = stealth accumulation
    - Funding rate very negative = short squeeze setup
    - Whale opening longs on Hyperliquid
    - Short liquidation spike = shorts getting rekt

    DUMP signals (negative score):
    - Funding rate extreme positive = longs overleveraged
    - OI rising + price rising + extreme FR = leverage bubble about to pop
    - Whale opening shorts
    - Long liquidation spike = longs getting rekt
    - OI dropping sharply = smart money exiting

    Args:
        mode: "pump" (only pump candidates), "dump" (only dump), "all" (both)
        top_n: Number of coins to return per category (default 15)
        min_oi_usd: Minimum OI in USD to filter noise (default $10M)
    """
    import asyncio

    scan_time = datetime.now(WIB).strftime("%Y-%m-%d %H:%M:%S WIB")

    # Fetch data in parallel: coins_markets (page1+2) + whale alerts
    tasks = [
        client.get("/api/futures/coins-markets", {"per_page": 200, "page": 1}),
        client.get("/api/hyperliquid/whale-alert"),
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    markets_result, whale_result = results

    # Parse market data
    coins = []
    if isinstance(markets_result, FetchResult) and isinstance(markets_result.data, list):
        coins = markets_result.data
    else:
        return f"**ERROR:** Failed to fetch coins-markets data. {markets_result}"

    # Parse whale data
    whale_longs = {}   # symbol -> total USD long
    whale_shorts = {}  # symbol -> total USD short
    if isinstance(whale_result, FetchResult) and isinstance(whale_result.data, list):
        for w in whale_result.data:
            sym = w.get("symbol", "").upper()
            action = w.get("position_action")  # 1=open, 2=close
            size = abs(w.get("position_value_usd", 0) or 0)
            is_long = (w.get("position_size", 0) or 0) > 0
            if action == 1 and size > 500_000:  # Only open positions > $500K
                if is_long:
                    whale_longs[sym] = whale_longs.get(sym, 0) + size
                else:
                    whale_shorts[sym] = whale_shorts.get(sym, 0) + size

    # Score each coin
    scored = []
    for coin in coins:
        sym = coin.get("symbol", "")
        oi_usd = coin.get("open_interest_usd") or 0
        if oi_usd < min_oi_usd:
            continue

        price = coin.get("current_price") or 0
        score = 0.0
        signals = []

        # ── OI Changes ──
        oi_chg_1h = coin.get("open_interest_change_percent_1h") or 0
        oi_chg_4h = coin.get("open_interest_change_percent_4h") or 0
        oi_chg_24h = coin.get("open_interest_change_percent_24h") or 0

        # ── Price Changes ──
        price_chg_1h = coin.get("price_change_percent_1h") or 0
        price_chg_4h = coin.get("price_change_percent_4h") or 0
        price_chg_24h = coin.get("price_change_percent_24h") or 0

        # ── Funding Rate ──
        fr = coin.get("avg_funding_rate_by_oi") or 0

        # ── Liquidation ──
        long_liq_4h = coin.get("long_liquidation_usd_4h") or 0
        short_liq_4h = coin.get("short_liquidation_usd_4h") or 0
        long_liq_24h = coin.get("long_liquidation_usd_24h") or 0
        short_liq_24h = coin.get("short_liquidation_usd_24h") or 0

        # ── Long/Short Ratio ──
        ls_ratio_4h = coin.get("long_short_ratio_4h") or 0

        # ═══════════════════════════════════════════
        # PUMP SCORING (positive = pump potential)
        # ═══════════════════════════════════════════

        # 1. Stealth accumulation: OI rising + price flat/down
        if oi_chg_4h > 3 and price_chg_4h < 0.5:
            score += 3
            signals.append(f"STEALTH ACCUM: OI+{oi_chg_4h:.1f}% price{price_chg_4h:+.1f}% (4h)")
        elif oi_chg_1h > 2 and price_chg_1h < 0.3:
            score += 2
            signals.append(f"ACCUM 1h: OI+{oi_chg_1h:.1f}% price{price_chg_1h:+.1f}%")

        # 2. Short squeeze setup: extreme negative FR
        if fr < -0.01:
            score += 3
            signals.append(f"SHORT SQUEEZE: FR={fr:.4f}% (extreme neg)")
        elif fr < -0.005:
            score += 1.5
            signals.append(f"NEG FR: {fr:.4f}%")

        # 3. Short liquidation cascade (shorts getting rekt = pump fuel)
        if short_liq_4h > 1_000_000:
            score += 2
            signals.append(f"SHORT LIQ: ${short_liq_4h/1e6:.1f}M (4h)")
        elif short_liq_4h > 500_000:
            score += 1
            signals.append(f"short liq: ${short_liq_4h/1e6:.1f}M (4h)")

        # 4. Whale longs on Hyperliquid
        wl = whale_longs.get(sym, 0)
        if wl > 2_000_000:
            score += 3
            signals.append(f"WHALE LONG: ${wl/1e6:.1f}M")
        elif wl > 500_000:
            score += 1.5
            signals.append(f"whale long: ${wl/1e6:.1f}M")

        # ═══════════════════════════════════════════
        # DUMP SCORING (negative = dump potential)
        # ═══════════════════════════════════════════

        # 5. Overleveraged longs: extreme positive FR
        if fr > 0.05:
            score -= 3
            signals.append(f"OVERLEVERAGED: FR=+{fr:.4f}% (extreme pos)")
        elif fr > 0.02:
            score -= 1.5
            signals.append(f"HIGH FR: +{fr:.4f}%")

        # 6. Leverage bubble: OI + price + FR all rising
        if oi_chg_4h > 3 and price_chg_4h > 3 and fr > 0.01:
            score -= 3
            signals.append(f"BUBBLE: OI+{oi_chg_4h:.1f}% price+{price_chg_4h:.1f}% FR+{fr:.4f}%")

        # 7. Long liquidation cascade (longs getting rekt = dump fuel)
        if long_liq_4h > 1_000_000:
            score -= 2
            signals.append(f"LONG LIQ: ${long_liq_4h/1e6:.1f}M (4h)")
        elif long_liq_4h > 500_000:
            score -= 1
            signals.append(f"long liq: ${long_liq_4h/1e6:.1f}M (4h)")

        # 8. Smart money exit: OI dropping sharply
        if oi_chg_4h < -3:
            score -= 2
            signals.append(f"OI EXODUS: {oi_chg_4h:.1f}% (4h)")
        elif oi_chg_1h < -2:
            score -= 1.5
            signals.append(f"OI drop: {oi_chg_1h:.1f}% (1h)")

        # 9. Whale shorts on Hyperliquid
        ws = whale_shorts.get(sym, 0)
        if ws > 2_000_000:
            score -= 3
            signals.append(f"WHALE SHORT: ${ws/1e6:.1f}M")
        elif ws > 500_000:
            score -= 1.5
            signals.append(f"whale short: ${ws/1e6:.1f}M")

        # 10. Crowded longs (>70% long = contrarian dump signal)
        if ls_ratio_4h > 3.0:
            score -= 1.5
            signals.append(f"CROWDED LONG: L/S={ls_ratio_4h:.2f}")

        if abs(score) >= 1 and signals:
            scored.append({
                "symbol": sym,
                "score": round(score, 1),
                "price": price,
                "oi_usd": oi_usd,
                "fr": fr,
                "oi_chg_4h": oi_chg_4h,
                "price_chg_4h": price_chg_4h,
                "signals": signals,
            })

    # Sort and format output
    pump_list = sorted([c for c in scored if c["score"] > 0], key=lambda x: x["score"], reverse=True)
    dump_list = sorted([c for c in scored if c["score"] < 0], key=lambda x: x["score"])

    output = f"# SMART SCREENER — Pump/Dump Early Detection\n"
    output += f"**Scan time:** {scan_time}\n"
    output += f"**Coins scanned:** {len(coins)} | **Filtered (OI>${min_oi_usd/1e6:.0f}M):** {len([c for c in coins if (c.get('open_interest_usd') or 0) >= min_oi_usd])}\n"
    output += f"**Signals detected:** {len(pump_list)} pump, {len(dump_list)} dump\n\n"

    if mode in ("all", "pump"):
        output += f"## PUMP CANDIDATES (top {top_n})\n\n"
        if pump_list:
            for i, c in enumerate(pump_list[:top_n], 1):
                output += (
                    f"### {i}. {c['symbol']} — Score: +{c['score']}\n"
                    f"Price: ${c['price']:,.4f} | OI: ${c['oi_usd']/1e6:,.0f}M | "
                    f"FR: {c['fr']:.4f}% | OI 4h: {c['oi_chg_4h']:+.1f}% | "
                    f"Price 4h: {c['price_chg_4h']:+.1f}%\n"
                )
                for sig in c["signals"]:
                    output += f"- {sig}\n"
                output += "\n"
        else:
            output += "*No pump signals detected right now.*\n\n"

    if mode in ("all", "dump"):
        output += f"## DUMP CANDIDATES (top {top_n})\n\n"
        if dump_list:
            for i, c in enumerate(dump_list[:top_n], 1):
                output += (
                    f"### {i}. {c['symbol']} — Score: {c['score']}\n"
                    f"Price: ${c['price']:,.4f} | OI: ${c['oi_usd']/1e6:,.0f}M | "
                    f"FR: {c['fr']:.4f}% | OI 4h: {c['oi_chg_4h']:+.1f}% | "
                    f"Price 4h: {c['price_chg_4h']:+.1f}%\n"
                )
                for sig in c["signals"]:
                    output += f"- {sig}\n"
                output += "\n"
        else:
            output += "*No dump signals detected right now.*\n\n"

    output += (
        "---\n\n"
        "## Cara Pakai\n\n"
        "1. Pilih coin dari list di atas\n"
        "2. Jalankan `coinglass_full_scan` pada coin tersebut\n"
        "3. Confirm dengan Phase 1 (CVD+OI) + Phase 2 (Whale+OB) + Phase 3 (Binance)\n"
        "4. Score tinggi = sinyal kuat, tapi SELALU confirm sebelum entry\n"
    )

    return output


# ═══════════════════════════════════════════════════════════════════════════════
# COMPOSITE TOOL — Ricoz Full Scan (HARDENED)
# ═══════════════════════════════════════════════════════════════════════════════

# Critical metrics — if ANY fails, analysis is BLOCKED
CRITICAL_METRICS = {"Spot CVD", "Futures CVD", "Open Interest"}

# Arkham chain mapping for exchange flow (Phase 4)
ARKHAM_CHAIN_MAP = {
    "SOL": "solana", "BTC": "bitcoin", "ETH": "ethereum",
    "BNB": "bnb", "AVAX": "avalanche", "SUI": "sui",
    "HYPE": "ethereum", "XRP": "",
}

# Tokens NOT supported by Nansen flows (skip API call)
NANSEN_UNSUPPORTED = {"BTC", "HYPE"}
# Supplementary metrics — partial failure is OK
SUPPLEMENTARY_METRICS = {
    "Funding Rate", "Orderbook Delta", "Liquidation Map",
    "Price OHLC", "Taker Buy/Sell", "Long/Short Ratio",
}
PRECISION_METRICS = {"Whale Alert", "OB Bidask ±1%", "Footprint", "RSI"}


@mcp.tool()
async def coinglass_full_scan(
    symbol: str = "BTC",
    interval: str = "5m",
    exchange: str = DEFAULT_EXCHANGE,
) -> str:
    """ALL-IN-ONE 2-phase scan — Ricoz Scalping Framework (12 endpoints).

    Phase 1 — Quick Scan (8 core endpoints):
    - CRITICAL (SpotCVD + FutCVD + OI): ALL must succeed or analysis BLOCKED
    - SUPPLEMENTARY (FR, OB, Price, Taker, L/S): partial OK

    Phase 2 — Pre-Entry Precision Check (4 endpoints, auto):
    - Whale Alert: Hyperliquid whale positions >$1M (filtered by coin)
    - OB Bidask ±1%: Real-time bids vs asks depth
    - Footprint: Buy/sell absorption per price level (Standard+ plan)
    - RSI: Overbought/oversold confirmation (>70 OB, <30 OS)

    Args:
        symbol: Coin symbol (BTC, ETH, SOL, HYPE, AVAX, etc.)
        interval: Candle interval for time-series data (5m recommended for scalping)
        exchange: Exchange name (Binance, OKX, Bybit, etc.)
    """
    import asyncio

    sym = to_cg_symbol(symbol)  # PEPE→1000PEPE, BTC→BTC
    raw_sym = normalize_symbol(symbol)  # Always base symbol for display
    pair = to_pair(symbol)
    limit = 50

    # Define all endpoints with their labels and criticality
    # V4 API: some endpoints need coin-level (1000PEPE), some need pair-level (1000PEPEUSDT)
    calls = [
        ("Spot CVD", "/api/spot/aggregated-cvd/history",
         {"exchange_list": exchange, "symbol": sym, "interval": interval, "limit": limit}),
        ("Futures CVD", "/api/futures/aggregated-cvd/history",
         {"exchange_list": exchange, "symbol": sym, "interval": interval, "limit": limit}),
        ("Open Interest", "/api/futures/open-interest/aggregated-history",
         {"exchange_list": exchange, "symbol": sym, "interval": interval, "limit": limit}),
        ("Funding Rate", "/api/futures/funding-rate/exchange-list",
         {}),
        ("Orderbook Delta", "/api/futures/orderbook/ask-bids-history",
         {"exchange": exchange, "symbol": pair, "interval": interval, "range": "1", "limit": limit}),
        ("Price OHLC", "/api/futures/price/history",
         {"exchange": exchange, "symbol": pair, "interval": interval, "limit": limit}),
        ("Taker Buy/Sell", "/api/futures/v2/taker-buy-sell-volume/history",
         {"exchange": exchange, "symbol": pair, "interval": interval, "limit": limit}),
        ("Long/Short Ratio", "/api/futures/global-long-short-account-ratio/history",
         {"exchange": exchange, "symbol": pair, "interval": interval, "limit": limit}),
    ]
    # Only include Liquidation Heatmap if plan supports it
    if config.has_feature("liquidation_heatmap"):
        calls.append(("Liquidation Heatmap", "/api/futures/liquidation/heatmap/model1",
                       {"exchange": exchange, "symbol": pair, "range": "3d"}))

    # ── Phase 2: Precision endpoints ──
    precision_calls = [
        ("Whale Alert", "/api/hyperliquid/whale-alert", {}),
        ("OB Bidask ±1%", "/api/futures/orderbook/ask-bids-history", {
            "exchange": exchange, "symbol": pair,
            "interval": interval, "limit": 3, "range": "1",
        }),
    ]
    if config.has_feature("footprint"):
        precision_calls.append(
            ("Footprint", "/api/futures/volume/footprint-history", {
                "exchange": exchange, "symbol": pair,
                "interval": "5m", "limit": 3,
            })
        )
    # Liquidation Orders — cluster data for TP/SL placement
    precision_calls.append(
        ("Liq Orders", "/api/futures/liquidation/order", {
            "exchange": exchange, "symbol": sym,
            "min_liquidation_amount": "10000",
        })
    )
    # Hyperliquid L/S Ratio — on-chain sophisticated trader sentiment
    precision_calls.append(
        ("Hyperliquid L/S", "/api/hyperliquid/global-long-short-account-ratio/history", {
            "symbol": sym, "interval": "1h", "limit": 5,
        })
    )
    # Top Trader Position Ratio — smart money positioning (not retail)
    precision_calls.append(
        ("Top Position L/S", "/api/futures/top-long-short-position-ratio/history", {
            "exchange": exchange, "symbol": pair, "interval": interval, "limit": 5,
        })
    )
    # Spot Large Orders — detect bid/ask walls, spoofing identification
    precision_calls.append(
        ("Spot Large Orders", "/api/spot/orderbook/large-limit-order", {
            "exchange": exchange, "symbol": pair,
        })
    )
    # RSI — use rsi/list (all coins) then filter, because per-coin endpoint is broken
    precision_calls.append(
        ("RSI", "/api/futures/rsi/list", {})
    )

    # Fetch ALL endpoints in one batch — rate limiter handles spacing
    all_calls = calls + precision_calls
    tasks = [client.get(ep, params) for _, ep, params in all_calls]
    raw_results = await asyncio.gather(*tasks, return_exceptions=True)

    # ── Auto-fallback: if Spot CVD is empty, try OKX then Bybit ──
    spot_cvd_idx = 0  # first call is always Spot CVD
    spot_result = raw_results[spot_cvd_idx]
    if (isinstance(spot_result, FetchResult)
            and isinstance(spot_result.data, list)
            and len(spot_result.data) == 0):
        for fallback_ex in ("OKX", "Bybit"):
            if fallback_ex == exchange:
                continue
            try:
                fb_result = await client.get(
                    "/api/spot/aggregated-cvd/history",
                    {"exchange_list": fallback_ex, "symbol": sym,
                     "interval": interval, "limit": limit},
                )
                if (isinstance(fb_result.data, list)
                        and len(fb_result.data) > 0):
                    raw_results[spot_cvd_idx] = fb_result
                    # Update call label to show fallback exchange
                    calls[spot_cvd_idx] = (
                        f"Spot CVD (fallback: {fallback_ex})",
                        calls[spot_cvd_idx][1],
                        calls[spot_cvd_idx][2],
                    )
                    break
            except Exception:
                continue

    # Split Phase 2 results for later use
    phase2_results = list(zip(precision_calls, raw_results[len(calls):]))

    # Post-filter: FR exchange-list returns all coins, extract only target symbol
    for i, (label, _, _) in enumerate(calls):
        if label == "Funding Rate" and isinstance(raw_results[i], FetchResult):
            fr_result = raw_results[i]
            if isinstance(fr_result.data, list):
                filtered = [c for c in fr_result.data
                            if c.get("symbol", "").upper() in (sym, raw_sym)]
                raw_results[i] = FetchResult(
                    data=_format_fr_compact(filtered, raw_sym),
                    age_seconds=fr_result.age_seconds,
                    is_cached=fr_result.is_cached,
                    fetched_at=fr_result.fetched_at,
                )

    # Build status map
    scan_time = datetime.now(WIB).strftime("%Y-%m-%d %H:%M:%S WIB")
    output = f"# FULL SCAN — {raw_sym} ({interval})\n"
    output += f"**Scan time:** {scan_time}\n\n"

    # Status checklist
    output += "## Phase 1 — Quick Scan Status\n"
    critical_failed = []
    supplementary_failed = []
    max_age = 0.0

    for (label, _, _), result in zip(calls, raw_results):
        # Match "Spot CVD (fallback: OKX)" to CRITICAL set "Spot CVD"
        base_label = label.split(" (fallback")[0]
        is_critical = base_label in CRITICAL_METRICS
        tag = "CRITICAL" if is_critical else "SUPPORT"

        if isinstance(result, Exception):
            status = f"FAILED ({client._mask_key(str(result))})"
            icon = "X"
            if is_critical:
                critical_failed.append(label)
            else:
                supplementary_failed.append(label)
        elif isinstance(result, FetchResult):
            # Detect empty dataset (API succeeded but returned no data or all $0)
            is_empty = (isinstance(result.data, list) and len(result.data) == 0)
            if not is_empty and isinstance(result.data, list) and result.data:
                is_empty = _check_all_zero(base_label, result.data)
            if is_empty:
                status = "NO DATA (empty — coin may not be listed on this exchange)"
                icon = "X"
                if is_critical:
                    critical_failed.append(f"{label} (NO DATA)")
                else:
                    supplementary_failed.append(f"{label} (NO DATA)")
            elif result.is_expired:
                status = f"EXPIRED ({result.age_seconds:.0f}s old)"
                icon = "X"
                if is_critical:
                    critical_failed.append(label)
                else:
                    supplementary_failed.append(label)
            elif result.is_stale:
                status = f"STALE ({result.age_seconds:.0f}s)"
                icon = "~"
                if is_critical:
                    critical_failed.append(f"{label} (STALE)")
            else:
                status = f"OK ({result.age_label})"
                icon = "+"
            if not is_empty:
                max_age = max(max_age, result.age_seconds)
        else:
            status = "UNKNOWN"
            icon = "?"

        output += f"- [{icon}] **{label}** [{tag}]: {status}\n"

    # ── Phase 2 Precision Status ──
    output += "\n**Phase 2 — Precision Endpoints:**\n"
    for (p2_label, _, _), p2_result in phase2_results:
        if isinstance(p2_result, Exception):
            p2_status = f"FAILED ({client._mask_key(str(p2_result))})"
            p2_icon = "X"
        elif isinstance(p2_result, FetchResult):
            if p2_result.is_expired:
                p2_status = f"EXPIRED ({p2_result.age_seconds:.0f}s old)"
                p2_icon = "X"
            elif p2_result.is_stale:
                p2_status = f"STALE ({p2_result.age_seconds:.0f}s)"
                p2_icon = "~"
            else:
                p2_status = f"OK ({p2_result.age_label})"
                p2_icon = "+"
            max_age = max(max_age, p2_result.age_seconds)
        else:
            p2_status = "UNKNOWN"
            p2_icon = "?"
        output += f"- [{p2_icon}] **{p2_label}** [PRECISION]: {p2_status}\n"

    output += "\n"

    # HARD BLOCK if critical metrics failed
    if critical_failed:
        output += (
            f"## ANALYSIS BLOCKED\n\n"
            f"**CRITICAL METRICS FAILED: {', '.join(critical_failed)}**\n\n"
            f"SpotCVD + FutCVD + OI are the MINIMUM required dataset for "
            f"Ricoz Scalping Framework analysis. Without ALL three, any "
            f"conclusion would be unreliable.\n\n"
            f"**ACTION: DO NOT TRADE based on this scan. Re-run or check individual tools.**\n\n"
        )
        if supplementary_failed:
            output += f"Also failed (supplementary): {', '.join(supplementary_failed)}\n\n"
        return output

    # Data sections — formatted as readable tables (data values unchanged)
    for (label, _, _), result in zip(calls, raw_results):
        output += f"---\n\n## {label}\n\n"
        if isinstance(result, Exception):
            output += f"**FAILED:** {client._mask_key(str(result))}\n\n"
        elif isinstance(result, FetchResult):
            output += _age_banner(result)
            data = result.data
            # Match label to formatter (handle fallback labels like "Spot CVD (fallback: OKX)")
            base_label = label.split(" (fallback")[0]
            formatter = _SCAN_FORMATTERS.get(base_label)
            if formatter and isinstance(data, (list, dict)) and data:
                output += formatter(data, label)
            elif isinstance(data, list) and len(data) == 0:
                output += "**Empty dataset**\n\n"
            else:
                # Fallback: compact JSON for unknown formats
                if isinstance(data, list) and len(data) > 10:
                    output += f"*(last 10 of {len(data)})*\n"
                    output += json.dumps(data[-10:], indent=2, default=str) + "\n\n"
                else:
                    output += json.dumps(data, indent=2, default=str) + "\n\n"

    # Warnings
    if supplementary_failed:
        output += (
            f"---\n\n## DATA GAPS\n\n"
            f"**Missing supplementary metrics:** {', '.join(supplementary_failed)}\n"
            f"Core analysis (SpotCVD/FutCVD/OI) is valid but be cautious "
            f"with incomplete supporting data.\n\n"
        )

    if max_age > STALE_WARNING_THRESHOLD:
        output += (
            f"## STALENESS WARNING\n\n"
            f"**Oldest data in this scan: {max_age:.0f}s**. "
            f"Some metrics may not reflect current market conditions.\n\n"
        )

    # Analysis checklist
    output += (
        "---\n\n"
        "## Ricoz Framework Checklist\n\n"
        f"1. **SpotCVD**: If negative → DO NOT LONG {raw_sym}\n"
        "2. **FutCVD**: Must align with SpotCVD direction\n"
        "3. **OI**: Rising + price direction = trend strength\n"
        "4. **FR**: Extreme = contrarian signal\n"
        "5. **OBDelta**: Confirm buy/sell pressure\n"
        "6. **Liq Map**: Set TP near liquidation clusters\n"
        "7. **Taker**: Confirm aggressor side\n"
        "8. **L/S Ratio**: Contrarian indicator\n"
    )

    # ═════════════════════════════════════════════════════════════════
    # PHASE 2 — PRE-ENTRY PRECISION CHECK
    # ═════════════════════════════════════════════════════════════════
    output += "\n---\n\n"
    output += f"# PHASE 2 — PRE-ENTRY PRECISION CHECK ({raw_sym})\n\n"
    output += "## 🐋 WHALE CHECK\n\n"

    # Build lookup for Phase 2 results
    p2 = {}
    for (p2_lbl, _, _), p2_res in phase2_results:
        p2[p2_lbl] = p2_res

    # ── Whale Alert ──
    whale_result = p2.get("Whale Alert")
    whale_entries = []
    if isinstance(whale_result, FetchResult) and not whale_result.is_expired:
        output += _age_banner(whale_result)
        whale_data = whale_result.data
        if isinstance(whale_data, list):
            sym_whales = [
                w for w in whale_data
                if w.get("symbol", "").upper() in (sym, raw_sym)
            ]
            if sym_whales:
                for w in sym_whales[:5]:
                    direction = "LONG" if w.get("position_size", 0) > 0 else "SHORT"
                    action_type = "OPEN" if w.get("position_action") == 1 else "CLOSE"
                    size_usd = w.get("position_value_usd", 0)
                    entry = w.get("entry_price", 0)
                    whale_entries.append({
                        "direction": direction, "action": action_type,
                        "size_usd": size_usd, "entry": entry,
                    })
                    output += (
                        f"- **Whale {action_type} {direction}**: "
                        f"${size_usd:,.0f} @ ${entry:,.2f}\n"
                    )
                output += "\n"
            else:
                output += (
                    f"**Whale Alert:** No {raw_sym} whale positions "
                    f"(>$1M) on Hyperliquid\n\n"
                )
        else:
            output += "**Whale Alert:** No data available\n\n"
    elif isinstance(whale_result, Exception):
        output += (
            f"**Whale Alert:** FAILED — "
            f"{client._mask_key(str(whale_result))}\n\n"
        )
    else:
        output += "**Whale Alert:** Data expired or unavailable\n\n"

    # ── OB Bidask ±1% ──
    ob_result = p2.get("OB Bidask ±1%")
    ob_bids = 0.0
    ob_asks = 0.0
    if isinstance(ob_result, FetchResult) and not ob_result.is_expired:
        output += _age_banner(ob_result)
        ob_data = ob_result.data
        if isinstance(ob_data, list) and ob_data:
            latest = ob_data[-1]
            ob_bids = latest.get("bids_usd", 0)
            ob_asks = latest.get("asks_usd", 0)
            dominant = "BIDS (buyers)" if ob_bids > ob_asks else "ASKS (sellers)"
            ratio = ob_bids / ob_asks if ob_asks > 0 else float("inf")
            output += (
                f"**OB Bidask ±1%:** Bids ${ob_bids / 1e6:.1f}M vs "
                f"Asks ${ob_asks / 1e6:.1f}M → **{dominant}** "
                f"(ratio {ratio:.2f})\n\n"
            )
        else:
            output += "**OB Bidask ±1%:** No data\n\n"
    elif isinstance(ob_result, Exception):
        output += (
            f"**OB Bidask ±1%:** FAILED — "
            f"{client._mask_key(str(ob_result))}\n\n"
        )
    else:
        output += "**OB Bidask ±1%:** Data expired or unavailable\n\n"

    # ── Footprint ──
    fp_result = p2.get("Footprint")
    fp_direction = None
    if fp_result is not None:
        if isinstance(fp_result, FetchResult) and not fp_result.is_expired:
            output += _age_banner(fp_result)
            fp_data = fp_result.data
            if isinstance(fp_data, list) and fp_data:
                latest_candle = fp_data[-1]
                # Footprint format: [timestamp, [[price_start, price_end,
                #   buy_vol, sell_vol, buy_quote, sell_quote,
                #   buy_usdt, sell_usdt, buy_count, sell_count], ...]]
                if isinstance(latest_candle, list) and len(latest_candle) >= 2:
                    levels = latest_candle[1]
                    total_buy = 0.0
                    total_sell = 0.0
                    max_imbalance = 0.0
                    imbalance_level = 0.0
                    imbalance_side = "BUY"
                    for level in levels:
                        if isinstance(level, list) and len(level) >= 8:
                            buy_usdt = level[6]
                            sell_usdt = level[7]
                            price_mid = (level[0] + level[1]) / 2
                            total_buy += buy_usdt
                            total_sell += sell_usdt
                            imbalance = abs(buy_usdt - sell_usdt)
                            if imbalance > max_imbalance:
                                max_imbalance = imbalance
                                imbalance_level = price_mid
                                imbalance_side = (
                                    "BUY" if buy_usdt > sell_usdt
                                    else "SELL"
                                )
                    fp_direction = "BUY" if total_buy > total_sell else "SELL"
                    output += (
                        f"**Footprint:** Absorption **{imbalance_side}** kuat "
                        f"di ${imbalance_level:,.0f} "
                        f"(total buy ${total_buy / 1e6:.2f}M vs "
                        f"sell ${total_sell / 1e6:.2f}M)\n\n"
                    )
                else:
                    output += "**Footprint:** Unexpected data format\n"
                    output += (
                        json.dumps(fp_data[-1:], indent=2, default=str)
                        + "\n\n"
                    )
            else:
                output += "**Footprint:** No data\n\n"
        elif isinstance(fp_result, Exception):
            output += (
                f"**Footprint:** FAILED — "
                f"{client._mask_key(str(fp_result))}\n\n"
            )
        else:
            output += "**Footprint:** Data expired or unavailable\n\n"
    else:
        output += "**Footprint:** Not available (requires Standard+ plan)\n\n"

    # ── Liquidation Orders ──
    liq_result = p2.get("Liq Orders")
    if isinstance(liq_result, FetchResult) and not liq_result.is_expired:
        output += _age_banner(liq_result)
        liq_data = liq_result.data
        if isinstance(liq_data, list) and liq_data:
            output += f"## Liquidation Orders — {raw_sym} | {exchange} | 24h\n\n"
            output += _fmt_scan_liq_orders(liq_data)
        else:
            output += f"**Liq Orders:** No {raw_sym} liquidation orders in last 24h\n\n"
    elif isinstance(liq_result, Exception):
        output += (
            f"**Liq Orders:** FAILED — "
            f"{client._mask_key(str(liq_result))}\n\n"
        )
    else:
        output += "**Liq Orders:** Data expired or unavailable\n\n"

    # ── Hyperliquid L/S Ratio ──
    hl_result = p2.get("Hyperliquid L/S")
    if isinstance(hl_result, FetchResult) and not hl_result.is_expired:
        output += _age_banner(hl_result)
        hl_data = hl_result.data
        if isinstance(hl_data, list) and hl_data:
            output += f"## Hyperliquid L/S Ratio — {raw_sym} | 1h\n\n"
            output += _fmt_p2_hyperliquid_ls(hl_data)
        else:
            output += f"**Hyperliquid L/S:** No data for {raw_sym}\n\n"
    elif isinstance(hl_result, Exception):
        output += f"**Hyperliquid L/S:** FAILED — {client._mask_key(str(hl_result))}\n\n"
    else:
        output += "**Hyperliquid L/S:** Data expired or unavailable\n\n"

    # ── Top Trader Position L/S Ratio ──
    tp_result = p2.get("Top Position L/S")
    if isinstance(tp_result, FetchResult) and not tp_result.is_expired:
        output += _age_banner(tp_result)
        tp_data = tp_result.data
        if isinstance(tp_data, list) and tp_data:
            output += f"## Top Position L/S — {raw_sym} | {exchange} | {interval}\n\n"
            output += _fmt_p2_top_position_ls(tp_data)
        else:
            output += f"**Top Position L/S:** No data for {raw_sym}\n\n"
    elif isinstance(tp_result, Exception):
        output += f"**Top Position L/S:** FAILED — {client._mask_key(str(tp_result))}\n\n"
    else:
        output += "**Top Position L/S:** Data expired or unavailable\n\n"

    # ── Spot Large Orders (filtered ±20% from current price) ──
    slo_result = p2.get("Spot Large Orders")
    if isinstance(slo_result, FetchResult) and not slo_result.is_expired:
        output += _age_banner(slo_result)
        slo_data = slo_result.data
        if isinstance(slo_data, list) and slo_data:
            # Get current price from Phase 1 Price OHLC for filtering
            current_price = 0.0
            for (lbl, _, _), res in zip(calls, raw_results):
                if lbl == "Price OHLC" and isinstance(res, FetchResult):
                    pdata = res.data
                    if isinstance(pdata, list) and pdata:
                        last_c = pdata[-1]
                        if isinstance(last_c, dict):
                            current_price = float(_get(last_c, "c", "close", default=0))
                    break
            # Filter orders within ±20% of current price
            if current_price > 0:
                lo = current_price * 0.8
                hi = current_price * 1.2
                slo_data = [
                    r for r in slo_data
                    if isinstance(r, dict) and lo <= float(_get(r, "limit_price", "price", default=0)) <= hi
                ]
            output += f"## Spot Large Orders — {raw_sym} | {exchange} | ±20% from ${current_price:,.2f}\n\n"
            output += _fmt_p2_spot_large_orders(slo_data)
        else:
            output += f"**Spot Large Orders:** No large orders for {raw_sym}\n\n"
    elif isinstance(slo_result, Exception):
        output += f"**Spot Large Orders:** FAILED — {client._mask_key(str(slo_result))}\n\n"
    else:
        output += "**Spot Large Orders:** Data expired or unavailable\n\n"

    # ── RSI (from rsi/list — filter by symbol) ──
    rsi_result = p2.get("RSI")
    rsi_value = None
    rsi_label = ""
    if isinstance(rsi_result, FetchResult) and not rsi_result.is_expired:
        output += _age_banner(rsi_result)
        rsi_data = rsi_result.data
        if isinstance(rsi_data, list) and rsi_data:
            # rsi/list returns all coins — filter for our symbol
            coin_rsi = None
            for entry in rsi_data:
                if isinstance(entry, dict) and entry.get("symbol", "").upper() in (sym, raw_sym):
                    coin_rsi = entry
                    break
            if coin_rsi:
                # Pick RSI for the closest matching interval
                interval_map = {"1m": "15m", "3m": "15m", "5m": "15m", "15m": "15m",
                                "30m": "1h", "1h": "1h", "2h": "4h", "4h": "4h",
                                "8h": "12h", "12h": "12h", "1d": "24h"}
                rsi_key = f"rsi_{interval_map.get(interval, '1h')}"
                rsi_value = coin_rsi.get(rsi_key)

                # Show all available RSI timeframes
                rsi_parts = []
                for tf in ("15m", "1h", "4h", "12h", "24h"):
                    v = coin_rsi.get(f"rsi_{tf}")
                    if v is not None:
                        rsi_parts.append(f"{tf}={v:.1f}")
                output += f"**RSI:** {' | '.join(rsi_parts)}\n"

                if rsi_value is None:
                    output += f"*(no RSI data for {interval} interval)*\n"
                output += "\n"
            else:
                output += f"**RSI:** {raw_sym} not found in RSI list\n\n"
        else:
            output += "**RSI:** No data\n\n"
    elif isinstance(rsi_result, Exception):
        output += (
            f"**RSI:** FAILED — "
            f"{client._mask_key(str(rsi_result))}\n\n"
        )
    else:
        output += "**RSI:** Data expired or unavailable\n\n"


    # ═════════════════════════════════════════════════════════════════
    # PHASE 3 — BINANCE DIRECT CROSS-CHECK
    # ═════════════════════════════════════════════════════════════════
    output += "\n---\n\n"
    output += f"# PHASE 3 — BINANCE DIRECT CROSS-CHECK ({raw_sym})\n\n"

    # Symbol mapping for Binance (some coins have different spot names)
    BINANCE_SPOT_MAP = {"HYPE": "HYPER"}
    bn_spot_sym = BINANCE_SPOT_MAP.get(sym, sym) + "USDT"
    bn_fut_sym = pair  # e.g. SOLUSDT

    # Fetch Binance data in parallel
    bn_tasks = [
        binance_futures_request("/fapi/v1/premiumIndex", {"symbol": bn_fut_sym}),
        binance_futures_request("/fapi/v1/openInterest", {"symbol": bn_fut_sym}),
        binance_futures_request(
            "/futures/data/takerlongshortRatio",
            {"symbol": bn_fut_sym, "period": interval if interval in ("5m","15m","30m","1h","2h","4h") else "5m", "limit": 5},
        ),
        binance_futures_request(
            "/futures/data/globalLongShortAccountRatio",
            {"symbol": bn_fut_sym, "period": interval if interval in ("5m","15m","30m","1h","2h","4h") else "5m", "limit": 3},
        ),
        binance_spot_request("/api/v3/klines", {"symbol": bn_spot_sym, "interval": interval, "limit": 10}, weight=2),
        binance_futures_request("/fapi/v1/klines", {"symbol": bn_fut_sym, "interval": interval, "limit": 50}, weight=5),
    ]
    bn_results = await asyncio.gather(*bn_tasks, return_exceptions=True)
    bn_premium, bn_oi, bn_taker, bn_ls, bn_klines, bn_fut_klines = bn_results

    # ── Futures Price + Funding Rate ──
    if isinstance(bn_premium, dict) and "markPrice" in bn_premium:
        mark = float(bn_premium["markPrice"])
        fr = float(bn_premium.get("lastFundingRate", 0))
        fr_pct = fr * 100
        fr_label = "NEGATIF (shorts pay)" if fr < 0 else "POSITIF (longs pay)"
        output += f"**Binance Mark Price:** ${mark:,.2f}\n"
        output += f"**Binance Funding Rate:** {fr_pct:+.4f}% — {fr_label}\n\n"
    elif isinstance(bn_premium, dict) and "error" in bn_premium:
        output += f"**Binance Price:** {bn_premium['error']}\n\n"

    # ── Open Interest ──
    if isinstance(bn_oi, dict) and "openInterest" in bn_oi:
        oi_val = float(bn_oi["openInterest"])
        if isinstance(bn_premium, dict) and "markPrice" in bn_premium:
            oi_usd = oi_val * float(bn_premium["markPrice"])
            output += f"**Binance OI:** {oi_val:,.0f} {raw_sym} (${oi_usd/1e6:,.1f}M)\n\n"
        else:
            output += f"**Binance OI:** {oi_val:,.0f} {raw_sym}\n\n"

    # ── Taker Buy/Sell Volume (cross-check FutCVD) ──
    if isinstance(bn_taker, list) and bn_taker:
        # Get mark price for USD conversion
        mark_price = 0.0
        if isinstance(bn_premium, dict) and "markPrice" in bn_premium:
            mark_price = float(bn_premium["markPrice"])
        output += "**Binance Futures Taker Buy/Sell:**\n```\n"
        output += f"{'Time':>6} | {'Buy':>10} | {'Sell':>10} | {'Net':>10} | {'Ratio':>6}\n"
        output += f"{'─'*6} | {'─'*10} | {'─'*10} | {'─'*10} | {'─'*6}\n"
        bn_buy_dom = 0
        bn_total_net = 0.0
        for t in bn_taker[-5:]:
            ratio = float(t.get("buySellRatio", 0))
            buy_qty = float(t.get("buyVol", 0))
            sell_qty = float(t.get("sellVol", 0))
            buy_usd = buy_qty * mark_price if mark_price else buy_qty
            sell_usd = sell_qty * mark_price if mark_price else sell_qty
            net_usd = buy_usd - sell_usd
            bn_total_net += net_usd
            if net_usd > 0:
                bn_buy_dom += 1
            ts_ms = t.get("timestamp", 0)
            ts_str = datetime.fromtimestamp(ts_ms / 1000, tz=WIB).strftime("%H:%M") if ts_ms else "?"
            output += f"{ts_str:>6} | {_fmt_num(buy_usd):>10} | {_fmt_num(sell_usd):>10} | {_fmt_num(net_usd, signed=True):>10} | {ratio:>6.3f}\n"
        output += "```\n"
        output += f"**Stats:** {bn_buy_dom}/5 buy-dominant | total net: {_fmt_num(bn_total_net, signed=True)}\n\n"

    # ── Long/Short Account Ratio ──
    if isinstance(bn_ls, list) and bn_ls:
        latest_ls = bn_ls[-1]
        long_pct = float(latest_ls.get("longAccount", 0)) * 100
        ratio = float(latest_ls.get("longShortRatio", 0))
        output += f"**Binance L/S Ratio:** {long_pct:.1f}% long (ratio {ratio:.3f})\n\n"

    # ── Spot Klines CVD (taker buy vs sell) ──
    if isinstance(bn_klines, list) and bn_klines:
        output += f"**Binance Spot CVD ({bn_spot_sym}):**\n"
        cumulative = 0.0
        for c in bn_klines[-5:]:
            ts_str = datetime.fromtimestamp(c[0] / 1000, tz=WIB).strftime("%H:%M")
            vol = float(c[5])
            buy = float(c[9])
            sell = vol - buy
            delta = buy - sell
            cumulative += delta
            bias = "BUY" if delta > 0 else "SELL"
            output += f"- {ts_str}: buy={buy:,.0f} sell={sell:,.0f} delta={delta:+,.0f} → **{bias}**\n"
        output += f"- **Cumulative (5 candles): {cumulative:+,.0f} {sym}**\n\n"
    elif isinstance(bn_klines, dict) and "error" in bn_klines:
        output += f"**Binance Spot:** {bn_spot_sym} — {bn_klines.get('error', 'not available')}\n\n"

    # ── VWAP (Volume Weighted Average Price) ──
    # Calculated from futures klines: VWAP = Σ(TP × Vol) / Σ(Vol)
    # where TP (Typical Price) = (High + Low + Close) / 3
    # Binance klines: [openTime, O, H, L, C, vol, closeTime, quoteVol, trades, takerBuyBaseVol, takerBuyQuoteVol, ...]
    if isinstance(bn_fut_klines, list) and len(bn_fut_klines) >= 3:
        try:
            sum_tp_vol = 0.0
            sum_vol = 0.0
            candle_count = len(bn_fut_klines)
            first_ts = bn_fut_klines[0][0]
            last_ts = bn_fut_klines[-1][0]

            for c in bn_fut_klines:
                h = float(c[2])
                l = float(c[3])
                cl = float(c[4])
                vol = float(c[5])
                tp = (h + l + cl) / 3
                sum_tp_vol += tp * vol
                sum_vol += vol

            if sum_vol > 0:
                vwap = sum_tp_vol / sum_vol
                current_price = float(bn_fut_klines[-1][4])  # last close
                diff = current_price - vwap
                diff_pct = (diff / vwap) * 100
                position = "ABOVE" if diff > 0 else "BELOW"
                first_str = datetime.fromtimestamp(first_ts / 1000, tz=WIB).strftime("%H:%M")
                last_str = datetime.fromtimestamp(last_ts / 1000, tz=WIB).strftime("%H:%M")

                output += (
                    f"**Futures VWAP ({candle_count}x {interval}, {first_str}-{last_str} WIB):**\n"
                    f"- VWAP: ${vwap:,.2f}\n"
                    f"- Price: ${current_price:,.2f}\n"
                    f"- Price {position} VWAP by ${abs(diff):,.2f} ({diff_pct:+.2f}%)\n"
                    f"- Total volume: {_fmt_num(sum_vol, '$')}\n\n"
                )
        except (IndexError, ValueError, TypeError):
            pass  # silently skip if klines format unexpected
    elif isinstance(bn_fut_klines, dict) and "error" in bn_fut_klines:
        output += f"**VWAP:** {bn_fut_klines.get('error', 'futures klines not available')}\n\n"

    output += "\n"

    # ═════════════════════════════════════════════════════════════════
    # PHASE 4 — ON-CHAIN LAYER (Nansen + Arkham)
    # ═════════════════════════════════════════════════════════════════
    output += "---\n\n"
    output += f"# PHASE 4 — ON-CHAIN LAYER ({raw_sym})\n\n"

    # Fetch Nansen + Arkham in parallel
    p4_tasks = []
    # Nansen token flows
    nansen_skip = raw_sym.upper() in NANSEN_UNSUPPORTED
    nansen_has_map = raw_sym.upper() in NANSEN_TOKEN_MAP
    if nansen_has_map and not nansen_skip:
        p4_tasks.append(("nansen", nansen_token_flow_intelligence(raw_sym)))
    else:
        async def _noop():
            return None
        p4_tasks.append(("nansen", _noop()))

    # Arkham exchange flow
    arkham_chain = ARKHAM_CHAIN_MAP.get(raw_sym.upper(), "ethereum")
    arkham_params: dict = {}
    if arkham_chain:
        arkham_params["chains"] = arkham_chain
    p4_tasks.append(("arkham", arkham_get(f"/flow/entity/{exchange.lower()}", arkham_params)))

    p4_results = await asyncio.gather(
        *[t for _, t in p4_tasks], return_exceptions=True
    )
    p4 = dict(zip([n for n, _ in p4_tasks], p4_results))

    # ── Nansen On-Chain Flows ──
    nansen_result = p4.get("nansen")
    if nansen_skip:
        output += (
            f"## Nansen On-Chain Flows — {raw_sym} | 1h\n\n"
            f"*(Not supported — BTC and Hyperliquid chain excluded)*\n\n"
        )
    elif isinstance(nansen_result, dict) and nansen_result:
        output += f"## Nansen On-Chain Flows — {raw_sym} | 1h\n\n"
        output += _fmt_nansen_flows(nansen_result, raw_sym)
    elif nansen_has_map:
        output += f"## Nansen On-Chain Flows — {raw_sym} | 1h\n\n"
        output += f"**No data returned for {raw_sym}**\n\n"
    else:
        output += (
            f"## Nansen On-Chain Flows — {raw_sym} | 1h\n\n"
            f"*(Token not in address map — add to NANSEN_TOKEN_MAP)*\n\n"
        )

    # ── Arkham Exchange Flow ──
    arkham_result = p4.get("arkham")
    output += f"## Arkham Exchange Flow — {exchange} | {arkham_chain or 'ALL'}\n\n"
    if isinstance(arkham_result, Exception):
        output += f"**FAILED:** {str(arkham_result)[:200]}\n\n"
    elif isinstance(arkham_result, dict):
        if arkham_result.get("status") == "error":
            output += f"**ERROR:** {arkham_result.get('error', 'unknown')}\n\n"
        else:
            data = arkham_result.get("data")
            # Arkham returns {chain_name: [time_series]} — extract the series
            series = []
            if isinstance(data, dict):
                # Try chain-specific key first, then fallback to any list value
                if arkham_chain and arkham_chain in data:
                    series = data[arkham_chain]
                else:
                    for v in data.values():
                        if isinstance(v, list):
                            series = v
                            break
            elif isinstance(data, list):
                series = data

            if isinstance(series, list) and series:
                # Show last 7 days (last 7 entries since it's daily data)
                recent = series[-7:]
                # Summary from last entry
                total_in = sum(float(p.get("inflow", 0)) for p in recent)
                total_out = sum(float(p.get("outflow", 0)) for p in recent)
                total_net = total_in - total_out
                direction = "NET INFLOW" if total_net > 0 else "NET OUTFLOW"
                output += (
                    f"**7d Summary:** In {_fmt_num(total_in)} | "
                    f"Out {_fmt_num(total_out)} | "
                    f"Net {_fmt_num(abs(total_net))} ({direction})\n\n"
                )
                output += "```\n"
                output += f"{'Date':>12} | {'Inflow':>12} | {'Outflow':>12} | {'Net':>12}\n"
                output += f"{'─'*12} | {'─'*12} | {'─'*12} | {'─'*12}\n"
                for point in recent:
                    ts = point.get("time", "")
                    # Arkham returns ISO date string "2026-03-20T00:00:00Z"
                    if isinstance(ts, str) and len(ts) >= 10:
                        ts_str = ts[:10]  # YYYY-MM-DD
                    else:
                        ts_str = _ts_wib(ts)
                    p_in = float(point.get("inflow", 0))
                    p_out = float(point.get("outflow", 0))
                    p_net = p_in - p_out
                    output += (
                        f"{ts_str:>12} | {_fmt_num(p_in):>12} | "
                        f"{_fmt_num(p_out):>12} | {_fmt_num(p_net, signed=True):>12}\n"
                    )
                output += "```\n\n"
            else:
                output += "**No flow data available**\n\n"
    else:
        output += "**No data**\n\n"

    output += "\n"
    return output


# ═══════════════════════════════════════════════════════════════════════════════
# HISTORICAL & TREND TOOLS (19-22) — Persistent Storage
# ═══════════════════════════════════════════════════════════════════════════════

ENDPOINT_MAP = {
    "spot_cvd": "/api/spot/aggregated-cvd/history",
    "futures_cvd": "/api/futures/aggregated-cvd/history",
    "funding_rate": "/api/futures/funding-rate/exchange-list",
    "open_interest": "/api/futures/open-interest/aggregated-history",
    "orderbook": "/api/futures/orderbook/aggregated-ask-bids-history",
    "liquidation": "/api/futures/liquidation/history",
    "price": "/api/futures/price/history",
    "taker": "/api/futures/aggregated-taker-buy-sell-volume/history",
    "long_short": "/api/futures/global-long-short-account-ratio/history",
}


@mcp.tool()
async def coinglass_compare(
    symbol: str = "BTC",
    metric: str = "spot_cvd",
    hours_ago: float = 1.0,
    interval: str = "5m",
    exchange: str = DEFAULT_EXCHANGE,
) -> str:
    """Compare current data with historical data — see if metric went UP or DOWN.

    Example: "Compare SOL spot_cvd now vs 2 hours ago"
    This requires the metric to have been fetched before (stored in local DB).

    Args:
        symbol: Coin symbol (BTC, ETH, SOL, etc.)
        metric: One of: spot_cvd, futures_cvd, funding_rate, open_interest,
                orderbook, liquidation, price, taker, long_short
        hours_ago: How many hours back to compare (e.g., 0.5, 1, 2, 4, 8, 24)
        interval: Candle interval for time-series metrics
        exchange: Exchange name (Binance, OKX, Bybit, etc.)
    """
    endpoint = ENDPOINT_MAP.get(metric)
    if not endpoint:
        available = ", ".join(ENDPOINT_MAP.keys())
        return f"**ERROR:** Unknown metric '{metric}'. Available: {available}"

    sym = normalize_symbol(symbol)
    pair = to_pair(symbol)

    # Build params per V4 API requirements
    if metric == "funding_rate":
        params = {}
    elif metric == "orderbook":
        params = {"exchange_list": exchange, "symbol": sym, "interval": interval, "range": "1", "limit": 20}
    elif metric in ("spot_cvd", "futures_cvd"):
        # Aggregated CVD: exchange_list + coin
        params = {"exchange_list": exchange, "symbol": sym, "interval": interval, "limit": 20}
    elif metric == "taker":
        # Aggregated taker: exchange_list + coin
        params = {"exchange_list": exchange, "symbol": sym, "interval": interval, "limit": 20}
    elif metric in ("price", "long_short", "liquidation"):
        # Pair-level: exchange + pair
        params = {"exchange": exchange, "symbol": pair, "interval": interval, "limit": 20}
    else:
        # Aggregated coin-level (open_interest): exchange_list + coin
        params = {"exchange_list": exchange, "symbol": sym, "interval": interval, "limit": 20}

    current: FetchResult | None = None
    current_err = ""
    try:
        current = await client.get(endpoint, params)
    except Exception as e:
        current_err = str(e)

    # Get historical from storage
    historical = await client.storage.aget_historical(endpoint, sym, hours_ago, interval)

    output = f"## Compare {metric.upper()} — {sym}\n"

    if current is not None:
        output += f"### Current\n"
        output += _age_banner(current)
        data = current.data
        if isinstance(data, list) and len(data) > 5:
            output += json.dumps(data[-5:], indent=2, default=str) + "\n\n"
        else:
            output += json.dumps(data, indent=2, default=str) + "\n\n"
    else:
        output += f"### Current\n**ERROR fetching live data:** {current_err}\n\n"

    if historical is not None:
        age = historical["age_minutes"]
        hist_data = historical["data"]
        ts = datetime.fromtimestamp(
            historical["fetched_at"], tz=WIB
        ).strftime("%Y-%m-%d %H:%M:%S WIB")

        # Accurate header: show actual snapshot age, not requested
        actual_h = age / 60
        output = output.replace(
            f"## Compare {metric.upper()} — {sym}\n",
            f"## Compare {metric.upper()} — {sym}\n"
            f"**Now vs {actual_h:.1f}h ago** "
            f"(requested {hours_ago}h, closest snapshot: {age:.0f}min ago)\n\n",
        )

        output += f"### Historical (from storage)\n"
        output += f"**Stored at:** {ts} ({age:.0f} minutes ago)\n\n"

        expected_age_min = hours_ago * 60
        drift = abs(age - expected_age_min)
        if drift > 30:
            output += (
                f"**WARNING:** No snapshot from {hours_ago}h ago. "
                f"Using closest available: {age:.0f}min ago "
                f"(drift {drift:.0f}min). Interpret with caution.\n\n"
            )

        if isinstance(hist_data, list) and len(hist_data) > 5:
            output += json.dumps(hist_data[-5:], indent=2, default=str) + "\n\n"
        else:
            output += json.dumps(hist_data, indent=2, default=str) + "\n\n"
    else:
        output += (
            f"### Historical\n"
            f"**NO DATA** from ~{hours_ago}h ago in storage.\n\n"
            f"Data is stored automatically each time you query a metric. "
            f"Keep querying periodically to build history for comparison.\n\n"
        )

    return output


@mcp.tool()
async def coinglass_trend(
    symbol: str = "BTC",
    metric: str = "spot_cvd",
    hours: float = 4.0,
    interval: str = "5m",
) -> str:
    """Show trend of a metric over time — all stored snapshots.

    Shows how a metric changed over the past N hours based on stored data.
    Useful for: "Is OI for SOL trending up or down over last 4 hours?"

    Args:
        symbol: Coin symbol (BTC, ETH, SOL, etc.)
        metric: One of: spot_cvd, futures_cvd, funding_rate, open_interest,
                orderbook, liquidation, price, taker, long_short
        hours: How many hours of history to show (e.g., 1, 2, 4, 8, 24)
        interval: Candle interval filter
    """
    endpoint = ENDPOINT_MAP.get(metric)
    if not endpoint:
        available = ", ".join(ENDPOINT_MAP.keys())
        return f"Unknown metric '{metric}'. Available: {available}"

    sym = normalize_symbol(symbol)
    snapshots = await client.storage.aget_trend(endpoint, sym, hours, interval)

    output = f"## Trend {metric.upper()} — {sym} (last {hours}h)\n\n"

    if not snapshots:
        output += (
            f"*No stored snapshots found for {sym} {metric} in the last {hours}h.*\n\n"
            "**Tip:** Data is stored automatically each time you query a metric. "
            "Use the individual tools periodically to build up historical "
            "snapshots for trend analysis.\n"
        )
        return output

    output += f"**{len(snapshots)} snapshots found**\n\n"

    # Extract CVD/value from each snapshot for reset detection
    cvd_values = []
    for snap in snapshots:
        ts = datetime.fromtimestamp(
            snap["fetched_at"], tz=WIB
        ).strftime("%H:%M:%S WIB")
        age = snap["age_minutes"]
        data = snap["data"]
        age_label = f"{age:.0f}min ago"
        if isinstance(data, list) and len(data) > 0:
            last = data[-1] if isinstance(data[-1], dict) else data[-1]
            output += f"**{ts}** ({age_label}) — last: {json.dumps(last, default=str)}\n\n"
            # Track CVD values for reset detection
            if isinstance(last, dict):
                for key in ("cum_vol_delta", "cvd", "v", "value"):
                    if key in last:
                        cvd_values.append((ts, float(last[key])))
                        break
        elif isinstance(data, dict):
            output += f"**{ts}** ({age_label}) — {json.dumps(data, default=str)[:200]}\n\n"
        else:
            output += f"**{ts}** ({age_label}) — {str(data)[:200]}\n\n"

    # Detect CVD resets (sudden drops >40% between consecutive snapshots)
    if "cvd" in metric and len(cvd_values) >= 2:
        for i in range(1, len(cvd_values)):
            prev_ts, prev_val = cvd_values[i - 1]
            curr_ts, curr_val = cvd_values[i]
            if prev_val != 0:
                change_pct = (curr_val - prev_val) / abs(prev_val) * 100
                if change_pct < -40:
                    output += (
                        f"**CVD RESET DETECTED** between {prev_ts} → {curr_ts} "
                        f"(drop {change_pct:.0f}%)\n"
                        f"This is likely a daily CVD reset, NOT a bearish signal. "
                        f"Verify against raw chart before interpreting.\n\n"
                    )

    return output


@mcp.tool()
async def coinglass_storage_stats(symbol: str = "") -> str:
    """Show storage statistics — how much historical data is stored.

    Shows total records, oldest/newest data, top symbols tracked.
    Use this to check if historical data is available for trend analysis.

    Args:
        symbol: Ignored — shows all stats. Accepted for compatibility.
    """
    stats = await client.storage.aget_stats()
    output = "## Storage Statistics\n\n"
    output += json.dumps(stats, indent=2, default=str) + "\n\n"

    # Rate limit info
    if client._rate_limiter:
        rl = client._rate_limiter.usage
        output += (
            f"## Rate Limit Status\n\n"
            f"- Used: {rl['used']}/{rl['limit']} req/min\n"
            f"- Remaining: {rl['remaining']}\n\n"
        )

    output += (
        "**How it works:**\n"
        "- Every API call is automatically stored in SQLite\n"
        "- Data is kept for 48 hours, then auto-cleaned\n"
        "- Use `coinglass_compare` to compare now vs X hours ago\n"
        "- Use `coinglass_trend` to see how a metric changed over time\n"
        "- More queries = more history = better trend analysis\n"
    )
    return output


# ═══════════════════════════════════════════════════════════════════════════════
# BINANCE TOOLS — Direct market data (no API key needed)
# ═══════════════════════════════════════════════════════════════════════════════

from .binance_spot import (
    binance_spot_price,
    binance_spot_depth,
    binance_spot_klines,
    binance_spot_trades,
    binance_spot_agg_trades,
    binance_spot_ticker_24h,
    binance_spot_book_ticker,
    binance_spot_avg_price,
)

from .binance_futures import (
    binance_futures_price,
    binance_futures_funding_rate,
    binance_futures_open_interest,
    binance_futures_oi_history,
    binance_futures_long_short_ratio,
    binance_futures_top_ls_ratio,
    binance_futures_taker_volume,
    binance_futures_klines,
    binance_futures_depth,
    binance_futures_ticker_24h,
    binance_futures_liquidation,
)

# Register Binance Spot tools
mcp.tool()(binance_spot_price)
mcp.tool()(binance_spot_depth)
mcp.tool()(binance_spot_klines)
mcp.tool()(binance_spot_trades)
mcp.tool()(binance_spot_agg_trades)
mcp.tool()(binance_spot_ticker_24h)
mcp.tool()(binance_spot_book_ticker)
mcp.tool()(binance_spot_avg_price)

# Register Binance Futures tools
mcp.tool()(binance_futures_price)
mcp.tool()(binance_futures_funding_rate)
mcp.tool()(binance_futures_open_interest)
mcp.tool()(binance_futures_oi_history)
mcp.tool()(binance_futures_long_short_ratio)
mcp.tool()(binance_futures_top_ls_ratio)
mcp.tool()(binance_futures_taker_volume)
mcp.tool()(binance_futures_klines)
mcp.tool()(binance_futures_depth)
mcp.tool()(binance_futures_ticker_24h)
mcp.tool()(binance_futures_liquidation)


# ═══════════════════════════════════════════════════════════════════════════════
# Server Entry Point
# ═══════════════════════════════════════════════════════════════════════════════


def main():
    """Run the MCP server."""
    transport = os.getenv("MCP_TRANSPORT", "streamable-http")

    if transport == "stdio":
        mcp.run(transport="stdio")
    else:
        mcp.run(
            transport="streamable-http",
            host=config.host,
            port=config.port,
            stateless_http=True,
        )


if __name__ == "__main__":
    main()
