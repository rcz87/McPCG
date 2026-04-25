"""Tests for multi-exchange CVD coverage transparency."""

import json
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from coinglass_mcp import multi_cvd


def _now_ms() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)


# ─── Helper-level tests ──────────────────────────────────────────────────────


def test_compute_coverage_btc_short_window():
    """High-volume symbol: oldest trade ~4min ago → coverage ~4.0min."""
    now = _now_ms()
    raw = {
        "binance": [{"ts_ms": now - 240_000, "delta_usd": 100.0}],  # 4 min ago
        "okx": [{"ts_ms": now - 120_000, "delta_usd": -50.0}],      # 2 min ago
        "bybit": [{"ts_ms": now - 270_000, "delta_usd": 80.0}],     # 4.5 min
        "hyperliquid": [{"ts_ms": now - 60_000, "delta_usd": 5.0}], # 1 min
    }
    cov = multi_cvd._compute_coverage(raw, now)
    assert cov["binance"]["status"] == "ok"
    assert cov["binance"]["minutes"] == pytest.approx(4.0, abs=0.1)
    assert cov["okx"]["minutes"] == pytest.approx(2.0, abs=0.1)
    assert cov["bybit"]["minutes"] == pytest.approx(4.5, abs=0.1)
    assert cov["hyperliquid"]["minutes"] == pytest.approx(1.0, abs=0.1)


def test_compute_coverage_doge_within_limit():
    """Low-volume symbol: 16min coverage → all > 10min request."""
    now = _now_ms()
    raw = {
        "binance": [{"ts_ms": now - 16 * 60_000, "delta_usd": 100.0}],
        "okx": [{"ts_ms": now - 12 * 60_000, "delta_usd": -50.0}],
        "bybit": [{"ts_ms": now - 18 * 60_000, "delta_usd": 80.0}],
        "hyperliquid": [{"ts_ms": now - 14 * 60_000, "delta_usd": 5.0}],
    }
    cov = multi_cvd._compute_coverage(raw, now)
    warning = multi_cvd._build_coverage_warning(cov, window_min=10)
    assert warning is None  # min cov 12min >= 10*0.5


def test_compute_coverage_empty_exchange():
    """One exchange returns [] → status='empty', trade_count=0."""
    now = _now_ms()
    raw = {
        "binance": [{"ts_ms": now - 120_000, "delta_usd": 100.0}],
        "okx": [],
        "bybit": [{"ts_ms": now - 100_000, "delta_usd": -50.0}],
        "hyperliquid": [{"ts_ms": now - 80_000, "delta_usd": 5.0}],
    }
    cov = multi_cvd._compute_coverage(raw, now)
    assert cov["okx"] == {"minutes": 0.0, "trade_count": 0, "status": "empty"}
    # Warning should ignore empty and use min of valid ok
    warning = multi_cvd._build_coverage_warning(cov, window_min=30)
    assert warning is not None
    assert "actual REST coverage" in warning


def test_compute_coverage_all_empty():
    """All exchanges return [] → warning 'All exchanges empty'."""
    raw = {"binance": [], "okx": [], "bybit": [], "hyperliquid": []}
    cov = multi_cvd._compute_coverage(raw, _now_ms())
    warning = multi_cvd._build_coverage_warning(cov, window_min=30)
    assert warning is not None
    assert "All exchanges returned empty" in warning


def test_build_warning_triggered_below_50pct():
    """Warning fires when min coverage < window_min * 0.5."""
    cov = {
        "binance": {"minutes": 4.0, "trade_count": 1000, "status": "ok"},
        "okx": {"minutes": 2.0, "trade_count": 500, "status": "ok"},
        "bybit": {"minutes": 4.5, "trade_count": 1000, "status": "ok"},
        "hyperliquid": {"minutes": 1.0, "trade_count": 487, "status": "ok"},
    }
    # 1.0 < 30 * 0.5 = 15 → warning
    assert multi_cvd._build_coverage_warning(cov, window_min=30) is not None
    # 1.0 < 2 * 0.5 = 1.0 is FALSE (not less than) → no warning at small window
    assert multi_cvd._build_coverage_warning(cov, window_min=2) is None


def test_format_coverage_section_includes_all_exchanges():
    cov = {
        "binance": {"minutes": 4.2, "trade_count": 1000, "status": "ok"},
        "okx": {"minutes": 0.0, "trade_count": 0, "status": "empty"},
        "bybit": {"minutes": 4.5, "trade_count": 1000, "status": "ok"},
        "hyperliquid": {"minutes": 0.9, "trade_count": 487, "status": "ok"},
    }
    text = multi_cvd._format_coverage_section(cov, window_min=30)
    assert "Binance" in text
    assert "Okx" in text and "empty / failed" in text
    assert "Bybit" in text
    assert "Hyperliquid" in text
    assert "Requested window: 30 min" in text


# ─── Public-function envelope tests ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_envelope_includes_coverage_section_and_warning():
    """End-to-end: BTC-like high-volume → coverage section + warning in envelope."""
    now = _now_ms()
    bnb = [{"ts_ms": now - 240_000, "delta_usd": 1000.0}]
    okx = [{"ts_ms": now - 120_000, "delta_usd": -500.0}]
    bybit = [{"ts_ms": now - 270_000, "delta_usd": 200.0}]
    hl = [{"ts_ms": now - 60_000, "delta_usd": 50.0}]

    async def _bnb(_): return bnb
    async def _okx(_): return okx
    async def _byb(_): return bybit
    async def _hl(_): return hl

    with patch.object(multi_cvd, "_fetch_binance_trades", _bnb), \
         patch.object(multi_cvd, "_fetch_okx_trades", _okx), \
         patch.object(multi_cvd, "_fetch_bybit_trades", _byb), \
         patch.object(multi_cvd, "_fetch_hl_trades", _hl):
        result = await multi_cvd.multi_exchange_cvd_live(
            "BTCUSDT", interval_min=5, window_min=30,
        )

    env = json.loads(result)
    # Backward compat: envelope keys still present
    assert env["status"] == "success"
    assert "warnings" in env
    assert "data" in env
    assert "timestamp" in env
    # Warning emitted (coverage ~1min < 15min threshold)
    assert len(env["warnings"]) == 1
    assert "actual REST coverage" in env["warnings"][0]
    # Coverage section embedded in markdown data
    assert "REST coverage (per exchange):" in env["data"]
    assert "Requested window: 30 min" in env["data"]


@pytest.mark.asyncio
async def test_envelope_no_warning_when_coverage_sufficient():
    """Low-volume case: 16min coverage, request 10min → no warning."""
    now = _now_ms()
    trades = [
        {"ts_ms": now - 16 * 60_000, "delta_usd": 100.0},
        {"ts_ms": now - 8 * 60_000, "delta_usd": -50.0},
        {"ts_ms": now - 2 * 60_000, "delta_usd": 80.0},
    ]

    async def _ret(_): return trades

    with patch.object(multi_cvd, "_fetch_binance_trades", _ret), \
         patch.object(multi_cvd, "_fetch_okx_trades", _ret), \
         patch.object(multi_cvd, "_fetch_bybit_trades", _ret), \
         patch.object(multi_cvd, "_fetch_hl_trades", _ret):
        result = await multi_cvd.multi_exchange_cvd_live(
            "DOGEUSDT", interval_min=1, window_min=10,
        )

    env = json.loads(result)
    assert env["status"] == "success"
    assert env["warnings"] == []
    # Coverage section still present (always rendered)
    assert "REST coverage (per exchange):" in env["data"]


@pytest.mark.asyncio
async def test_envelope_all_empty_emits_warning():
    """All exchanges return [] → failed status with 'all empty' warning."""
    async def _empty(_): return []

    with patch.object(multi_cvd, "_fetch_binance_trades", _empty), \
         patch.object(multi_cvd, "_fetch_okx_trades", _empty), \
         patch.object(multi_cvd, "_fetch_bybit_trades", _empty), \
         patch.object(multi_cvd, "_fetch_hl_trades", _empty):
        result = await multi_cvd.multi_exchange_cvd_live(
            "FAKEUSDT", interval_min=5, window_min=30,
        )

    env = json.loads(result)
    assert env["status"] == "failed"
    assert any("All exchanges returned empty" in w for w in env["warnings"])
