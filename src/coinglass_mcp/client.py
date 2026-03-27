"""CoinGlass API Client — hardened for trading-critical data integrity.

Safety guarantees:
- FetchResult wraps every response with age/staleness metadata
- RateLimiter prevents 429 errors (200ms spacing + per-minute counter)
- Symbol normalization prevents silent empty results
- API key never appears in logs or responses
- 10s timeout prevents infinite hangs
- Async storage calls never block event loop
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from dataclasses import dataclass
from typing import Any

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    stop_after_delay,
    wait_exponential,
)

from .config import (
    BASE_URL,
    DEFAULT_TIMEOUT,
    MIN_REQUEST_SPACING,
    STALE_EXPIRED_THRESHOLD,
    STALE_WARNING_THRESHOLD,
    Config,
    normalize_symbol,
)
from .storage import Storage

logger = logging.getLogger("coinglass-mcp")


# ─── FetchResult ─────────────────────────────────────────────────────────────


@dataclass
class FetchResult:
    """Wraps every API response with age/staleness metadata.

    TRADING-CRITICAL: Every piece of data served to Claude must carry
    its age so stale data is never mistaken for live data.
    """

    data: Any
    age_seconds: float  # How old is this data (0 = just fetched)
    is_cached: bool  # True = from SQLite cache, False = fresh API call
    fetched_at: float  # Unix timestamp when data was originally fetched

    @property
    def is_stale(self) -> bool:
        """Data older than 2 minutes is stale."""
        return self.age_seconds > STALE_WARNING_THRESHOLD

    @property
    def is_expired(self) -> bool:
        """Data older than 5 minutes is expired — DO NOT USE FOR ENTRY."""
        return self.age_seconds > STALE_EXPIRED_THRESHOLD

    @property
    def age_label(self) -> str:
        """Human-readable age label with warning level."""
        age = self.age_seconds
        if age < 5:
            return "LIVE"
        elif age < STALE_WARNING_THRESHOLD:
            return f"{age:.0f}s ago"
        elif age < STALE_EXPIRED_THRESHOLD:
            return f"STALE {age:.0f}s ago"
        else:
            return f"EXPIRED {age:.0f}s ago"


# ─── Rate Limiter ────────────────────────────────────────────────────────────


class RateLimiter:
    """Async rate limiter — prevents 429 errors from CoinGlass API.

    - Enforces 200ms minimum spacing between requests
    - Tracks requests per minute against plan limit
    - Warns at 80% capacity
    - Serializes concurrent requests (safe for asyncio.gather)
    """

    def __init__(self, max_per_minute: int, min_spacing: float = MIN_REQUEST_SPACING):
        self._gate = asyncio.Lock()
        self._min_spacing = min_spacing
        self._max_per_minute = max_per_minute
        self._request_times: list[float] = []

    async def acquire(self) -> dict[str, Any]:
        """Acquire permission to make a request. Blocks if rate limited."""
        async with self._gate:
            now = time.time()
            # Clean timestamps older than 60s
            self._request_times = [t for t in self._request_times if now - t < 60]

            # If at rate limit, wait for oldest request to age out
            if len(self._request_times) >= self._max_per_minute:
                oldest = self._request_times[0]
                wait = 60 - (now - oldest) + 0.1
                if wait > 0:
                    logger.warning(
                        "Rate limit reached (%d/%d req/min), waiting %.1fs",
                        len(self._request_times),
                        self._max_per_minute,
                        wait,
                    )
                    await asyncio.sleep(wait)
                    now = time.time()
                    self._request_times = [
                        t for t in self._request_times if now - t < 60
                    ]

            # Enforce minimum spacing between requests
            if self._request_times:
                elapsed = now - self._request_times[-1]
                if elapsed < self._min_spacing:
                    await asyncio.sleep(self._min_spacing - elapsed)

            self._request_times.append(time.time())

            used = len(self._request_times)
            pct = round(used / self._max_per_minute * 100, 1) if self._max_per_minute else 0
            if pct >= 80:
                logger.warning(
                    "Rate limit at %s%% (%d/%d)", pct, used, self._max_per_minute
                )

            return {
                "used": used,
                "limit": self._max_per_minute,
                "remaining": self._max_per_minute - used,
                "pct": pct,
            }

    @property
    def usage(self) -> dict[str, Any]:
        """Current rate limit usage (non-blocking check)."""
        now = time.time()
        used = sum(1 for t in self._request_times if now - t < 60)
        return {
            "used": used,
            "limit": self._max_per_minute,
            "remaining": self._max_per_minute - used,
        }


# ─── Errors ──────────────────────────────────────────────────────────────────


class APIError(Exception):
    """General CoinGlass API error."""

    def __init__(self, status_code: int, message: str):
        self.status_code = status_code
        super().__init__(f"CoinGlass API error ({status_code}): {message}")


class RateLimitError(APIError):
    """Rate limit exceeded."""

    def __init__(self, message: str = "Rate limit exceeded"):
        super().__init__(429, message)


class PlanLimitError(APIError):
    """Endpoint not available on current plan."""

    def __init__(self, message: str = "Endpoint not available on your plan"):
        super().__init__(403, message)


# ─── Client ──────────────────────────────────────────────────────────────────


class CoinGlassClient:
    """Async HTTP client for CoinGlass API V4 — hardened for trading.

    Thread-safe, rate-limited, with data integrity guarantees.
    """

    def __init__(self, config: Config, storage: Storage | None = None):
        self.config = config
        self._http: httpx.AsyncClient | None = None
        self.storage = storage or Storage()
        self._rate_limiter: RateLimiter | None = None

    async def start(self) -> None:
        """Initialize the HTTP client, storage, and rate limiter."""
        self._http = httpx.AsyncClient(
            base_url=BASE_URL,
            headers={
                "CG-API-KEY": self.config.api_key,
                "Accept": "application/json",
            },
            timeout=httpx.Timeout(DEFAULT_TIMEOUT),
        )
        self.storage.start()
        self._rate_limiter = RateLimiter(
            max_per_minute=self.config.rate_limit,
            min_spacing=MIN_REQUEST_SPACING,
        )

    async def close(self) -> None:
        """Close the HTTP client and storage."""
        if self._http:
            await self._http.aclose()
            self._http = None
        self.storage.close()

    def _mask_key(self, text: str) -> str:
        """Mask API key in any text — prevents key leakage in logs/responses."""
        if self.config.api_key and self.config.api_key in text:
            return text.replace(self.config.api_key, "CG-***MASKED***")
        return text

    @staticmethod
    def _params_hash(endpoint: str, params: dict[str, Any]) -> str:
        """Generate SHA256 hash from endpoint + params."""
        raw = f"{endpoint}:{json.dumps(params, sort_keys=True)}"
        return hashlib.sha256(raw.encode()).hexdigest()

    @retry(
        stop=stop_after_attempt(2) | stop_after_delay(20),
        wait=wait_exponential(multiplier=1, min=1, max=5),
        retry=retry_if_exception_type(
            (httpx.TimeoutException, httpx.ConnectError, RateLimitError)
        ),
        reraise=True,
    )
    async def get(
        self, endpoint: str, params: dict[str, Any] | None = None
    ) -> FetchResult:
        """Make a GET request with rate limiting, caching, and age tracking.

        Returns FetchResult with data + age metadata.
        Every response carries its age so stale data is never invisible.
        """
        if not self._http:
            raise RuntimeError("Client not started. Call start() first.")

        params = params or {}
        params = {k: v for k, v in params.items() if v is not None}

        # Check persistent cache (TTL-based) — single async read, never blocks
        phash = self._params_hash(endpoint, params)
        cache_row = await self.storage.aget_cached_with_time(
            phash, ttl=self.config.cache_ttl
        )
        if cache_row is not None:
            age = time.time() - cache_row["fetched_at"]
            logger.debug(
                "[CACHED %.0fs] %s %s",
                age,
                endpoint,
                params.get("symbol", ""),
            )
            return FetchResult(
                data=cache_row["data"],
                age_seconds=round(age, 1),
                is_cached=True,
                fetched_at=cache_row["fetched_at"],
            )

        # Rate limit — wait if needed
        if self._rate_limiter:
            await self._rate_limiter.acquire()

        # Make request
        response = await self._http.get(endpoint, params=params)
        fetch_time = time.time()

        if response.status_code == 429:
            raise RateLimitError(
                f"Rate limit exceeded. Your {self.config.plan} plan allows "
                f"{self.config.rate_limit} req/min. Wait and retry."
            )
        if response.status_code == 403:
            raise PlanLimitError(
                f"This endpoint requires a higher plan than '{self.config.plan}'. "
                f"Upgrade at https://www.coinglass.com/pricing"
            )
        if response.status_code == 401:
            raise APIError(
                401,
                "Invalid or expired API key. Check COINGLASS_API_KEY in .env file.",
            )
        if response.status_code >= 400:
            raise APIError(
                response.status_code, self._mask_key(response.text[:500])
            )

        # Validate response is valid JSON
        try:
            data = response.json()
        except (json.JSONDecodeError, ValueError) as e:
            raise APIError(
                response.status_code,
                f"Invalid JSON response from CoinGlass: {e}. "
                f"Raw: {self._mask_key(response.text[:200])}",
            )

        # Strict response validation
        if isinstance(data, dict):
            code = data.get("code", "0")
            if str(code) != "0":
                raise APIError(
                    response.status_code,
                    f"CoinGlass error code {code}: "
                    f"{self._mask_key(data.get('msg', 'Unknown error'))}",
                )
            result = data.get("data")
            if result is None:
                raise APIError(
                    response.status_code,
                    f"CoinGlass response missing 'data' field. "
                    f"Response: {self._mask_key(json.dumps(data)[:300])}",
                )
        else:
            result = data

        if result is None:
            raise APIError(
                response.status_code,
                "CoinGlass returned null data. The symbol may not exist or "
                "the endpoint may not support this parameter combination.",
            )

        # Store in persistent cache — async, never blocks
        symbol = params.get("symbol", "")
        interval = params.get("interval", "")
        await self.storage.astore(
            endpoint, phash, result, symbol=symbol, interval=interval
        )
        logger.debug("[FRESH] %s %s", endpoint, params.get("symbol", ""))

        return FetchResult(
            data=result,
            age_seconds=0.0,
            is_cached=False,
            fetched_at=fetch_time,
        )
