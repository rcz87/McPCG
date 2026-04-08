"""Configuration for CoinGlass MCP Server."""

from __future__ import annotations

import json as _json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta

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
DEFAULT_TIMEOUT = 8  # seconds — tight for MCP; retries handle transient failures
CACHE_TTL = 60  # seconds
DEFAULT_EXCHANGE = "Binance"  # Default exchange for endpoints that require it

# Stale data thresholds (seconds)
STALE_WARNING_THRESHOLD = 120   # 2 min → WARNING
STALE_EXPIRED_THRESHOLD = 300   # 5 min → DO NOT USE

# Rate limiter
MIN_REQUEST_SPACING = 0.1  # 100ms between requests (was 200ms — per-minute counter is the real guard)

# ─── Symbol Normalization ────────────────────────────────────────────────────

SYMBOL_ALIASES = {
    "BITCOIN": "BTC",
    "ETHEREUM": "ETH",
    "SOLANA": "SOL",
    "RIPPLE": "XRP",
    "CARDANO": "ADA",
    "DOGECOIN": "DOGE",
    "AVALANCHE": "AVAX",
    "POLKADOT": "DOT",
    "CHAINLINK": "LINK",
    "POLYGON": "MATIC",
    "LITECOIN": "LTC",
    "UNISWAP": "UNI",
    "COSMOS": "ATOM",
    "NEAR": "NEAR",
    "ARBITRUM": "ARB",
    "OPTIMISM": "OP",
    "FANTOM": "FTM",
    "INJECTIVE": "INJ",
    "CELESTIA": "TIA",
    "SEI": "SEI",
    "SUI": "SUI",
    "APTOS": "APT",
    "PEPE": "PEPE",
    "BONK": "BONK",
    "WIF": "WIF",
    "HYPERLIQUID": "HYPE",
    "JUPITER": "JUP",
    "ONDO": "ONDO",
    "PENDLE": "PENDLE",
}

STRIP_SUFFIXES = [
    "/USDT", "/USD", "/BUSD", "/USDC",
    "-PERP", "-SWAP", ".P",
    "USDT", "BUSD", "USDC", "USD",
    "PERP",
]


def normalize_symbol(raw: str) -> str:
    """Normalize a trading symbol for CoinGlass API.

    Handles: btc → BTC, BTCUSDT → BTC, BTC/USDT → BTC, btc-perp → BTC,
    bitcoin → BTC, etc.
    """
    s = raw.strip().upper()
    # Check aliases first (BITCOIN → BTC)
    if s in SYMBOL_ALIASES:
        return SYMBOL_ALIASES[s]
    # Strip common suffixes (longest first to avoid partial matches)
    for suffix in sorted(STRIP_SUFFIXES, key=len, reverse=True):
        if s.endswith(suffix) and len(s) > len(suffix):
            s = s[: -len(suffix)]
            break
    return s


# Coins that use "1000x" denomination on exchanges
THOUSAND_COINS = {
    "PEPE", "SHIB", "FLOKI", "BONK", "LUNC", "SATS", "RATS", "CAT",
    "CHEEMS", "MOGGO", "APU", "WHY", "X", "STARL", "BABYDOGE",
}


def to_cg_symbol(symbol: str) -> str:
    """Convert coin symbol to CoinGlass-compatible format.

    Most coins: BTC → BTC, ETH → ETH
    1000x coins: PEPE → 1000PEPE, SHIB → 1000SHIB, etc.
    """
    sym = normalize_symbol(symbol)
    if sym in THOUSAND_COINS:
        return f"1000{sym}"
    return sym


def to_pair(symbol: str, quote: str = "USDT") -> str:
    """Convert coin symbol to trading pair for endpoints that need pair format.

    BTC → BTCUSDT, ETH → ETHUSDT, PEPE → 1000PEPEUSDT, etc.
    """
    return f"{to_cg_symbol(symbol)}{quote}"


# ─── Config Dataclass ────────────────────────────────────────────────────────


@dataclass
class Config:
    """Runtime configuration loaded from environment."""

    api_key: str = ""
    plan: str = "standard"
    host: str = "0.0.0.0"
    port: int = 8787
    cache_ttl: int = CACHE_TTL
    auth_token: str = ""  # Bearer token for MCP access control

    @classmethod
    def from_env(cls) -> Config:
        return cls(
            api_key=os.getenv("COINGLASS_API_KEY", ""),
            plan=os.getenv("COINGLASS_PLAN", "standard").lower(),
            host=os.getenv("MCP_HOST", "0.0.0.0"),
            port=int(os.getenv("MCP_PORT", "8787")),
            cache_ttl=int(os.getenv("CACHE_TTL", str(CACHE_TTL))),
            auth_token=os.getenv("MCP_AUTH_TOKEN", ""),
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


# ─── Standard Response Envelope ─────────────────────────────────────────────

_WIB = timezone(timedelta(hours=7))


def make_envelope(
    status: str,
    source: str,
    data: str,
    data_age_seconds: float = 0.0,
    warnings: list[str] | None = None,
    failed_endpoints: list[str] | None = None,
    fallback_suggestion: str = "",
    access: dict[str, str] | None = None,
) -> str:
    """Wrap tool output in standard response envelope.

    Args:
        status: "success" | "partial" | "failed"
        source: "coinglass" | "binance" | "arkham" | "nansen"
        data: The formatted content (markdown text)
        data_age_seconds: How old the data is (0 = live)
        warnings: List of warning messages
        failed_endpoints: For partial failures — which endpoints failed
        fallback_suggestion: Suggested alternative tool on failure
        access: Plan-gated metadata {required_plan, fallback_tool}
    """
    envelope: dict = {
        "status": status,
        "source": source,
        "timestamp": datetime.now(_WIB).isoformat(),
        "data_age_seconds": round(data_age_seconds, 1),
        "warnings": warnings or [],
        "data": data,
    }
    if failed_endpoints:
        envelope["failed_endpoints"] = failed_endpoints
    if fallback_suggestion:
        envelope["fallback_suggestion"] = fallback_suggestion
    if access:
        envelope["access"] = access
    return _json.dumps(envelope, ensure_ascii=False)
