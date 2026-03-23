"""Tests for SQLite persistent storage."""

import json
import os
import tempfile
import time

import pytest

from coinglass_mcp.storage import Storage


@pytest.fixture
def storage():
    """Create a temporary storage instance."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    s = Storage(db_path=db_path, retention_hours=48)
    s.start()
    yield s
    s.close()
    os.unlink(db_path)


def test_store_and_retrieve(storage):
    """Store data and retrieve via cache."""
    storage.store("/api/test", "hash1", {"value": 42}, symbol="BTC", interval="5m")
    result = storage.get_cached("hash1", ttl=60)
    assert result == {"value": 42}


def test_cache_miss(storage):
    """Cache miss returns None."""
    result = storage.get_cached("nonexistent", ttl=60)
    assert result is None


def test_cache_expired(storage):
    """Expired cache returns None."""
    storage.store("/api/test", "hash_old", {"value": 1}, symbol="BTC")
    # Manually set old timestamp
    storage._conn.execute(
        "UPDATE api_cache SET fetched_at = ? WHERE params_hash = ?",
        (time.time() - 120, "hash_old")
    )
    storage._conn.commit()
    result = storage.get_cached("hash_old", ttl=60)
    assert result is None


def test_historical_query(storage):
    """Get data from N hours ago."""
    # Store data with timestamp from 1 hour ago
    storage._conn.execute(
        """INSERT INTO api_cache (endpoint, symbol, interval, params_hash, response_data, fetched_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        ("/api/spot/aggregated-cvd-history", "SOL", "5m", "hist1",
         json.dumps({"cvd": 100}), time.time() - 3600)
    )
    storage._conn.commit()

    result = storage.get_historical("/api/spot/aggregated-cvd-history", "SOL", hours_ago=1.0)
    assert result == {"cvd": 100}


def test_historical_no_data(storage):
    """No historical data returns None."""
    result = storage.get_historical("/api/test", "BTC", hours_ago=5.0)
    assert result is None


def test_trend_query(storage):
    """Get multiple snapshots for trend analysis."""
    base_time = time.time()
    for i in range(5):
        storage._conn.execute(
            """INSERT INTO api_cache (endpoint, symbol, interval, params_hash, response_data, fetched_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            ("/api/futures/openInterest/ohlc-aggregated-history", "ETH", "1h",
             f"trend_{i}", json.dumps({"oi": 1000 + i * 100}),
             base_time - (4 - i) * 3600)  # 4h ago, 3h ago, 2h ago, 1h ago, now
        )
    storage._conn.commit()

    snapshots = storage.get_trend(
        "/api/futures/openInterest/ohlc-aggregated-history", "ETH", hours=5.0, interval="1h"
    )
    assert len(snapshots) == 5
    # Should be ordered by time ASC
    assert snapshots[0]["data"]["oi"] == 1000
    assert snapshots[-1]["data"]["oi"] == 1400


def test_trend_empty(storage):
    """No trend data returns empty list."""
    snapshots = storage.get_trend("/api/test", "BTC", hours=4.0)
    assert snapshots == []


def test_stats(storage):
    """Storage stats should work."""
    stats = storage.get_stats()
    assert stats["total_records"] == 0

    storage.store("/api/test", "s1", {"v": 1}, symbol="BTC")
    storage.store("/api/test", "s2", {"v": 2}, symbol="ETH")
    storage.store("/api/test", "s3", {"v": 3}, symbol="BTC")

    stats = storage.get_stats()
    assert stats["total_records"] == 3
    assert "BTC" in stats["top_symbols"]
    assert stats["top_symbols"]["BTC"] == 2


def test_symbol_case_insensitive(storage):
    """Symbol should be stored uppercase."""
    storage.store("/api/test", "case1", {"v": 1}, symbol="sol")
    # Query with different case
    storage._conn.execute(
        """INSERT INTO api_cache (endpoint, symbol, interval, params_hash, response_data, fetched_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        ("/api/test", "SOL", "", "case2", json.dumps({"v": 2}), time.time() - 3600)
    )
    storage._conn.commit()

    result = storage.get_historical("/api/test", "sol", hours_ago=1.0)
    assert result == {"v": 2}


def test_cleanup(storage):
    """Old data should be cleaned up."""
    # Insert old data (49 hours ago, beyond 48h retention)
    storage._conn.execute(
        """INSERT INTO api_cache (endpoint, symbol, interval, params_hash, response_data, fetched_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        ("/api/test", "BTC", "", "old1", json.dumps({"v": "old"}),
         time.time() - 49 * 3600)
    )
    storage._conn.commit()

    storage._cleanup()

    row = storage._conn.execute("SELECT COUNT(*) FROM api_cache").fetchone()
    assert row[0] == 0
