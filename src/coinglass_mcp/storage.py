"""SQLite persistent storage for historical data and caching."""

from __future__ import annotations

import json
import os
import sqlite3
import time
from typing import Any


class Storage:
    """SQLite-based persistent storage for CoinGlass data.

    Stores every API response with metadata for:
    - Persistent caching (survives server restart)
    - Historical queries ("CVD 3 hours ago")
    - Trend comparison ("OI naik atau turun dari tadi pagi")
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
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS api_cache (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                endpoint TEXT NOT NULL,
                symbol TEXT DEFAULT '',
                interval TEXT DEFAULT '',
                params_hash TEXT NOT NULL,
                response_data TEXT NOT NULL,
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
        self._conn.execute("DELETE FROM api_cache WHERE fetched_at < ?", (cutoff,))
        self._conn.commit()

    def store(
        self,
        endpoint: str,
        params_hash: str,
        data: Any,
        symbol: str = "",
        interval: str = "",
    ) -> None:
        """Store an API response."""
        if not self._conn:
            return
        self._conn.execute(
            """INSERT INTO api_cache (endpoint, symbol, interval, params_hash, response_data, fetched_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (endpoint, symbol.upper(), interval, params_hash, json.dumps(data, default=str), time.time()),
        )
        self._conn.commit()

    def get_cached(self, params_hash: str, ttl: int = 60) -> Any | None:
        """Get cached response if within TTL."""
        if not self._conn:
            return None
        cutoff = time.time() - ttl
        row = self._conn.execute(
            """SELECT response_data FROM api_cache
               WHERE params_hash = ? AND fetched_at > ?
               ORDER BY fetched_at DESC LIMIT 1""",
            (params_hash, cutoff),
        ).fetchone()
        if row:
            return json.loads(row[0])
        return None

    def get_historical(
        self,
        endpoint: str,
        symbol: str,
        hours_ago: float = 1.0,
        interval: str = "",
    ) -> Any | None:
        """Get the closest stored data from N hours ago.

        Args:
            endpoint: API endpoint
            symbol: Coin symbol
            hours_ago: How many hours back to look
            interval: Candle interval filter (optional)

        Returns:
            Stored response data or None
        """
        if not self._conn:
            return None
        target_time = time.time() - (hours_ago * 3600)
        # Find closest record to target time (within 30 min window)
        window = 1800  # 30 minutes tolerance
        query = """
            SELECT response_data, fetched_at FROM api_cache
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
            return json.loads(row[0])
        return None

    def get_trend(
        self,
        endpoint: str,
        symbol: str,
        hours: float = 4.0,
        interval: str = "",
    ) -> list[dict[str, Any]]:
        """Get all stored snapshots for trend analysis.

        Returns list of {fetched_at, data} ordered by time.

        Args:
            endpoint: API endpoint
            symbol: Coin symbol
            hours: How many hours of history
            interval: Candle interval filter (optional)
        """
        if not self._conn:
            return []
        cutoff = time.time() - (hours * 3600)
        query = """
            SELECT response_data, fetched_at FROM api_cache
            WHERE endpoint = ? AND symbol = ?
              AND fetched_at > ?
        """
        params: list[Any] = [endpoint, symbol.upper(), cutoff]
        if interval:
            query += " AND interval = ?"
            params.append(interval)
        query += " ORDER BY fetched_at ASC"

        rows = self._conn.execute(query, params).fetchall()
        return [
            {"fetched_at": row[1], "data": json.loads(row[0])}
            for row in rows
        ]

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
