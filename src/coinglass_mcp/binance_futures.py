"""Binance USDT-M Futures Market Data Tools for McPCG.

These are the MOST RELEVANT for trading framework —
OI, funding rate, taker ratio, long/short ratio.
All PUBLIC endpoints — no API key required.
"""

from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Any

from .binance_client import binance_futures_request
from .config import make_envelope

WIB = timezone(timedelta(hours=7))


# ═══════════════════════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def _ts() -> str:
    return datetime.now(WIB).strftime("%H:%M:%S WIB")


def _header(title: str) -> str:
    return f"## {title}\n\nData: LIVE | Source: Binance Futures | {_ts()}\n\n"


def _dt(ts) -> str:
    """Smart timestamp: HH:MM if today, else MM-DD HH:MM."""
    if not ts:
        return "N/A"
    dt_obj = datetime.fromtimestamp(int(ts) / 1000, WIB)
    now = datetime.now(WIB)
    if dt_obj.date() == now.date():
        return dt_obj.strftime("%H:%M")
    return dt_obj.strftime("%m-%d %H:%M")


def _f(val) -> float:
    """Safe float conversion."""
    try:
        return float(val)
    except (TypeError, ValueError):
        return 0.0


def _dollar(val: float) -> str:
    """Format dollar value with appropriate suffix."""
    av = abs(val)
    sign = "" if val >= 0 else "-"
    if av >= 1_000_000_000:
        return f"{sign}${av / 1e9:,.2f}B"
    if av >= 1_000_000:
        return f"{sign}${av / 1e6:,.2f}M"
    if av >= 1_000:
        return f"{sign}${av / 1e3:,.1f}K"
    return f"{sign}${av:,.2f}"


def _price(val: float) -> str:
    """Smart price formatting based on magnitude."""
    if val == 0:
        return "$0.00"
    if val >= 10:
        return f"${val:,.2f}"
    if val >= 0.01:
        return f"${val:.4f}"
    if val >= 0.0001:
        return f"${val:.6f}"
    return f"${val:.8f}"


def _err_check(data: Any, hdr: str) -> str | None:
    """Return envelope error string if data is error/empty, else None."""
    if isinstance(data, dict) and "error" in data:
        fallback = "Try coinglass equivalent tools"
        return make_envelope("failed", "binance", hdr + f"**ERROR:** {data['error']}",
                             fallback_suggestion=fallback)
    if isinstance(data, list) and len(data) == 0:
        return make_envelope("failed", "binance", hdr + "**WARNING: Empty dataset.**")
    return None


def _ok(content: str) -> str:
    """Wrap success content in Binance envelope."""
    return make_envelope("success", "binance", content)


# ═══════════════════════════════════════════════════════════════════════════════
# TOOL 9: binance_futures_price
# ═══════════════════════════════════════════════════════════════════════════════

async def binance_futures_price(symbol: str = "") -> str:
    """Get futures mark price + live funding rate from Binance.

    Without symbol, returns ALL perpetual contracts.

    Response includes: symbol, markPrice, indexPrice,
    lastFundingRate, nextFundingTime, interestRate.

    Args:
        symbol: Futures pair (e.g. SOLUSDT). Leave empty for all.
    """
    params = {}
    weight = 10
    if symbol:
        params["symbol"] = symbol.upper()
        weight = 1
    data = await binance_futures_request("/fapi/v1/premiumIndex", params, weight)
    label = f"Futures Price — {symbol.upper()}" if symbol else "Futures Prices (all)"

    hdr = _header(label)
    e = _err_check(data, hdr)
    if e:
        return e

    items = data if isinstance(data, list) else [data]

    # Single symbol — detailed view
    if len(items) == 1:
        d = items[0]
        mark = _f(d.get("markPrice"))
        index = _f(d.get("indexPrice"))
        settle = _f(d.get("estimatedSettlePrice"))
        fr = _f(d.get("lastFundingRate")) * 100
        interest = _f(d.get("interestRate")) * 100
        next_t = _dt(d.get("nextFundingTime", 0))
        fr_tag = "NEGATIF (shorts pay)" if fr < 0 else "POSITIF (longs pay)" if fr > 0 else "NEUTRAL"

        return _ok(hdr + "\n".join([
            f"- **Mark Price:** {_price(mark)}",
            f"- **Index Price:** {_price(index)}",
            f"- **Est. Settle Price:** {_price(settle)}",
            f"- **Funding Rate:** {fr:+.4f}% — {fr_tag}",
            f"- **Interest Rate:** {interest:.4f}%",
            f"- **Next Funding:** {next_t}",
        ]) + "\n")

    # Multiple symbols — table view
    if len(items) > 30:
        total = len(items)
        items = items[:30]
        hdr += f"*(showing first 30 of {total})*\n\n"

    table = "```\n"
    table += f" {'Symbol':<14} | {'Mark Price':>13} | {'FR':>9} | Next\n"
    table += f" {'─' * 14} | {'─' * 13} | {'─' * 9} | ─────\n"
    for d in items:
        sym = d.get("symbol", "")
        mark = _f(d.get("markPrice"))
        fr = _f(d.get("lastFundingRate")) * 100
        next_t = _dt(d.get("nextFundingTime", 0))
        table += f" {sym:<14} | {_price(mark):>13} | {fr:>+8.4f}% | {next_t}\n"
    table += "```\n"
    return _ok(hdr + table)


# ═══════════════════════════════════════════════════════════════════════════════
# TOOL 10: binance_futures_funding_rate
# ═══════════════════════════════════════════════════════════════════════════════

async def binance_futures_funding_rate(symbol: str, limit: int = 100) -> str:
    """Get funding rate history from Binance.

    Cross-check with CoinGlass funding rate data.

    Args:
        symbol: Futures pair (e.g. SOLUSDT)
        limit: Number of records (default 100, max 1000)
    """
    params = {"symbol": symbol.upper(), "limit": min(limit, 1000)}
    data = await binance_futures_request("/fapi/v1/fundingRate", params, 1)
    title = f"Funding Rate History — {symbol.upper()}"

    hdr = _header(title)
    e = _err_check(data, hdr)
    if e:
        return e

    items = data if isinstance(data, list) else [data]
    if len(items) > 30:
        total = len(items)
        items = items[-30:]
        hdr += f"*(showing last 30 of {total})*\n\n"

    table = "```\n"
    table += f" {'Time':>16} | {'FR':>9} | {'Mark Price':>13}\n"
    table += f" {'─' * 16} | {'─' * 9} | {'─' * 13}\n"
    for d in items:
        t = _dt(d.get("fundingTime", 0))
        fr = _f(d.get("fundingRate")) * 100
        mark = _f(d.get("markPrice"))
        table += f" {t:>16} | {fr:>+8.4f}% | {_price(mark):>13}\n"
    table += "```\n"

    rates = [_f(d.get("fundingRate")) * 100 for d in items]
    avg_fr = sum(rates) / len(rates) if rates else 0
    table += f"\n**Summary:** Avg FR: {avg_fr:+.4f}% over {len(items)} periods"

    return _ok(hdr + table)


# ═══════════════════════════════════════════════════════════════════════════════
# TOOL 11: binance_futures_open_interest
# ═══════════════════════════════════════════════════════════════════════════════

async def binance_futures_open_interest(symbol: str) -> str:
    """Get current Open Interest (total open contracts) from Binance.

    Realtime single snapshot.

    Args:
        symbol: Futures pair (e.g. SOLUSDT)
    """
    params = {"symbol": symbol.upper()}
    data = await binance_futures_request("/fapi/v1/openInterest", params, 1)
    title = f"Open Interest — {symbol.upper()}"

    hdr = _header(title)
    e = _err_check(data, hdr)
    if e:
        return e

    d = data if isinstance(data, dict) else (data[0] if isinstance(data, list) and data else {})
    oi = _f(d.get("openInterest"))
    sym = d.get("symbol", symbol.upper())
    t = _dt(d.get("time", 0))

    return _ok(hdr + "\n".join([
        f"- **Symbol:** {sym}",
        f"- **Open Interest:** {oi:,.3f} contracts",
        f"- **Time:** {t}",
    ]) + "\n")


# ═══════════════════════════════════════════════════════════════════════════════
# TOOL 12: binance_futures_oi_history
# ═══════════════════════════════════════════════════════════════════════════════

async def binance_futures_oi_history(symbol: str, period: str = "5m", limit: int = 100) -> str:
    """Get Open Interest history from Binance.

    Cross-check with CoinGlass OI aggregate.

    Args:
        symbol: Futures pair (e.g. SOLUSDT)
        period: 5m, 15m, 30m, 1h, 2h, 4h, 6h, 12h, 1d
        limit: Number of records (default 100, max 500)
    """
    params = {"symbol": symbol.upper(), "period": period, "limit": min(limit, 500)}
    data = await binance_futures_request("/futures/data/openInterestHist", params, 1)
    title = f"OI History — {symbol.upper()} {period}"

    hdr = _header(title)
    e = _err_check(data, hdr)
    if e:
        return e

    items = data if isinstance(data, list) else [data]
    if len(items) > 30:
        total = len(items)
        items = items[-30:]
        hdr += f"*(showing last 30 of {total})*\n\n"

    table = "```\n"
    table += f" {'Time':>16} | {'OI (contracts)':>14} | {'OI (USD)':>12}\n"
    table += f" {'─' * 16} | {'─' * 14} | {'─' * 12}\n"
    for d in items:
        t = _dt(d.get("timestamp", 0))
        oi = _f(d.get("sumOpenInterest"))
        oi_val = _f(d.get("sumOpenInterestValue"))
        table += f" {t:>16} | {oi:>14,.2f} | {_dollar(oi_val):>12}\n"
    table += "```\n"

    if len(items) >= 2:
        first_val = _f(items[0].get("sumOpenInterestValue"))
        last_val = _f(items[-1].get("sumOpenInterestValue"))
        change = last_val - first_val
        pct = (change / first_val * 100) if first_val else 0
        table += f"\n**Summary:** {_dollar(first_val)} → {_dollar(last_val)} (change: {_dollar(change)}, {pct:+.2f}%)"

    return _ok(hdr + table)


# ═══════════════════════════════════════════════════════════════════════════════
# TOOL 13 & 14: Long/Short Ratio (shared formatter)
# ═══════════════════════════════════════════════════════════════════════════════

def _fmt_ls(data: Any, title: str) -> str:
    """Shared formatter for L/S ratio tools (global + top trader)."""
    hdr = _header(title)
    e = _err_check(data, hdr)
    if e:
        return e

    items = data if isinstance(data, list) else [data]
    if len(items) > 30:
        total = len(items)
        items = items[-30:]
        hdr += f"*(showing last 30 of {total})*\n\n"

    table = "```\n"
    table += f" {'Time':>16} | {'Long %':>8} | {'Short %':>8} | {'Ratio':>8}\n"
    table += f" {'─' * 16} | {'─' * 8} | {'─' * 8} | {'─' * 8}\n"
    for d in items:
        t = _dt(d.get("timestamp", 0))
        long_pct = _f(d.get("longAccount")) * 100
        short_pct = _f(d.get("shortAccount")) * 100
        ratio = _f(d.get("longShortRatio"))
        table += f" {t:>16} | {long_pct:>7.1f}% | {short_pct:>7.1f}% | {ratio:>8.3f}\n"
    table += "```\n"

    ratios = [_f(d.get("longShortRatio")) for d in items]
    avg_r = sum(ratios) / len(ratios) if ratios else 0
    if len(items) >= 2:
        shift = ratios[-1] - ratios[0]
        direction = "more long" if shift > 0 else "more short"
        table += f"\n**Summary:** Avg ratio: {avg_r:.3f}, range {min(ratios):.3f}-{max(ratios):.3f} | Shift: {shift:+.3f} ({direction})"
    else:
        table += f"\n**Summary:** Ratio: {avg_r:.3f}"

    return _ok(hdr + table)


async def binance_futures_long_short_ratio(symbol: str, period: str = "5m", limit: int = 100) -> str:
    """Get global long/short ACCOUNT ratio from Binance.

    Ratio > 1 = more accounts are long.
    Ratio < 1 = more accounts are short.

    Args:
        symbol: Futures pair (e.g. SOLUSDT)
        period: 5m, 15m, 30m, 1h, 2h, 4h, 6h, 12h, 1d
        limit: Number of records (default 100, max 500)
    """
    params = {"symbol": symbol.upper(), "period": period, "limit": min(limit, 500)}
    data = await binance_futures_request("/futures/data/globalLongShortAccountRatio", params, 1)
    return _fmt_ls(data, f"Global L/S Account Ratio — {symbol.upper()} {period}")


# ═══════════════════════════════════════════════════════════════════════════════
# TOOL 14: binance_futures_top_ls_ratio
# ═══════════════════════════════════════════════════════════════════════════════

async def binance_futures_top_ls_ratio(symbol: str, period: str = "5m", limit: int = 100) -> str:
    """Get top trader long/short POSITION ratio from Binance.

    More insightful than global ratio — positions from top traders.

    Args:
        symbol: Futures pair (e.g. SOLUSDT)
        period: 5m, 15m, 30m, 1h, 2h, 4h, 6h, 12h, 1d
        limit: Number of records (default 100, max 500)
    """
    params = {"symbol": symbol.upper(), "period": period, "limit": min(limit, 500)}
    data = await binance_futures_request("/futures/data/topLongShortPositionRatio", params, 1)
    return _fmt_ls(data, f"Top Trader L/S Position Ratio — {symbol.upper()} {period}")


# ═══════════════════════════════════════════════════════════════════════════════
# TOOL 15: binance_futures_taker_volume
# ═══════════════════════════════════════════════════════════════════════════════

async def binance_futures_taker_volume(symbol: str, period: str = "5m", limit: int = 100) -> str:
    """Get futures taker buy/sell volume ratio from Binance.

    buySellRatio > 1 = buyers dominant (bullish taker flow).
    buySellRatio < 1 = sellers dominant (bearish taker flow).
    Cross-check with CoinGlass FutCVD.

    Args:
        symbol: Futures pair (e.g. SOLUSDT)
        period: 5m, 15m, 30m, 1h, 2h, 4h, 6h, 12h, 1d
        limit: Number of records (default 100, max 500)
    """
    params = {"symbol": symbol.upper(), "period": period, "limit": min(limit, 500)}
    data = await binance_futures_request("/futures/data/takerlongshortRatio", params, 1)
    title = f"Futures Taker Buy/Sell — {symbol.upper()} {period}"

    hdr = _header(title)
    e = _err_check(data, hdr)
    if e:
        return e

    items = data if isinstance(data, list) else [data]
    if len(items) > 30:
        total = len(items)
        items = items[-30:]
        hdr += f"*(showing last 30 of {total})*\n\n"

    table = "```\n"
    table += f" {'Time':>16} | {'Buy Vol':>10} | {'Sell Vol':>10} | {'Net':>10} | {'Ratio':>6} | Side\n"
    table += f" {'─' * 16} | {'─' * 10} | {'─' * 10} | {'─' * 10} | {'─' * 6} | ────\n"
    for d in items:
        t = _dt(d.get("timestamp", 0))
        buy = _f(d.get("buyVol"))
        sell = _f(d.get("sellVol"))
        net = buy - sell
        ratio = _f(d.get("buySellRatio"))
        side = "BUY" if ratio >= 1 else "SELL"
        net_str = f"+{_dollar(net)}" if net >= 0 else _dollar(net)
        table += f" {t:>16} | {_dollar(buy):>10} | {_dollar(sell):>10} | {net_str:>10} | {ratio:>6.3f} | {side:>4}\n"
    table += "```\n"

    buy_count = sum(1 for d in items if _f(d.get("buySellRatio")) >= 1)
    total_buy = sum(_f(d.get("buyVol")) for d in items)
    total_sell = sum(_f(d.get("sellVol")) for d in items)
    total_net = total_buy - total_sell
    net_str = f"+{_dollar(total_net)}" if total_net >= 0 else _dollar(total_net)
    table += f"\n**Summary:** {buy_count}/{len(items)} buy-dominant | Buy {_dollar(total_buy)} vs Sell {_dollar(total_sell)} | Net {net_str}"

    return _ok(hdr + table)


# ═══════════════════════════════════════════════════════════════════════════════
# TOOL 16: binance_futures_klines
# ═══════════════════════════════════════════════════════════════════════════════

async def binance_futures_klines(symbol: str, interval: str = "5m", limit: int = 100) -> str:
    """Get futures OHLCV candlestick data from Binance.

    Includes taker buy volume — can derive aggressor side.

    Args:
        symbol: Futures pair (e.g. SOLUSDT)
        interval: 1m,3m,5m,15m,30m,1h,2h,4h,6h,8h,12h,1d,3d,1w,1M
        limit: Number of candles (default 100, max 1500)

    Response per candle: [openTime, O, H, L, C, volume, closeTime,
    quoteVolume, trades, takerBuyBaseVol, takerBuyQuoteVol, ignore]
    """
    if limit <= 100:
        weight = 1
    elif limit <= 500:
        weight = 2
    elif limit <= 1000:
        weight = 5
    else:
        weight = 10
        limit = min(limit, 1500)
    params = {"symbol": symbol.upper(), "interval": interval, "limit": limit}
    data = await binance_futures_request("/fapi/v1/klines", params, weight)
    title = f"Futures Klines — {symbol.upper()} {interval}"

    hdr = _header(title)
    e = _err_check(data, hdr)
    if e:
        return e

    items = data if isinstance(data, list) else []
    if not items:
        return make_envelope("failed", "binance", hdr + "**WARNING: Empty dataset.**")

    if len(items) > 30:
        total = len(items)
        items = items[-30:]
        hdr += f"*(showing last 30 of {total})*\n\n"

    table = "```\n"
    table += f" {'Time':>16} | {'Open':>13} | {'High':>13} | {'Low':>13} | {'Close':>13} | {'Volume':>10} | {'Trades':>6}\n"
    table += f" {'─' * 16} | {'─' * 13} | {'─' * 13} | {'─' * 13} | {'─' * 13} | {'─' * 10} | {'─' * 6}\n"
    for c in items:
        t = _dt(c[0])
        o, h, l, cl = _f(c[1]), _f(c[2]), _f(c[3]), _f(c[4])
        vol = _f(c[7])  # quoteVolume (USD)
        trades = int(c[8]) if len(c) > 8 else 0
        table += f" {t:>16} | {_price(o):>13} | {_price(h):>13} | {_price(l):>13} | {_price(cl):>13} | {_dollar(vol):>10} | {trades:>6}\n"
    table += "```\n"

    first_o = _f(items[0][1])
    last_c = _f(items[-1][4])
    high = max(_f(c[2]) for c in items)
    low = min(_f(c[3]) for c in items)
    pct = ((last_c - first_o) / first_o * 100) if first_o else 0
    total_vol = sum(_f(c[7]) for c in items)
    table += f"\n**Summary:** Range {_price(low)}-{_price(high)} | {_price(first_o)} → {_price(last_c)} ({pct:+.2f}%) | Total vol: {_dollar(total_vol)}"

    return _ok(hdr + table)


# ═══════════════════════════════════════════════════════════════════════════════
# TOOL 17: binance_futures_depth
# ═══════════════════════════════════════════════════════════════════════════════

async def binance_futures_depth(symbol: str, limit: int = 100) -> str:
    """Get futures order book (L2) from Binance.

    Cross-check with CoinGlass OBDelta — compare bid vs ask volume.

    Args:
        symbol: Futures pair (e.g. SOLUSDT)
        limit: 5, 10, 20, 50, 100, 500, 1000
    """
    weight_map = {5: 2, 10: 2, 20: 2, 50: 5, 100: 10, 500: 20, 1000: 40}
    valid_limits = sorted(weight_map.keys())
    # Snap to nearest valid limit
    limit = min(valid_limits, key=lambda x: abs(x - limit))
    weight = weight_map[limit]
    params = {"symbol": symbol.upper(), "limit": limit}
    data = await binance_futures_request("/fapi/v1/depth", params, weight)
    title = f"Futures Order Book — {symbol.upper()} (top {limit})"

    hdr = _header(title)
    e = _err_check(data, hdr)
    if e:
        return e

    if not isinstance(data, dict) or "bids" not in data:
        return make_envelope("failed", "binance", hdr + "**WARNING: Unexpected response format.**")

    bids = data.get("bids", [])
    asks = data.get("asks", [])

    total_bid_val = sum(_f(b[0]) * _f(b[1]) for b in bids)
    total_ask_val = sum(_f(a[0]) * _f(a[1]) for a in asks)

    top_n = min(15, len(bids), len(asks))

    table = "```\n"
    table += f" {'Bid Price':>13} | {'Bid Qty':>10} | {'Bid $':>10} | {'Ask Price':>13} | {'Ask Qty':>10} | {'Ask $':>10}\n"
    table += f" {'─' * 13} | {'─' * 10} | {'─' * 10} | {'─' * 13} | {'─' * 10} | {'─' * 10}\n"
    for i in range(top_n):
        bp, bq = _f(bids[i][0]), _f(bids[i][1])
        ap, aq = _f(asks[i][0]), _f(asks[i][1])
        table += f" {_price(bp):>13} | {bq:>10,.4f} | {_dollar(bp * bq):>10} | {_price(ap):>13} | {aq:>10,.4f} | {_dollar(ap * aq):>10}\n"
    table += "```\n"

    ratio = total_bid_val / total_ask_val if total_ask_val else 0
    dominant = "BIDS (buyers)" if ratio > 1 else "ASKS (sellers)"
    spread = _f(asks[0][0]) - _f(bids[0][0]) if bids and asks else 0
    spread_pct = (spread / _f(bids[0][0]) * 100) if bids and _f(bids[0][0]) else 0

    table += f"\n**Summary:** {len(bids)} bids ({_dollar(total_bid_val)}) vs {len(asks)} asks ({_dollar(total_ask_val)}) → **{dominant}** (ratio {ratio:.2f})"
    table += f"\n**Spread:** {_price(spread)} ({spread_pct:.4f}%)"

    return _ok(hdr + table)


# ═══════════════════════════════════════════════════════════════════════════════
# TOOL 18: binance_futures_ticker_24h
# ═══════════════════════════════════════════════════════════════════════════════

async def binance_futures_ticker_24h(symbol: str = "") -> str:
    """Get 24hr futures ticker statistics from Binance.

    Includes volume, priceChange, highPrice, lowPrice, lastPrice, weightedAvgPrice.

    Args:
        symbol: Futures pair. Leave empty for all.
    """
    params = {}
    weight = 40
    if symbol:
        params["symbol"] = symbol.upper()
        weight = 1
    data = await binance_futures_request("/fapi/v1/ticker/24hr", params, weight)
    label = f"Futures 24h Ticker — {symbol.upper()}" if symbol else "Futures 24h Tickers (all)"

    hdr = _header(label)
    e = _err_check(data, hdr)
    if e:
        return e

    items = data if isinstance(data, list) else [data]

    # Single symbol — detailed view
    if len(items) == 1:
        d = items[0]
        last = _f(d.get("lastPrice"))
        change = _f(d.get("priceChange"))
        change_pct = _f(d.get("priceChangePercent"))
        high = _f(d.get("highPrice"))
        low = _f(d.get("lowPrice"))
        vol = _f(d.get("quoteVolume"))
        vwap = _f(d.get("weightedAvgPrice"))
        trades = int(_f(d.get("count")))

        return _ok(hdr + "\n".join([
            f"- **Last Price:** {_price(last)}",
            f"- **24h Change:** {change_pct:+.2f}% ({_price(abs(change))})",
            f"- **24h High:** {_price(high)}",
            f"- **24h Low:** {_price(low)}",
            f"- **24h Volume:** {_dollar(vol)}",
            f"- **VWAP:** {_price(vwap)}",
            f"- **Trades:** {trades:,}",
        ]) + "\n")

    # Multi-ticker: sort by volume, show top 30
    if len(items) > 30:
        total = len(items)
        items.sort(key=lambda x: _f(x.get("quoteVolume")), reverse=True)
        items = items[:30]
        hdr += f"*(top 30 by volume of {total})*\n\n"

    table = "```\n"
    table += f" {'Symbol':<14} | {'Last Price':>13} | {'24h %':>8} | {'Volume':>12}\n"
    table += f" {'─' * 14} | {'─' * 13} | {'─' * 8} | {'─' * 12}\n"
    for d in items:
        sym = d.get("symbol", "")
        last = _f(d.get("lastPrice"))
        pct = _f(d.get("priceChangePercent"))
        vol = _f(d.get("quoteVolume"))
        table += f" {sym:<14} | {_price(last):>13} | {pct:>+7.2f}% | {_dollar(vol):>12}\n"
    table += "```\n"
    return _ok(hdr + table)


# ═══════════════════════════════════════════════════════════════════════════════
# TOOL 19: binance_futures_liquidation
# ═══════════════════════════════════════════════════════════════════════════════

async def binance_futures_liquidation(symbol: str = "", limit: int = 100) -> str:
    """Get recent forced liquidation orders from Binance.

    Cross-check with CoinGlass liquidation data.
    NOTE: This endpoint may require API key on some regions.

    Args:
        symbol: Futures pair. Leave empty for all.
        limit: Max 1000, default 100
    """
    params = {"limit": min(limit, 1000)}
    weight = 50
    if symbol:
        params["symbol"] = symbol.upper()
        weight = 20
    data = await binance_futures_request("/fapi/v1/forceOrders", params, weight)
    label = f"Forced Liquidations — {symbol.upper()}" if symbol else "Forced Liquidations (all)"

    hdr = _header(label)
    e = _err_check(data, hdr)
    if e:
        return e

    items = data if isinstance(data, list) else [data]
    if len(items) > 30:
        total = len(items)
        items = items[-30:]
        hdr += f"*(showing last 30 of {total})*\n\n"

    total_long_val = 0.0
    total_short_val = 0.0

    table = "```\n"
    table += f" {'Symbol':<14} | {'Price':>13} | {'Qty':>10} | {'Value':>10} | {'Side':>5} | Time\n"
    table += f" {'─' * 14} | {'─' * 13} | {'─' * 10} | {'─' * 10} | {'─' * 5} | ─────\n"
    for d in items:
        sym = d.get("symbol", "")
        price = _f(d.get("averagePrice") or d.get("price"))
        qty = _f(d.get("executedQty") or d.get("origQty"))
        val = price * qty
        side = d.get("side", "")
        # SELL side = long liquidated, BUY side = short liquidated
        liq_side = "LONG" if side == "SELL" else "SHORT"
        t = _dt(d.get("time", 0))

        if liq_side == "LONG":
            total_long_val += val
        else:
            total_short_val += val

        table += f" {sym:<14} | {_price(price):>13} | {qty:>10,.4f} | {_dollar(val):>10} | {liq_side:>5} | {t}\n"
    table += "```\n"

    table += f"\n**Summary:** LONG liq: {_dollar(total_long_val)} | SHORT liq: {_dollar(total_short_val)}"

    return _ok(hdr + table)
