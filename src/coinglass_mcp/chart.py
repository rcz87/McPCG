"""chart-img.com API integration for TradingView chart screenshots.

Base URL: https://api.chart-img.com/v2/tradingview/advanced-chart/storage
Auth: x-api-key header

Auto-adapt: tries paid-tier settings first (1200x600, 4 studies),
falls back to free-tier limits (800x600, 3 studies) on 403.
"""

from __future__ import annotations

import os

import httpx

CHART_IMG_BASE_URL = "https://api.chart-img.com/v2/tradingview/advanced-chart/storage"

# Exchange prefix mapping for TradingView symbols
EXCHANGE_PREFIX = {
    "SOL": "BINANCE:SOLUSDT",
    "BTC": "BINANCE:BTCUSDT",
    "ETH": "BINANCE:ETHUSDT",
    "BNB": "BINANCE:BNBUSDT",
    "AVAX": "BINANCE:AVAXUSDT",
    "SUI": "BINANCE:SUIUSDT",
    "XRP": "BINANCE:XRPUSDT",
    "HYPE": "BYBIT:HYPEUSDT",
    "DOGE": "BINANCE:DOGEUSDT",
    "ADA": "BINANCE:ADAUSDT",
    "PEPE": "BINANCE:PEPEUSDT",
    "WLD": "BINANCE:WLDUSDT",
}

# Supported intervals (chart-img.com accepts these directly)
VALID_INTERVALS = {"1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "1d", "1w"}

# Ricoz chart — full (paid): EMA21 + EMA50 + VWAP + Volume (4 studies)
RICOZ_STUDIES_FULL = [
    {
        "name": "Moving Average Exponential",
        "input": {"length": 21},
        "override": {
            "Plot.color": "rgb(0,255,255)",
            "Plot.linewidth": 2,
        },
    },
    {
        "name": "Moving Average Exponential",
        "input": {"length": 50},
        "override": {
            "Plot.color": "rgb(255,255,255)",
            "Plot.linewidth": 2,
        },
    },
    {
        "name": "VWAP",
        "override": {
            "Plot.color": "rgb(255,215,0)",
            "Plot.linewidth": 2,
        },
    },
    {
        "name": "Volume",
        "forceOverlay": True,
        "override": {
            "Volume.color.0": "rgba(247,82,95,0.4)",
            "Volume.color.1": "rgba(34,171,148,0.4)",
        },
    },
]

# Ricoz chart — free tier fallback: EMA21 + EMA50 + VWAP (3 studies)
RICOZ_STUDIES_FREE = RICOZ_STUDIES_FULL[:3]

# Paid-tier defaults
_PAID_WIDTH = 1200
_PAID_HEIGHT = 600
# Free-tier fallback
_FREE_WIDTH = 800
_FREE_HEIGHT = 600


async def _post_chart(client: httpx.AsyncClient, api_key: str, payload: dict) -> httpx.Response:
    return await client.post(
        CHART_IMG_BASE_URL,
        json=payload,
        headers={"x-api-key": api_key, "content-type": "application/json"},
    )


async def get_chart(
    symbol: str,
    interval: str = "5m",
    width: int = _PAID_WIDTH,
    height: int = _PAID_HEIGHT,
    theme: str = "dark",
    studies: list | None = None,
) -> dict:
    """Generate TradingView chart screenshot via chart-img.com API.

    Auto-adapt: tries full spec first, on 403 retries with free-tier limits.
    Returns dict with url, expire, symbol_tv, interval_tv — or error key on failure.
    """
    api_key = os.getenv("CHART_IMG_API_KEY", "")
    if not api_key:
        return {"error": "CHART_IMG_API_KEY not set in .env"}

    symbol_tv = EXCHANGE_PREFIX.get(symbol.upper(), f"BINANCE:{symbol.upper()}USDT")
    interval_tv = interval if interval in VALID_INTERVALS else "5m"
    chart_studies = studies if studies is not None else RICOZ_STUDIES_FULL

    payload = {
        "symbol": symbol_tv,
        "interval": interval_tv,
        "width": width,
        "height": height,
        "theme": theme,
        "timezone": "Asia/Jakarta",
        "studies": chart_studies,
        "override": {"showSymbolWatermark": True},
    }

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await _post_chart(client, api_key, payload)

            # Auto-fallback: if 403 and we used paid-tier settings, retry with free-tier
            if resp.status_code == 403 and studies is None:
                payload["studies"] = RICOZ_STUDIES_FREE
                payload["width"] = min(width, _FREE_WIDTH)
                payload["height"] = min(height, _FREE_HEIGHT)
                resp = await _post_chart(client, api_key, payload)

            resp.raise_for_status()
            data = resp.json()
            return {
                "url": data.get("url", ""),
                "expire": data.get("expire", ""),
                "symbol_tv": symbol_tv,
                "interval_tv": interval_tv,
                "size": data.get("size", 0),
            }
    except httpx.HTTPStatusError as e:
        return {"error": f"HTTP {e.response.status_code}: {e.response.text[:200]}"}
    except Exception as e:
        return {"error": str(e)}
