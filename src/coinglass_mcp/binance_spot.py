"""Binance Spot Market Data Tools for McPCG.

All endpoints are PUBLIC — no API key required.
Uses httpx via binance_client for consistency.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone, timedelta
from typing import Any

from .binance_client import binance_spot_request

WIB = timezone(timedelta(hours=7))


def _ts() -> str:
    return datetime.now(WIB).strftime("%H:%M:%S WIB")


def _fmt(data: Any, title: str) -> str:
    """Format Binance response with title and timestamp."""
    header = f"## {title}\n\nData: LIVE | Source: Binance Spot | {_ts()}\n\n"
    if isinstance(data, dict) and "error" in data:
        return f"{header}**ERROR:** {data['error']}"
    if isinstance(data, list):
        if len(data) == 0:
            return f"{header}**WARNING: Empty dataset.**"
        if len(data) > 30:
            total = len(data)
            data = data[-30:]
            header += f"*(showing last 30 of {total} entries)*\n\n"
    return header + json.dumps(data, indent=2, default=str)


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
    return _fmt(data, label)


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
    return _fmt(data, f"Spot Order Book — {symbol.upper()} (top {limit})")


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
    return _fmt(data, f"Spot Klines — {symbol.upper()} {interval}")


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
    return _fmt(data, f"Spot Recent Trades — {symbol.upper()}")


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
    return _fmt(data, f"Spot Aggregate Trades — {symbol.upper()}")


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
    return _fmt(data, label)


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
    return _fmt(data, label)


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
    return _fmt(data, f"Spot 5min Avg Price — {symbol.upper()}")
