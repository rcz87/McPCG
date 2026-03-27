"""Binance API Client for McPCG MCP Server.

Market data endpoints only — NO API key required.
Uses httpx (consistent with CoinGlass client).

Base URLs:
  - Spot: api1.binance.com (with fallbacks to api2, api4)
  - USDT-M Futures: fapi.binance.com
Rate Limits: 6000 weight/minute per IP (safe margin: 5500)

NOTE: If fapi.binance.com is DNS-poisoned by ISP, add the real IP
to /etc/hosts (resolve via: dig fapi.binance.com @1.1.1.1).
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import httpx

logger = logging.getLogger("binance-mcp")

# ─── Base URLs ────────────────────────────────────────────────────────────────

SPOT_BASES = [
    "https://api1.binance.com",
    "https://api2.binance.com",
    "https://api4.binance.com",
]
FUTURES_BASE = "https://fapi.binance.com"

# ─── Rate Limit Tracking ─────────────────────────────────────────────────────

WEIGHT_LIMIT = 5500  # Safe margin from 6000

_weight_used = 0
_weight_reset_time = 0.0
_lock = asyncio.Lock()


async def _check_rate_limit(weight: int) -> None:
    """Check and track rate limit before request."""
    global _weight_used, _weight_reset_time

    async with _lock:
        now = time.time()
        if now > _weight_reset_time:
            _weight_used = 0
            _weight_reset_time = now + 60

        if _weight_used + weight > WEIGHT_LIMIT:
            wait = _weight_reset_time - now
            logger.warning("Rate limit approaching (%d/%d). Waiting %.1fs", _weight_used, WEIGHT_LIMIT, wait)
            await asyncio.sleep(wait + 0.5)
            _weight_used = 0
            _weight_reset_time = time.time() + 60

        _weight_used += weight


# ─── Shared Client ────────────────────────────────────────────────────────────

_client: httpx.AsyncClient | None = None


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            timeout=httpx.Timeout(10.0, connect=5.0),
            follow_redirects=True,
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
        )
    return _client


async def close_client() -> None:
    global _client
    if _client and not _client.is_closed:
        await _client.aclose()
        _client = None


# ─── Request Helpers ──────────────────────────────────────────────────────────


async def binance_spot_request(
    endpoint: str,
    params: dict[str, Any] | None = None,
    weight: int = 2,
) -> dict | list:
    """Make a Binance Spot API request with fallback URLs."""
    await _check_rate_limit(weight)
    client = _get_client()

    last_error = None
    for base in SPOT_BASES:
        url = f"{base}{endpoint}"
        try:
            resp = await client.get(url, params=params)
            _update_weight_from_headers(resp)
            if resp.status_code == 200:
                return resp.json()
            if resp.status_code == 429:
                retry_after = resp.headers.get("Retry-After", "60")
                return {"error": f"Rate limited. Retry after {retry_after}s", "code": 429}
            if resp.status_code == 418:
                return {"error": "IP banned by Binance. Wait before retrying.", "code": 418}
            if resp.status_code >= 500:
                last_error = f"HTTP {resp.status_code} from {base}"
                continue
            data = resp.json()
            return {"error": data.get("msg", f"HTTP {resp.status_code}"), "code": resp.status_code}
        except (httpx.ConnectTimeout, httpx.ReadTimeout, httpx.ConnectError) as e:
            last_error = f"{type(e).__name__} on {base}"
            logger.debug("Spot fallback: %s", last_error)
            continue
        except Exception as e:
            return {"error": str(e), "code": -1}

    return {"error": f"All spot endpoints failed. Last: {last_error}", "code": -1}


async def binance_futures_request(
    endpoint: str,
    params: dict[str, Any] | None = None,
    weight: int = 1,
) -> dict | list:
    """Make a Binance Futures API request with retry on timeout."""
    await _check_rate_limit(weight)
    client = _get_client()

    url = f"{FUTURES_BASE}{endpoint}"
    last_error = None
    for attempt in range(2):  # 1 retry on timeout
        try:
            resp = await client.get(url, params=params)
            _update_weight_from_headers(resp)
            if resp.status_code == 200:
                return resp.json()
            if resp.status_code == 429:
                retry_after = resp.headers.get("Retry-After", "60")
                return {"error": f"Rate limited. Retry after {retry_after}s", "code": 429}
            if resp.status_code == 418:
                return {"error": "IP banned by Binance. Wait before retrying.", "code": 418}
            data = resp.json()
            return {"error": data.get("msg", f"HTTP {resp.status_code}"), "code": resp.status_code}
        except (httpx.ConnectTimeout, httpx.ReadTimeout, httpx.ConnectError) as e:
            last_error = f"{type(e).__name__}: {e}"
            logger.debug("Futures attempt %d failed: %s", attempt + 1, last_error)
            continue
        except Exception as e:
            return {"error": str(e), "code": -1}
    return {"error": f"Futures request failed after retries. {last_error}", "code": -1}


def _update_weight_from_headers(resp: httpx.Response) -> None:
    """Update weight counter from Binance response headers."""
    global _weight_used
    for header in ("X-MBX-USED-WEIGHT-1M", "X-MBX-USED-WEIGHT-1m"):
        val = resp.headers.get(header)
        if val:
            try:
                _weight_used = int(val)
            except ValueError:
                pass
            break
