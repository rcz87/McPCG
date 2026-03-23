"""Tests for SQLite persistent storage — data integrity focused."""

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


# ─── Basic Store/Retrieve ────────────────────────────────────────────────────


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
    """Expired cache returns None — prevents serving stale data."""
    storage.store("/api/test", "hash_old", {"value": 1}, symbol="BTC")
    # Manually set old timestamp
    storage._conn.execute(
        "UPDATE api_cache SET fetched_at = ? WHERE params_hash = ?",
        (time.time() - 120, "hash_old")
    )
    storage._conn.commit()
    result = storage.get_cached("hash_old", ttl=60)
    assert result is None


# ─── Data Integrity ──────────────────────────────────────────────────────────


def test_checksum_stored(storage):
    """Data should be stored with a checksum."""
    storage.store("/api/test", "chk1", {"v": 1}, symbol="BTC")
    row = storage._conn.execute(
        "SELECT data_checksum FROM api_cache WHERE params_hash = 'chk1'"
    ).fetchone()
    assert row[0] != ""  # Checksum should not be empty
    assert len(row[0]) == 16  # SHA256 truncated to 16 chars


def test_corrupted_data_rejected(storage):
    """Corrupted data should be rejected on read."""
    storage.store("/api/test", "corrupt1", {"v": 1}, symbol="BTC")
    # Corrupt the data in DB
    storage._conn.execute(
        "UPDATE api_cache SET response_data = '{invalid json' WHERE params_hash = 'corrupt1'"
    )
    storage._conn.commit()
    result = storage.get_cached("corrupt1", ttl=60)
    assert result is None  # Should reject, not crash


def test_checksum_mismatch_rejected(storage):
    """Data with wrong checksum should be rejected."""
    storage.store("/api/test", "tamper1", {"v": 1}, symbol="BTC")
    # Tamper with data but keep valid JSON
    storage._conn.execute(
        "UPDATE api_cache SET response_data = '{\"v\": 999}' WHERE params_hash = 'tamper1'"
    )
    storage._conn.commit()
    result = storage.get_cached("tamper1", ttl=60)
    assert result is None  # Checksum mismatch → rejected


# ─── Historical Queries ──────────────────────────────────────────────────────


def test_historical_returns_metadata(storage):
    """Historical query should return data + age metadata."""
    storage._conn.execute(
        """INSERT INTO api_cache (endpoint, symbol, interval, params_hash, response_data, data_checksum, fetched_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        ("/api/spot/aggregated-cvd-history", "SOL", "5m", "hist1",
         json.dumps({"cvd": 100}), storage._checksum(json.dumps({"cvd": 100})),
         time.time() - 3600)
    )
    storage._conn.commit()

    result = storage.get_historical("/api/spot/aggregated-cvd-history", "SOL", hours_ago=1.0)
    assert result is not None
    assert result["data"] == {"cvd": 100}
    assert "age_minutes" in result
    assert result["age_minutes"] > 55  # Should be ~60 min
    assert "fetched_at" in result


def test_historical_no_data(storage):
    """No historical data returns None."""
    result = storage.get_historical("/api/test", "BTC", hours_ago=5.0)
    assert result is None


# ─── Trend Queries ────────────────────────────────────────────────────────────


def test_trend_returns_age(storage):
    """Trend snapshots should include age metadata."""
    base_time = time.time()
    for i in range(3):
        data_json = json.dumps({"oi": 1000 + i * 100})
        storage._conn.execute(
            """INSERT INTO api_cache (endpoint, symbol, interval, params_hash, response_data, data_checksum, fetched_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            ("/api/futures/openInterest/ohlc-aggregated-history", "ETH", "1h",
             f"trend_{i}", data_json, storage._checksum(data_json),
             base_time - (2 - i) * 3600)
        )
    storage._conn.commit()

    snapshots = storage.get_trend(
        "/api/futures/openInterest/ohlc-aggregated-history", "ETH", hours=5.0, interval="1h"
    )
    assert len(snapshots) == 3
    assert snapshots[0]["data"]["oi"] == 1000
    assert snapshots[-1]["data"]["oi"] == 1200
    # Each snapshot should have age
    for snap in snapshots:
        assert "age_minutes" in snap
        assert "fetched_at" in snap


def test_trend_empty(storage):
    """No trend data returns empty list."""
    snapshots = storage.get_trend("/api/test", "BTC", hours=4.0)
    assert snapshots == []


def test_trend_corrupted_entries_skipped(storage):
    """Corrupted entries in trend should be skipped, not crash."""
    data_json = json.dumps({"v": 1})
    storage._conn.execute(
        """INSERT INTO api_cache (endpoint, symbol, interval, params_hash, response_data, data_checksum, fetched_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        ("/api/test", "BTC", "5m", "t1", data_json, storage._checksum(data_json), time.time() - 1800)
    )
    # Insert corrupted entry
    storage._conn.execute(
        """INSERT INTO api_cache (endpoint, symbol, interval, params_hash, response_data, data_checksum, fetched_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        ("/api/test", "BTC", "5m", "t2", "{bad json", "wrongchecksum", time.time() - 900)
    )
    # Insert good entry
    data_json2 = json.dumps({"v": 3})
    storage._conn.execute(
        """INSERT INTO api_cache (endpoint, symbol, interval, params_hash, response_data, data_checksum, fetched_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        ("/api/test", "BTC", "5m", "t3", data_json2, storage._checksum(data_json2), time.time())
    )
    storage._conn.commit()

    snapshots = storage.get_trend("/api/test", "BTC", hours=1.0, interval="5m")
    # Should only return 2 valid entries, corrupted one skipped
    assert len(snapshots) == 2
    assert snapshots[0]["data"] == {"v": 1}
    assert snapshots[1]["data"] == {"v": 3}


# ─── Misc ─────────────────────────────────────────────────────────────────────


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
    data_json = json.dumps({"v": 2})
    storage._conn.execute(
        """INSERT INTO api_cache (endpoint, symbol, interval, params_hash, response_data, data_checksum, fetched_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        ("/api/test", "SOL", "", "case2", data_json, storage._checksum(data_json), time.time() - 3600)
    )
    storage._conn.commit()

    # Query with lowercase
    result = storage.get_historical("/api/test", "sol", hours_ago=1.0)
    assert result is not None
    assert result["data"] == {"v": 2}


def test_cleanup(storage):
    """Old data should be cleaned up."""
    storage._conn.execute(
        """INSERT INTO api_cache (endpoint, symbol, interval, params_hash, response_data, data_checksum, fetched_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        ("/api/test", "BTC", "", "old1", json.dumps({"v": "old"}), "x",
         time.time() - 49 * 3600)
    )
    storage._conn.commit()

    storage._cleanup()

    row = storage._conn.execute("SELECT COUNT(*) FROM api_cache").fetchone()
    assert row[0] == 0
