"""Tests for CoinGlass API client — FetchResult, RateLimiter, symbol normalization."""

import asyncio
import os
import tempfile
import time

import pytest

from coinglass_mcp.client import (
    APIError,
    CoinGlassClient,
    FetchResult,
    PlanLimitError,
    RateLimitError,
    RateLimiter,
)
from coinglass_mcp.config import Config, normalize_symbol
from coinglass_mcp.storage import Storage


@pytest.fixture
def client_with_storage():
    """Create a client with temporary storage."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    storage = Storage(db_path=db_path)
    cfg = Config(api_key="test-key-12345")
    c = CoinGlassClient(cfg, storage=storage)
    yield c
    os.unlink(db_path)


# ─── Params Hash ─────────────────────────────────────────────────────────────


def test_params_hash_deterministic():
    """Same endpoint + params should produce same hash."""
    hash1 = CoinGlassClient._params_hash("/api/test", {"symbol": "BTC", "interval": "5m"})
    hash2 = CoinGlassClient._params_hash("/api/test", {"interval": "5m", "symbol": "BTC"})
    assert hash1 == hash2


def test_params_hash_different():
    """Different params should produce different hash."""
    hash1 = CoinGlassClient._params_hash("/api/test", {"symbol": "BTC"})
    hash2 = CoinGlassClient._params_hash("/api/test", {"symbol": "ETH"})
    assert hash1 != hash2


def test_client_has_storage(client_with_storage):
    """Client should have storage attached."""
    assert client_with_storage.storage is not None


# ─── Error Classes ───────────────────────────────────────────────────────────


def test_error_classes():
    """Error classes should have correct attributes."""
    err = APIError(500, "Internal error")
    assert err.status_code == 500
    assert "500" in str(err)

    rate_err = RateLimitError()
    assert rate_err.status_code == 429

    plan_err = PlanLimitError()
    assert plan_err.status_code == 403


# ─── FetchResult ─────────────────────────────────────────────────────────────


def test_fetch_result_live():
    """Fresh data should not be stale or expired."""
    r = FetchResult(data={"v": 1}, age_seconds=0.0, is_cached=False, fetched_at=time.time())
    assert not r.is_stale
    assert not r.is_expired
    assert r.age_label == "LIVE"


def test_fetch_result_cached_fresh():
    """Cached data under 2 min should not be stale."""
    r = FetchResult(data={"v": 1}, age_seconds=30.0, is_cached=True, fetched_at=time.time() - 30)
    assert not r.is_stale
    assert not r.is_expired
    assert "30s ago" in r.age_label


def test_fetch_result_stale():
    """Data older than 120s should be stale."""
    r = FetchResult(data={"v": 1}, age_seconds=150.0, is_cached=True, fetched_at=time.time() - 150)
    assert r.is_stale
    assert not r.is_expired
    assert "STALE" in r.age_label


def test_fetch_result_expired():
    """Data older than 300s should be expired — DO NOT USE FOR ENTRY."""
    r = FetchResult(data={"v": 1}, age_seconds=400.0, is_cached=True, fetched_at=time.time() - 400)
    assert r.is_stale
    assert r.is_expired
    assert "EXPIRED" in r.age_label


# ─── Symbol Normalization ────────────────────────────────────────────────────


def test_normalize_basic():
    """Basic symbol normalization."""
    assert normalize_symbol("btc") == "BTC"
    assert normalize_symbol("ETH") == "ETH"
    assert normalize_symbol("  sol  ") == "SOL"


def test_normalize_strip_suffix():
    """Should strip USDT, USD, PERP suffixes."""
    assert normalize_symbol("BTCUSDT") == "BTC"
    assert normalize_symbol("ETHUSD") == "ETH"
    assert normalize_symbol("SOL/USDT") == "SOL"
    assert normalize_symbol("BTC-PERP") == "BTC"
    assert normalize_symbol("AVAXUSDC") == "AVAX"


def test_normalize_aliases():
    """Should resolve aliases."""
    assert normalize_symbol("BITCOIN") == "BTC"
    assert normalize_symbol("ETHEREUM") == "ETH"
    assert normalize_symbol("SOLANA") == "SOL"
    assert normalize_symbol("HYPERLIQUID") == "HYPE"


def test_normalize_no_over_strip():
    """Should not strip suffix from short symbols."""
    # "USD" should not be stripped to empty
    assert normalize_symbol("USD") == "USD"


# ─── API Key Masking ────────────────────────────────────────────────────────


def test_mask_key(client_with_storage):
    """API key should be masked in any text."""
    text = "Error: Invalid key test-key-12345 at endpoint"
    masked = client_with_storage._mask_key(text)
    assert "test-key-12345" not in masked
    assert "CG-***MASKED***" in masked


def test_mask_key_no_key():
    """Text without key should pass through unchanged."""
    cfg = Config(api_key="secret")
    c = CoinGlassClient(cfg)
    assert c._mask_key("no key here") == "no key here"


# ─── Rate Limiter ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_rate_limiter_spacing():
    """Rate limiter should enforce minimum spacing between requests."""
    rl = RateLimiter(max_per_minute=100, min_spacing=0.1)
    t1 = time.time()
    await rl.acquire()
    await rl.acquire()
    await rl.acquire()
    t2 = time.time()
    # 3 requests with 0.1s spacing → should take at least 0.2s
    assert t2 - t1 >= 0.18  # small tolerance


@pytest.mark.asyncio
async def test_rate_limiter_tracks_usage():
    """Rate limiter should track per-minute usage."""
    rl = RateLimiter(max_per_minute=100, min_spacing=0.01)
    for _ in range(5):
        await rl.acquire()
    usage = rl.usage
    assert usage["used"] == 5
    assert usage["remaining"] == 95
    assert usage["limit"] == 100


@pytest.mark.asyncio
async def test_rate_limiter_serializes_concurrent():
    """Concurrent acquire() calls should be serialized."""
    rl = RateLimiter(max_per_minute=100, min_spacing=0.05)

    results = []

    async def worker(idx):
        await rl.acquire()
        results.append((idx, time.time()))

    # Launch 5 concurrent workers
    await asyncio.gather(*[worker(i) for i in range(5)])

    # All 5 should complete
    assert len(results) == 5

    # Check spacing between consecutive requests
    times = sorted(t for _, t in results)
    for i in range(1, len(times)):
        gap = times[i] - times[i - 1]
        assert gap >= 0.04  # small tolerance on 0.05s spacing
