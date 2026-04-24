"""Binance Spot Market Data Tools for McPCG.

All endpoints are PUBLIC — no API key required.
Uses httpx via binance_client for consistency.
"""

from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Any

from .binance_client import binance_spot_request
from .config import make_envelope

WIB = timezone(timedelta(hours=7))


# ═══════════════════════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def _ts() -> str:
    return datetime.now(WIB).strftime("%H:%M:%S WIB")


def _header(title: str) -> str:
    return f"## {title}\n\nData: LIVE | Source: Binance Spot | {_ts()}\n\n"


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
        fallback = "Try coinglass_price_ohlc or coinglass_spot_cvd"
        return make_envelope("failed", "binance", hdr + f"**ERROR:** {data['error']}",
                             fallback_suggestion=fallback)
    if isinstance(data, list) and len(data) == 0:
        return make_envelope("failed", "binance", hdr + "**WARNING: Empty dataset.**")
    return None


def _ok(content: str) -> str:
    """Wrap success content in Binance envelope."""
    return make_envelope("success", "binance", content)


# ─── Spot symbol aliases (Binance quirks: perp != spot listing) ──────────────
# Some tokens list under a different symbol on Binance spot vs futures.
# User typically passes the futures/generic name (e.g. HYPEUSDT); we auto-resolve.
_SPOT_SYMBOL_ALIASES = {
    "HYPEUSDT": "HYPERUSDT",   # Hyperliquid token — spot listed as HYPER
}


def _resolve_spot_symbol(symbol: str) -> str:
    """Map user-friendly / futures symbol to actual Binance spot symbol.

    Accepts either the aliased name (HYPEUSDT) or the real name (HYPERUSDT).
    Returns whatever Binance spot API accepts.
    """
    s = symbol.upper().strip()
    return _SPOT_SYMBOL_ALIASES.get(s, s)


# ═══════════════════════════════════════════════════════════════════════════════
# TOOL 1: binance_spot_price
# ═══════════════════════════════════════════════════════════════════════════════

async def binance_spot_price(symbol: str = "") -> str:
    """Get latest spot price from Binance.

    Args:
        symbol: Trading pair (e.g. SOLUSDT, BTCUSDT). Leave empty for all symbols.

    Returns price data. Weight: 2 (single) / 4 (all).
    """
    params = {}
    weight = 4
    if symbol:
        params["symbol"] = symbol.upper()
        weight = 2
    data = await binance_spot_request("/api/v3/ticker/price", params, weight)
    label = f"Spot Price — {symbol.upper()}" if symbol else "Spot Prices (all)"

    hdr = _header(label)
    e = _err_check(data, hdr)
    if e:
        return e

    items = data if isinstance(data, list) else [data]

    # Single symbol
    if len(items) == 1:
        d = items[0]
        p = _f(d.get("price"))
        return _ok(hdr + f"- **{d.get('symbol', '')}:** {_price(p)}\n")

    # Multiple symbols — table
    if len(items) > 30:
        total = len(items)
        items = items[:30]
        hdr += f"*(showing first 30 of {total})*\n\n"

    table = "```\n"
    table += f" {'Symbol':<14} | {'Price':>13}\n"
    table += f" {'─' * 14} | {'─' * 13}\n"
    for d in items:
        sym = d.get("symbol", "")
        p = _f(d.get("price"))
        table += f" {sym:<14} | {_price(p):>13}\n"
    table += "```\n"
    return _ok(hdr + table)


# ═══════════════════════════════════════════════════════════════════════════════
# TOOL 2: binance_spot_depth
# ═══════════════════════════════════════════════════════════════════════════════

async def binance_spot_depth(symbol: str, limit: int = 100) -> str:
    """Get spot order book (L2) from Binance. Up to 5000 levels.

    Args:
        symbol: Trading pair (e.g. SOLUSDT)
        limit: Number of levels (default 100, max 5000).
               1-100=weight5, 101-500=weight25, 501-1000=weight50, 1001-5000=weight250
    """
    if limit <= 100:
        weight = 5
    elif limit <= 500:
        weight = 25
    elif limit <= 1000:
        weight = 50
    else:
        weight = 250
        limit = min(limit, 5000)
    params = {"symbol": symbol.upper(), "limit": limit}
    data = await binance_spot_request("/api/v3/depth", params, weight)
    title = f"Spot Order Book — {symbol.upper()} (top {limit})"

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
# TOOL 3: binance_spot_klines
# ═══════════════════════════════════════════════════════════════════════════════

async def binance_spot_klines(symbol: str, interval: str = "5m", limit: int = 100) -> str:
    """Get spot OHLCV candlestick data from Binance.

    Includes taker buy volume — can derive taker buy/sell ratio.

    Args:
        symbol: Trading pair (e.g. SOLUSDT)
        interval: 1s,1m,3m,5m,15m,30m,1h,2h,4h,6h,8h,12h,1d,3d,1w,1M
        limit: Number of candles (default 100, max 1000)

    Response per candle: [openTime, O, H, L, C, volume, closeTime,
    quoteVolume, trades, takerBuyBaseVol, takerBuyQuoteVol, ignore]
    """
    params = {"symbol": symbol.upper(), "interval": interval, "limit": min(limit, 1000)}
    data = await binance_spot_request("/api/v3/klines", params, 2)
    title = f"Spot Klines — {symbol.upper()} {interval}"

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
# TOOL 4: binance_spot_trades
# ═══════════════════════════════════════════════════════════════════════════════

async def binance_spot_trades(symbol: str, limit: int = 500) -> str:
    """Get recent spot trades from Binance.

    Field isBuyerMaker:
    - true = seller aggressor (taker SELL)
    - false = buyer aggressor (taker BUY)
    Can be used to calculate CVD manually.

    Args:
        symbol: Trading pair (e.g. SOLUSDT)
        limit: Number of trades (default 500, max 1000)
    """
    params = {"symbol": symbol.upper(), "limit": min(limit, 1000)}
    data = await binance_spot_request("/api/v3/trades", params, 25)
    title = f"Spot Recent Trades — {symbol.upper()}"

    hdr = _header(title)
    e = _err_check(data, hdr)
    if e:
        return e

    items = data if isinstance(data, list) else [data]
    if len(items) > 30:
        total = len(items)
        items = items[-30:]
        hdr += f"*(showing last 30 of {total})*\n\n"

    total_buy_val = 0.0
    total_sell_val = 0.0

    table = "```\n"
    table += f" {'Time':>16} | {'Price':>13} | {'Qty':>12} | {'Value':>10} | Side\n"
    table += f" {'─' * 16} | {'─' * 13} | {'─' * 12} | {'─' * 10} | ────\n"
    for d in items:
        t = _dt(d.get("time", 0))
        p = _f(d.get("price"))
        q = _f(d.get("qty"))
        val = _f(d.get("quoteQty", p * q))
        is_maker = d.get("isBuyerMaker", False)
        side = "SELL" if is_maker else "BUY"
        if is_maker:
            total_sell_val += val
        else:
            total_buy_val += val
        table += f" {t:>16} | {_price(p):>13} | {q:>12,.4f} | {_dollar(val):>10} | {side:>4}\n"
    table += "```\n"

    buy_count = sum(1 for d in items if not d.get("isBuyerMaker", False))
    table += f"\n**Summary:** {buy_count}/{len(items)} taker BUY | Buy {_dollar(total_buy_val)} vs Sell {_dollar(total_sell_val)}"

    return _ok(hdr + table)


# ═══════════════════════════════════════════════════════════════════════════════
# TOOL 5: binance_spot_agg_trades
# ═══════════════════════════════════════════════════════════════════════════════

async def binance_spot_agg_trades(symbol: str, limit: int = 500) -> str:
    """Get compressed/aggregate spot trades from Binance.

    Trades with same price & taker side are merged.
    Field "m" = buyer was maker (true = SELL aggressor).

    Args:
        symbol: Trading pair (e.g. SOLUSDT)
        limit: Number of agg trades (default 500, max 1000)
    """
    params = {"symbol": symbol.upper(), "limit": min(limit, 1000)}
    data = await binance_spot_request("/api/v3/aggTrades", params, 4)
    title = f"Spot Aggregate Trades — {symbol.upper()}"

    hdr = _header(title)
    e = _err_check(data, hdr)
    if e:
        return e

    items = data if isinstance(data, list) else [data]
    if len(items) > 30:
        total = len(items)
        items = items[-30:]
        hdr += f"*(showing last 30 of {total})*\n\n"

    total_buy_val = 0.0
    total_sell_val = 0.0

    table = "```\n"
    table += f" {'Time':>16} | {'Price':>13} | {'Qty':>12} | {'Value':>10} | Side\n"
    table += f" {'─' * 16} | {'─' * 13} | {'─' * 12} | {'─' * 10} | ────\n"
    for d in items:
        t = _dt(d.get("T", 0))
        p = _f(d.get("p"))
        q = _f(d.get("q"))
        val = p * q
        is_maker = d.get("m", False)
        side = "SELL" if is_maker else "BUY"
        if is_maker:
            total_sell_val += val
        else:
            total_buy_val += val
        table += f" {t:>16} | {_price(p):>13} | {q:>12,.4f} | {_dollar(val):>10} | {side:>4}\n"
    table += "```\n"

    buy_count = sum(1 for d in items if not d.get("m", False))
    table += f"\n**Summary:** {buy_count}/{len(items)} taker BUY | Buy {_dollar(total_buy_val)} vs Sell {_dollar(total_sell_val)}"

    return _ok(hdr + table)


# ═══════════════════════════════════════════════════════════════════════════════
# TOOL 6: binance_spot_ticker_24h
# ═══════════════════════════════════════════════════════════════════════════════

async def binance_spot_ticker_24h(symbol: str = "") -> str:
    """Get 24hr spot price change statistics from Binance.

    Includes: priceChange, priceChangePercent, volume, highPrice, lowPrice, count.

    Args:
        symbol: Trading pair. Leave empty for all (WARNING: weight 80!).
    """
    params = {}
    weight = 80
    if symbol:
        params["symbol"] = symbol.upper()
        weight = 2
    data = await binance_spot_request("/api/v3/ticker/24hr", params, weight)
    label = f"Spot 24h Ticker — {symbol.upper()}" if symbol else "Spot 24h Tickers (all)"

    hdr = _header(label)
    e = _err_check(data, hdr)
    if e:
        return e

    items = data if isinstance(data, list) else [data]

    # Single symbol — detailed view
    if len(items) == 1:
        d = items[0]
        last = _f(d.get("lastPrice"))
        change_pct = _f(d.get("priceChangePercent"))
        change = _f(d.get("priceChange"))
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
# TOOL 7: binance_spot_book_ticker
# ═══════════════════════════════════════════════════════════════════════════════

async def binance_spot_book_ticker(symbol: str = "") -> str:
    """Get best bid/ask price & quantity from Binance.

    Useful for checking spread and immediate liquidity.

    Args:
        symbol: Trading pair. Leave empty for all.
    """
    params = {}
    weight = 4
    if symbol:
        params["symbol"] = symbol.upper()
        weight = 2
    data = await binance_spot_request("/api/v3/ticker/bookTicker", params, weight)
    label = f"Spot Best Bid/Ask — {symbol.upper()}" if symbol else "Spot Book Tickers (all)"

    hdr = _header(label)
    e = _err_check(data, hdr)
    if e:
        return e

    items = data if isinstance(data, list) else [data]

    # Single symbol — detailed view
    if len(items) == 1:
        d = items[0]
        bid_p = _f(d.get("bidPrice"))
        bid_q = _f(d.get("bidQty"))
        ask_p = _f(d.get("askPrice"))
        ask_q = _f(d.get("askQty"))
        spread = ask_p - bid_p
        spread_pct = (spread / bid_p * 100) if bid_p else 0

        return _ok(hdr + "\n".join([
            f"- **Best Bid:** {_price(bid_p)} × {bid_q:,.4f} ({_dollar(bid_p * bid_q)})",
            f"- **Best Ask:** {_price(ask_p)} × {ask_q:,.4f} ({_dollar(ask_p * ask_q)})",
            f"- **Spread:** {_price(spread)} ({spread_pct:.4f}%)",
        ]) + "\n")

    # Multiple — table
    if len(items) > 30:
        total = len(items)
        items = items[:30]
        hdr += f"*(showing first 30 of {total})*\n\n"

    table = "```\n"
    table += f" {'Symbol':<14} | {'Bid Price':>13} | {'Bid Qty':>10} | {'Ask Price':>13} | {'Ask Qty':>10} | {'Spread %':>8}\n"
    table += f" {'─' * 14} | {'─' * 13} | {'─' * 10} | {'─' * 13} | {'─' * 10} | {'─' * 8}\n"
    for d in items:
        sym = d.get("symbol", "")
        bp = _f(d.get("bidPrice"))
        bq = _f(d.get("bidQty"))
        ap = _f(d.get("askPrice"))
        aq = _f(d.get("askQty"))
        sp = ((ap - bp) / bp * 100) if bp else 0
        table += f" {sym:<14} | {_price(bp):>13} | {bq:>10,.4f} | {_price(ap):>13} | {aq:>10,.4f} | {sp:>7.4f}%\n"
    table += "```\n"
    return _ok(hdr + table)


# ═══════════════════════════════════════════════════════════════════════════════
# TOOL 8: binance_spot_avg_price
# ═══════════════════════════════════════════════════════════════════════════════

async def binance_spot_avg_price(symbol: str) -> str:
    """Get current 5-minute average spot price from Binance.

    Args:
        symbol: Trading pair (e.g. SOLUSDT)
    """
    params = {"symbol": symbol.upper()}
    data = await binance_spot_request("/api/v3/avgPrice", params, 2)
    title = f"Spot 5min Avg Price — {symbol.upper()}"

    hdr = _header(title)
    e = _err_check(data, hdr)
    if e:
        return e

    d = data if isinstance(data, dict) else {}
    mins = d.get("mins", 5)
    p = _f(d.get("price"))
    t = _dt(d.get("closeTime", 0))

    return _ok(hdr + "\n".join([
        f"- **Avg Price ({mins}min):** {_price(p)}",
        f"- **Close Time:** {t}",
    ]) + "\n")


# ═══════════════════════════════════════════════════════════════════════════════
# TOOL: binance_spot_cvd (Cumulative Volume Delta)
# ═══════════════════════════════════════════════════════════════════════════════


async def binance_spot_cvd(symbol: str, interval: str = "5m", limit: int = 100) -> str:
    """Compute Spot CVD (Cumulative Volume Delta) from Binance spot klines.

    CVD = running sum of (takerBuy − takerSell) per candle, in USD (quote vol).
    Rising CVD = buyers dominant. Falling CVD = sellers dominant.

    Primary VETO signal for Ricoz scalping framework:
      - Price ↑ + CVD ↑ → healthy uptrend (confirm long)
      - Price ↑ + CVD ↓ → bearish divergence (avoid long / short setup)
      - Price ↓ + CVD ↑ → bullish divergence (avoid short / long setup)
      - Price ↓ + CVD ↓ → healthy downtrend (confirm short)

    Args:
        symbol: Spot pair (e.g. BTCUSDT, SOLUSDT)
        interval: 1m,3m,5m,15m,30m,1h,2h,4h,6h,8h,12h,1d,3d,1w,1M
        limit: Number of candles to aggregate (default 100, max 1000)
    """
    return await _compute_cvd(
        venue="spot",
        endpoint="/api/v3/klines",
        symbol=symbol,
        interval=interval,
        limit=limit,
    )


async def _compute_cvd(
    venue: str, endpoint: str, symbol: str, interval: str, limit: int,
) -> str:
    """Shared CVD compute logic for spot + futures."""
    # For spot venue, resolve Binance spot symbol aliases (e.g. HYPE→HYPER).
    # Futures venue keeps the original symbol.
    if venue == "spot":
        sym_upper = _resolve_spot_symbol(symbol)
    else:
        sym_upper = symbol.upper()
    # weight map for klines
    if limit <= 100:
        weight = 2
    elif limit <= 500:
        weight = 5
    elif limit <= 1000:
        weight = 10
    else:
        weight = 20
        limit = min(limit, 1000)

    params = {"symbol": sym_upper, "interval": interval, "limit": limit}
    if venue == "spot":
        data = await binance_spot_request(endpoint, params, weight)
    else:
        from .binance_client import binance_futures_request
        data = await binance_futures_request(endpoint, params, weight)

    venue_label = "Spot" if venue == "spot" else "Futures"
    title = f"{venue_label} CVD — {sym_upper} {interval}"
    hdr = _header(title).replace("Binance Spot", f"Binance {venue_label}")

    e = _err_check(data, hdr)
    if e:
        return e

    items = data if isinstance(data, list) else []
    if not items:
        return make_envelope("failed", "binance", hdr + "**WARNING: Empty dataset.**")

    # Parse each candle → delta (USD) + cumulative CVD
    rows = []
    cvd = 0.0
    for c in items:
        ts = c[0]
        close = _f(c[4])
        vol_base = _f(c[5])
        quote_vol = _f(c[7])
        taker_buy_base = _f(c[9])
        taker_buy_quote = _f(c[10])
        # delta in USD = takerBuy - takerSell = 2*takerBuy - total
        delta_usd = 2 * taker_buy_quote - quote_vol
        delta_base = 2 * taker_buy_base - vol_base
        cvd += delta_usd
        rows.append({
            "ts": ts,
            "close": close,
            "vol_base": vol_base,
            "quote_vol": quote_vol,
            "delta_usd": delta_usd,
            "delta_base": delta_base,
            "cvd": cvd,
        })

    # Trim display to last 30 rows
    display_rows = rows
    if len(rows) > 30:
        display_rows = rows[-30:]
        hdr += f"*(showing last 30 of {len(rows)})*\n\n"

    table = "```\n"
    table += f" {'Time':>16} | {'Close':>12} | {'Delta':>11} | {'CVD':>11} | Side\n"
    table += f" {'─' * 16} | {'─' * 12} | {'─' * 11} | {'─' * 11} | ────\n"
    for r in display_rows:
        t = _dt(r["ts"])
        delta = r["delta_usd"]
        delta_str = f"+{_dollar(delta)}" if delta >= 0 else _dollar(delta)
        cvd_str = f"+{_dollar(r['cvd'])}" if r["cvd"] >= 0 else _dollar(r["cvd"])
        side = "BUY" if delta >= 0 else "SELL"
        table += (
            f" {t:>16} | {_price(r['close']):>12} | {delta_str:>11} | "
            f"{cvd_str:>11} | {side:>4}\n"
        )
    table += "```\n"

    # Summary metrics over the FULL series (not just displayed slice)
    first_price = rows[0]["close"]
    last_price = rows[-1]["close"]
    price_change_pct = ((last_price - first_price) / first_price * 100) if first_price else 0

    first_cvd = rows[0]["cvd"]
    last_cvd = rows[-1]["cvd"]
    cvd_change = last_cvd - first_cvd
    # Net delta = same as last_cvd - (first_cvd - first_delta) = sum of all deltas
    net_delta = sum(r["delta_usd"] for r in rows)

    # Trend: compare first third vs last third avg CVD
    third = max(1, len(rows) // 3)
    first_third_cvd = sum(r["cvd"] for r in rows[:third]) / third
    last_third_cvd = sum(r["cvd"] for r in rows[-third:]) / third
    cvd_direction = "RISING" if last_third_cvd > first_third_cvd else "FALLING"

    # Divergence flag
    price_up = price_change_pct > 0.1
    price_dn = price_change_pct < -0.1
    cvd_up = cvd_direction == "RISING"
    cvd_dn = cvd_direction == "FALLING"
    divergence = ""
    if price_up and cvd_dn:
        divergence = "⚠️ **BEARISH DIVERGENCE** — price naik tapi CVD turun (buyers menipis)"
    elif price_dn and cvd_up:
        divergence = "⚠️ **BULLISH DIVERGENCE** — price turun tapi CVD naik (buyers accumulating)"
    elif price_up and cvd_up:
        divergence = "✅ Confirmed uptrend — price + CVD both rising"
    elif price_dn and cvd_dn:
        divergence = "✅ Confirmed downtrend — price + CVD both falling"
    else:
        divergence = "➖ Neutral — price mostly flat"

    # Buy-dominant candle ratio
    buy_count = sum(1 for r in rows if r["delta_usd"] > 0)

    summary = "\n".join([
        f"**Summary over {len(rows)} candles:**",
        f"- Price: {_price(first_price)} → {_price(last_price)} ({price_change_pct:+.2f}%)",
        f"- CVD: {_dollar(first_cvd)} → {_dollar(last_cvd)} ({_dollar(cvd_change)})",
        f"- Net delta: {_dollar(net_delta)} | CVD trend: **{cvd_direction}**",
        f"- Buy-dominant candles: {buy_count}/{len(rows)} ({buy_count*100//len(rows)}%)",
        f"- Signal: {divergence}",
    ])

    return _ok(hdr + table + "\n" + summary)
