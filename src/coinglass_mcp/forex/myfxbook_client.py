"""MyFXBook Community Outlook API client.

Public API: https://www.myfxbook.com/api
Docs: https://www.myfxbook.com/community/api
Rate limit: 100 req/day for community-outlook endpoint.

Features:
- Session caching in SQLite (29d TTL, survives PM2 restart)
- Auto re-login on "Invalid session"
- Daily rate limiter with warn/block thresholds
- Email masked in all log output
"""

from __future__ import annotations

import asyncio
import logging
import os
import urllib.parse
from typing import Any

import httpx

from .rate_limiter import DailyRateLimiter, RateLimitExceeded
from .storage import ForexStorage

logger = logging.getLogger("coinglass-mcp.forex")

BASE_URL = "https://www.myfxbook.com/api"
TIMEOUT = 10.0
MAX_RETRIES = 3


class MyFXBookError(Exception):
    """Generic MyFXBook API error."""


class MyFXBookAuthError(MyFXBookError):
    """Session invalid or credentials wrong."""


def _mask_email(email: str) -> str:
    if not email or "@" not in email:
        return "***"
    local, _, domain = email.partition("@")
    return f"{local[:1]}***@{domain}" if local else f"***@{domain}"


class MyFXBookClient:
    """MyFXBook API client with persistent session + rate limiting."""

    def __init__(
        self,
        storage: ForexStorage | None = None,
        rate_limiter: DailyRateLimiter | None = None,
    ):
        self.storage = storage or ForexStorage()
        self.rate_limiter = rate_limiter or DailyRateLimiter(self.storage.db_path)
        self._session_lock = asyncio.Lock()

    def _creds(self) -> tuple[str, str]:
        email = os.getenv("MYFXBOOK_EMAIL", "")
        password = os.getenv("MYFXBOOK_PASSWORD", "")
        if not email or not password:
            raise MyFXBookAuthError(
                "MYFXBOOK_EMAIL / MYFXBOOK_PASSWORD not set in .env"
            )
        return email, password

    async def _login(self) -> str:
        email, password = self._creds()
        url = f"{BASE_URL}/login.json"
        logger.info("MyFXBook login for %s", _mask_email(email))
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            resp = await client.get(url, params={"email": email, "password": password})
        if resp.status_code != 200:
            raise MyFXBookAuthError(f"Login HTTP {resp.status_code}")
        try:
            data = resp.json()
        except Exception as e:
            raise MyFXBookAuthError(f"Login returned non-JSON: {e}")
        if data.get("error"):
            raise MyFXBookAuthError(f"Login failed: {data.get('message', 'unknown')}")
        token = data.get("session", "")
        if not token:
            raise MyFXBookAuthError("Login succeeded but no session token returned")
        # MyFXBook returns the token URL-encoded (e.g. '%2F' for '/'). httpx will
        # re-encode on subsequent requests, producing '%252F' which the server
        # rejects as 'Invalid session'. Decode once here so params={"session": tok}
        # serializes cleanly.
        token = urllib.parse.unquote(token)
        self.storage.save_session(token)
        return token

    async def _get_session(self, force_refresh: bool = False) -> str:
        async with self._session_lock:
            if not force_refresh:
                cached = self.storage.get_session()
                if cached:
                    return cached
            return await self._login()

    async def _call(self, endpoint: str, params: dict[str, Any] | None = None) -> dict:
        """GET endpoint with session, auto-relogin on invalid session."""
        self.rate_limiter.check_and_increment()

        params = dict(params or {})
        session = await self._get_session()
        params["session"] = session

        url = f"{BASE_URL}/{endpoint}"
        last_err: Exception | None = None
        session_retries = 0
        for attempt in range(MAX_RETRIES):
            try:
                async with httpx.AsyncClient(timeout=TIMEOUT) as client:
                    resp = await client.get(url, params=params)
                if resp.status_code != 200:
                    last_err = MyFXBookError(f"HTTP {resp.status_code}")
                    await asyncio.sleep(0.5 * (2 ** attempt))
                    continue
                data = resp.json()
                if data.get("error"):
                    msg = str(data.get("message", "")).lower()
                    if ("session" in msg or "invalid" in msg) and session_retries == 0:
                        session_retries += 1
                        self.storage.clear_session()
                        session = await self._get_session(force_refresh=True)
                        params["session"] = session
                        continue
                    raise MyFXBookError(data.get("message") or "Unknown API error")
                return data
            except httpx.TimeoutException as e:
                last_err = e
                await asyncio.sleep(0.5 * (2 ** attempt))
            except httpx.HTTPError as e:
                last_err = e
                await asyncio.sleep(0.5 * (2 ** attempt))
        raise MyFXBookError(f"MyFXBook {endpoint} failed after retries: {last_err}")

    async def get_community_outlook(self) -> list[dict]:
        """Fetch sentiment for ALL tracked symbols (1 API call covers everything).

        Returns list of dicts with normalized keys.
        """
        data = await self._call("get-community-outlook.json")
        symbols_raw = data.get("symbols", []) or []
        out: list[dict] = []
        for s in symbols_raw:
            try:
                out.append({
                    "symbol": s.get("name", "").upper(),
                    "long_pct": float(s.get("longPercentage", 0) or 0),
                    "short_pct": float(s.get("shortPercentage", 0) or 0),
                    "long_positions": int(s.get("longPositions", 0) or 0),
                    "short_positions": int(s.get("shortPositions", 0) or 0),
                    "long_volume_usd": float(s.get("longVolume", 0) or 0),
                    "short_volume_usd": float(s.get("shortVolume", 0) or 0),
                    "avg_long_entry": float(s.get("avgLongPrice", 0) or 0),
                    "avg_short_entry": float(s.get("avgShortPrice", 0) or 0),
                    "total_positions": int(s.get("totalPositions", 0) or 0),
                })
            except (ValueError, TypeError) as e:
                logger.warning("skipping malformed symbol row %s: %s", s.get("name"), e)
        return out

    async def get_sentiment(self, symbol: str) -> dict | None:
        all_syms = await self.get_community_outlook()
        target = symbol.upper().replace("/", "")
        for s in all_syms:
            if s["symbol"] == target:
                return s
        return None
