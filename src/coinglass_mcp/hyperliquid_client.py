"""Hyperliquid Public API Client for McPCG MCP Server.

Hyperliquid is a perpetual DEX — on-chain orderbook + margin engine.
Signal differs from CEX: whale/pro positions visible, retail thin.

Base URL: https://api.hyperliquid.xyz
Protocol: POST /info with JSON body {"type": ..., ...}
Auth: None for public info queries.

Key endpoint body types:
  metaAndAssetCtxs  → one shot: returns [universe, assetCtxs] where assetCtxs[i]
                      = {markPx, openInterest, funding (1h!), dayNtlVlm, dayBaseVlm,
                         premium, oraclePx, midPx, impactPxs, prevDayPx}
  fundingHistory    → {coin, fundingRate, premium, time} array (1h cadence)
  l2Book            → orderbook
  allMids           → all mark prices snapshot

Symbol mapping (Binance → Hyperliquid):
  BTCUSDT → BTC, ETHUSDT → ETH, SOLUSDT → SOL
  PEPEUSDT → kPEPE (k = 1000x notation on HL — different from Binance 1000PEPEUSDT)
  SHIBUSDT → kSHIB (same convention)

Notes:
  - OI is in BASE units (BTC, ETH) — multiply by markPx for USD
  - Funding rate cadence is 1 HOUR on HL (vs 8h on Binance/OKX/Bybit).
    For cross-exchange weighted avg, raw FR value is still usable as
    directional/crowding signal; magnitude is not directly comparable.
  - Some coins delisted — check ctx.get("isDelisted") in universe.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import httpx

logger = logging.getLogger("hyperliquid-mcp")

HL_BASE = "https://api.hyperliquid.xyz"
DEFAULT_TIMEOUT = httpx.Timeout(8.0, connect=5.0)

# ─── Rate Limiter ─────────────────────────────────────────────────────────────
# HL rate limit is generous (~1200/min per IP). Keep a light cap.
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
            limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
        )
    return _client


async def close_client() -> None:
    global _client
    if _client and not _client.is_closed:
        await _client.aclose()
        _client = None


# ─── Request Helper ───────────────────────────────────────────────────────────


async def hl_request(body: dict[str, Any]) -> Any:
    """POST /info with JSON body. Returns parsed response or {"error": ...}."""
    await _check_rate_limit()
    client = _get_client()
    url = f"{HL_BASE}/info"

    last_error = None
    for attempt in range(2):
        try:
            resp = await client.post(url, json=body)
            if resp.status_code == 200:
                return resp.json()
            if resp.status_code == 429:
                return {"error": "HL rate limited", "code": 429}
            last_error = f"HTTP {resp.status_code}"
            if resp.status_code >= 500:
                continue
            return {"error": last_error, "code": resp.status_code}
        except (httpx.ConnectTimeout, httpx.ReadTimeout, httpx.ConnectError) as e:
            last_error = f"{type(e).__name__}: {e}"
            logger.debug("HL attempt %d failed: %s", attempt + 1, last_error)
            continue
        except Exception as e:
            return {"error": str(e), "code": -1}

    return {"error": f"HL request failed. {last_error}", "code": -1}


# ─── Symbol Mapping ──────────────────────────────────────────────────────────

# Coins that use "1000x" notation on Binance but "k" prefix on HL
_K_COINS = {"PEPE", "SHIB", "BONK", "FLOKI", "DOGS"}


def to_hl_coin(binance_symbol: str) -> str:
    """BTCUSDT → BTC. Handles k-prefix coins (PEPE → kPEPE, etc).

    Accepts either Binance-style (BTCUSDT, 1000PEPEUSDT) or raw base (BTC).
    """
    s = binance_symbol.upper().strip()
    # Strip quote suffix
    for quote in ("USDT", "USDC", "USD"):
        if s.endswith(quote):
            s = s[: -len(quote)]
            break
    # Strip Binance 1000-prefix
    if s.startswith("1000") and s[4:] in _K_COINS:
        return f"k{s[4:]}"
    # Raw k-coin on HL
    if s in _K_COINS:
        return f"k{s}"
    return s
