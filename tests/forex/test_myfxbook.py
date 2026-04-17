"""MyFXBook client tests — session caching, mocked HTTP, auto-relogin."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from coinglass_mcp.forex.myfxbook_client import (
    MyFXBookAuthError,
    MyFXBookClient,
    MyFXBookError,
    _mask_email,
)
from coinglass_mcp.forex.storage import ForexStorage


@pytest.fixture
def tmp_storage(tmp_path: Path) -> ForexStorage:
    return ForexStorage(db_path=str(tmp_path / "forex.db"))


@pytest.fixture
def with_creds(monkeypatch):
    monkeypatch.setenv("MYFXBOOK_EMAIL", "test@example.com")
    monkeypatch.setenv("MYFXBOOK_PASSWORD", "hunter2")


def test_mask_email():
    assert _mask_email("ricoz@gmail.com") == "r***@gmail.com"
    assert _mask_email("") == "***"
    assert _mask_email("noatsign") == "***"


def test_missing_creds_raises(tmp_storage):
    # Ensure env clean
    os.environ.pop("MYFXBOOK_EMAIL", None)
    os.environ.pop("MYFXBOOK_PASSWORD", None)
    client = MyFXBookClient(storage=tmp_storage)
    with pytest.raises(MyFXBookAuthError):
        client._creds()


@pytest.mark.asyncio
async def test_session_cached_across_calls(tmp_storage, with_creds):
    """After first login, subsequent calls should reuse cached session token."""
    client = MyFXBookClient(storage=tmp_storage)
    # Seed a session into storage directly
    tmp_storage.save_session("cached-token-xyz")
    token = await client._get_session()
    assert token == "cached-token-xyz"


@pytest.mark.asyncio
async def test_relogin_on_invalid_session(tmp_storage, with_creds):
    """Simulate first call returning 'invalid session', client should re-login."""
    client = MyFXBookClient(storage=tmp_storage)
    tmp_storage.save_session("stale-token")

    # First response: error "invalid session"; second: success with data
    responses = [
        _fake_response({"error": True, "message": "Invalid session"}),
        _fake_response({"error": False, "session": "fresh-token"}),
        _fake_response({"error": False, "symbols": [
            {"name": "EURUSD", "longPercentage": 40, "shortPercentage": 60,
             "longPositions": 10, "shortPositions": 15, "longVolume": 1.2,
             "shortVolume": 2.1, "avgLongPrice": 1.07, "avgShortPrice": 1.08,
             "totalPositions": 25},
        ]}),
    ]

    async def fake_get(self, url, params=None):
        return responses.pop(0)

    with patch("httpx.AsyncClient.get", new=fake_get):
        # Disable rate limiter to avoid unrelated interference
        client.rate_limiter.check_and_increment = lambda: 1
        data = await client.get_community_outlook()

    assert len(data) == 1
    assert data[0]["symbol"] == "EURUSD"
    assert data[0]["long_pct"] == 40.0
    assert tmp_storage.get_session() == "fresh-token"


def _fake_response(body: dict):
    class _R:
        status_code = 200
        def json(self): return body
    return _R()


@pytest.mark.asyncio
async def test_get_sentiment_filters_symbol(tmp_storage, with_creds):
    client = MyFXBookClient(storage=tmp_storage)

    async def fake_call(endpoint, params=None):
        return {"symbols": [
            {"name": "EURUSD", "longPercentage": 30, "shortPercentage": 70,
             "longPositions": 5, "shortPositions": 10,
             "longVolume": 1.0, "shortVolume": 2.0,
             "avgLongPrice": 1.07, "avgShortPrice": 1.08, "totalPositions": 15},
            {"name": "XAUUSD", "longPercentage": 80, "shortPercentage": 20,
             "longPositions": 50, "shortPositions": 12,
             "longVolume": 10.0, "shortVolume": 2.0,
             "avgLongPrice": 2400, "avgShortPrice": 2450, "totalPositions": 62},
        ]}

    client._call = fake_call  # type: ignore[assignment]
    row = await client.get_sentiment("XAUUSD")
    assert row is not None
    assert row["long_pct"] == 80.0

    missing = await client.get_sentiment("NOTAPAIR")
    assert missing is None
