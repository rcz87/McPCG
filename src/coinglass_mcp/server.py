"""CoinGlass MCP Server — all tools for Ricoz Scalping Framework."""

from __future__ import annotations

import json
import os
from contextlib import asynccontextmanager
from typing import Any

from dotenv import load_dotenv
from fastmcp import FastMCP

from .client import CoinGlassClient
from .config import Config

load_dotenv()

# ─── Global State ─────────────────────────────────────────────────────────────

config = Config.from_env()
client = CoinGlassClient(config)


@asynccontextmanager
async def lifespan(app):
    """Manage shared httpx client + SQLite storage lifecycle."""
    await client.start()
    try:
        yield
    finally:
        await client.close()


mcp = FastMCP(
    name="coinglass-mcp",
    instructions=(
        "CoinGlass crypto derivatives analytics for order flow trading. "
        "Provides SpotCVD, FuturesCVD, Funding Rate, Open Interest, "
        "Liquidation, Orderbook, and more — optimized for Ricoz Scalping Framework."
    ),
    lifespan=lifespan,
)


# ─── Helper Functions ─────────────────────────────────────────────────────────


def fmt(data: Any, title: str = "") -> str:
    """Format API response for Claude consumption."""
    if title:
        header = f"## {title}\n\n"
    else:
        header = ""

    if isinstance(data, list):
        if len(data) == 0:
            return f"{header}No data available."
        # Show last N entries for time series
        if len(data) > 20:
            data = data[-20:]
            header += f"*(showing last 20 of many entries)*\n\n"
        return header + json.dumps(data, indent=2, default=str)
    elif isinstance(data, dict):
        return header + json.dumps(data, indent=2, default=str)
    else:
        return header + str(data)


def fmt_cvd(data: Any, cvd_type: str) -> str:
    """Format CVD response with trading context."""
    title = f"{'Spot' if cvd_type == 'spot' else 'Futures'} CVD (Cumulative Volume Delta)"
    result = fmt(data, title)

    if cvd_type == "spot":
        result += (
            "\n\n**Trading Context (Ricoz Framework):**\n"
            "- SpotCVD POSITIVE + rising = spot buyers dominant → BULLISH bias\n"
            "- SpotCVD NEGATIVE = DO NOT LONG regardless of other signals (VETO)\n"
            "- Look at DIRECTION of line, not just absolute number\n"
            "- Compare with FuturesCVD for confluence"
        )
    else:
        result += (
            "\n\n**Trading Context (Ricoz Framework):**\n"
            "- FutCVD confirms directional bias from SpotCVD\n"
            "- FutCVD rising + SpotCVD rising = strong long setup\n"
            "- Divergence between Spot & Futures CVD = caution"
        )
    return result


# ═══════════════════════════════════════════════════════════════════════════════
# CRITICAL TOOLS (1-6) — Ricoz Scalping Framework Core
# ═══════════════════════════════════════════════════════════════════════════════


@mcp.tool()
async def coinglass_spot_cvd(
    symbol: str = "BTC",
    interval: str = "5m",
    limit: int = 100,
) -> str:
    """Get Spot CVD (Cumulative Volume Delta) — PRIMARY VETO SIGNAL.

    SpotCVD is the #1 signal in Ricoz Scalping Framework.
    - SpotCVD positive + rising = spot buyers dominant (BULLISH)
    - SpotCVD negative = DO NOT LONG regardless of other signals
    - Look at DIRECTION, not just absolute number

    Args:
        symbol: Coin symbol (BTC, ETH, SOL, HYPE, AVAX, etc.)
        interval: Candle interval (1m, 5m, 15m, 30m, 1h, 4h, 12h, 1d)
        limit: Number of data points (max 4500)
    """
    data = await client.get("/api/spot/aggregated-cvd-history", {
        "symbol": symbol,
        "interval": interval,
        "limit": limit,
    })
    return fmt_cvd(data, "spot")


@mcp.tool()
async def coinglass_futures_cvd(
    symbol: str = "BTC",
    interval: str = "5m",
    limit: int = 100,
) -> str:
    """Get Futures CVD (Cumulative Volume Delta) — ENTRY FILTER signal.

    Used together with SpotCVD to confirm directional bias.
    - Both CVDs rising = strong LONG setup
    - Both CVDs falling = strong SHORT setup
    - Divergence = caution, wait for alignment

    Args:
        symbol: Coin symbol (BTC, ETH, SOL, etc.)
        interval: Candle interval (1m, 5m, 15m, 30m, 1h, 4h, 12h, 1d)
        limit: Number of data points (max 4500)
    """
    data = await client.get("/api/futures/aggregated-cvd-history", {
        "symbol": symbol,
        "interval": interval,
        "limit": limit,
    })
    return fmt_cvd(data, "futures")


@mcp.tool()
async def coinglass_funding_rate(
    symbol: str = "BTC",
) -> str:
    """Get current Funding Rate across all exchanges.

    Funding Rate indicates market sentiment:
    - Positive FR = longs pay shorts (market bullish/overleveraged long)
    - Negative FR = shorts pay longs (market bearish/overleveraged short)
    - Extreme FR (>0.05%) = potential reversal zone
    - Near zero = neutral, good for directional trades

    Args:
        symbol: Coin symbol (BTC, ETH, SOL, etc.)
    """
    data = await client.get("/api/futures/funding-rate/exchange-list", {
        "symbol": symbol,
    })
    result = fmt(data, f"Funding Rate — {symbol}")
    result += (
        "\n\n**Trading Context:**\n"
        "- FR > 0.03% = overleveraged longs, SHORT bias\n"
        "- FR < -0.03% = overleveraged shorts, LONG bias\n"
        "- Check FR trend over 8h for better signal"
    )
    return result


@mcp.tool()
async def coinglass_open_interest(
    symbol: str = "BTC",
    interval: str = "5m",
    limit: int = 100,
) -> str:
    """Get Open Interest history (aggregated across exchanges).

    OI shows total futures positions:
    - OI rising + price rising = new longs entering (trend continuation)
    - OI rising + price falling = new shorts entering (bearish)
    - OI dropping + price rising = short squeeze
    - OI dropping + price falling = long liquidation cascade

    Args:
        symbol: Coin symbol (BTC, ETH, SOL, etc.)
        interval: Candle interval (1m, 5m, 15m, 30m, 1h, 4h, 12h, 1d)
        limit: Number of data points
    """
    data = await client.get("/api/futures/openInterest/ohlc-aggregated-history", {
        "symbol": symbol,
        "interval": interval,
        "limit": limit,
    })
    result = fmt(data, f"Open Interest — {symbol}")
    result += (
        "\n\n**Trading Context (Ricoz Framework):**\n"
        "- Compare OI change with price direction\n"
        "- Sudden OI spike = new positions, volatility incoming\n"
        "- OI dropping sharply = liquidation cascade"
    )
    return result


@mcp.tool()
async def coinglass_liquidation_map(
    symbol: str = "BTC",
) -> str:
    """Get Liquidation Map — shows where liquidation clusters are.

    Critical for identifying:
    - Magnetic zones (price tends to move toward liquidation clusters)
    - Stop loss clusters that market makers target
    - Potential reversal zones after liquidation sweeps

    Args:
        symbol: Coin symbol (BTC, ETH, SOL, etc.)
    """
    data = await client.get("/api/futures/liquidation/aggregated-map", {
        "symbol": symbol,
    })
    result = fmt(data, f"Liquidation Map — {symbol}")
    result += (
        "\n\n**Trading Context:**\n"
        "- Large liquidation clusters = magnetic targets\n"
        "- After sweep through cluster = potential reversal\n"
        "- Use for TP/SL placement"
    )
    return result


@mcp.tool()
async def coinglass_orderbook(
    symbol: str = "BTC",
    interval: str = "5m",
    limit: int = 100,
) -> str:
    """Get Aggregated Orderbook History — OBDelta (bid/ask imbalance).

    Shows orderbook depth imbalance over time:
    - More bids than asks = buying pressure (bullish)
    - More asks than bids = selling pressure (bearish)
    - Sudden bid wall = potential support
    - Sudden ask wall = potential resistance

    Args:
        symbol: Coin symbol (BTC, ETH, SOL, etc.)
        interval: Candle interval (1m, 5m, 15m, 30m, 1h, 4h, 12h, 1d)
        limit: Number of data points
    """
    data = await client.get("/api/futures/aggregated-orderbook-history", {
        "symbol": symbol,
        "interval": interval,
        "limit": limit,
    })
    result = fmt(data, f"Orderbook Delta — {symbol}")
    result += (
        "\n\n**Trading Context (Ricoz Framework):**\n"
        "- OBDelta positive = more bids, bullish pressure\n"
        "- OBDelta negative = more asks, bearish pressure\n"
        "- Combine with CVD for entry confirmation"
    )
    return result


# ═══════════════════════════════════════════════════════════════════════════════
# IMPORTANT TOOLS (7-12) — Supporting Analytics
# ═══════════════════════════════════════════════════════════════════════════════


@mcp.tool()
async def coinglass_price_ohlc(
    symbol: str = "BTC",
    interval: str = "5m",
    limit: int = 100,
) -> str:
    """Get price OHLC history from futures markets.

    Args:
        symbol: Coin symbol (BTC, ETH, SOL, etc.)
        interval: Candle interval (1m, 5m, 15m, 30m, 1h, 4h, 12h, 1d)
        limit: Number of data points
    """
    data = await client.get("/api/futures/price/ohlc-history", {
        "symbol": symbol,
        "interval": interval,
        "limit": limit,
    })
    return fmt(data, f"Price OHLC — {symbol} ({interval})")


@mcp.tool()
async def coinglass_liquidation_history(
    symbol: str = "BTC",
    interval: str = "1h",
    limit: int = 100,
) -> str:
    """Get aggregated liquidation history — volume of liquidations over time.

    Shows when and how much was liquidated:
    - High liquidation volume = volatile period
    - Long liquidations at support = potential bottom
    - Short liquidations at resistance = potential top

    Args:
        symbol: Coin symbol (BTC, ETH, SOL, etc.)
        interval: Candle interval (1m, 5m, 15m, 30m, 1h, 4h, 12h, 1d)
        limit: Number of data points
    """
    data = await client.get("/api/futures/liquidation/aggregated-history", {
        "symbol": symbol,
        "interval": interval,
        "limit": limit,
    })
    return fmt(data, f"Liquidation History — {symbol}")


@mcp.tool()
async def coinglass_long_short_ratio(
    symbol: str = "BTC",
    interval: str = "1h",
    limit: int = 100,
) -> str:
    """Get Global Long/Short Account Ratio.

    Shows retail sentiment:
    - High L/S ratio (>1.5) = too many longs, contrarian SHORT
    - Low L/S ratio (<0.7) = too many shorts, contrarian LONG
    - Use as contrarian indicator, not primary signal

    Args:
        symbol: Coin symbol (BTC, ETH, SOL, etc.)
        interval: Candle interval (1h, 4h, 12h, 1d)
        limit: Number of data points
    """
    data = await client.get("/api/futures/global-longshort-account-ratio", {
        "symbol": symbol,
        "interval": interval,
        "limit": limit,
    })
    return fmt(data, f"Long/Short Ratio — {symbol}")


@mcp.tool()
async def coinglass_taker_buysell(
    symbol: str = "BTC",
    interval: str = "5m",
    limit: int = 100,
) -> str:
    """Get Taker Buy/Sell Volume — shows aggressor side.

    Taker = market orders (aggressive traders):
    - Taker buy > sell = aggressive buyers, bullish
    - Taker sell > buy = aggressive sellers, bearish
    - Supports CVD signals

    Args:
        symbol: Coin symbol (BTC, ETH, SOL, etc.)
        interval: Candle interval (1m, 5m, 15m, 30m, 1h, 4h, 12h, 1d)
        limit: Number of data points
    """
    data = await client.get("/api/futures/taker-buysell-volume", {
        "symbol": symbol,
        "interval": interval,
        "limit": limit,
    })
    return fmt(data, f"Taker Buy/Sell — {symbol}")


@mcp.tool()
async def coinglass_fr_arbitrage() -> str:
    """Get Funding Rate Arbitrage — find extreme funding rates across coins.

    Shows coins with highest/lowest funding rates:
    - Extreme positive FR = potential short opportunity
    - Extreme negative FR = potential long opportunity
    - Good for finding FR arbitrage trades
    """
    data = await client.get("/api/futures/funding-rate/arbitrage")
    return fmt(data, "Funding Rate Arbitrage — Top Opportunities")


@mcp.tool()
async def coinglass_coins_markets() -> str:
    """Get market overview for all futures coins.

    Returns comprehensive market data including:
    - Price, 24h change, volume
    - Open Interest, FR, Long/Short ratio
    - Good for market scanning and finding opportunities
    """
    data = await client.get("/api/futures/coins-markets")
    return fmt(data, "Futures Market Overview")


# ═══════════════════════════════════════════════════════════════════════════════
# NICE-TO-HAVE TOOLS (13-17)
# ═══════════════════════════════════════════════════════════════════════════════


@mcp.tool()
async def coinglass_whale_alert(
    limit: int = 20,
) -> str:
    """Get Hyperliquid whale position alerts.

    Shows large trader positions on Hyperliquid:
    - Whale opening large long = bullish signal
    - Whale opening large short = bearish signal
    - Track whale PnL for sentiment

    Args:
        limit: Number of alerts to return
    """
    data = await client.get("/api/hyperliquid/whale-alert", {
        "limit": limit,
    })
    return fmt(data, "Whale Alerts — Hyperliquid")


@mcp.tool()
async def coinglass_fear_greed() -> str:
    """Get Fear & Greed Index history.

    Market sentiment indicator:
    - 0-25 = Extreme Fear (contrarian BUY zone)
    - 25-45 = Fear
    - 45-55 = Neutral
    - 55-75 = Greed
    - 75-100 = Extreme Greed (contrarian SELL zone)
    """
    data = await client.get("/api/index/fear-greed-history")
    return fmt(data, "Fear & Greed Index")


@mcp.tool()
async def coinglass_footprint(
    symbol: str = "BTC",
    interval: str = "1h",
    limit: int = 50,
) -> str:
    """Get Footprint chart data (90-day history max).

    Shows volume at each price level:
    - High volume nodes = support/resistance
    - Volume imbalance = directional pressure
    - Requires Standard plan or higher

    Args:
        symbol: Coin symbol (BTC, ETH, SOL, etc.)
        interval: Candle interval
        limit: Number of data points (max ~2160 for 90d at 1h)
    """
    data = await client.get("/api/futures/footprint", {
        "symbol": symbol,
        "interval": interval,
        "limit": limit,
    })
    return fmt(data, f"Footprint — {symbol}")


@mcp.tool()
async def coinglass_spot_netflow(
    symbol: str = "BTC",
    interval: str = "1h",
    limit: int = 100,
) -> str:
    """Get spot exchange net flow — coins moving in/out of exchanges.

    - Net inflow = coins deposited to exchange (potential sell pressure)
    - Net outflow = coins withdrawn (HODLing, bullish)

    Args:
        symbol: Coin symbol (BTC, ETH, SOL, etc.)
        interval: Candle interval
        limit: Number of data points
    """
    data = await client.get("/api/spot/exchange-net-flow-history", {
        "symbol": symbol,
        "interval": interval,
        "limit": limit,
    })
    return fmt(data, f"Spot Exchange Net Flow — {symbol}")


@mcp.tool()
async def coinglass_orderbook_heatmap(
    symbol: str = "BTC",
) -> str:
    """Get Orderbook Heatmap data — visual representation of order depth.

    Shows where large orders are placed:
    - Dense bid zones = potential support levels
    - Dense ask zones = potential resistance levels
    - Requires Professional plan

    Args:
        symbol: Coin symbol (BTC, ETH, SOL, etc.)
    """
    data = await client.get("/api/futures/orderbook-heatmap", {
        "symbol": symbol,
    })
    return fmt(data, f"Orderbook Heatmap — {symbol}")


# ═══════════════════════════════════════════════════════════════════════════════
# COMPOSITE TOOL (18) — Ricoz Full Scan
# ═══════════════════════════════════════════════════════════════════════════════


@mcp.tool()
async def coinglass_full_scan(
    symbol: str = "BTC",
    interval: str = "5m",
) -> str:
    """ALL-IN-ONE scan for a coin — Ricoz Scalping Framework complete analysis.

    Fetches ALL key metrics in one call:
    1. Spot CVD (VETO signal)
    2. Futures CVD (entry filter)
    3. Funding Rate (sentiment)
    4. Open Interest (positioning)
    5. Orderbook Delta (pressure)
    6. Liquidation Map (targets)
    7. Price OHLC (context)
    8. Taker Buy/Sell (aggressor)
    9. Long/Short Ratio (sentiment)

    Use this for quick comprehensive analysis before entering a trade.

    Args:
        symbol: Coin symbol (BTC, ETH, SOL, HYPE, AVAX, etc.)
        interval: Candle interval for time-series data (5m recommended for scalping)
    """
    import asyncio

    limit = 50  # Less data per metric for composite call

    # Fetch all data concurrently
    results = await asyncio.gather(
        client.get("/api/spot/aggregated-cvd-history", {
            "symbol": symbol, "interval": interval, "limit": limit,
        }),
        client.get("/api/futures/aggregated-cvd-history", {
            "symbol": symbol, "interval": interval, "limit": limit,
        }),
        client.get("/api/futures/funding-rate/exchange-list", {
            "symbol": symbol,
        }),
        client.get("/api/futures/openInterest/ohlc-aggregated-history", {
            "symbol": symbol, "interval": interval, "limit": limit,
        }),
        client.get("/api/futures/aggregated-orderbook-history", {
            "symbol": symbol, "interval": interval, "limit": limit,
        }),
        client.get("/api/futures/liquidation/aggregated-map", {
            "symbol": symbol,
        }),
        client.get("/api/futures/price/ohlc-history", {
            "symbol": symbol, "interval": interval, "limit": limit,
        }),
        client.get("/api/futures/taker-buysell-volume", {
            "symbol": symbol, "interval": interval, "limit": limit,
        }),
        client.get("/api/futures/global-longshort-account-ratio", {
            "symbol": symbol, "interval": interval, "limit": limit,
        }),
        return_exceptions=True,
    )

    labels = [
        "Spot CVD", "Futures CVD", "Funding Rate", "Open Interest",
        "Orderbook Delta", "Liquidation Map", "Price OHLC",
        "Taker Buy/Sell", "Long/Short Ratio",
    ]

    output = f"# FULL SCAN — {symbol} ({interval})\n\n"
    output += "**Ricoz Scalping Framework — Complete Analysis**\n\n"

    for label, result in zip(labels, results):
        output += f"---\n\n## {label}\n\n"
        if isinstance(result, Exception):
            output += f"*Error: {result}*\n\n"
        elif isinstance(result, list) and len(result) > 10:
            # Show only last 10 entries for time series in full scan
            output += json.dumps(result[-10:], indent=2, default=str) + "\n\n"
        else:
            output += json.dumps(result, indent=2, default=str) + "\n\n"

    output += (
        "---\n\n"
        "## Analysis Checklist (Ricoz Framework)\n\n"
        f"1. **SpotCVD**: Check direction — if negative, DO NOT LONG {symbol}\n"
        "2. **FutCVD**: Should align with SpotCVD direction\n"
        "3. **FR**: Extreme = contrarian signal\n"
        "4. **OI**: Rising + price direction = trend strength\n"
        "5. **OBDelta**: Confirm buy/sell pressure\n"
        "6. **Liq Map**: Set TP near liquidation clusters\n"
        "7. **Taker**: Confirm aggressor side\n"
        "8. **L/S Ratio**: Contrarian indicator\n"
    )
    return output


# ═══════════════════════════════════════════════════════════════════════════════
# HISTORICAL & TREND TOOLS (19-22) — Persistent Storage
# ═══════════════════════════════════════════════════════════════════════════════


# Endpoint mapping for historical lookups
ENDPOINT_MAP = {
    "spot_cvd": "/api/spot/aggregated-cvd-history",
    "futures_cvd": "/api/futures/aggregated-cvd-history",
    "funding_rate": "/api/futures/funding-rate/exchange-list",
    "open_interest": "/api/futures/openInterest/ohlc-aggregated-history",
    "orderbook": "/api/futures/aggregated-orderbook-history",
    "liquidation": "/api/futures/liquidation/aggregated-history",
    "price": "/api/futures/price/ohlc-history",
    "taker": "/api/futures/taker-buysell-volume",
    "long_short": "/api/futures/global-longshort-account-ratio",
}


@mcp.tool()
async def coinglass_compare(
    symbol: str = "BTC",
    metric: str = "spot_cvd",
    hours_ago: float = 1.0,
    interval: str = "5m",
) -> str:
    """Compare current data with historical data — see if metric went UP or DOWN.

    Example: "Compare SOL spot_cvd now vs 2 hours ago"
    This requires the metric to have been fetched before (stored in local DB).

    Args:
        symbol: Coin symbol (BTC, ETH, SOL, etc.)
        metric: One of: spot_cvd, futures_cvd, funding_rate, open_interest,
                orderbook, liquidation, price, taker, long_short
        hours_ago: How many hours back to compare (e.g., 0.5, 1, 2, 4, 8, 24)
        interval: Candle interval for time-series metrics
    """
    endpoint = ENDPOINT_MAP.get(metric)
    if not endpoint:
        available = ", ".join(ENDPOINT_MAP.keys())
        return f"Unknown metric '{metric}'. Available: {available}"

    # Get current data
    params = {"symbol": symbol, "interval": interval, "limit": 20}
    if metric == "funding_rate":
        params = {"symbol": symbol}
    try:
        current = await client.get(endpoint, params)
    except Exception as e:
        current = None
        current_err = str(e)

    # Get historical from storage
    historical = client.storage.get_historical(endpoint, symbol, hours_ago, interval)

    output = f"## Compare {metric.upper()} — {symbol}\n"
    output += f"**Now vs {hours_ago}h ago**\n\n"

    if current is not None:
        output += "### Current\n"
        if isinstance(current, list) and len(current) > 5:
            output += json.dumps(current[-5:], indent=2, default=str) + "\n\n"
        else:
            output += json.dumps(current, indent=2, default=str) + "\n\n"
    else:
        output += f"### Current\n*Error fetching: {current_err}*\n\n"

    if historical is not None:
        output += f"### {hours_ago}h Ago (from storage)\n"
        if isinstance(historical, list) and len(historical) > 5:
            output += json.dumps(historical[-5:], indent=2, default=str) + "\n\n"
        else:
            output += json.dumps(historical, indent=2, default=str) + "\n\n"
    else:
        output += (
            f"### {hours_ago}h Ago\n"
            f"*No stored data from {hours_ago}h ago. Data is stored each time you "
            f"query a metric. Keep querying periodically to build history.*\n\n"
        )

    return output


@mcp.tool()
async def coinglass_trend(
    symbol: str = "BTC",
    metric: str = "spot_cvd",
    hours: float = 4.0,
    interval: str = "5m",
) -> str:
    """Show trend of a metric over time — all stored snapshots.

    Shows how a metric changed over the past N hours based on stored data.
    Useful for: "Is OI for SOL trending up or down over last 4 hours?"

    Args:
        symbol: Coin symbol (BTC, ETH, SOL, etc.)
        metric: One of: spot_cvd, futures_cvd, funding_rate, open_interest,
                orderbook, liquidation, price, taker, long_short
        hours: How many hours of history to show (e.g., 1, 2, 4, 8, 24)
        interval: Candle interval filter
    """
    endpoint = ENDPOINT_MAP.get(metric)
    if not endpoint:
        available = ", ".join(ENDPOINT_MAP.keys())
        return f"Unknown metric '{metric}'. Available: {available}"

    snapshots = client.storage.get_trend(endpoint, symbol, hours, interval)

    output = f"## Trend {metric.upper()} — {symbol} (last {hours}h)\n\n"

    if not snapshots:
        output += (
            f"*No stored snapshots found for {symbol} {metric} in the last {hours}h.*\n\n"
            "**Tip:** Data is stored automatically each time you query a metric. "
            "Use `coinglass_spot_cvd`, `coinglass_open_interest`, etc. periodically "
            "to build up historical snapshots for trend analysis.\n"
        )
        return output

    output += f"**{len(snapshots)} snapshots found**\n\n"

    from datetime import datetime

    for i, snap in enumerate(snapshots):
        ts = datetime.fromtimestamp(snap["fetched_at"]).strftime("%H:%M:%S")
        data = snap["data"]
        # Show summary for each snapshot
        if isinstance(data, list) and len(data) > 0:
            last = data[-1] if isinstance(data[-1], dict) else data[-1]
            output += f"**{ts}** — last entry: {json.dumps(last, default=str)}\n\n"
        elif isinstance(data, dict):
            output += f"**{ts}** — {json.dumps(data, default=str)[:200]}\n\n"
        else:
            output += f"**{ts}** — {str(data)[:200]}\n\n"

    return output


@mcp.tool()
async def coinglass_storage_stats() -> str:
    """Show storage statistics — how much historical data is stored.

    Shows total records, oldest/newest data, top symbols tracked.
    Use this to check if historical data is available for trend analysis.
    """
    stats = client.storage.get_stats()
    output = "## Storage Statistics\n\n"
    output += json.dumps(stats, indent=2, default=str) + "\n\n"
    output += (
        "**How it works:**\n"
        "- Every API call is automatically stored in SQLite\n"
        "- Data is kept for 48 hours, then auto-cleaned\n"
        "- Use `coinglass_compare` to compare now vs X hours ago\n"
        "- Use `coinglass_trend` to see how a metric changed over time\n"
        "- More queries = more history = better trend analysis\n"
    )
    return output


# ═══════════════════════════════════════════════════════════════════════════════
# Server Entry Point
# ═══════════════════════════════════════════════════════════════════════════════


def main():
    """Run the MCP server."""
    transport = os.getenv("MCP_TRANSPORT", "streamable-http")

    if transport == "stdio":
        mcp.run(transport="stdio")
    else:
        mcp.run(
            transport="streamable-http",
            host=config.host,
            port=config.port,
        )


if __name__ == "__main__":
    main()
