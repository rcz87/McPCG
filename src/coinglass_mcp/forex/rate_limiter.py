"""Persistent daily rate limiter for MyFXBook (100 req/day cap).

Persisted to SQLite so PM2 restarts don't reset the counter mid-day.
Resets at UTC midnight. Hard-blocks at 95% to leave buffer for manual debug calls.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
import time
from datetime import datetime, timezone

logger = logging.getLogger("coinglass-mcp.forex")

DAILY_CAP = 100
WARN_THRESHOLD = 80
BLOCK_THRESHOLD = 95


class RateLimitExceeded(Exception):
    """Raised when daily cap reached."""


class DailyRateLimiter:
    """UTC-day counter persisted in SQLite. Thread-safe via RLock."""

    def __init__(self, db_path: str, scope: str = "myfxbook"):
        self.db_path = db_path
        self.scope = scope
        self._lock = threading.RLock()
        self._ensure_table()

    def _ensure_table(self) -> None:
        with self._lock, sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS forex_rate_limit (
                    scope TEXT NOT NULL,
                    utc_date TEXT NOT NULL,
                    count INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (scope, utc_date)
                )
            """)

    @staticmethod
    def _utc_date() -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def current(self) -> int:
        with self._lock, sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT count FROM forex_rate_limit WHERE scope=? AND utc_date=?",
                (self.scope, self._utc_date()),
            ).fetchone()
            return row[0] if row else 0

    def check_and_increment(self) -> int:
        """Atomically check + increment counter. Raises RateLimitExceeded at BLOCK_THRESHOLD."""
        with self._lock, sqlite3.connect(self.db_path) as conn:
            today = self._utc_date()
            conn.execute(
                "INSERT OR IGNORE INTO forex_rate_limit (scope, utc_date, count) VALUES (?, ?, 0)",
                (self.scope, today),
            )
            row = conn.execute(
                "SELECT count FROM forex_rate_limit WHERE scope=? AND utc_date=?",
                (self.scope, today),
            ).fetchone()
            count = row[0] if row else 0
            if count >= BLOCK_THRESHOLD:
                raise RateLimitExceeded(
                    f"MyFXBook daily cap near limit ({count}/{DAILY_CAP}). "
                    f"Resets at UTC midnight. Wait or rely on cached data."
                )
            new_count = count + 1
            conn.execute(
                "UPDATE forex_rate_limit SET count=? WHERE scope=? AND utc_date=?",
                (new_count, self.scope, today),
            )
            if new_count == WARN_THRESHOLD:
                logger.warning(
                    "MyFXBook usage at %d/%d for %s — approaching daily cap",
                    new_count, DAILY_CAP, today,
                )
            return new_count

    def status(self) -> dict:
        count = self.current()
        return {
            "used": count,
            "cap": DAILY_CAP,
            "remaining": max(0, DAILY_CAP - count),
            "warn_at": WARN_THRESHOLD,
            "block_at": BLOCK_THRESHOLD,
            "utc_date": self._utc_date(),
            "seconds_to_reset": _seconds_to_utc_midnight(),
        }


def _seconds_to_utc_midnight() -> int:
    now = datetime.now(timezone.utc)
    tomorrow = now.replace(hour=0, minute=0, second=0, microsecond=0)
    tomorrow = tomorrow.replace(day=now.day) if now.hour == 0 else tomorrow
    # next UTC midnight
    from datetime import timedelta
    nm = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return int((nm - now).total_seconds())
