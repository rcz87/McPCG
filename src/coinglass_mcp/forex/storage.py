"""SQLite storage for forex module.

Uses a separate DB file (forex.db) in DATA_DIR to avoid interfering with the
main CoinGlass cache. Schemas:
  - myfxbook_session: cached API session token (TTL 29 days)
  - myfxbook_snapshot: historical sentiment per symbol (for sentiment_change)
  - cot_snapshot: weekly CFTC COT reports per symbol
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
import time
from typing import Any

logger = logging.getLogger("coinglass-mcp.forex")

SESSION_TTL_SECONDS = 29 * 24 * 3600  # 29 days — MyFXBook sessions live 30d


def get_db_path() -> str:
    data_dir = os.getenv("DATA_DIR", os.path.expanduser("~/.coinglass-mcp"))
    os.makedirs(data_dir, exist_ok=True)
    return os.path.join(data_dir, "forex.db")


class ForexStorage:
    """Thread-safe SQLite storage for forex data."""

    def __init__(self, db_path: str | None = None):
        self.db_path = db_path or get_db_path()
        self._lock = threading.RLock()
        self._init_schema()

    def _init_schema(self) -> None:
        with self._lock, sqlite3.connect(self.db_path) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS myfxbook_session (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    token TEXT NOT NULL,
                    created_at REAL NOT NULL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS myfxbook_snapshot (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL,
                    ts REAL NOT NULL,
                    long_pct REAL,
                    short_pct REAL,
                    long_positions INTEGER,
                    short_positions INTEGER,
                    long_volume REAL,
                    short_volume REAL,
                    avg_long_entry REAL,
                    avg_short_entry REAL,
                    raw_json TEXT
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_mfb_symbol_ts
                ON myfxbook_snapshot (symbol, ts DESC)
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS cot_snapshot (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL,
                    week_date TEXT NOT NULL,
                    noncomm_long INTEGER,
                    noncomm_short INTEGER,
                    comm_long INTEGER,
                    comm_short INTEGER,
                    net_position INTEGER,
                    fetched_at REAL NOT NULL,
                    raw_json TEXT,
                    UNIQUE(symbol, week_date)
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_cot_symbol_week
                ON cot_snapshot (symbol, week_date DESC)
            """)

    # ─── Session token cache ─────────────────────────────────────────────────

    def get_session(self) -> str | None:
        with self._lock, sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT token, created_at FROM myfxbook_session WHERE id=1"
            ).fetchone()
            if not row:
                return None
            token, created_at = row
            if time.time() - created_at > SESSION_TTL_SECONDS:
                return None
            return token

    def save_session(self, token: str) -> None:
        with self._lock, sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO myfxbook_session (id, token, created_at) VALUES (1, ?, ?)",
                (token, time.time()),
            )

    def clear_session(self) -> None:
        with self._lock, sqlite3.connect(self.db_path) as conn:
            conn.execute("DELETE FROM myfxbook_session WHERE id=1")

    # ─── Sentiment snapshots ─────────────────────────────────────────────────

    def save_snapshot(self, symbol: str, data: dict[str, Any]) -> None:
        with self._lock, sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """INSERT INTO myfxbook_snapshot
                   (symbol, ts, long_pct, short_pct, long_positions, short_positions,
                    long_volume, short_volume, avg_long_entry, avg_short_entry, raw_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    symbol.upper(), time.time(),
                    data.get("long_pct"), data.get("short_pct"),
                    data.get("long_positions"), data.get("short_positions"),
                    data.get("long_volume_usd"), data.get("short_volume_usd"),
                    data.get("avg_long_entry"), data.get("avg_short_entry"),
                    json.dumps(data, default=str),
                ),
            )

    def latest_snapshot(self, symbol: str) -> dict | None:
        with self._lock, sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                """SELECT ts, long_pct, short_pct, long_positions, short_positions,
                          long_volume, short_volume, avg_long_entry, avg_short_entry
                   FROM myfxbook_snapshot WHERE symbol=? ORDER BY ts DESC LIMIT 1""",
                (symbol.upper(),),
            ).fetchone()
            if not row:
                return None
            return {
                "ts": row[0], "long_pct": row[1], "short_pct": row[2],
                "long_positions": row[3], "short_positions": row[4],
                "long_volume_usd": row[5], "short_volume_usd": row[6],
                "avg_long_entry": row[7], "avg_short_entry": row[8],
            }

    def snapshot_near(self, symbol: str, target_ts: float, tolerance_sec: float = 1800) -> dict | None:
        """Find snapshot closest to target_ts within tolerance (default 30min)."""
        with self._lock, sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                """SELECT ts, long_pct, short_pct
                   FROM myfxbook_snapshot
                   WHERE symbol=? AND ABS(ts - ?) <= ?
                   ORDER BY ABS(ts - ?) ASC LIMIT 1""",
                (symbol.upper(), target_ts, tolerance_sec, target_ts),
            ).fetchone()
            if not row:
                return None
            return {"ts": row[0], "long_pct": row[1], "short_pct": row[2]}

    def snapshots_range(self, symbol: str, since_ts: float) -> list[dict]:
        with self._lock, sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                """SELECT ts, long_pct, short_pct, long_positions, short_positions
                   FROM myfxbook_snapshot WHERE symbol=? AND ts >= ? ORDER BY ts ASC""",
                (symbol.upper(), since_ts),
            ).fetchall()
            return [
                {"ts": r[0], "long_pct": r[1], "short_pct": r[2],
                 "long_positions": r[3], "short_positions": r[4]}
                for r in rows
            ]

    # ─── COT snapshots ───────────────────────────────────────────────────────

    def save_cot(self, symbol: str, week_date: str, data: dict) -> None:
        with self._lock, sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """INSERT OR REPLACE INTO cot_snapshot
                   (symbol, week_date, noncomm_long, noncomm_short, comm_long, comm_short,
                    net_position, fetched_at, raw_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    symbol.upper(), week_date,
                    data.get("noncomm_long"), data.get("noncomm_short"),
                    data.get("comm_long"), data.get("comm_short"),
                    (data.get("noncomm_long") or 0) - (data.get("noncomm_short") or 0),
                    time.time(), json.dumps(data, default=str),
                ),
            )

    def latest_cot(self, symbol: str) -> dict | None:
        with self._lock, sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                """SELECT symbol, week_date, noncomm_long, noncomm_short, comm_long,
                          comm_short, net_position, fetched_at
                   FROM cot_snapshot WHERE symbol=? ORDER BY week_date DESC LIMIT 1""",
                (symbol.upper(),),
            ).fetchone()
            if not row:
                return None
            return {
                "symbol": row[0], "week_date": row[1],
                "noncomm_long": row[2], "noncomm_short": row[3],
                "comm_long": row[4], "comm_short": row[5],
                "net_position": row[6], "fetched_at": row[7],
            }

    def cot_history(self, symbol: str, weeks: int = 52) -> list[dict]:
        with self._lock, sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                """SELECT week_date, noncomm_long, noncomm_short, comm_long, comm_short, net_position
                   FROM cot_snapshot WHERE symbol=? ORDER BY week_date DESC LIMIT ?""",
                (symbol.upper(), weeks),
            ).fetchall()
            return [
                {"week_date": r[0], "noncomm_long": r[1], "noncomm_short": r[2],
                 "comm_long": r[3], "comm_short": r[4], "net_position": r[5]}
                for r in rows
            ]

    def cot_all_symbols(self) -> list[str]:
        with self._lock, sqlite3.connect(self.db_path) as conn:
            rows = conn.execute("SELECT DISTINCT symbol FROM cot_snapshot").fetchall()
            return [r[0] for r in rows]
