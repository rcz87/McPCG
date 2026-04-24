"""Bybit Public API Client for McPCG MCP Server.

Public market data only — NO API key required.
Used as tertiary exchange source for 3-way Binance+OKX+Bybit aggregation.

Base URL:
  - Primary:  https://api.bytick.com      (HK mirror, reachable from geo-restricted regions)
  - Fallback: https://api.bybit.com       (main, may be geo-blocked in some regions e.g. ID/US/UK)

Symbol convention: Bybit uses the SAME format as Binance (BTCUSDT, ETHUSDT) —
no mapper required. Category must be specified: "linear" | "inverse" | "spot".

Response wrapper:
  {"retCode": 0, "retMsg": "OK", "result": {...}, "retExtInfo": {...}, "time": ms}
  On success, this module returns `result` directly.

Interval mapping (Binance period → Bybit):
  5m   → 5min
  15m  → 15min
  30m  → 30min
  1h   → 1h
  2h,4h→ 4h
  6h,12h→ 4h  (Bybit has no 8h/12h on OI/LSR — fall back to 4h)
  1d   → 1d
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import httpx

logger = logging.getLogger("bybit-mcp")

# Primary = mirror (reachable everywhere incl. ID).
# Fallback = main domain (fails in geo-blocked regions).
BYBIT_BASES = [
    "https://api.bytick.com",
    "https://api.bybit.com",
]
DEFAULT_TIMEOUT = httpx.Timeout(8.0, connect=5.0)

# ─── Rate Limiter (public: 600 req / 5s — very generous, keep light) ─────────
_MAX_REQUESTS_PER_WINDOW = 50
_WINDOW_SECONDS = 5.0
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


async def bybit_request(endpoint: str, params: dict[str, Any] | None = None) -> dict | list:
    """GET Bybit public endpoint with mirror fallback.

    Returns `result` dict on success, or {"error": ..., "code": ...} on failure.
    """
    await _check_rate_limit()
    client = _get_client()

    last_error = None
    for base in BYBIT_BASES:
        url = f"{base}{endpoint}"
        try:
            resp = await client.get(url, params=params)
            if resp.status_code == 200:
                body = resp.json()
                if isinstance(body, dict):
                    if body.get("retCode") == 0:
                        return body.get("result", {})
                    return {
                        "error": body.get("retMsg", "Bybit error"),
                        "code": body.get("retCode", -1),
                    }
                return body
            if resp.status_code == 429:
                return {"error": "Bybit rate limited", "code": 429}
            last_error = f"HTTP {resp.status_code} from {base}"
            continue
        except (httpx.ConnectTimeout, httpx.ReadTimeout, httpx.ConnectError) as e:
            last_error = f"{type(e).__name__} on {base}"
            logger.debug("Bybit fallback: %s", last_error)
            continue
        except Exception as e:
            return {"error": str(e), "code": -1}

    return {"error": f"All Bybit endpoints failed. Last: {last_error}", "code": -1}


# ─── Interval Mapping ────────────────────────────────────────────────────────

# Bybit OI history + L/S ratio support: 5min, 15min, 30min, 1h, 4h, 1d
_INTERVAL_MAP = {
    "5m": "5min", "15m": "15min", "30m": "30min",
    "1h": "1h", "2h": "4h", "4h": "4h",
    "6h": "4h", "8h": "4h", "12h": "4h",
    "1d": "1d", "3d": "1d", "1w": "1d",
}


def to_bybit_interval(binance_period: str) -> str | None:
    """Map Binance period to Bybit intervalTime/period value."""
    return _INTERVAL_MAP.get(binance_period.lower())
