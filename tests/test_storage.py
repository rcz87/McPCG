"""Tests for SQLite persistent storage — data integrity focused.

TRADING-CRITICAL: These tests ensure data is never wrong, stale, or corrupted.
"""

import asyncio
import json
import os
import tempfile
import threading
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
    assert len(row[0]) == 32  # SHA256 truncated to 32 chars (128-bit)


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


def test_historical_dynamic_window(storage):
    """Dynamic window should find data that fixed 30min window would miss.

    Scenario: data stored 2.5h ago, user asks for 3h ago.
    Fixed 30min window: target=3h±30m = 2.5h-3.5h → MISS (2.5h is edge)
    Dynamic window: target=3h±50% = 1.5h-4.5h → HIT
    """
    data_json = json.dumps({"cvd": 500})
    # Store data from 2.5 hours ago
    storage._conn.execute(
        """INSERT INTO api_cache (endpoint, symbol, interval, params_hash, response_data, data_checksum, fetched_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        ("/api/test", "SOL", "5m", "dw1", data_json,
         storage._checksum(data_json), time.time() - 2.5 * 3600)
    )
    storage._conn.commit()

    # Ask for 3h ago — dynamic window (50% of 3h = 1.5h) should find it
    result = storage.get_historical("/api/test", "SOL", hours_ago=3.0, interval="5m")
    assert result is not None
    assert result["data"] == {"cvd": 500}


def test_historical_dynamic_window_rejects_too_far(storage):
    """Dynamic window should still reject data that's way too far from target."""
    data_json = json.dumps({"cvd": 500})
    # Store data from 10 hours ago
    storage._conn.execute(
        """INSERT INTO api_cache (endpoint, symbol, interval, params_hash, response_data, data_checksum, fetched_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        ("/api/test", "BTC", "5m", "dw2", data_json,
         storage._checksum(data_json), time.time() - 10 * 3600)
    )
    storage._conn.commit()

    # Ask for 3h ago — 10h ago is way outside dynamic window (1.5h-4.5h)
    result = storage.get_historical("/api/test", "BTC", hours_ago=3.0, interval="5m")
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


def test_stats_shows_max_records(storage):
    """Stats should include max_records for monitoring."""
    stats = storage.get_stats()
    assert stats["total_records"] == 0

    storage.store("/api/test", "x1", {"v": 1}, symbol="BTC")
    stats = storage.get_stats()
    assert "max_records" in stats
    assert stats["max_records"] == 50_000


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


# ─── NEW: Size-Based Cleanup ─────────────────────────────────────────────────


def test_size_based_cleanup():
    """DB should enforce max_records limit — oldest records deleted first."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    s = Storage(db_path=db_path, retention_hours=48, max_records=5)
    s.start()
    try:
        # Insert 8 records (exceeds max_records=5)
        base_time = time.time()
        for i in range(8):
            data_json = json.dumps({"v": i})
            s._conn.execute(
                """INSERT INTO api_cache (endpoint, symbol, interval, params_hash,
                   response_data, data_checksum, fetched_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                ("/api/test", "BTC", "5m", f"size_{i}", data_json,
                 s._checksum(data_json), base_time + i),
            )
        s._conn.commit()

        count_before = s._conn.execute("SELECT COUNT(*) FROM api_cache").fetchone()[0]
        assert count_before == 8

        # Run cleanup
        s._cleanup()

        count_after = s._conn.execute("SELECT COUNT(*) FROM api_cache").fetchone()[0]
        assert count_after == 5  # Should trim to max_records

        # Oldest records should be gone, newest kept
        oldest = s._conn.execute(
            "SELECT params_hash FROM api_cache ORDER BY fetched_at ASC LIMIT 1"
        ).fetchone()
        assert oldest[0] == "size_3"  # Records 0,1,2 deleted

        newest = s._conn.execute(
            "SELECT params_hash FROM api_cache ORDER BY fetched_at DESC LIMIT 1"
        ).fetchone()
        assert newest[0] == "size_7"  # Newest kept
    finally:
        s.close()
        os.unlink(db_path)


# ─── NEW: Periodic Cleanup ───────────────────────────────────────────────────


def test_periodic_cleanup_triggers():
    """Cleanup should run periodically during store(), not just on startup."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    s = Storage(db_path=db_path, retention_hours=48, max_records=5)
    s.start()
    try:
        # Insert old record directly (bypassing store to avoid auto-cleanup)
        s._conn.execute(
            """INSERT INTO api_cache (endpoint, symbol, interval, params_hash,
               response_data, data_checksum, fetched_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            ("/api/test", "BTC", "", "expired1", json.dumps({"v": "old"}),
             "x", time.time() - 49 * 3600),
        )
        s._conn.commit()

        # Force _last_cleanup to be old so _maybe_cleanup triggers
        s._last_cleanup = time.time() - 2000  # >30min ago

        # This store() should trigger _maybe_cleanup → _cleanup_locked
        s.store("/api/test", "new1", {"v": "new"}, symbol="BTC")

        # Expired record should be gone
        row = s._conn.execute(
            "SELECT COUNT(*) FROM api_cache WHERE params_hash = 'expired1'"
        ).fetchone()
        assert row[0] == 0  # Old record cleaned up

        # New record should exist
        row = s._conn.execute(
            "SELECT COUNT(*) FROM api_cache WHERE params_hash = 'new1'"
        ).fetchone()
        assert row[0] == 1
    finally:
        s.close()
        os.unlink(db_path)


# ─── NEW: Thread Safety ──────────────────────────────────────────────────────


def test_concurrent_writes_no_crash():
    """Multiple threads writing simultaneously should not crash or lose data."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    s = Storage(db_path=db_path, retention_hours=48)
    s.start()
    try:
        errors = []
        num_threads = 10
        writes_per_thread = 20

        def writer(thread_id):
            try:
                for i in range(writes_per_thread):
                    s.store(
                        "/api/test",
                        f"thread_{thread_id}_{i}",
                        {"thread": thread_id, "i": i},
                        symbol="BTC",
                    )
            except Exception as e:
                errors.append(e)

        threads = [
            threading.Thread(target=writer, args=(tid,)) for tid in range(num_threads)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0, f"Thread errors: {errors}"

        # All records should be written
        count = s._conn.execute("SELECT COUNT(*) FROM api_cache").fetchone()[0]
        assert count == num_threads * writes_per_thread
    finally:
        s.close()
        os.unlink(db_path)


def test_concurrent_read_write_no_crash():
    """Reading and writing simultaneously should not crash."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    s = Storage(db_path=db_path, retention_hours=48)
    s.start()
    try:
        # Pre-populate some data
        for i in range(50):
            s.store("/api/test", f"pre_{i}", {"v": i}, symbol="BTC", interval="5m")

        errors = []

        def writer():
            try:
                for i in range(50):
                    s.store("/api/test", f"w_{i}", {"v": i}, symbol="ETH", interval="5m")
            except Exception as e:
                errors.append(("writer", e))

        def reader():
            try:
                for _ in range(50):
                    s.get_cached(f"pre_{_ % 50}", ttl=600)
                    s.get_trend("/api/test", "BTC", hours=1.0, interval="5m")
                    s.get_stats()
            except Exception as e:
                errors.append(("reader", e))

        threads = [
            threading.Thread(target=writer),
            threading.Thread(target=reader),
            threading.Thread(target=reader),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0, f"Concurrent errors: {errors}"
    finally:
        s.close()
        os.unlink(db_path)


# ─── NEW: Async Wrappers ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_async_store_and_retrieve():
    """Async wrappers should work correctly."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    s = Storage(db_path=db_path, retention_hours=48)
    s.start()
    try:
        await s.astore("/api/test", "async1", {"v": 42}, symbol="BTC", interval="5m")
        result = await s.aget_cached("async1", ttl=60)
        assert result == {"v": 42}
    finally:
        s.close()
        os.unlink(db_path)


@pytest.mark.asyncio
async def test_async_historical():
    """Async historical should return same results as sync."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    s = Storage(db_path=db_path, retention_hours=48)
    s.start()
    try:
        data_json = json.dumps({"cvd": 200})
        s._conn.execute(
            """INSERT INTO api_cache (endpoint, symbol, interval, params_hash,
               response_data, data_checksum, fetched_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            ("/api/test", "ETH", "5m", "ah1", data_json,
             s._checksum(data_json), time.time() - 3600),
        )
        s._conn.commit()

        result = await s.aget_historical("/api/test", "ETH", hours_ago=1.0)
        assert result is not None
        assert result["data"] == {"cvd": 200}
        assert result["age_minutes"] > 55
    finally:
        s.close()
        os.unlink(db_path)


@pytest.mark.asyncio
async def test_async_trend():
    """Async trend should return same results as sync."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    s = Storage(db_path=db_path, retention_hours=48)
    s.start()
    try:
        await s.astore("/api/test", "at1", {"v": 1}, symbol="SOL", interval="1h")
        await s.astore("/api/test", "at2", {"v": 2}, symbol="SOL", interval="1h")
        snapshots = await s.aget_trend("/api/test", "SOL", hours=1.0, interval="1h")
        assert len(snapshots) == 2
    finally:
        s.close()
        os.unlink(db_path)


@pytest.mark.asyncio
async def test_async_stats():
    """Async stats should work."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    s = Storage(db_path=db_path, retention_hours=48)
    s.start()
    try:
        await s.astore("/api/test", "as1", {"v": 1}, symbol="BTC")
        stats = await s.aget_stats()
        assert stats["total_records"] == 1
    finally:
        s.close()
        os.unlink(db_path)


@pytest.mark.asyncio
async def test_async_concurrent_no_crash():
    """Multiple async operations concurrently should not crash."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    s = Storage(db_path=db_path, retention_hours=48)
    s.start()
    try:
        # Simulate what coinglass_full_scan does: 9 concurrent stores
        tasks = [
            s.astore("/api/test", f"conc_{i}", {"v": i}, symbol="BTC", interval="5m")
            for i in range(9)
        ]
        await asyncio.gather(*tasks)

        stats = await s.aget_stats()
        assert stats["total_records"] == 9
    finally:
        s.close()
        os.unlink(db_path)
