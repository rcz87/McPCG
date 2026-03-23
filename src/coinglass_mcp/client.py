"""CoinGlass API Client with caching, retry, and rate limit handling."""

from __future__ import annotations

import hashlib
import json
import time
from typing import Any

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from .config import BASE_URL, DEFAULT_TIMEOUT, Config


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


class CoinGlassClient:
    """Async HTTP client for CoinGlass API V4."""

    def __init__(self, config: Config):
        self.config = config
        self._http: httpx.AsyncClient | None = None
        self._cache: dict[str, tuple[float, Any]] = {}

    async def start(self) -> None:
        """Initialize the HTTP client."""
        self._http = httpx.AsyncClient(
            base_url=BASE_URL,
            headers={
                "CG-API-KEY": self.config.api_key,
                "Accept": "application/json",
            },
            timeout=httpx.Timeout(DEFAULT_TIMEOUT),
        )

    async def close(self) -> None:
        """Close the HTTP client."""
        if self._http:
            await self._http.aclose()
            self._http = None

    def _cache_key(self, endpoint: str, params: dict[str, Any]) -> str:
        """Generate a cache key from endpoint + params."""
        raw = f"{endpoint}:{json.dumps(params, sort_keys=True)}"
        return hashlib.md5(raw.encode()).hexdigest()

    def _get_cached(self, key: str) -> Any | None:
        """Return cached data if still valid."""
        if key in self._cache:
            ts, data = self._cache[key]
            if time.time() - ts < self.config.cache_ttl:
                return data
            del self._cache[key]
        return None

    def _set_cache(self, key: str, data: Any) -> None:
        """Store data in cache."""
        self._cache[key] = (time.time(), data)
        # Evict old entries if cache grows too large
        if len(self._cache) > 500:
            cutoff = time.time() - self.config.cache_ttl
            self._cache = {
                k: (ts, d) for k, (ts, d) in self._cache.items() if ts > cutoff
            }

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type((httpx.TimeoutException, httpx.ConnectError)),
        reraise=True,
    )
    async def get(self, endpoint: str, params: dict[str, Any] | None = None) -> Any:
        """Make a GET request to CoinGlass API with caching and retry.

        Args:
            endpoint: API endpoint path (e.g., '/api/spot/cvd-history')
            params: Query parameters

        Returns:
            Parsed JSON response data

        Raises:
            APIError: On API errors
            RateLimitError: When rate limit is exceeded
            PlanLimitError: When endpoint requires higher plan
        """
        if not self._http:
            raise RuntimeError("Client not started. Call start() first.")

        params = params or {}
        # Remove None values
        params = {k: v for k, v in params.items() if v is not None}

        # Check cache
        cache_key = self._cache_key(endpoint, params)
        cached = self._get_cached(cache_key)
        if cached is not None:
            return cached

        # Make request
        response = await self._http.get(endpoint, params=params)

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
            raise APIError(401, "Invalid API key. Check COINGLASS_API_KEY.")
        if response.status_code >= 400:
            raise APIError(response.status_code, response.text)

        data = response.json()

        # CoinGlass wraps responses: {"code": "0", "msg": "success", "data": ...}
        if isinstance(data, dict):
            code = data.get("code", "0")
            if str(code) != "0":
                raise APIError(response.status_code, data.get("msg", "Unknown error"))
            result = data.get("data", data)
        else:
            result = data

        # Cache successful response
        self._set_cache(cache_key, result)
        return result
