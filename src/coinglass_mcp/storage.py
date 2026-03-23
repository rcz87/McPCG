"""SQLite persistent storage for historical data and caching."""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
from typing import Any

logger = logging.getLogger("coinglass-mcp")


class Storage:
    """SQLite-based persistent storage for CoinGlass data.

    Stores every API response with metadata for:
    - Persistent caching (survives server restart)
    - Historical queries ("CVD 3 hours ago")
    - Trend comparison ("OI naik atau turun dari tadi pagi")

    Data Integrity:
    - All writes use WAL mode (crash-safe)
    - JSON serialization verified on read-back
    - Data age always shown to prevent stale data confusion
    - Auto-cleanup of data older than retention period
    """

    def __init__(self, db_path: str | None = None, retention_hours: int = 48):
        if db_path is None:
            data_dir = os.getenv("DATA_DIR", os.path.expanduser("~/.coinglass-mcp"))
            os.makedirs(data_dir, exist_ok=True)
            db_path = os.path.join(data_dir, "cache.db")
        self.db_path = db_path
        self.retention_hours = retention_hours
        self._conn: sqlite3.Connection | None = None

    def start(self) -> None:
        """Initialize database and create tables."""
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        # FIX #5: Enable foreign key checks and integrity
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
        # FIX #6: Run integrity check on startup
        result = self._conn.execute("PRAGMA integrity_check").fetchone()
        if result[0] != "ok":
            logger.error("Database integrity check FAILED: %s", result[0])
            # Reset corrupted database
            self._conn.close()
            os.remove(self.db_path)
            self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
            self._conn.execute("PRAGMA journal_mode=WAL")
            self.start()
            return
        self._cleanup()

    def close(self) -> None:
        """Close database connection."""
        if self._conn:
            self._conn.close()
            self._conn = None

    def _cleanup(self) -> None:
        """Remove data older than retention period."""
        if not self._conn:
            return
        cutoff = time.time() - (self.retention_hours * 3600)
        cursor = self._conn.execute("DELETE FROM api_cache WHERE fetched_at < ?", (cutoff,))
        if cursor.rowcount > 0:
            logger.info("Cleaned up %d expired records", cursor.rowcount)
        self._conn.commit()

    @staticmethod
    def _checksum(data_json: str) -> str:
        """Generate checksum for data integrity verification."""
        import hashlib
        return hashlib.sha256(data_json.encode()).hexdigest()[:16]

    def store(
        self,
        endpoint: str,
        params_hash: str,
        data: Any,
        symbol: str = "",
        interval: str = "",
    ) -> None:
        """Store an API response with integrity checksum."""
        if not self._conn:
            return
        data_json = json.dumps(data, default=str)
        checksum = self._checksum(data_json)
        self._conn.execute(
            """INSERT INTO api_cache
               (endpoint, symbol, interval, params_hash, response_data, data_checksum, fetched_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (endpoint, symbol.upper(), interval, params_hash, data_json, checksum, time.time()),
        )
        self._conn.commit()

    def _verify_and_load(self, row: tuple) -> Any | None:
        """Load JSON from a database row with integrity check.

        FIX #7: Verify data wasn't corrupted in storage.
        """
        data_json = row[0]
        stored_checksum = row[1] if len(row) > 1 else ""

        try:
            data = json.loads(data_json)
        except (json.JSONDecodeError, ValueError) as e:
            logger.error("Corrupted data in storage (JSON parse failed): %s", e)
            return None

        # Verify checksum if available
        if stored_checksum:
            actual_checksum = self._checksum(data_json)
            if actual_checksum != stored_checksum:
                logger.error(
                    "Data integrity check FAILED! Stored checksum %s != actual %s",
                    stored_checksum, actual_checksum,
                )
                return None

        return data

    def get_cached(self, params_hash: str, ttl: int = 60) -> Any | None:
        """Get cached response if within TTL."""
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

    def get_historical(
        self,
        endpoint: str,
        symbol: str,
        hours_ago: float = 1.0,
        interval: str = "",
    ) -> dict[str, Any] | None:
        """Get the closest stored data from N hours ago.

        FIX #8: Returns dict with metadata (fetched_at, age) so caller
        always knows how old the data is.

        Returns:
            Dict with {data, fetched_at, age_minutes} or None
        """
        if not self._conn:
            return None
        target_time = time.time() - (hours_ago * 3600)
        # Find closest record to target time (within 30 min window)
        window = 1800  # 30 minutes tolerance
        query = """
            SELECT response_data, data_checksum, fetched_at FROM api_cache
            WHERE endpoint = ? AND symbol = ?
              AND fetched_at BETWEEN ? AND ?
        """
        params: list[Any] = [endpoint, symbol.upper(), target_time - window, target_time + window]
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

    def get_trend(
        self,
        endpoint: str,
        symbol: str,
        hours: float = 4.0,
        interval: str = "",
    ) -> list[dict[str, Any]]:
        """Get all stored snapshots for trend analysis.

        Returns list of {fetched_at, age_minutes, data} ordered by time.
        """
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

    def get_stats(self) -> dict[str, Any]:
        """Get storage statistics."""
        if not self._conn:
            return {"status": "not connected"}
        row = self._conn.execute("SELECT COUNT(*), MIN(fetched_at), MAX(fetched_at) FROM api_cache").fetchone()
        total = row[0]
        if total == 0:
            return {"total_records": 0, "status": "empty"}

        oldest_hours = (time.time() - row[1]) / 3600 if row[1] else 0
        newest_mins = (time.time() - row[2]) / 60 if row[2] else 0

        # Count by symbol
        symbols = self._conn.execute(
            "SELECT symbol, COUNT(*) FROM api_cache WHERE symbol != '' GROUP BY symbol ORDER BY COUNT(*) DESC LIMIT 10"
        ).fetchall()

        return {
            "total_records": total,
            "oldest_data": f"{oldest_hours:.1f} hours ago",
            "newest_data": f"{newest_mins:.1f} minutes ago",
            "retention": f"{self.retention_hours} hours",
            "top_symbols": {s: c for s, c in symbols},
            "db_path": self.db_path,
        }
