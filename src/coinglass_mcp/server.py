"""CoinGlass MCP Server — hardened for trading-critical data integrity.

AUDIT COMPLIANCE:
- Every response includes data_age + staleness warnings
- full_scan blocks analysis if critical metrics fail
- Symbol normalization prevents silent mismatches
- Rate-limited requests prevent 429 errors
- API key never appears in any output
- Interpretation hints for every metric

Architecture (split for maintainability):
- server.py                     — This file: MCP init, lifespan, chart tool, registration
- formatters.py                 — All formatting helpers + shared decorator (pure functions)
- coinglass_tools.py            — 17 core CoinGlass endpoint tools
- coinglass_category_tools.py   — 12 multi-action category tools
- coinglass_composite_tools.py  — 5 composite tools (screener, full_scan, etc.)
- data_binance_tool.py          — data_binance all-in-one tool
- client.py                     — CoinGlass HTTP client (httpx)
- binance_client.py             — Binance HTTP client (httpx, no API key)
- binance_spot.py               — 8 spot market data tools
- binance_futures.py            — 11 futures market data tools
- config.py                     — CoinGlass config + symbol normalization
- storage.py                    — SQLite persistent cache
- arkham.py                     — Arkham Intel on-chain tools
- nansen.py                     — Nansen Smart Money tools
- backtest.py                   — SMC Backtest engine
- chart.py                      — TradingView chart generation
"""

from __future__ import annotations

import base64
import os
import time
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastmcp import FastMCP
from fastmcp.server.auth import AuthProvider, AccessToken
from mcp.types import ImageContent, TextContent
from starlette.requests import Request
from starlette.responses import JSONResponse

from .arkham import register_arkham_tools
from .backtest import register_backtest_tools
from .binance_client import close_client as close_binance_client
from .chart import get_chart
from .client import CoinGlassClient
from .coinglass_category_tools import register_coinglass_category_tools
from .coinglass_composite_tools import register_coinglass_composite_tools
from .coinglass_tools import register_coinglass_tools
from .config import Config
from .data_binance_tool import register_data_binance_tool
from .formatters import _tool_envelope, _err
from .nansen import register_nansen_tools

load_dotenv()

_SERVER_START_TIME = time.time()

# ─── Global State ─────────────────────────────────────────────────────────────

config = Config.from_env()
client = CoinGlassClient(config)


# ─── Bearer Token Auth ────────────────────────────────────────────────────────

_AUTH_TOKEN = os.getenv("MCP_AUTH_TOKEN", "")


class BearerTokenAuth(AuthProvider):
    """Simple bearer token auth — validates against MCP_AUTH_TOKEN env var."""

    async def verify_token(self, token: str) -> AccessToken | None:
        if token == _AUTH_TOKEN:
            return AccessToken(token=token, client_id="ricoz", scopes=[])
        return None


@asynccontextmanager
async def lifespan(app):
    """Manage shared httpx client + SQLite storage lifecycle."""
    await client.start()
    try:
        yield
    finally:
        await client.close()
        await close_binance_client()


mcp = FastMCP(
    name="coinglass-mcp",
    instructions=(
        "CoinGlass + Binance crypto derivatives analytics for order flow trading. "
        "CoinGlass: SpotCVD, FuturesCVD, Funding Rate, Open Interest, Liquidation, Orderbook. "
        "Binance: Direct market data — spot prices, futures OI, funding rate, L/S ratio, taker volume, klines, depth. "
        "Optimized for Ricoz Scalping Framework. "
        "IMPORTANT: Always check data_age in CoinGlass responses. "
        "Data older than 2 minutes has WARNING. Data older than 5 minutes must NOT be used for entries."
    ),
    lifespan=lifespan,
    # Auth disabled — claude.ai connector only supports OAuth, not Bearer tokens.
    # Token kept in .env for future use when claude.ai adds header support.
    # auth=BearerTokenAuth() if _AUTH_TOKEN else None,
)


# ─── Health Check Endpoint ────────────────────────────────────────────────────


@mcp.custom_route("/health", methods=["GET"])
async def health_check(request: Request) -> JSONResponse:
    """Health check — no auth required, used by monitoring cron."""
    uptime = time.time() - _SERVER_START_TIME
    return JSONResponse({
        "status": "ok",
        "server": "coinglass-mcp",
        "uptime_seconds": round(uptime),
        "uptime_human": f"{int(uptime // 3600)}h {int((uptime % 3600) // 60)}m",
        "tools": 69,
        "auth_enabled": False,
        "timestamp": int(time.time()),
    })

# ═══════════════════════════════════════════════════════════════════════════════
# Register All Tools
# ═══════════════════════════════════════════════════════════════════════════════

# ─── Arkham Intel Tools ───────────────────────────────────────────────────────
register_arkham_tools(mcp)

# ─── Nansen Smart Money Tools ────────────────────────────────────────────────
register_nansen_tools(mcp)

# ─── SMC Backtest Tools ──────────────────────────────────────────────────────
register_backtest_tools(mcp)

# ─── CoinGlass Core Tools (17 tools) ─────────────────────────────────────────
register_coinglass_tools(mcp, client, config)

# ─── CoinGlass Category Tools (12 tools) ─────────────────────────────────────
register_coinglass_category_tools(mcp, client)

# ─── CoinGlass Composite Tools (5 tools) ─────────────────────────────────────
register_coinglass_composite_tools(mcp, client, config)

# ─── Data Binance Composite Tool ─────────────────────────────────────────────
register_data_binance_tool(mcp)


# ─── Chart Tool ──────────────────────────────────────────────────────────────


@mcp.tool(output_schema=None)
@_tool_envelope()
async def coinglass_chart(
    symbol: str = "BTC",
    interval: str = "5m",
    width: int = 1200,
    height: int = 600,
    theme: str = "dark",
):
    """Generate TradingView chart screenshot — EMA21 + EMA50 + VWAP + Volume.

    Returns chart IMAGE directly (Claude can see it) + metadata text.
    Setup: Ricoz chart (dark theme, EMA21 cyan, EMA50 white, VWAP yellow)
    Auto-adapts to plan limits (paid: 1200x600 + Volume, free: 800x600).
    Timezone: WIB (Asia/Jakarta)

    Args:
        symbol: Coin symbol (SOL, BTC, ETH, HYPE, AVAX, SUI, XRP, BNB, etc.)
        interval: Candle interval (1m, 5m, 15m, 30m, 1h, 4h, 1d)
        width: Chart width px (default 1200, auto-downsized on free plan)
        height: Chart height px (default 600)
        theme: dark or light (default dark)
    """
    result = await get_chart(
        symbol=symbol, interval=interval,
        width=width, height=height, theme=theme,
    )
    if "error" in result:
        return _err(f"## Chart Error — {symbol}\n\nError: {result['error']}")

    # Download image so Claude can see it directly
    import httpx
    try:
        async with httpx.AsyncClient(timeout=15) as http_client:
            img_resp = await http_client.get(result["url"])
            img_resp.raise_for_status()
            img_b64 = base64.b64encode(img_resp.content).decode("utf-8")
    except Exception:
        # Fallback to URL-only if download fails
        return (
            f"## TradingView Chart — {symbol} | {interval}\n\n"
            f"**Chart URL:** {result['url']}\n"
            f"*(Image download failed — open URL manually)*"
        )

    meta = (
        f"## TradingView Chart — {symbol} | {interval}\n"
        f"**Symbol:** {result['symbol_tv']} | "
        f"**Setup:** EMA21 (cyan) + EMA50 (white) + VWAP (yellow) + Volume | "
        f"**Theme:** {theme} | **Timezone:** WIB"
    )
    return [
        TextContent(type="text", text=meta),
        ImageContent(type="image", data=img_b64, mimeType="image/png"),
    ]


# ─── Binance Spot + Futures Tools ────────────────────────────────────────────

from .binance_spot import (
    binance_spot_price,
    binance_spot_depth,
    binance_spot_klines,
    binance_spot_trades,
    binance_spot_agg_trades,
    binance_spot_ticker_24h,
    binance_spot_book_ticker,
    binance_spot_avg_price,
)

from .binance_futures import (
    binance_futures_price,
    binance_futures_funding_rate,
    binance_futures_open_interest,
    binance_futures_oi_history,
    binance_futures_long_short_ratio,
    binance_futures_top_ls_ratio,
    binance_futures_taker_volume,
    binance_futures_klines,
    binance_futures_depth,
    binance_futures_ticker_24h,
    binance_futures_liquidation,
)

# Register Binance Spot tools
mcp.tool()(binance_spot_price)
mcp.tool()(binance_spot_depth)
mcp.tool()(binance_spot_klines)
mcp.tool()(binance_spot_trades)
mcp.tool()(binance_spot_agg_trades)
mcp.tool()(binance_spot_ticker_24h)
mcp.tool()(binance_spot_book_ticker)
mcp.tool()(binance_spot_avg_price)

# Register Binance Futures tools
mcp.tool()(binance_futures_price)
mcp.tool()(binance_futures_funding_rate)
mcp.tool()(binance_futures_open_interest)
mcp.tool()(binance_futures_oi_history)
mcp.tool()(binance_futures_long_short_ratio)
mcp.tool()(binance_futures_top_ls_ratio)
mcp.tool()(binance_futures_taker_volume)
mcp.tool()(binance_futures_klines)
mcp.tool()(binance_futures_depth)
mcp.tool()(binance_futures_ticker_24h)
mcp.tool()(binance_futures_liquidation)


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
            stateless_http=True,
        )


if __name__ == "__main__":
    main()
