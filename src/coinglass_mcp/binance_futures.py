"""Binance USDT-M Futures Market Data Tools for McPCG.

These are the MOST RELEVANT for trading framework —
OI, funding rate, taker ratio, long/short ratio.
All PUBLIC endpoints — no API key required.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta
from typing import Any

from .binance_client import binance_futures_request

WIB = timezone(timedelta(hours=7))


def _ts() -> str:
    return datetime.now(WIB).strftime("%H:%M:%S WIB")


def _fmt(data: Any, title: str) -> str:
    """Format Binance response with title and timestamp."""
    header = f"## {title}\n\nData: LIVE | Source: Binance Futures | {_ts()}\n\n"
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
    return _fmt(data, label)


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
    return _fmt(data, f"Funding Rate History — {symbol.upper()}")


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
    return _fmt(data, f"Open Interest — {symbol.upper()}")


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
    return _fmt(data, f"OI History — {symbol.upper()} {period}")


# ═══════════════════════════════════════════════════════════════════════════════
# TOOL 13: binance_futures_long_short_ratio
# ═══════════════════════════════════════════════════════════════════════════════

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
    return _fmt(data, f"Global L/S Account Ratio — {symbol.upper()} {period}")


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
    return _fmt(data, f"Top Trader L/S Position Ratio — {symbol.upper()} {period}")


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
    return _fmt(data, f"Futures Taker Buy/Sell Ratio — {symbol.upper()} {period}")


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
    return _fmt(data, f"Futures Klines — {symbol.upper()} {interval}")


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
    return _fmt(data, f"Futures Order Book — {symbol.upper()} (top {limit})")


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
    return _fmt(data, label)


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
    return _fmt(data, label)
