"""Tests for config module — includes symbol normalization."""

from coinglass_mcp.config import Config, PLAN_TIERS, INTERVAL_MAP, normalize_symbol, to_pair, DEFAULT_EXCHANGE
import pytest


def test_plan_tiers_exist():
    """All expected plan tiers should be defined."""
    assert "hobbyist" in PLAN_TIERS
    assert "startup" in PLAN_TIERS
    assert "standard" in PLAN_TIERS
    assert "professional" in PLAN_TIERS
    assert "enterprise" in PLAN_TIERS


def test_standard_plan_config():
    """Standard plan should have correct rate limit and features."""
    cfg = Config(plan="standard")
    assert cfg.rate_limit == 300
    assert cfg.has_feature("basic")
    assert cfg.has_feature("footprint")
    assert not cfg.has_feature("liquidation_heatmap")


def test_professional_plan_has_all_main_features():
    """Professional should have heatmap access."""
    cfg = Config(plan="professional")
    assert cfg.has_feature("liquidation_heatmap")
    assert cfg.rate_limit == 1200


def test_validate_interval_standard():
    """Standard plan should allow all intervals."""
    cfg = Config(plan="standard")
    assert cfg.validate_interval("5m") == "m5"
    assert cfg.validate_interval("1h") == "h1"
    assert cfg.validate_interval("1d") == "d1"


def test_validate_interval_hobbyist_rejects_1m():
    """Hobbyist plan should not allow 1m interval."""
    cfg = Config(plan="hobbyist")
    with pytest.raises(ValueError, match="not available"):
        cfg.validate_interval("1m")


def test_interval_map_completeness():
    """All standard intervals should be in the map."""
    expected = ["1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "8h", "12h", "1d", "1w"]
    for interval in expected:
        assert interval in INTERVAL_MAP


def test_config_from_env_defaults(monkeypatch):
    """Config should have sensible defaults."""
    monkeypatch.delenv("COINGLASS_API_KEY", raising=False)
    monkeypatch.delenv("COINGLASS_PLAN", raising=False)
    cfg = Config.from_env()
    assert cfg.plan == "standard"
    assert cfg.port == 8787
    assert cfg.host == "0.0.0.0"


def test_to_pair_basic():
    """to_pair should convert coin symbol to trading pair."""
    assert to_pair("BTC") == "BTCUSDT"
    assert to_pair("ETH") == "ETHUSDT"
    assert to_pair("sol") == "SOLUSDT"


def test_to_pair_from_raw():
    """to_pair should normalize first, then append USDT."""
    assert to_pair("BTCUSDT") == "BTCUSDT"
    assert to_pair("bitcoin") == "BTCUSDT"
    assert to_pair("eth/usdt") == "ETHUSDT"


def test_default_exchange():
    """Default exchange should be Binance."""
    assert DEFAULT_EXCHANGE == "Binance"
