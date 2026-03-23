"""CoinGlass API Client with persistent caching, retry, and rate limit handling."""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from .config import BASE_URL, DEFAULT_TIMEOUT, Config
from .storage import Storage

logger = logging.getLogger("coinglass-mcp")


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
    """Async HTTP client for CoinGlass API V4 with persistent storage."""

    def __init__(self, config: Config, storage: Storage | None = None):
        self.config = config
        self._http: httpx.AsyncClient | None = None
        self.storage = storage or Storage()

    async def start(self) -> None:
        """Initialize the HTTP client and storage."""
        self._http = httpx.AsyncClient(
            base_url=BASE_URL,
            headers={
                "CG-API-KEY": self.config.api_key,
                "Accept": "application/json",
            },
            timeout=httpx.Timeout(DEFAULT_TIMEOUT),
        )
        self.storage.start()

    async def close(self) -> None:
        """Close the HTTP client and storage."""
        if self._http:
            await self._http.aclose()
            self._http = None
        self.storage.close()

    @staticmethod
    def _params_hash(endpoint: str, params: dict[str, Any]) -> str:
        """Generate a hash from endpoint + params.

        Uses SHA256 instead of MD5 to eliminate collision risk.
        """
        raw = f"{endpoint}:{json.dumps(params, sort_keys=True)}"
        return hashlib.sha256(raw.encode()).hexdigest()

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type((httpx.TimeoutException, httpx.ConnectError)),
        reraise=True,
    )
    async def get(self, endpoint: str, params: dict[str, Any] | None = None) -> Any:
        """Make a GET request with persistent caching and retry.

        Every successful response is stored in SQLite for historical queries.
        Cache is NEVER returned without explicit [CACHED] label in logs.
        """
        if not self._http:
            raise RuntimeError("Client not started. Call start() first.")

        params = params or {}
        params = {k: v for k, v in params.items() if v is not None}

        # Check persistent cache (60s TTL)
        phash = self._params_hash(endpoint, params)
        cached = self.storage.get_cached(phash, ttl=self.config.cache_ttl)
        if cached is not None:
            logger.debug("[CACHED] %s %s", endpoint, params.get("symbol", ""))
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

        # FIX #1: Validate response is valid JSON
        try:
            data = response.json()
        except (json.JSONDecodeError, ValueError) as e:
            raise APIError(
                response.status_code,
                f"Invalid JSON response from CoinGlass: {e}. "
                f"Raw response: {response.text[:200]}"
            )

        # FIX #2: Strict response validation
        if isinstance(data, dict):
            code = data.get("code", "0")
            if str(code) != "0":
                raise APIError(
                    response.status_code,
                    f"CoinGlass returned error code {code}: {data.get('msg', 'Unknown error')}"
                )
            result = data.get("data")
            # FIX #3: Reject if "data" key is missing (malformed response)
            if result is None:
                raise APIError(
                    response.status_code,
                    f"CoinGlass response missing 'data' field. "
                    f"Full response: {json.dumps(data)[:300]}"
                )
        else:
            result = data

        # FIX #4: Validate result is not empty when we expect data
        if result is None:
            raise APIError(
                response.status_code,
                "CoinGlass returned null data. The symbol may not exist or "
                "the endpoint may not support this parameter combination."
            )

        # Store in persistent cache + historical storage
        symbol = params.get("symbol", "")
        interval = params.get("interval", "")
        self.storage.store(endpoint, phash, result, symbol=symbol, interval=interval)
        logger.debug("[FRESH] %s %s", endpoint, params.get("symbol", ""))

        return result
