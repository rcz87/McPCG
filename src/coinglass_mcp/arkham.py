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


def _fmt_time(ts_wib: str) -> str:
    return ts_wib


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

    # ─── 2. Recent Transfers (Whale Movements) ────────────────────────────────

    @mcp.tool()
    async def arkham_transfers(
        coin: str = "",
        entity: str = "",
        min_usd: float = 100000,
        limit: int = 20,
        flow: str = "",
    ) -> str:
        """Get recent large on-chain transfers — detect whale accumulation.

        Shows large token movements between wallets, exchanges, and protocols.
        Key for confirming SpotCVD signals with actual on-chain activity.

        Args:
            coin: Token symbol to filter (e.g., BTC, ETH, SOL, USDT)
                  Leave empty for all tokens.
            entity: Filter by entity name (e.g., "binance", "coinbase", "a16z")
                    Leave empty for all entities.
            min_usd: Minimum transfer size in USD (default $100K = whale territory)
            limit: Number of transfers to return (max 20)
            flow: Direction filter — "in" (to exchange), "out" (from exchange), "" (all)

        Use cases:
        - Large BTC moving TO exchange = potential sell pressure (bearish SpotCVD confirm)
        - Large BTC moving FROM exchange = accumulation (bullish SpotCVD confirm)
        - Sudden whale transfer before price move = leading indicator
        """
        params: dict = {
            "limit": min(limit, 20),
            "sortKey": "time",
            "sortDir": "desc",
        }
        if coin:
            params["tokenSymbol"] = coin.upper()
        if min_usd > 0:
            params["usdGte"] = min_usd
        if entity:
            if flow == "in":
                params["toEntity"] = entity
            elif flow == "out":
                params["fromEntity"] = entity
            else:
                params["entityName"] = entity

        result = await arkham_get("/transfers", params)
        output = f"## Arkham Transfers — Whale On-Chain Activity\n\n"
        output += _header("/transfers")

        if result["status"] == "error":
            return output + f"**ERROR:** {result['error']}"

        data = result["data"]
        transfers = data.get("transfers", []) if isinstance(data, dict) else []

        if not transfers:
            return output + f"**No transfers found** (min ${min_usd:,.0f}, coin={coin or 'all'}, entity={entity or 'all'})"

        output += f"**Found {len(transfers)} transfers** | Min: {_format_usd(min_usd)} | "
        output += f"Coin: {coin or 'ALL'} | Entity: {entity or 'ALL'}\n\n"

        for i, tx in enumerate(transfers, 1):
            ts_ms = tx.get("blockTimestamp", tx.get("timestamp", 0))
            if ts_ms:
                ts_sec = ts_ms / 1000 if ts_ms > 1e10 else ts_ms
                dt = datetime.fromtimestamp(ts_sec, tz=WIB).strftime("%m/%d %H:%M")
            else:
                dt = "N/A"

            from_entity = (tx.get("fromAddress", {}) or {})
            to_entity = (tx.get("toAddress", {}) or {})
            from_name = (from_entity.get("arkhamEntity", {}) or {}).get("name") or from_entity.get("address", "Unknown")[:12]
            to_name = (to_entity.get("arkhamEntity", {}) or {}).get("name") or to_entity.get("address", "Unknown")[:12]

            token = (tx.get("tokenSymbol") or tx.get("unitValue", "?"))
            usd_val = tx.get("historicalUSD", tx.get("usdValue", 0)) or 0
            amount = tx.get("tokenAmount", tx.get("amount", "?"))

            output += f"**{i}. {dt}** | {_format_usd(usd_val)}\n"
            output += f"   `{from_name}` → `{to_name}`\n"
            output += f"   Token: {token} | Amount: {amount}\n"
            tx_hash = tx.get("transactionHash", tx.get("hash", ""))
            if tx_hash:
                output += f"   Hash: `{tx_hash[:20]}...`\n"
            output += "\n"

        output += (
            "**Ricoz Framework:**\n"
            "- Token TO exchange = sell pressure (bearish) → confirms negative SpotCVD\n"
            "- Token FROM exchange = accumulation (bullish) → confirms positive SpotCVD\n"
            "- Unknown→Unknown large transfer = OTC deal, less market impact\n"
            "- Multiple transfers same direction = coordinated whale activity\n"
        )
        return output

    # ─── 3. Exchange Flow (Confirm SpotCVD) ───────────────────────────────────

    @mcp.tool()
    async def arkham_exchange_flow(
        entity: str = "binance",
        coin: str = "BTC",
        window: str = "1d",
    ) -> str:
        """Get exchange inflow/outflow — on-chain confirmation of SpotCVD.

        Shows net flow of tokens into or out of a specific exchange.
        This is the on-chain ground truth behind SpotCVD movements.

        Args:
            entity: Exchange entity slug (binance, coinbase, okx, bybit, kraken, etc.)
            coin: Token symbol (BTC, ETH, SOL, USDT, etc.)
            window: Time window (1h, 4h, 8h, 1d, 7d, 30d)

        Use cases:
        - SpotCVD positive but want on-chain confirmation → check exchange outflow
        - Sudden SpotCVD flip → check if large exchange transfer happened
        - Exchange inflow spike = smart money depositing to sell
        - Exchange outflow spike = smart money withdrawing to hold/accumulate
        """
        # Map window to Arkham API format
        window_map = {
            "1h": "hour", "4h": "hour", "8h": "hour",
            "1d": "day", "7d": "week", "30d": "month"
        }
        api_window = window_map.get(window, "day")

        params = {
            "tokenSymbol": coin.upper(),
            "window": api_window,
        }

        # Get inflow and outflow separately
        inflow_result = await arkham_get(f"/flow/entity/{entity}", {**params, "flow": "in"})
        outflow_result = await arkham_get(f"/flow/entity/{entity}", {**params, "flow": "out"})

        output = f"## Arkham Exchange Flow — {entity.upper()} | {coin}\n\n"
        output += _header(f"/flow/entity/{entity}")
        output += f"**Exchange:** {entity} | **Token:** {coin} | **Window:** {window}\n\n"

        if inflow_result["status"] == "error":
            # Try without flow filter
            flow_result = await arkham_get(f"/flow/entity/{entity}", params)
            if flow_result["status"] == "error":
                return output + f"**ERROR:** {flow_result['error']}"
            data = flow_result["data"]
            output += f"```json\n{json.dumps(data, indent=2, default=str)[:3000]}\n```\n"
            return output

        inflow_data = inflow_result.get("data") or {}
        outflow_data = outflow_result.get("data") or {}

        # Extract USD values
        in_usd = 0
        out_usd = 0
        if isinstance(inflow_data, dict):
            in_usd = inflow_data.get("totalUSD", inflow_data.get("usd", 0)) or 0
        if isinstance(outflow_data, dict):
            out_usd = outflow_data.get("totalUSD", outflow_data.get("usd", 0)) or 0

        net = in_usd - out_usd
        direction = "NET INFLOW 🔴" if net > 0 else "NET OUTFLOW 🟢"

        output += f"### {window} Summary\n"
        output += f"| | USD Value |\n|--|--|\n"
        output += f"| **Inflow** (→ exchange) | {_format_usd(in_usd)} |\n"
        output += f"| **Outflow** (← exchange) | {_format_usd(out_usd)} |\n"
        output += f"| **Net** | **{_format_usd(net)} ({direction})** |\n\n"

        output += f"**Raw Inflow:** `{json.dumps(inflow_data, default=str)[:500]}`\n\n"
        output += f"**Raw Outflow:** `{json.dumps(outflow_data, default=str)[:500]}`\n\n"

        output += (
            "**Ricoz Framework:**\n"
            f"- Net Inflow to {entity} = coins deposited to sell = **bearish SpotCVD confirm**\n"
            f"- Net Outflow from {entity} = coins withdrawn to hold = **bullish SpotCVD confirm**\n"
            "- Divergence from SpotCVD = investigate further before trading\n"
        )
        return output

    # ─── 4. Token Holders (Top Whales) ───────────────────────────────────────

    @mcp.tool()
    async def arkham_token_holders(
        token_id: str = "bitcoin",
        limit: int = 20,
    ) -> str:
        """Get top token holders — identify whales accumulating/distributing.

        Shows the largest holders of a token with their entity labels.
        Useful for detecting early whale accumulation before price moves.

        Args:
            token_id: CoinGecko pricing ID (bitcoin, ethereum, solana, avalanche-2, etc.)
                      Or use chain/address format: ethereum/0x...
            limit: Number of holders to return (max 20)

        Use cases:
        - Check if top holders are accumulating or distributing
        - Identify which exchanges/funds hold most of a coin
        - Cross-reference with OI data (whale holds + OI rising = strong long setup)
        """
        result = await arkham_get(f"/token/holders/{token_id}", {"limit": limit})
        output = f"## Arkham Top Holders — {token_id}\n\n"
        output += _header(f"/token/holders/{token_id}")

        if result["status"] == "error":
            return output + f"**ERROR:** {result['error']}\n\nTip: Use CoinGecko ID (bitcoin, ethereum, solana, etc.)"

        data = result["data"]
        holders = data.get("holders", []) if isinstance(data, dict) else []

        if not holders:
            return output + "**No holder data found.**"

        output += f"**Top {len(holders)} holders of {token_id}**\n\n"

        for i, holder in enumerate(holders, 1):
            entity = (holder.get("entity") or {})
            label = (holder.get("label") or {})
            address = holder.get("address", {}) or {}

            name = (entity.get("name") or label.get("name") or
                    address.get("address", "Unknown")[:16])
            entity_type = entity.get("type", "unknown")
            balance_usd = holder.get("usdValue", holder.get("balanceUSD", 0)) or 0
            pct = holder.get("percentage", holder.get("pct", 0)) or 0

            output += f"**{i}.** `{name}` ({entity_type})\n"
            output += f"   Balance: {_format_usd(balance_usd)} | Share: {pct:.2f}%\n\n"

        output += (
            "\n**Ricoz Framework:**\n"
            "- Top holders = exchanges → supply can sell anytime\n"
            "- Top holders = funds/whales → likely HODLing, bullish\n"
            "- Concentration increasing = whale accumulation setup\n"
            "- Exchange share growing = distribution phase\n"
        )
        return output

    # ─── 5. Token Market Data ─────────────────────────────────────────────────

    @mcp.tool()
    async def arkham_token_flow(
        token_id: str = "solana",
    ) -> str:
        """Get top exchange flows for a token — where is money moving?

        Shows which exchanges are seeing most inflow/outflow for a token.
        Combines with CoinGlass SpotCVD for full picture.

        Args:
            token_id: CoinGecko pricing ID (bitcoin, ethereum, solana, etc.)

        Use cases:
        - See which exchange has most spot buying pressure
        - Identify if flows are concentrated on one exchange (manipulation risk)
        - Cross-reference with CoinGlass exchange-specific CVD
        """
        result = await arkham_get(f"/token/top_flow/{token_id}")
        output = f"## Arkham Token Flow — {token_id}\n\n"
        output += _header(f"/token/top_flow/{token_id}")

        if result["status"] == "error":
            return output + f"**ERROR:** {result['error']}"

        data = result["data"]
        output += f"```json\n{json.dumps(data, indent=2, default=str)[:3000]}\n```\n"
        output += (
            "\n**Ricoz Framework:**\n"
            "- High inflow to major exchange = sell pressure, bearish\n"
            "- High outflow from exchange = accumulation, bullish\n"
            "- Correlate with SpotCVD direction for high-conviction trades\n"
        )
        return output

    # ─── 6. Entity Balance (Fund Positions) ───────────────────────────────────

    @mcp.tool()
    async def arkham_entity_balance(
        entity: str = "binance",
        coin: str = "",
    ) -> str:
        """Get current token balances for an exchange/fund/entity.

        Shows what an entity currently holds across all chains.
        Track if major players are accumulating specific coins.

        Args:
            entity: Entity slug (binance, coinbase, okx, bybit, jump-trading,
                    alameda-research, a16z, paradigm, three-arrows-capital, etc.)
            coin: Optional token filter (BTC, ETH, SOL — leave empty for all)

        Use cases:
        - Check if Binance reserves are decreasing (bullish signal)
        - Track if major fund changed their coin exposure
        - Verify exchange solvency via on-chain balances
        """
        result = await arkham_get(f"/balances/entity/{entity}")
        output = f"## Arkham Entity Balance — {entity}\n\n"
        output += _header(f"/balances/entity/{entity}")

        if result["status"] == "error":
            return output + f"**ERROR:** {result['error']}"

        data = result["data"]
        balances = []

        if isinstance(data, dict):
            # Try to extract balance list
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

        # Filter by coin if specified
        if coin:
            balances = [b for b in balances
                        if isinstance(b, dict) and
                        b.get("symbol", b.get("tokenSymbol", "")).upper() == coin.upper()]

        output += f"**Entity:** {entity} | **Coin filter:** {coin or 'ALL'}\n\n"

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

    # ─── 8. Trending Tokens (On-chain activity spike) ─────────────────────────

    @mcp.tool()
    async def arkham_trending() -> str:
        """Get trending tokens by on-chain activity — early signal scanner.

        Shows tokens with unusually high on-chain transfer activity.
        Can detect whale accumulation before it shows up in CoinGlass data.

        Use cases:
        - Find coins with sudden on-chain activity spike (pre-pump signal)
        - Cross-reference with CoinGlass smart screener stealth accum signals
        - Discover new opportunities before retail catches on
        """
        result = await arkham_get("/token/trending")
        output = f"## Arkham Trending Tokens — On-Chain Activity Spike\n\n"
        output += _header("/token/trending")

        if result["status"] == "error":
            return output + f"**ERROR:** {result['error']}"

        data = result["data"]
        tokens = data.get("tokens", []) if isinstance(data, dict) else (data if isinstance(data, list) else [])

        if not tokens:
            output += f"**Raw:** `{json.dumps(data, default=str)[:2000]}`\n"
            return output

        output += f"**{len(tokens)} trending tokens by on-chain activity**\n\n"

        for i, t in enumerate(tokens[:15], 1):
            symbol = t.get("symbol", t.get("ticker", "?"))
            name = t.get("name", "?")
            price = t.get("price", t.get("priceUsd", 0)) or 0
            change = t.get("percentChange24h", t.get("priceChange24h", 0)) or 0
            vol = t.get("volume24h", t.get("volumeUSD24h", 0)) or 0
            mc = t.get("marketCap", t.get("marketCapUSD", 0)) or 0

            change_icon = "🟢" if change > 0 else "🔴"
            output += f"**{i}. {symbol}** ({name})\n"
            output += f"   Price: {_format_usd(float(price))} | 24H: {change_icon} {change:+.1f}%\n"
            if vol:
                output += f"   Vol: {_format_usd(float(vol))} | MCap: {_format_usd(float(mc))}\n"
            output += "\n"

        output += (
            "**Ricoz Framework:**\n"
            "- On-chain trending ≠ price pump yet → early accumulation signal\n"
            "- Cross-check with CoinGlass: OI rising + on-chain trending = high conviction\n"
            "- FR still negative + on-chain activity = short squeeze + accumulation combo\n"
        )
        return output
