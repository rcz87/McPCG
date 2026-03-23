"""Tests for CoinGlass API client."""

import os
import tempfile

import pytest

from coinglass_mcp.client import CoinGlassClient, APIError, RateLimitError, PlanLimitError
from coinglass_mcp.config import Config
from coinglass_mcp.storage import Storage


@pytest.fixture
def client_with_storage():
    """Create a client with temporary storage."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    storage = Storage(db_path=db_path)
    cfg = Config(api_key="test")
    c = CoinGlassClient(cfg, storage=storage)
    yield c
    os.unlink(db_path)


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


def test_error_classes():
    """Error classes should have correct attributes."""
    err = APIError(500, "Internal error")
    assert err.status_code == 500
    assert "500" in str(err)

    rate_err = RateLimitError()
    assert rate_err.status_code == 429

    plan_err = PlanLimitError()
    assert plan_err.status_code == 403
