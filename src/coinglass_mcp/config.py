"""Configuration for CoinGlass MCP Server."""

from __future__ import annotations

import os
from dataclasses import dataclass, field

# ─── Plan Tiers ───────────────────────────────────────────────────────────────

PLAN_TIERS = {
    "hobbyist": {
        "rate_limit": 30,  # req/min
        "intervals": ["4h", "8h", "12h", "1d", "1w"],
        "features": {"basic"},
    },
    "startup": {
        "rate_limit": 80,
        "intervals": ["1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "8h", "12h", "1d", "1w"],
        "features": {"basic", "whale", "extended_intervals"},
    },
    "standard": {
        "rate_limit": 300,
        "intervals": ["1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "8h", "12h", "1d", "1w"],
        "features": {"basic", "whale", "extended_intervals", "liquidation_orders", "footprint"},
    },
    "professional": {
        "rate_limit": 1200,
        "intervals": ["1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "8h", "12h", "1d", "1w"],
        "features": {
            "basic", "whale", "extended_intervals",
            "liquidation_orders", "liquidation_heatmap", "footprint",
            "orderbook_heatmap",
        },
    },
    "enterprise": {
        "rate_limit": 9999,
        "intervals": ["1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "8h", "12h", "1d", "1w"],
        "features": {"all"},
    },
}

# ─── Interval Mapping ─────────────────────────────────────────────────────────

INTERVAL_MAP = {
    "1m": "m1", "3m": "m3", "5m": "m5", "15m": "m15", "30m": "m30",
    "1h": "h1", "2h": "h2", "4h": "h4", "8h": "h8", "12h": "h12",
    "1d": "d1", "1w": "w1",
}

# ─── API Constants ────────────────────────────────────────────────────────────

BASE_URL = "https://open-api-v4.coinglass.com"
DEFAULT_TIMEOUT = 30  # seconds
CACHE_TTL = 60  # seconds


@dataclass
class Config:
    """Runtime configuration loaded from environment."""

    api_key: str = ""
    plan: str = "standard"
    host: str = "0.0.0.0"
    port: int = 8787
    cache_ttl: int = CACHE_TTL

    @classmethod
    def from_env(cls) -> Config:
        return cls(
            api_key=os.getenv("COINGLASS_API_KEY", ""),
            plan=os.getenv("COINGLASS_PLAN", "standard").lower(),
            host=os.getenv("MCP_HOST", "0.0.0.0"),
            port=int(os.getenv("MCP_PORT", "8787")),
            cache_ttl=int(os.getenv("CACHE_TTL", str(CACHE_TTL))),
        )

    @property
    def rate_limit(self) -> int:
        return PLAN_TIERS.get(self.plan, PLAN_TIERS["standard"])["rate_limit"]

    @property
    def allowed_intervals(self) -> list[str]:
        return PLAN_TIERS.get(self.plan, PLAN_TIERS["standard"])["intervals"]

    @property
    def features(self) -> set[str]:
        return PLAN_TIERS.get(self.plan, PLAN_TIERS["standard"])["features"]

    def has_feature(self, feature: str) -> bool:
        feats = self.features
        return "all" in feats or feature in feats

    def validate_interval(self, interval: str) -> str:
        """Validate and return the API interval string."""
        if interval not in self.allowed_intervals:
            allowed = ", ".join(self.allowed_intervals)
            raise ValueError(
                f"Interval '{interval}' not available on {self.plan} plan. "
                f"Available: {allowed}"
            )
        return INTERVAL_MAP.get(interval, interval)
