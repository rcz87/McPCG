"""Tests for CoinGlass API client."""

import pytest
from coinglass_mcp.client import CoinGlassClient, APIError, RateLimitError, PlanLimitError
from coinglass_mcp.config import Config


def test_cache_key_deterministic():
    """Same endpoint + params should produce same cache key."""
    cfg = Config(api_key="test")
    c = CoinGlassClient(cfg)
    key1 = c._cache_key("/api/test", {"symbol": "BTC", "interval": "5m"})
    key2 = c._cache_key("/api/test", {"interval": "5m", "symbol": "BTC"})
    assert key1 == key2  # sorted keys


def test_cache_key_different_params():
    """Different params should produce different cache keys."""
    cfg = Config(api_key="test")
    c = CoinGlassClient(cfg)
    key1 = c._cache_key("/api/test", {"symbol": "BTC"})
    key2 = c._cache_key("/api/test", {"symbol": "ETH"})
    assert key1 != key2


def test_cache_set_and_get():
    """Cache should store and retrieve data within TTL."""
    cfg = Config(api_key="test", cache_ttl=60)
    c = CoinGlassClient(cfg)
    c._set_cache("key1", {"data": "test"})
    assert c._get_cached("key1") == {"data": "test"}


def test_cache_miss():
    """Cache miss should return None."""
    cfg = Config(api_key="test")
    c = CoinGlassClient(cfg)
    assert c._get_cached("nonexistent") is None


def test_error_classes():
    """Error classes should have correct attributes."""
    err = APIError(500, "Internal error")
    assert err.status_code == 500
    assert "500" in str(err)

    rate_err = RateLimitError()
    assert rate_err.status_code == 429

    plan_err = PlanLimitError()
    assert plan_err.status_code == 403
