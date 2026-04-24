"""OKX Public API Client for McPCG MCP Server.

Public market data only — NO API key required.
Used as secondary exchange source for aggregated Binance+OKX tools.

Base URL: https://www.okx.com
Rate Limits: Vary per endpoint (typ. 20 req/2s per IP).

Symbol convention:
  - Binance perp: BTCUSDT
  - OKX perp:     BTC-USDT-SWAP
  - OKX ccy:      BTC  (for rubik stats endpoints — aggregated across contracts)

Period mapping (Binance → OKX rubik):
  5m,15m,30m → 5m   (OKX rubik only supports 5m for intraday)
  1h,2h,4h   → 1H
  6h,12h     → 8H
  1d,3d,1w   → 1D
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import httpx

logger = logging.getLogger("okx-mcp")

OKX_BASE = "https://www.okx.com"
DEFAULT_TIMEOUT = httpx.Timeout(8.0, connect=5.0)

# ─── Rate Limiter (simple per-second cap) ─────────────────────────────────────
# OKX public endpoints typ. 20/2s. Keep it conservative at 15/2s.
_MAX_REQUESTS_PER_WINDOW = 15
_WINDOW_SECONDS = 2.0
_request_times: list[float] = []
_lock = asyncio.Lock()


async def _check_rate_limit() -> None:
    async with _lock:
        now = time.time()
        global _request_times
        _request_times = [t for t in _request_times if now - t < _WINDOW_SECONDS]
        if len(_request_times) >= _MAX_REQUESTS_PER_WINDOW:
            wait = _WINDOW_SECONDS - (now - _request_times[0]) + 0.05
            if wait > 0:
                await asyncio.sleep(wait)
        _request_times.append(time.time())


# ─── Shared Client ────────────────────────────────────────────────────────────

_client: httpx.AsyncClient | None = None


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            timeout=DEFAULT_TIMEOUT,
            follow_redirects=True,
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
        )
    return _client


async def close_client() -> None:
    global _client
    if _client and not _client.is_closed:
        await _client.aclose()
        _client = None


# ─── Request Helper ───────────────────────────────────────────────────────────


async def okx_request(endpoint: str, params: dict[str, Any] | None = None) -> dict | list:
    """GET OKX public endpoint. Returns normalized dict/list.

    OKX wraps responses as {"code":"0", "msg":"", "data":[...]}.
    On success returns `data` directly; on error returns {"error": ..., "code": ...}.
    """
    await _check_rate_limit()
    client = _get_client()
    url = f"{OKX_BASE}{endpoint}"

    last_error = None
    for attempt in range(2):
        try:
            resp = await client.get(url, params=params)
            if resp.status_code == 200:
                body = resp.json()
                if isinstance(body, dict):
                    if body.get("code") == "0":
                        return body.get("data", [])
                    return {"error": body.get("msg", "OKX error"), "code": body.get("code", "-1")}
                return body
            if resp.status_code == 429:
                return {"error": "OKX rate limited", "code": 429}
            return {"error": f"HTTP {resp.status_code}", "code": resp.status_code}
        except (httpx.ConnectTimeout, httpx.ReadTimeout, httpx.ConnectError) as e:
            last_error = f"{type(e).__name__}: {e}"
            logger.debug("OKX attempt %d failed: %s", attempt + 1, last_error)
            continue
        except Exception as e:
            return {"error": str(e), "code": -1}

    return {"error": f"OKX request failed. {last_error}", "code": -1}


# ─── Symbol & Period Mapping ──────────────────────────────────────────────────


def to_okx_swap(binance_symbol: str) -> str:
    """BTCUSDT → BTC-USDT-SWAP. Handles 1000PEPEUSDT → 1000PEPE-USDT-SWAP too."""
    s = binance_symbol.upper().strip()
    # Common quote currencies
    for quote in ("USDT", "USDC", "USD"):
        if s.endswith(quote):
            base = s[: -len(quote)]
            return f"{base}-{quote}-SWAP"
    # Fallback: assume USDT
    return f"{s}-USDT-SWAP"


def to_okx_ccy(binance_symbol: str) -> str:
    """BTCUSDT → BTC. Strips quote suffix. Used by rubik ccy-level endpoints."""
    s = binance_symbol.upper().strip()
    for quote in ("USDT", "USDC", "USD"):
        if s.endswith(quote):
            return s[: -len(quote)]
    return s


# OKX rubik period values: 5m, 1H, 8H, 1D
_RUBIK_PERIOD_MAP = {
    "5m": "5m", "15m": "5m", "30m": "5m",  # OKX rubik only has 5m intraday
    "1h": "1H", "2h": "1H", "4h": "1H",
    "6h": "8H", "8h": "8H", "12h": "8H",
    "1d": "1D", "3d": "1D", "1w": "1D",
}


def to_okx_rubik_period(binance_period: str) -> str | None:
    """Map Binance period to nearest OKX rubik period. Returns None if unsupported."""
    return _RUBIK_PERIOD_MAP.get(binance_period.lower())


# OKX market candle bar values (for /market/candles)
_CANDLE_BAR_MAP = {
    "1m": "1m", "3m": "3m", "5m": "5m", "15m": "15m", "30m": "30m",
    "1h": "1H", "2h": "2H", "4h": "4H", "6h": "6H", "12h": "12H",
    "1d": "1D", "3d": "3D", "1w": "1W",
}


def to_okx_bar(binance_interval: str) -> str | None:
    return _CANDLE_BAR_MAP.get(binance_interval.lower())
