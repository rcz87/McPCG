"""Arkham Intel API Integration — On-chain whale tracking for Ricoz Trading Framework.

Adds on-chain intelligence to complement CoinGlass order flow data:
- Track whale wallet movements & accumulation
- Monitor exchange inflows/outflows (confirm SpotCVD)
- Identify labeled entities (funds, market makers, exchanges)
- Real-time large transfer alerts

Base URL: https://api.arkm.com
Auth: API-Key header (set ARKHAM_API_KEY in .env)
Docs: https://intel.arkm.com/api/docs
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone, timedelta
from typing import Any

import httpx

WIB = timezone(timedelta(hours=7))

ARKHAM_BASE_URL = "https://api.arkm.com"
ARKHAM_TIMEOUT = 15.0


def _get_arkham_key() -> str:
    """Get Arkham API key from environment."""
    key = os.getenv("ARKHAM_API_KEY", "")
    if not key:
        raise ValueError(
            "ARKHAM_API_KEY not set. Add it to your .env file:\n"
            "ARKHAM_API_KEY=your_key_here\n"
            "Get your key at: https://intel.arkm.com → Settings → API Keys"
        )
    return key


def _mask_key(text: str, key: str) -> str:
    """Mask API key in any error messages."""
    if key and len(key) > 8:
        masked = key[:4] + "****" + key[-4:]
        return text.replace(key, masked)
    return text


async def arkham_get(
    endpoint: str,
    params: dict | None = None,
) -> dict:
    """Execute Arkham API GET request with auth header.
    
    Returns dict with keys: data, status, error
    """
    try:
        key = _get_arkham_key()
    except ValueError as e:
        return {"data": None, "status": "error", "error": str(e)}

    url = f"{ARKHAM_BASE_URL}{endpoint}"
    headers = {
        "API-Key": key,
        "Accept": "application/json",
    }

    try:
        async with httpx.AsyncClient(timeout=ARKHAM_TIMEOUT) as client:
            resp = await client.get(url, params=params or {}, headers=headers)

        if resp.status_code == 401:
            return {
                "data": None, "status": "error",
                "error": "Invalid or expired ARKHAM_API_KEY. Check your key at intel.arkm.com → Settings → API Keys"
            }
        elif resp.status_code == 429:
            return {
                "data": None, "status": "error",
                "error": "Rate limit exceeded. Arkham free tier: 100 req/min. Wait before retrying."
            }
        elif resp.status_code == 402:
            return {
                "data": None, "status": "error",
                "error": "Insufficient Arkham credits. Top up at intel.arkm.com."
            }
        elif resp.status_code != 200:
            return {
                "data": None, "status": "error",
                "error": f"HTTP {resp.status_code}: {resp.text[:200]}"
            }

        data = resp.json()
        return {"data": data, "status": "ok", "error": None}

    except httpx.TimeoutException:
        return {"data": None, "status": "error", "error": f"Timeout after {ARKHAM_TIMEOUT}s"}
    except Exception as e:
        return {"data": None, "status": "error", "error": _mask_key(str(e), os.getenv("ARKHAM_API_KEY", ""))}


def _header(endpoint: str) -> str:
    """Standard header for all Arkham responses."""
    ts = datetime.now(WIB).strftime("%H:%M:%S WIB")
    return f"**Source:** Arkham Intel | **Endpoint:** {endpoint} | **Time:** {ts}\n\n"


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


def register_arkham_tools(mcp):
    """Register all Arkham tools on the FastMCP instance.
    
    Call this from server.py after creating mcp = FastMCP(...)
    """

    # ─── 1. Address Intelligence ──────────────────────────────────────────────

    @mcp.tool()
    async def arkham_address(
        address: str,
        chain: str = "",
    ) -> str:
        """Get Arkham intelligence for a blockchain address — who owns it?

        Identifies the entity (exchange, fund, whale, protocol) behind a wallet.
        Also shows labels, tags, and confidence scores.

        Args:
            address: Blockchain wallet address (0x... for EVM, or Solana/BTC address)
            chain: Optional chain filter (ethereum, bitcoin, solana, arbitrum, bsc, etc.)
                   Leave empty to search all chains.

        Use cases:
        - Identify mystery whale wallet from CoinGlass whale alert
        - Check if large transfer is from known exchange or fund
        - Verify if accumulation is from smart money vs retail
        """
        endpoint = f"/intelligence/address/{address}/all" if not chain else f"/intelligence/address/{address}"
        params = {"chain": chain} if chain else {}

        result = await arkham_get(endpoint, params)
        output = f"## Arkham Address Intelligence\n\n"
        output += _header(endpoint)

        if result["status"] == "error":
            return output + f"**ERROR:** {result['error']}"

        data = result["data"]
        if not data:
            return output + "**No data found for this address.**"

        # Extract key fields
        entity = data.get("arkhamEntity", data.get("entity", {})) or {}
        label = data.get("arkhamLabel", data.get("label", {})) or {}

        output += f"**Address:** `{address}`\n"
        if chain:
            output += f"**Chain:** {chain}\n"

        if entity:
            output += f"\n### Entity\n"
            output += f"- **Name:** {entity.get('name', 'Unknown')}\n"
            output += f"- **Type:** {entity.get('type', 'Unknown')}\n"
            output += f"- **Website:** {entity.get('website', 'N/A')}\n"
            output += f"- **Twitter:** {entity.get('twitter', {}).get('screen_name', 'N/A')}\n"

        if label:
            output += f"\n### Label\n"
            output += f"- **Name:** {label.get('name', 'Unknown')}\n"
            output += f"- **Address:** `{label.get('address', 'N/A')}`\n"
            output += f"- **Chain:** {label.get('chainType', 'N/A')}\n"

        # Tags
        tags = data.get("tags", [])
        if tags:
            tag_names = [t.get("name", t) if isinstance(t, dict) else str(t) for t in tags[:10]]
            output += f"\n**Tags:** {', '.join(tag_names)}\n"

        output += f"\n### Raw Data\n```json\n{json.dumps(data, indent=2, default=str)[:2000]}\n```\n"
        output += (
            "\n**Ricoz Framework:**\n"
            "- Exchange wallet = transfers in/out affect SpotCVD\n"
            "- Known fund/whale = smart money signal\n"
            "- Unknown = check transfer history for accumulation pattern\n"
        )
        return output

    # ─── 2. Counterparties (Whale Movements) ────────────────────────────────

    @mcp.tool()
    async def arkham_transfers(
        entity: str = "binance",
        flow: str = "",
        tokens: str = "",
        usd_gte: float = 100000,
        limit: int = 20,
    ) -> str:
        """Get counterparty transfers for an entity — detect whale accumulation.

        Shows who is sending/receiving tokens from a specific entity (exchange, fund).
        Key for confirming SpotCVD signals with actual on-chain activity.
        Rate limit: 1 req/sec.

        Args:
            entity: Entity slug (binance, coinbase, okx, bybit, jump-trading, etc.)
                    Use arkham_search to find entity slugs.
            flow: Direction — "in" (deposits to entity), "out" (withdrawals), "" (all)
            tokens: Comma-separated token symbols to filter (e.g., "BTC,ETH,SOL")
                    Leave empty for all tokens.
            usd_gte: Minimum transfer size in USD (default $100K = whale territory)
            limit: Number of counterparties to return (max 20)

        Use cases:
        - Large BTC flowing IN to Binance = potential sell pressure (bearish SpotCVD confirm)
        - Large BTC flowing OUT of Binance = accumulation (bullish SpotCVD confirm)
        - Identify which whales/funds are the biggest counterparties
        """
        params: dict = {
            "limit": min(limit, 20),
        }
        if flow:
            params["flow"] = flow
        if tokens:
            params["tokens"] = tokens.upper()
        if usd_gte > 0:
            params["usdGte"] = usd_gte

        endpoint = f"/counterparties/entity/{entity}"
        result = await arkham_get(endpoint, params)
        output = f"## Arkham Counterparties — {entity}\n\n"
        output += _header(endpoint)

        if result["status"] == "error":
            return output + f"**ERROR:** {result['error']}"

        data = result["data"]
        counterparties = data if isinstance(data, list) else (data.get("counterparties", []) if isinstance(data, dict) else [])

        if not counterparties:
            output += f"**No counterparties found** (entity={entity}, flow={flow or 'all'}, min ${usd_gte:,.0f})\n"
            output += f"**Raw:** `{json.dumps(data, default=str)[:1000]}`\n"
            return output

        flow_label = {"in": "INFLOW to", "out": "OUTFLOW from"}.get(flow, "ALL flows for")
        output += f"**{flow_label} {entity}** | Min: {_format_usd(usd_gte)} | Tokens: {tokens or 'ALL'}\n\n"

        for i, cp in enumerate(counterparties[:20], 1):
            cp_entity = (cp.get("entity") or cp.get("arkhamEntity") or {})
            cp_addr = (cp.get("address") or {})
            name = cp_entity.get("name") or str(cp_addr.get("address", "Unknown"))[:16]
            cp_type = cp_entity.get("type", "unknown")
            usd_val = cp.get("usd", cp.get("totalUSD", 0)) or 0
            tx_count = cp.get("count", cp.get("transfers", "?"))

            output += f"**{i}. {name}** ({cp_type})\n"
            output += f"   Volume: {_format_usd(float(usd_val))} | Transfers: {tx_count}\n\n"

        output += (
            "**Ricoz Framework:**\n"
            "- Inflow to exchange = sell pressure (bearish) → confirms negative SpotCVD\n"
            "- Outflow from exchange = accumulation (bullish) → confirms positive SpotCVD\n"
            "- Large fund as counterparty = smart money signal\n"
            "- Multiple whales same direction = coordinated activity\n"
        )
        return output

    # ─── 3. Exchange Flow (Confirm SpotCVD) ───────────────────────────────────

    @mcp.tool()
    async def arkham_exchange_flow(
        entity: str = "binance",
        chains: str = "",
    ) -> str:
        """Get historical USD inflow/outflow for an entity — on-chain SpotCVD confirmation.

        Shows net flow of tokens into or out of a specific exchange over time.
        This is the on-chain ground truth behind SpotCVD movements.

        Args:
            entity: Exchange entity slug (binance, coinbase, okx, bybit, kraken, etc.)
            chains: Optional comma-separated chain filter (ethereum, bitcoin, solana, etc.)
                    Leave empty for all chains.

        Use cases:
        - SpotCVD positive but want on-chain confirmation → check exchange outflow
        - Sudden SpotCVD flip → check if large exchange transfer happened
        - Exchange inflow spike = smart money depositing to sell
        - Exchange outflow spike = smart money withdrawing to hold/accumulate
        """
        endpoint = f"/flow/entity/{entity}"
        params: dict = {}
        if chains:
            params["chains"] = chains

        result = await arkham_get(endpoint, params)
        output = f"## Arkham Exchange Flow — {entity.upper()}\n\n"
        output += _header(endpoint)
        output += f"**Exchange:** {entity} | **Chains:** {chains or 'ALL'}\n\n"

        if result["status"] == "error":
            return output + f"**ERROR:** {result['error']}"

        data = result["data"]

        if isinstance(data, dict):
            # Try to extract flow series
            inflow = data.get("inflow", data.get("in", 0))
            outflow = data.get("outflow", data.get("out", 0))

            if isinstance(inflow, (int, float)) and isinstance(outflow, (int, float)):
                net = inflow - outflow
                direction = "NET INFLOW" if net > 0 else "NET OUTFLOW"
                output += f"| | USD Value |\n|--|--|\n"
                output += f"| **Inflow** (deposits) | {_format_usd(inflow)} |\n"
                output += f"| **Outflow** (withdrawals) | {_format_usd(outflow)} |\n"
                output += f"| **Net** | **{_format_usd(abs(net))} ({direction})** |\n\n"

            # Show time series if available
            series = data.get("series", data.get("data", []))
            if isinstance(series, list) and series:
                output += f"### Time Series ({len(series)} points)\n"
                for point in series[-10:]:  # Last 10 data points
                    ts = point.get("time", point.get("timestamp", ""))
                    if isinstance(ts, (int, float)):
                        ts_sec = ts / 1000 if ts > 1e10 else ts
                        ts = datetime.fromtimestamp(ts_sec, tz=WIB).strftime("%m/%d %H:%M")
                    p_in = point.get("inflow", point.get("in", 0)) or 0
                    p_out = point.get("outflow", point.get("out", 0)) or 0
                    p_net = p_in - p_out
                    icon = "+" if p_net > 0 else ""
                    output += f"- {ts}: In {_format_usd(p_in)} | Out {_format_usd(p_out)} | Net {icon}{_format_usd(p_net)}\n"
                output += "\n"

        output += f"**Raw:** `{json.dumps(data, default=str)[:1500]}`\n\n"

        output += (
            "**Ricoz Framework:**\n"
            f"- Net Inflow to {entity} = coins deposited to sell = **bearish SpotCVD confirm**\n"
            f"- Net Outflow from {entity} = coins withdrawn to hold = **bullish SpotCVD confirm**\n"
            "- Divergence from SpotCVD = investigate further before trading\n"
        )
        return output

    # ─── 4. Portfolio Time Series (Track Entity Holdings Over Time) ──────────

    @mcp.tool()
    async def arkham_portfolio(
        entity: str = "binance",
        pricing_id: str = "bitcoin",
        chains: str = "",
    ) -> str:
        """Get historical portfolio time series for an entity — track accumulation/distribution.

        Shows how an entity's holdings of a specific token changed over time.
        Useful for detecting whale accumulation before price moves.

        Args:
            entity: Entity slug (binance, coinbase, jump-trading, a16z, etc.)
                    Use arkham_search to find entity slugs.
            pricing_id: CoinGecko pricing ID (bitcoin, ethereum, solana, etc.)
            chains: Optional chain filter (ethereum, bitcoin, solana, etc.)

        Use cases:
        - Track if Binance BTC reserves are decreasing over time (bullish)
        - See if a fund is slowly accumulating a token (early signal)
        - Cross-reference with OI data (entity accumulating + OI rising = strong long)
        """
        endpoint = f"/portfolio/timeSeries/entity/{entity}"
        params: dict = {"pricingId": pricing_id}
        if chains:
            params["chains"] = chains

        result = await arkham_get(endpoint, params)
        output = f"## Arkham Portfolio — {entity} | {pricing_id}\n\n"
        output += _header(endpoint)

        if result["status"] == "error":
            return output + f"**ERROR:** {result['error']}\n\nTip: Use CoinGecko ID (bitcoin, ethereum, solana, etc.)"

        data = result["data"]
        series = data if isinstance(data, list) else (data.get("series", data.get("data", [])) if isinstance(data, dict) else [])

        if not series:
            output += f"**No portfolio data for {entity} / {pricing_id}.**\n"
            output += f"**Raw:** `{json.dumps(data, default=str)[:1000]}`\n"
            return output

        output += f"**Entity:** {entity} | **Token:** {pricing_id} | **Data points:** {len(series)}\n\n"

        # Show last 15 data points
        for point in series[-15:]:
            if not isinstance(point, dict):
                continue
            ts = point.get("time", point.get("timestamp", ""))
            if isinstance(ts, (int, float)):
                ts_sec = ts / 1000 if ts > 1e10 else ts
                ts = datetime.fromtimestamp(ts_sec, tz=WIB).strftime("%Y-%m-%d %H:%M")
            balance = point.get("balance", point.get("amount", 0)) or 0
            usd_val = point.get("usd", point.get("usdValue", 0)) or 0
            output += f"- **{ts}**: {balance} ({_format_usd(float(usd_val))})\n"

        output += (
            "\n**Ricoz Framework:**\n"
            "- Balance decreasing over time = distribution / selling\n"
            "- Balance increasing over time = accumulation (bullish)\n"
            "- Sudden large drop = possible OTC sell or internal transfer\n"
            "- Cross-check with SpotCVD direction for confirmation\n"
        )
        return output

    # ─── 5. Entity Summary (Fund/Exchange Intel) ────────────────────────────

    @mcp.tool()
    async def arkham_entity_info(
        entity: str = "binance",
    ) -> str:
        """Get Arkham intelligence summary for an entity — exchange, fund, or whale.

        Shows entity profile, type, associated addresses, and metadata.
        Useful for understanding who a counterparty is before trading.

        Args:
            entity: Entity slug (binance, coinbase, jump-trading, a16z, paradigm, etc.)
                    Use arkham_search to find entity slugs.

        Use cases:
        - Get overview of an exchange or fund before checking their flows
        - Identify entity type (exchange, fund, protocol, individual)
        - Find associated wallet addresses for deeper analysis
        """
        endpoint = f"/intelligence/entity/{entity}/summary"
        result = await arkham_get(endpoint)
        output = f"## Arkham Entity Info — {entity}\n\n"
        output += _header(endpoint)

        if result["status"] == "error":
            # Fallback to base entity endpoint
            result = await arkham_get(f"/intelligence/entity/{entity}")
            if result["status"] == "error":
                return output + f"**ERROR:** {result['error']}"

        data = result["data"]
        if not data:
            return output + f"**No data found for entity '{entity}'.**"

        if isinstance(data, dict):
            name = data.get("name", entity)
            etype = data.get("type", "unknown")
            website = data.get("website", "N/A")
            twitter = data.get("twitter", {})
            if isinstance(twitter, dict):
                twitter = twitter.get("screen_name", "N/A")

            output += f"**Name:** {name}\n"
            output += f"**Type:** {etype}\n"
            output += f"**Website:** {website}\n"
            output += f"**Twitter:** {twitter}\n\n"

            # Address count / summary stats
            addr_count = data.get("addressCount", data.get("numAddresses", "?"))
            output += f"**Known Addresses:** {addr_count}\n"

            # Show portfolio summary if available
            portfolio = data.get("portfolio", data.get("holdings", {}))
            if portfolio and isinstance(portfolio, dict):
                total = portfolio.get("totalUSD", portfolio.get("total", 0))
                output += f"**Total Holdings:** {_format_usd(float(total) if total else 0)}\n"

        output += f"\n**Raw:** `{json.dumps(data, default=str)[:2000]}`\n"
        output += (
            "\n**Ricoz Framework:**\n"
            "- Exchange entity = check flows for SpotCVD confirmation\n"
            "- Fund/whale entity = smart money, follow their accumulation\n"
            "- Use entity slug in arkham_exchange_flow and arkham_entity_balance\n"
        )
        return output

    # ─── 6. Entity Balance (Fund Positions) ───────────────────────────────────

    @mcp.tool()
    async def arkham_entity_balance(
        entity: str = "binance",
        chains: str = "",
        coin: str = "",
        cheap: bool = False,
    ) -> str:
        """Get current token balances for an exchange/fund/entity.

        Shows what an entity currently holds across all chains.
        Track if major players are accumulating specific coins.

        Args:
            entity: Entity slug (binance, coinbase, okx, bybit, jump-trading,
                    alameda-research, a16z, paradigm, three-arrows-capital, etc.)
            chains: Optional comma-separated chain filter (ethereum, bitcoin, solana, etc.)
            coin: Optional token symbol filter for display (BTC, ETH, SOL — leave empty for all)
            cheap: Use faster but less detailed query (default False)

        Use cases:
        - Check if Binance reserves are decreasing (bullish signal)
        - Track if major fund changed their coin exposure
        - Verify exchange solvency via on-chain balances
        """
        params: dict = {}
        if chains:
            params["chains"] = chains
        if cheap:
            params["cheap"] = "true"

        endpoint = f"/balances/entity/{entity}"
        result = await arkham_get(endpoint, params)
        output = f"## Arkham Entity Balance — {entity}\n\n"
        output += _header(endpoint)

        if result["status"] == "error":
            return output + f"**ERROR:** {result['error']}"

        data = result["data"]
        balances = []

        if isinstance(data, dict):
            for key in ("balances", "tokens", "holdings"):
                if key in data:
                    balances = data[key]
                    break
            if not balances:
                balances = list(data.values())[:20] if data else []
        elif isinstance(data, list):
            balances = data

        if not balances:
            output += f"**No balance data for {entity}.**\n"
            output += f"**Raw:** `{json.dumps(data, default=str)[:1000]}`\n"
            return output

        if coin:
            balances = [b for b in balances
                        if isinstance(b, dict) and
                        b.get("symbol", b.get("tokenSymbol", "")).upper() == coin.upper()]

        output += f"**Entity:** {entity} | **Chains:** {chains or 'ALL'} | **Coin:** {coin or 'ALL'}\n\n"

        total_usd = 0
        for bal in balances[:20]:
            if not isinstance(bal, dict):
                continue
            symbol = bal.get("symbol", bal.get("tokenSymbol", "?"))
            usd = bal.get("usdValue", bal.get("balanceUSD", bal.get("usd", 0))) or 0
            amount = bal.get("amount", bal.get("balance", bal.get("quantity", "?")))
            chain = bal.get("chain", bal.get("chainType", "?"))
            total_usd += float(usd) if usd else 0

            output += f"- **{symbol}** ({chain}): {_format_usd(float(usd) if usd else 0)}"
            if amount:
                output += f" | Amount: {amount}"
            output += "\n"

        output += f"\n**Total Value: {_format_usd(total_usd)}**\n"
        output += (
            "\n**Ricoz Framework:**\n"
            f"- {entity} balance decreasing = outflows = users withdrawing = bullish\n"
            f"- {entity} balance increasing = deposits = users selling = bearish\n"
            "- Track over time to detect trend changes before SpotCVD moves\n"
        )
        return output

    # ─── 7. Search (Find any entity/address) ─────────────────────────────────

    @mcp.tool()
    async def arkham_search(
        query: str,
    ) -> str:
        """Search Arkham for any address, entity, or token.

        Quickly identify unknown wallets, find entity slugs for other tools,
        or look up tokens by symbol/name.

        Args:
            query: Search term — wallet address, entity name, token symbol
                   Examples: "0x123...", "binance", "jump trading", "SOL", "hyperliquid"

        Use cases:
        - Get entity slug for use in arkham_exchange_flow or arkham_entity_balance
        - Identify unknown whale wallet from CoinGlass whale alert
        - Find token ID for arkham_token_holders
        """
        result = await arkham_get("/intelligence/search", {"query": query})
        output = f"## Arkham Search — `{query}`\n\n"
        output += _header("/intelligence/search")

        if result["status"] == "error":
            return output + f"**ERROR:** {result['error']}"

        data = result["data"]

        if isinstance(data, dict):
            # Entities
            entities = data.get("entities", [])
            if entities:
                output += f"### Entities ({len(entities)} found)\n"
                for e in entities[:5]:
                    output += f"- **{e.get('name', '?')}** (slug: `{e.get('id', '?')}`) — {e.get('type', '?')}\n"
                output += "\n"

            # Addresses
            addresses = data.get("addresses", [])
            if addresses:
                output += f"### Addresses ({len(addresses)} found)\n"
                for a in addresses[:5]:
                    addr_info = a.get("address", {}) or {}
                    entity_info = a.get("arkhamEntity", {}) or {}
                    output += f"- `{addr_info.get('address', '?')[:20]}...`"
                    if entity_info:
                        output += f" → **{entity_info.get('name', 'Unknown')}**"
                    output += f" ({addr_info.get('chain', '?')})\n"
                output += "\n"

            # Tokens
            tokens = data.get("tokens", [])
            if tokens:
                output += f"### Tokens ({len(tokens)} found)\n"
                for t in tokens[:5]:
                    output += f"- **{t.get('symbol', '?')}** (id: `{t.get('id', '?')}`) — {t.get('name', '?')}\n"
                output += "\n"

            if not entities and not addresses and not tokens:
                output += "**No results found.**\n"
                output += f"Raw: `{json.dumps(data, default=str)[:500]}`\n"
        else:
            output += f"```json\n{json.dumps(data, indent=2, default=str)[:2000]}\n```\n"

        output += "\n*Use entity slugs in `arkham_exchange_flow` or `arkham_entity_balance` tools.*\n"
        return output

    # ─── 8. Entity Balance Changes (Whale Accumulation Scanner) ──────────────

    @mcp.tool()
    async def arkham_balance_changes(
        entity_types: str = "exchange",
        pricing_ids: str = "",
        interval: str = "day",
        order_by: str = "absoluteChange",
        limit: int = 20,
    ) -> str:
        """Get entity balance changes — detect whale accumulation/distribution.

        Shows which entities had the largest balance changes for specific tokens.
        Can detect smart money accumulation before it shows in CoinGlass data.

        Args:
            entity_types: Comma-separated entity types to filter
                          (exchange, fund, protocol, individual, mev, etc.)
            pricing_ids: Comma-separated CoinGecko IDs to filter (bitcoin, ethereum, solana)
                         Leave empty for all tokens.
            interval: Time interval (hour, day, week, month)
            order_by: Sort by (absoluteChange, percentChange)
            limit: Number of results (max 20)

        Use cases:
        - Find which exchanges had biggest BTC outflows today (accumulation signal)
        - Detect funds accumulating altcoins before price moves
        - Cross-reference with CoinGlass smart screener for high conviction
        """
        endpoint = "/intelligence/entity_balance_changes"
        params: dict = {
            "interval": interval,
            "orderBy": order_by,
            "limit": min(limit, 20),
        }
        if entity_types:
            params["entityTypes"] = entity_types
        if pricing_ids:
            params["pricingIds"] = pricing_ids

        result = await arkham_get(endpoint, params)
        output = f"## Arkham Balance Changes — Entity Accumulation Scanner\n\n"
        output += _header(endpoint)

        if result["status"] == "error":
            return output + f"**ERROR:** {result['error']}"

        data = result["data"]
        changes = data if isinstance(data, list) else (data.get("changes", data.get("data", [])) if isinstance(data, dict) else [])

        if not changes:
            output += f"**No balance change data found.**\n"
            output += f"**Raw:** `{json.dumps(data, default=str)[:1500]}`\n"
            return output

        output += f"**Filter:** types={entity_types or 'ALL'} | tokens={pricing_ids or 'ALL'} | interval={interval}\n\n"

        for i, ch in enumerate(changes[:20], 1):
            if not isinstance(ch, dict):
                continue
            entity = ch.get("entity", ch.get("entityName", {}))
            if isinstance(entity, dict):
                name = entity.get("name", "Unknown")
                etype = entity.get("type", "?")
            else:
                name = str(entity)
                etype = "?"

            token = ch.get("pricingId", ch.get("token", "?"))
            abs_change = ch.get("absoluteChange", ch.get("change", 0)) or 0
            pct_change = ch.get("percentChange", ch.get("pctChange", 0)) or 0

            icon = "+" if abs_change > 0 else ""
            direction = "ACCUMULATING" if abs_change > 0 else "DISTRIBUTING"

            output += f"**{i}. {name}** ({etype}) — {direction}\n"
            output += f"   Token: {token} | Change: {icon}{_format_usd(float(abs_change))} ({pct_change:+.1f}%)\n\n"

        output += (
            "**Ricoz Framework:**\n"
            "- Exchange balance decreasing = users withdrawing = bullish accumulation\n"
            "- Fund balance increasing = smart money buying = follow the whale\n"
            "- Cross-check with CoinGlass: OI rising + whale accumulating = high conviction\n"
            "- FR negative + whale accumulating = short squeeze + accumulation combo\n"
        )
        return output
