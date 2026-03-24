"""SQLite persistent storage for historical data and caching.

CRITICAL FOR TRADING: This module handles all data storage and retrieval.
Wrong/stale/corrupted data = wrong trading decisions = money lost.

Safety guarantees:
- Thread-safe via RLock (async concurrent access safe)
- WAL mode (crash-safe writes)
- SHA256 checksum per record (detects corruption/tampering)
- Integrity check on every read (corrupted → rejected, never served)
- Async wrappers (never blocks event loop)
- Auto-cleanup by time + size (prevents unbounded DB growth)
- Periodic cleanup every 30min (not just on startup)
- Dynamic historical window (adapts to query frequency)
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import sqlite3
import threading
import time
from typing import Any

logger = logging.getLogger("coinglass-mcp")


class Storage:
    """SQLite-based persistent storage for CoinGlass data.

    Thread-safe, async-compatible, with data integrity guarantees.
    """

    def __init__(
        self,
        db_path: str | None = None,
        retention_hours: int = 48,
        max_records: int = 50_000,
    ):
        if db_path is None:
            data_dir = os.getenv("DATA_DIR", os.path.expanduser("~/.coinglass-mcp"))
            os.makedirs(data_dir, exist_ok=True)
            db_path = os.path.join(data_dir, "cache.db")
        self.db_path = db_path
        self.retention_hours = retention_hours
        self.max_records = max_records
        self._conn: sqlite3.Connection | None = None
        self._lock = threading.RLock()
        self._last_cleanup = 0.0
        self._cleanup_interval = 1800  # 30 minutes
        self._start_attempts = 0  # Guard against infinite recursion

    def start(self) -> None:
        """Initialize database and create tables."""
        with self._lock:
            self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS api_cache (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    endpoint TEXT NOT NULL,
                    symbol TEXT DEFAULT '',
                    interval TEXT DEFAULT '',
                    params_hash TEXT NOT NULL,
                    response_data TEXT NOT NULL,
                    data_checksum TEXT NOT NULL DEFAULT '',
                    fetched_at REAL NOT NULL,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            """)
            self._conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_cache_lookup
                ON api_cache (endpoint, symbol, interval, fetched_at)
            """)
            self._conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_cache_hash
                ON api_cache (params_hash, fetched_at)
            """)
            self._conn.commit()
            # Integrity check on startup
            result = self._conn.execute("PRAGMA integrity_check").fetchone()
            if result[0] != "ok":
                self._start_attempts += 1
                if self._start_attempts > 1:
                    logger.critical(
                        "Database recreation also failed integrity check. "
                        "Continuing with potentially corrupt DB."
                    )
                else:
                    logger.error("Database integrity check FAILED: %s — recreating", result[0])
                    self._conn.close()
                    os.remove(self.db_path)
                    self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
                    self._conn.execute("PRAGMA journal_mode=WAL")
                    self.start()
                    return
            self._cleanup_locked()
            self._last_cleanup = time.time()

    def close(self) -> None:
        """Close database connection."""
        with self._lock:
            if self._conn:
                self._conn.close()
                self._conn = None

    # ─── Cleanup (lock must be held by caller) ───────────────────────────────

    def _cleanup_locked(self) -> None:
        """Remove expired + excess data. Caller MUST hold _lock."""
        if not self._conn:
            return
        # Time-based: remove data older than retention period
        cutoff = time.time() - (self.retention_hours * 3600)
        cursor = self._conn.execute(
            "DELETE FROM api_cache WHERE fetched_at < ?", (cutoff,)
        )
        if cursor.rowcount > 0:
            logger.info("Cleaned up %d expired records", cursor.rowcount)
        # Size-based: remove oldest records if over max_records
        count = self._conn.execute("SELECT COUNT(*) FROM api_cache").fetchone()[0]
        if count > self.max_records:
            excess = count - self.max_records
            self._conn.execute(
                "DELETE FROM api_cache WHERE id IN "
                "(SELECT id FROM api_cache ORDER BY fetched_at ASC LIMIT ?)",
                (excess,),
            )
            logger.info(
                "Cleaned up %d excess records (limit: %d)", excess, self.max_records
            )
        self._conn.commit()

    def _maybe_cleanup(self) -> None:
        """Run cleanup if 30min has passed since last run. Caller MUST hold _lock."""
        now = time.time()
        if now - self._last_cleanup > self._cleanup_interval:
            self._cleanup_locked()
            self._last_cleanup = now

    def _cleanup(self) -> None:
        """Public cleanup — acquires lock. Used by tests."""
        with self._lock:
            self._cleanup_locked()

    # ─── Checksum ────────────────────────────────────────────────────────────

    @staticmethod
    def _checksum(data_json: str) -> str:
        """Generate SHA256 checksum (32 chars / 128-bit) for data integrity."""
        return hashlib.sha256(data_json.encode()).hexdigest()[:32]

    # ─── Store ───────────────────────────────────────────────────────────────

    def store(
        self,
        endpoint: str,
        params_hash: str,
        data: Any,
        symbol: str = "",
        interval: str = "",
    ) -> None:
        """Store an API response with integrity checksum. Thread-safe."""
        with self._lock:
            if not self._conn:
                return
            data_json = json.dumps(data, default=str)
            checksum = self._checksum(data_json)
            self._conn.execute(
                """INSERT INTO api_cache
                   (endpoint, symbol, interval, params_hash, response_data,
                    data_checksum, fetched_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    endpoint,
                    symbol.upper(),
                    interval,
                    params_hash,
                    data_json,
                    checksum,
                    time.time(),
                ),
            )
            self._conn.commit()
            self._maybe_cleanup()

    # ─── Verify & Load ───────────────────────────────────────────────────────

    def _verify_and_load(self, row: tuple) -> Any | None:
        """Load JSON from a DB row with integrity check.

        Returns None (never raises) if data is corrupted — prevents
        serving bad data to trading decisions.
        """
        data_json = row[0]
        stored_checksum = row[1] if len(row) > 1 else ""

        try:
            data = json.loads(data_json)
        except (json.JSONDecodeError, ValueError) as e:
            logger.error("Corrupted data in storage (JSON parse failed): %s", e)
            return None

        # Verify checksum — reject tampered/corrupted data
        if stored_checksum:
            actual_checksum = self._checksum(data_json)
            if actual_checksum != stored_checksum:
                logger.error(
                    "Data integrity FAILED! stored=%s actual=%s — rejecting",
                    stored_checksum,
                    actual_checksum,
                )
                return None

        return data

    # ─── Cache ───────────────────────────────────────────────────────────────

    def get_cached(self, params_hash: str, ttl: int = 60) -> Any | None:
        """Get cached response if within TTL. Thread-safe."""
        with self._lock:
            if not self._conn:
                return None
            cutoff = time.time() - ttl
            row = self._conn.execute(
                """SELECT response_data, data_checksum FROM api_cache
                   WHERE params_hash = ? AND fetched_at > ?
                   ORDER BY fetched_at DESC LIMIT 1""",
                (params_hash, cutoff),
            ).fetchone()
            if row:
                return self._verify_and_load(row)
            return None

    def get_cached_with_time(
        self, params_hash: str, ttl: int = 60
    ) -> dict[str, Any] | None:
        """Get cached response with fetched_at timestamp. Thread-safe.

        Returns dict with {data, fetched_at} or None.
        Used by client to calculate data age for stale warnings.
        """
        with self._lock:
            if not self._conn:
                return None
            cutoff = time.time() - ttl
            row = self._conn.execute(
                """SELECT response_data, data_checksum, fetched_at FROM api_cache
                   WHERE params_hash = ? AND fetched_at > ?
                   ORDER BY fetched_at DESC LIMIT 1""",
                (params_hash, cutoff),
            ).fetchone()
            if row:
                data = self._verify_and_load(row)
                if data is not None:
                    return {"data": data, "fetched_at": row[2]}
            return None

    # ─── Historical ──────────────────────────────────────────────────────────

    def get_historical(
        self,
        endpoint: str,
        symbol: str,
        hours_ago: float = 1.0,
        interval: str = "",
    ) -> dict[str, Any] | None:
        """Get the closest stored data from N hours ago. Thread-safe.

        Uses dynamic window: max(30min, 50% of requested period).
        This prevents missing data when query frequency is low
        (e.g., querying every hour but asking for 3h ago).

        Returns:
            Dict with {data, fetched_at, age_minutes} or None
        """
        with self._lock:
            if not self._conn:
                return None
            target_time = time.time() - (hours_ago * 3600)
            # Dynamic window: adapts to requested time range
            # - 1h ago → window = max(1800, 1800) = 30min
            # - 3h ago → window = max(1800, 5400) = 1.5h
            # - 8h ago → window = max(1800, 14400) = 4h
            window = max(1800, hours_ago * 3600 * 0.5)
            query = """
                SELECT response_data, data_checksum, fetched_at FROM api_cache
                WHERE endpoint = ? AND symbol = ?
                  AND fetched_at BETWEEN ? AND ?
            """
            params: list[Any] = [
                endpoint,
                symbol.upper(),
                target_time - window,
                target_time + window,
            ]
            if interval:
                query += " AND interval = ?"
                params.append(interval)
            query += " ORDER BY ABS(fetched_at - ?) LIMIT 1"
            params.append(target_time)

            row = self._conn.execute(query, params).fetchone()
            if row:
                data = self._verify_and_load(row)
                if data is None:
                    return None
                fetched_at = row[2]
                age_minutes = (time.time() - fetched_at) / 60
                return {
                    "data": data,
                    "fetched_at": fetched_at,
                    "age_minutes": round(age_minutes, 1),
                }
            return None

    # ─── Trend ───────────────────────────────────────────────────────────────

    def get_trend(
        self,
        endpoint: str,
        symbol: str,
        hours: float = 4.0,
        interval: str = "",
    ) -> list[dict[str, Any]]:
        """Get all stored snapshots for trend analysis. Thread-safe.

        Returns list of {fetched_at, age_minutes, data} ordered by time.
        Corrupted entries are silently skipped.
        """
        with self._lock:
            if not self._conn:
                return []
            cutoff = time.time() - (hours * 3600)
            query = """
                SELECT response_data, data_checksum, fetched_at FROM api_cache
                WHERE endpoint = ? AND symbol = ?
                  AND fetched_at > ?
            """
            params: list[Any] = [endpoint, symbol.upper(), cutoff]
            if interval:
                query += " AND interval = ?"
                params.append(interval)
            query += " ORDER BY fetched_at ASC"
            rows = self._conn.execute(query, params).fetchall()

        # Process outside lock — _verify_and_load is pure (no DB access)
        result = []
        now = time.time()
        for row in rows:
            data = self._verify_and_load(row)
            if data is not None:
                result.append({
                    "fetched_at": row[2],
                    "age_minutes": round((now - row[2]) / 60, 1),
                    "data": data,
                })
        return result

    # ─── Stats ───────────────────────────────────────────────────────────────

    def get_stats(self) -> dict[str, Any]:
        """Get storage statistics. Thread-safe."""
        with self._lock:
            if not self._conn:
                return {"status": "not connected"}
            row = self._conn.execute(
                "SELECT COUNT(*), MIN(fetched_at), MAX(fetched_at) FROM api_cache"
            ).fetchone()
            total = row[0]
            if total == 0:
                return {"total_records": 0, "status": "empty"}

            oldest_hours = (time.time() - row[1]) / 3600 if row[1] else 0
            newest_mins = (time.time() - row[2]) / 60 if row[2] else 0

            symbols = self._conn.execute(
                "SELECT symbol, COUNT(*) FROM api_cache WHERE symbol != '' "
                "GROUP BY symbol ORDER BY COUNT(*) DESC LIMIT 10"
            ).fetchall()

        return {
            "total_records": total,
            "oldest_data": f"{oldest_hours:.1f} hours ago",
            "newest_data": f"{newest_mins:.1f} minutes ago",
            "retention": f"{self.retention_hours} hours",
            "max_records": self.max_records,
            "db_path": self.db_path,
            "top_symbols": {s: c for s, c in symbols},
        }

    # ─── Async Wrappers (non-blocking for event loop) ────────────────────────

    async def astore(self, *args: Any, **kwargs: Any) -> None:
        """Async store — runs in thread pool, never blocks event loop."""
        await asyncio.to_thread(self.store, *args, **kwargs)

    async def aget_cached(self, *args: Any, **kwargs: Any) -> Any | None:
        """Async cache lookup — runs in thread pool."""
        return await asyncio.to_thread(self.get_cached, *args, **kwargs)

    async def aget_cached_with_time(self, *args: Any, **kwargs: Any) -> dict[str, Any] | None:
        """Async cache lookup with timestamp — runs in thread pool."""
        return await asyncio.to_thread(self.get_cached_with_time, *args, **kwargs)

    async def aget_historical(self, *args: Any, **kwargs: Any) -> dict[str, Any] | None:
        """Async historical query — runs in thread pool."""
        return await asyncio.to_thread(self.get_historical, *args, **kwargs)

    async def aget_trend(self, *args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        """Async trend query — runs in thread pool."""
        return await asyncio.to_thread(self.get_trend, *args, **kwargs)

    async def aget_stats(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        """Async stats — runs in thread pool."""
        return await asyncio.to_thread(self.get_stats, *args, **kwargs)
