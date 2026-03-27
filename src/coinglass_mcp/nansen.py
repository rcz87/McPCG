"""Nansen API Integration — Smart Money on-chain analytics for Ricoz Trading Framework.

Adds smart money intelligence to complement CoinGlass + Arkham data:
- Token screener with smart money filters (who's buying/selling)
- Multi-chain coverage (Ethereum, Solana, Base, Arbitrum, BSC, etc.)
- Buy/sell volume, netflow, liquidity, market cap data

Base URL: https://api.nansen.ai
Auth: apiKey header (set NANSEN_API_KEY in .env)
Endpoint: POST /api/v1/token-screener
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone, timedelta
from typing import Any

import httpx

WIB = timezone(timedelta(hours=7))

NANSEN_BASE_URL = "https://api.nansen.ai"
NANSEN_TIMEOUT = 10.0

# Supported chains
NANSEN_CHAINS = [
    "ethereum", "solana", "base", "arbitrum", "polygon",
    "bsc", "avalanche", "optimism",
]

# Valid sort fields (tested against API)
VALID_SORT_FIELDS = [
    "buy_volume", "sell_volume", "netflow", "volume",
    "nof_traders", "market_cap_usd", "price_change", "liquidity",
]


def _get_nansen_key() -> str:
    """Get Nansen API key from environment."""
    key = os.getenv("NANSEN_API_KEY", "")
    if not key:
        raise ValueError(
            "NANSEN_API_KEY not set. Add it to your .env file:\n"
            "NANSEN_API_KEY=your_key_here"
        )
    return key


def _mask_key(text: str, key: str) -> str:
    """Mask API key in error messages."""
    if key and len(key) > 8:
        masked = key[:4] + "****" + key[-4:]
        return text.replace(key, masked)
    return text


async def nansen_post(
    endpoint: str,
    body: dict,
) -> dict:
    """Execute Nansen API POST request with auth header.

    Returns dict with keys: data, pagination, status, error
    """
    try:
        key = _get_nansen_key()
    except ValueError as e:
        return {"data": None, "pagination": None, "status": "error", "error": str(e)}

    url = f"{NANSEN_BASE_URL}{endpoint}"
    headers = {
        "Content-Type": "application/json",
        "apiKey": key,
    }

    try:
        async with httpx.AsyncClient(timeout=NANSEN_TIMEOUT) as client:
            resp = await client.post(url, json=body, headers=headers)

        if resp.status_code == 401:
            return {
                "data": None, "pagination": None, "status": "error",
                "error": "Invalid NANSEN_API_KEY. Check your key."
            }
        elif resp.status_code == 403:
            body_text = resp.text[:200]
            if "credits" in body_text.lower():
                return {
                    "data": None, "pagination": None, "status": "error",
                    "error": "Insufficient Nansen credits. Top up your plan."
                }
            return {
                "data": None, "pagination": None, "status": "error",
                "error": f"Access denied (403): {body_text}"
            }
        elif resp.status_code == 422:
            return {
                "data": None, "pagination": None, "status": "error",
                "error": f"Invalid request: {resp.text[:300]}"
            }
        elif resp.status_code == 429:
            return {
                "data": None, "pagination": None, "status": "error",
                "error": "Rate limit exceeded. Wait before retrying."
            }
        elif resp.status_code != 200:
            return {
                "data": None, "pagination": None, "status": "error",
                "error": f"HTTP {resp.status_code}: {resp.text[:200]}"
            }

        result = resp.json()
        return {
            "data": result.get("data", []),
            "pagination": result.get("pagination"),
            "status": "ok",
            "error": None,
        }

    except httpx.TimeoutException:
        return {"data": None, "pagination": None, "status": "error", "error": f"Timeout after {NANSEN_TIMEOUT}s"}
    except Exception as e:
        return {"data": None, "pagination": None, "status": "error", "error": _mask_key(str(e), os.getenv("NANSEN_API_KEY", ""))}


# ─── Nansen Token Flow Intelligence (REST API) ──────────────────────────────

NANSEN_TGM_URL = "https://api.nansen.ai/api/v1/tgm/flow-intelligence"

# Token address mapping for common coins
NANSEN_TOKEN_MAP = {
    "SOL": ("solana", "So11111111111111111111111111111111111111112"),
    "ETH": ("ethereum", "0xeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"),
    "USDT": ("ethereum", "0xdac17f958d2ee523a2206206994597c13d831ec7"),
    "USDC": ("ethereum", "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"),
    "WBTC": ("ethereum", "0x2260fac5e5542a773aa44fbcfedf7c193bc2c599"),
    "LINK": ("ethereum", "0x514910771af9ca656af840dff83e8264ecf986ca"),
    "UNI": ("ethereum", "0x1f9840a85d5af5bf1d1762f925bdaddc4201f984"),
    "AAVE": ("ethereum", "0x7fc66500c84a76ad7e9c93437bfc5ac33e2ddae9"),
    "ARB": ("arbitrum", "0x912ce59144191c1204e64559fe8253a0e49e6548"),
    "OP": ("optimism", "0x4200000000000000000000000000000000000042"),
    "MATIC": ("polygon", "0x0000000000000000000000000000000000001010"),
    "AVAX": ("avalanche", "0xeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"),
    "BNB": ("bnb", "0xeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"),
    "SUI": ("sui", "0x2::sui::SUI"),
    "XRP": ("xrp", "XRP"),
}


async def nansen_token_flow_intelligence(symbol: str) -> dict | None:
    """Call Nansen REST API tgm/flow-intelligence (1 credit).

    Returns raw dict with segment flows, or None on failure.
    """
    sym = symbol.upper()
    if sym not in NANSEN_TOKEN_MAP:
        return None

    chain, token_addr = NANSEN_TOKEN_MAP[sym]
    try:
        key = _get_nansen_key()
    except ValueError:
        return None

    try:
        async with httpx.AsyncClient(timeout=NANSEN_TIMEOUT) as c:
            resp = await c.post(NANSEN_TGM_URL, headers={
                "Content-Type": "application/json",
                "apiKey": key,
            }, json={
                "chain": chain,
                "token_address": token_addr,
            })
            if resp.status_code != 200:
                return None
            result = resp.json()
            data = result.get("data", [])
            if isinstance(data, list) and data:
                return data[0]
            return None
    except Exception:
        return None


def _header(info: str) -> str:
    """Standard header for all Nansen responses."""
    ts = datetime.now(WIB).strftime("%H:%M:%S WIB")
    return f"**Source:** Nansen | **Info:** {info} | **Time:** {ts}\n\n"


def _format_usd(val: float | None) -> str:
    if val is None:
        return "N/A"
    if abs(val) >= 1_000_000_000:
        return f"${val/1_000_000_000:.2f}B"
    elif abs(val) >= 1_000_000:
        return f"${val/1_000_000:.2f}M"
    elif abs(val) >= 1_000:
        return f"${val/1_000:.1f}K"
    return f"${val:.2f}"


def register_nansen_tools(mcp):
    """Register all Nansen tools on the FastMCP instance."""

    # ─── 1. Smart Money Token Screener ────────────────────────────────────────

    @mcp.tool()
    async def nansen_smart_money(
        chains: str = "ethereum,solana,base",
        timeframe: str = "24h",
        sort_by: str = "buy_volume",
        sort_dir: str = "DESC",
        min_mcap: float = 0,
        max_mcap: float = 0,
        min_liquidity: float = 0,
        min_age_days: int = 1,
        max_age_days: int = 365,
        limit: int = 20,
    ) -> str:
        """Nansen Smart Money screener — what are whales buying/selling right now?

        Shows tokens with highest smart money activity across chains.
        Smart money = wallets labeled by Nansen as funds, whales, and profitable traders.

        Args:
            chains: Comma-separated chains (ethereum, solana, base, arbitrum, polygon, bsc, avalanche, optimism)
            timeframe: Time window (24h, 7d, 30d)
            sort_by: Sort field — buy_volume, sell_volume, netflow, volume, nof_traders, market_cap_usd, price_change, liquidity
            sort_dir: Sort direction — DESC (highest first) or ASC (lowest first)
            min_mcap: Minimum market cap USD filter (0 = no filter)
            max_mcap: Maximum market cap USD filter (0 = no filter)
            min_liquidity: Minimum liquidity USD (0 = no filter)
            min_age_days: Minimum token age in days (default 1 = skip <1 day old)
            max_age_days: Maximum token age in days (default 365)
            limit: Number of tokens to return (max 100)

        Use cases:
        - Find what smart money is accumulating before retail
        - Detect distribution (high sell_volume) = bearish signal
        - Cross-reference with CoinGlass OI/CVD for high-conviction trades
        - Sort by netflow to see strongest accumulation
        """
        chain_list = [c.strip() for c in chains.split(",") if c.strip()]

        if sort_by not in VALID_SORT_FIELDS:
            sort_by = "buy_volume"

        body: dict[str, Any] = {
            "chains": chain_list,
            "timeframe": timeframe,
            "filters": {
                "only_smart_money": True,
                "token_age_days": {
                    "min": min_age_days,
                    "max": max_age_days,
                },
            },
            "order_by": [{"field": sort_by, "direction": sort_dir.upper()}],
            "pagination": {"page": 1, "per_page": min(limit, 100)},
        }

        # Add optional filters
        if min_mcap > 0 or max_mcap > 0:
            mcap_filter: dict[str, float] = {}
            if min_mcap > 0:
                mcap_filter["min"] = min_mcap
            if max_mcap > 0:
                mcap_filter["max"] = max_mcap
            body["filters"]["market_cap_usd"] = mcap_filter

        if min_liquidity > 0:
            body["filters"]["liquidity"] = {"min": min_liquidity}

        result = await nansen_post("/api/v1/token-screener", body)
        output = "## Nansen Smart Money Screener\n\n"
        output += _header(f"chains={chains} | {timeframe} | sort={sort_by}")

        if result["status"] == "error":
            return output + f"**ERROR:** {result['error']}"

        tokens = result["data"] or []

        if not tokens:
            return output + "**No tokens found matching filters.**"

        pagination = result.get("pagination") or {}
        total = pagination.get("total", len(tokens))
        output += f"**{len(tokens)} tokens** (of {total}) | Sorted by: {sort_by} {sort_dir}\n\n"

        for i, t in enumerate(tokens, 1):
            symbol = t.get("token_symbol", "?")
            chain = t.get("chain", "?")
            price = t.get("price_usd", 0) or 0
            change = (t.get("price_change", 0) or 0) * 100  # API returns decimal
            mcap = t.get("market_cap_usd", 0) or 0
            liq = t.get("liquidity", 0) or 0
            buy = t.get("buy_volume", 0) or 0
            sell = t.get("sell_volume", 0) or 0
            net = t.get("netflow", 0) or 0
            traders = t.get("nof_traders", 0) or 0
            age = t.get("token_age_days", 0) or 0

            # Determine signal
            if net > 0 and buy > sell * 2:
                signal = "STRONG BUY"
            elif net > 0:
                signal = "BUY"
            elif net < 0 and sell > buy * 2:
                signal = "STRONG SELL"
            elif net < 0:
                signal = "SELL"
            else:
                signal = "NEUTRAL"

            output += f"**{i}. {symbol}** ({chain}) — {signal}\n"
            output += f"   Price: ${price:.6g} | 24H: {change:+.1f}% | MCap: {_format_usd(mcap)}\n"
            output += f"   Buy: {_format_usd(buy)} | Sell: {_format_usd(sell)} | Net: {_format_usd(net)}\n"
            output += f"   Traders: {traders} | Liq: {_format_usd(liq)} | Age: {age:.0f}d\n"
            addr = t.get("token_address", "")
            if addr:
                output += f"   `{addr[:20]}...`\n"
            output += "\n"

        output += (
            "**Ricoz Framework:**\n"
            "- Smart money buying + positive SpotCVD = high-conviction long\n"
            "- Smart money selling + negative SpotCVD = high-conviction short\n"
            "- High netflow + OI rising = whale accumulation with leverage\n"
            "- Cross-check with Arkham for specific whale wallet identification\n"
        )
        return output

    # ─── 2. Smart Money Sells (Distribution Detection) ────────────────────────

    @mcp.tool()
    async def nansen_smart_sells(
        chains: str = "ethereum,solana,base",
        timeframe: str = "24h",
        limit: int = 20,
    ) -> str:
        """Nansen Smart Money distribution — what are whales dumping?

        Shows tokens with highest smart money SELL volume.
        Use to detect distribution before price drops.

        Args:
            chains: Comma-separated chains
            timeframe: Time window (24h, 7d, 30d)
            limit: Number of tokens (max 100)

        Use cases:
        - Detect tokens being dumped by smart money
        - Confirm bearish SpotCVD with on-chain selling
        - Avoid buying tokens that whales are exiting
        """
        chain_list = [c.strip() for c in chains.split(",") if c.strip()]

        body: dict[str, Any] = {
            "chains": chain_list,
            "timeframe": timeframe,
            "filters": {
                "only_smart_money": True,
                "token_age_days": {"min": 1, "max": 365},
            },
            "order_by": [{"field": "sell_volume", "direction": "DESC"}],
            "pagination": {"page": 1, "per_page": min(limit, 100)},
        }

        result = await nansen_post("/api/v1/token-screener", body)
        output = "## Nansen Smart Money SELLS — Distribution Alert\n\n"
        output += _header(f"chains={chains} | {timeframe} | sort=sell_volume DESC")

        if result["status"] == "error":
            return output + f"**ERROR:** {result['error']}"

        tokens = result["data"] or []
        if not tokens:
            return output + "**No smart money sells detected.**"

        output += f"**Top {len(tokens)} tokens being SOLD by smart money**\n\n"

        for i, t in enumerate(tokens, 1):
            symbol = t.get("token_symbol", "?")
            chain = t.get("chain", "?")
            price = t.get("price_usd", 0) or 0
            change = (t.get("price_change", 0) or 0) * 100
            sell = t.get("sell_volume", 0) or 0
            buy = t.get("buy_volume", 0) or 0
            net = t.get("netflow", 0) or 0
            traders = t.get("nof_traders", 0) or 0

            ratio = sell / buy if buy > 0 else float('inf')

            output += f"**{i}. {symbol}** ({chain})\n"
            output += f"   SELL: {_format_usd(sell)} | Buy: {_format_usd(buy)} | Ratio: {ratio:.1f}x\n"
            output += f"   Net: {_format_usd(net)} | Price: {change:+.1f}% | Traders: {traders}\n\n"

        output += (
            "**Ricoz Framework:**\n"
            "- High sell/buy ratio = smart money exiting = AVOID\n"
            "- Sell spike + negative SpotCVD = confirmed distribution\n"
            "- Multiple tokens same sector selling = sector rotation signal\n"
        )
        return output

    # ─── 3. Smart Money Netflow (Strongest Accumulation) ──────────────────────

    @mcp.tool()
    async def nansen_netflow(
        chains: str = "ethereum,solana,base",
        timeframe: str = "24h",
        direction: str = "positive",
        min_mcap: float = 1000000,
        limit: int = 20,
    ) -> str:
        """Nansen Smart Money netflow — strongest accumulation or distribution.

        Shows tokens with highest net buy (accumulation) or net sell (distribution).
        Netflow = buy_volume - sell_volume from smart money wallets.

        Args:
            chains: Comma-separated chains
            timeframe: Time window (24h, 7d, 30d)
            direction: "positive" (accumulation, DESC) or "negative" (distribution, ASC)
            min_mcap: Minimum market cap filter (default $1M to skip dust)
            limit: Number of tokens (max 100)

        Use cases:
        - Find strongest smart money accumulation (positive netflow)
        - Detect heaviest distribution (negative netflow)
        - Cross-reference with CoinGlass for derivatives confirmation
        """
        chain_list = [c.strip() for c in chains.split(",") if c.strip()]
        sort_dir = "DESC" if direction == "positive" else "ASC"

        body: dict[str, Any] = {
            "chains": chain_list,
            "timeframe": timeframe,
            "filters": {
                "only_smart_money": True,
                "token_age_days": {"min": 1, "max": 365},
            },
            "order_by": [{"field": "netflow", "direction": sort_dir}],
            "pagination": {"page": 1, "per_page": min(limit, 100)},
        }

        if min_mcap > 0:
            body["filters"]["market_cap_usd"] = {"min": min_mcap}

        result = await nansen_post("/api/v1/token-screener", body)

        label = "Accumulation" if direction == "positive" else "Distribution"
        output = f"## Nansen Smart Money Netflow — {label}\n\n"
        output += _header(f"chains={chains} | {timeframe} | netflow {sort_dir}")

        if result["status"] == "error":
            return output + f"**ERROR:** {result['error']}"

        tokens = result["data"] or []
        if not tokens:
            return output + f"**No {label.lower()} detected.**"

        output += f"**Top {len(tokens)} — Smart Money {label}** | Min MCap: {_format_usd(min_mcap)}\n\n"

        for i, t in enumerate(tokens, 1):
            symbol = t.get("token_symbol", "?")
            chain = t.get("chain", "?")
            net = t.get("netflow", 0) or 0
            buy = t.get("buy_volume", 0) or 0
            sell = t.get("sell_volume", 0) or 0
            change = (t.get("price_change", 0) or 0) * 100
            mcap = t.get("market_cap_usd", 0) or 0
            traders = t.get("nof_traders", 0) or 0

            output += f"**{i}. {symbol}** ({chain})\n"
            output += f"   Netflow: {_format_usd(net)} | Buy: {_format_usd(buy)} | Sell: {_format_usd(sell)}\n"
            output += f"   Price: {change:+.1f}% | MCap: {_format_usd(mcap)} | Traders: {traders}\n\n"

        output += (
            "**Ricoz Framework:**\n"
            f"- {'Positive netflow = smart money accumulation = bullish' if direction == 'positive' else 'Negative netflow = smart money distribution = bearish'}\n"
            "- Confirm with CoinGlass SpotCVD direction\n"
            "- High trader count = consensus among smart money wallets\n"
        )
        return output

    # ─── 4. Multi-Chain Activity Overview ─────────────────────────────────────

    @mcp.tool()
    async def nansen_chain_activity(
        timeframe: str = "24h",
        sort_by: str = "volume",
        limit: int = 15,
    ) -> str:
        """Nansen chain-by-chain smart money activity comparison.

        Runs the screener per-chain and compares which chains have most smart money activity.
        Helps identify where capital is flowing across chains.

        Args:
            timeframe: Time window (24h, 7d, 30d)
            sort_by: Sort tokens within each chain (buy_volume, sell_volume, netflow, volume)
            limit: Tokens per chain (max 10)

        Use cases:
        - Identify which chain smart money is most active on
        - Detect cross-chain capital rotation
        - Find the "hot" chain for the current cycle
        """
        output = "## Nansen Chain Activity — Smart Money Cross-Chain\n\n"
        output += _header(f"{timeframe} | sort={sort_by}")

        if sort_by not in VALID_SORT_FIELDS:
            sort_by = "volume"

        per_chain = min(limit, 10)
        chain_stats: list[dict] = []

        for chain in NANSEN_CHAINS:
            body: dict[str, Any] = {
                "chains": [chain],
                "timeframe": timeframe,
                "filters": {
                    "only_smart_money": True,
                    "token_age_days": {"min": 1, "max": 365},
                },
                "order_by": [{"field": sort_by, "direction": "DESC"}],
                "pagination": {"page": 1, "per_page": per_chain},
            }

            result = await nansen_post("/api/v1/token-screener", body)
            if result["status"] == "error":
                chain_stats.append({"chain": chain, "error": result["error"], "tokens": []})
                continue

            tokens = result["data"] or []
            total_buy = sum(t.get("buy_volume", 0) or 0 for t in tokens)
            total_sell = sum(t.get("sell_volume", 0) or 0 for t in tokens)
            total_net = sum(t.get("netflow", 0) or 0 for t in tokens)
            total_traders = sum(t.get("nof_traders", 0) or 0 for t in tokens)

            chain_stats.append({
                "chain": chain,
                "tokens": tokens,
                "buy": total_buy,
                "sell": total_sell,
                "net": total_net,
                "traders": total_traders,
                "count": len(tokens),
            })

        # Sort chains by total volume
        chain_stats.sort(key=lambda x: x.get("buy", 0) + x.get("sell", 0), reverse=True)

        output += "| Chain | Tokens | Buy Vol | Sell Vol | Netflow | Traders |\n"
        output += "|-------|--------|---------|----------|---------|---------|\n"

        for cs in chain_stats:
            if "error" in cs:
                output += f"| **{cs['chain']}** | ERROR | — | — | — | — |\n"
                continue
            output += (
                f"| **{cs['chain']}** | {cs['count']} | "
                f"{_format_usd(cs['buy'])} | {_format_usd(cs['sell'])} | "
                f"{_format_usd(cs['net'])} | {cs['traders']} |\n"
            )

        # Show top token per active chain
        output += "\n### Top Token Per Chain\n"
        for cs in chain_stats:
            if cs.get("tokens"):
                top = cs["tokens"][0]
                symbol = top.get("token_symbol", "?")
                net = top.get("netflow", 0) or 0
                output += f"- **{cs['chain']}**: {symbol} (net {_format_usd(net)})\n"

        output += (
            "\n**Ricoz Framework:**\n"
            "- Highest activity chain = where smart money is focused\n"
            "- Cross-chain netflow comparison = capital rotation signal\n"
            "- Combine with CoinGlass for derivatives-side confirmation\n"
        )
        return output
