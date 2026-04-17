"""Rate limiter tests — daily cap, persistence, reset behavior."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from coinglass_mcp.forex.rate_limiter import (
    BLOCK_THRESHOLD,
    DAILY_CAP,
    DailyRateLimiter,
    RateLimitExceeded,
)


@pytest.fixture
def tmp_db(tmp_path: Path) -> str:
    return str(tmp_path / "forex_test.db")


def test_counter_increments(tmp_db: str):
    rl = DailyRateLimiter(tmp_db, scope="test")
    assert rl.current() == 0
    assert rl.check_and_increment() == 1
    assert rl.check_and_increment() == 2
    assert rl.current() == 2


def test_persists_across_instances(tmp_db: str):
    rl1 = DailyRateLimiter(tmp_db, scope="test")
    rl1.check_and_increment()
    rl1.check_and_increment()
    rl2 = DailyRateLimiter(tmp_db, scope="test")
    assert rl2.current() == 2
    assert rl2.check_and_increment() == 3


def test_blocks_at_threshold(tmp_db: str):
    rl = DailyRateLimiter(tmp_db, scope="test")
    for _ in range(BLOCK_THRESHOLD):
        rl.check_and_increment()
    assert rl.current() == BLOCK_THRESHOLD
    with pytest.raises(RateLimitExceeded):
        rl.check_and_increment()


def test_status_shape(tmp_db: str):
    rl = DailyRateLimiter(tmp_db, scope="test")
    rl.check_and_increment()
    st = rl.status()
    assert st["used"] == 1
    assert st["cap"] == DAILY_CAP
    assert st["remaining"] == DAILY_CAP - 1
    assert "seconds_to_reset" in st and st["seconds_to_reset"] > 0
