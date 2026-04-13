"""CoinGlass Category Tools — multi-action aggregation endpoints.

12 tools that each bundle multiple related CoinGlass API endpoints
behind a single `action` parameter:

  coinglass_trading_market      (9 endpoints)
  coinglass_open_interest_cat   (6 endpoints)
  coinglass_funding_rate_cat    (6 endpoints)
  coinglass_long_short_cat      (6 endpoints)
  coinglass_liquidation_cat     (5 endpoints)
  coinglass_orderbook_cat       (5 endpoints)
  coinglass_hyperliquid_cat     (3 endpoints)
  coinglass_futures_taker_cat   (4 endpoints)
  coinglass_spot_market_cat     (4 endpoints)
  coinglass_spot_orderbook_cat  (5 endpoints)
  coinglass_indicators_cat      (10 endpoints)
  coinglass_index_news_cat      (3 endpoints)
"""

from __future__ import annotations

from .client import CoinGlassClient, FetchResult
from .config import normalize_symbol, to_pair, DEFAULT_EXCHANGE
from .formatters import (
    _tool_envelope,
    _err,
    fmt,
    fmt_parsed,
    _fmt_coins_markets,
    _fmt_scan_price,
    _fmt_scan_oi,
    _fmt_fr_exchange_list,
    _fmt_fr_arbitrage,
    _fmt_scan_ls_ratio,
    _fmt_p2_top_position_ls,
    _fmt_liq_history,
    _fmt_scan_liq_orders,
    _fmt_scan_ob_delta,
    _fmt_p2_hyperliquid_ls,
    _fmt_scan_cvd,
    _fmt_scan_taker,
    _fmt_indicator_ts,
    _fmt_indicator_snapshot,
    _fmt_news,
    _fmt_large_orders,
    _fmt_ob_heatmap,
    _fmt_fr_ohlc,
    _fmt_fr_cumulative,
    make_envelope,
)


def _indicator_by_symbol(result: FetchResult, symbol: str, indicator: str, formatter) -> str:
    """Extract a single coin from a list-endpoint result and format it."""
    data = result.data
    if not isinstance(data, list):
        return make_envelope("failed", "coinglass",
                             f"**{indicator}:** unexpected response format.",
                             data_age_seconds=result.age_seconds)
    sym_upper = symbol.upper()
    match = next((d for d in data if isinstance(d, dict)
                  and d.get("symbol", "").upper() == sym_upper), None)
    if match is None:
        return make_envelope("failed", "coinglass",
                             f"**{indicator}:** symbol '{symbol}' not found in {len(data)} coins.",
                             data_age_seconds=result.age_seconds)
    content = f"## {indicator} — {sym_upper}\n\n" + formatter(match)
    return make_envelope("success", "coinglass", content,
                         data_age_seconds=result.age_seconds)


def register_coinglass_category_tools(mcp, client: CoinGlassClient):
    """Register 12 CoinGlass category tools on the MCP server."""

    # ═══════════════════════════════════════════════════════════════════════════
    # CATEGORY TOOL — Trading Market (9 endpoints)
    # ═══════════════════════════════════════════════════════════════════════════

    @mcp.tool()
    @_tool_envelope()
    async def coinglass_trading_market(
        action: str = "coins_markets",
        symbol: str = "",
        exchange: str = DEFAULT_EXCHANGE,
        interval: str = "1h",
        limit: int = 100,
        page: int = 1,
        per_page: int = 20,
    ) -> str:
        """Futures Trading Market data — 9 endpoints in one tool.

        Actions:
        - supported_coins: List all supported coin symbols
        - supported_exchanges: List all supported futures exchanges
        - supported_pairs: List exchange-pair combinations (optional: filter by exchange)
        - coins_markets: Market overview all coins (price, OI, volume, FR, liquidation)
        - pairs_markets: All trading pairs for a coin (requires symbol)
        - price_change: Price change % across timeframes (5m, 15m, 30m, 1h, 4h, 12h, 24h)
        - price_history: OHLC candlestick data (requires symbol, exchange, interval)
        - delisted_pairs: Retired/delisted trading pairs
        - exchange_rank: Exchange rankings by volume/OI

        Args:
            action: One of the actions listed above
            symbol: Coin symbol — needed for pairs_markets and price_history
            exchange: Exchange name — needed for price_history, optional for supported_pairs
            interval: Candle interval for price_history (1m, 5m, 15m, 1h, 4h, 1d)
            limit: Data points for price_history (max 4500)
            page: Page number for coins_markets
            per_page: Results per page for coins_markets
        """
        action = action.strip().lower()

        if action == "supported_coins":
            result = await client.get("/api/futures/supported-coins")
            return fmt(result, "Futures — Supported Coins")

        elif action == "supported_exchanges":
            result = await client.get("/api/futures/supported-exchanges")
            return fmt(result, "Futures — Supported Exchanges")

        elif action == "supported_pairs":
            params = {}
            if exchange:
                params["exchange"] = exchange
            result = await client.get("/api/futures/supported-exchange-pairs", params or None)
            return fmt(result, f"Futures — Supported Pairs ({exchange or 'All'})")

        elif action == "coins_markets":
            params = {"per_page": per_page, "page": page}
            if exchange:
                params["exchange_list"] = exchange
            result = await client.get("/api/futures/coins-markets", params)
            return fmt_parsed(result, f"Futures — Coins Markets (page {page})", _fmt_coins_markets)

        elif action == "pairs_markets":
            if not symbol:
                return _err("`symbol` is required for pairs_markets (e.g., BTC, ETH)")
            sym = normalize_symbol(symbol)
            result = await client.get("/api/futures/pairs-markets", {"symbol": sym})
            return fmt(result, f"Futures — Pairs Markets ({sym})")

        elif action == "price_change":
            result = await client.get("/api/futures/coins-price-change")
            return fmt(result, "Futures — Coins Price Change (5m→24h)")

        elif action == "price_history":
            if not symbol:
                return _err("`symbol` is required for price_history (e.g., BTC, ETH)")
            pair = to_pair(symbol)
            result = await client.get("/api/futures/price/history", {
                "exchange": exchange,
                "symbol": pair,
                "interval": interval,
                "limit": limit,
            })
            return fmt_parsed(result, f"Futures — Price OHLC ({normalize_symbol(symbol)} {interval})", _fmt_scan_price)

        elif action == "delisted_pairs":
            result = await client.get("/api/futures/delisted-exchange-pairs")
            return fmt(result, "Futures — Delisted Pairs")

        elif action == "exchange_rank":
            result = await client.get("/api/futures/exchange-rank")
            return fmt(result, "Futures — Exchange Ranking")

        else:
            return _err(f"Unknown action '{action}'. Available: supported_coins, supported_exchanges, supported_pairs, coins_markets, pairs_markets, price_change, price_history, delisted_pairs, exchange_rank")

    # ═══════════════════════════════════════════════════════════════════════════
    # CATEGORY TOOL — Open Interest (6 endpoints)
    # ═══════════════════════════════════════════════════════════════════════════

    @mcp.tool()
    @_tool_envelope()
    async def coinglass_open_interest_cat(
        action: str = "aggregated_history",
        symbol: str = "BTC",
        exchange: str = DEFAULT_EXCHANGE,
        interval: str = "1h",
        limit: int = 100,
        range: str = "24h",
    ) -> str:
        """Open Interest data — 6 endpoints in one tool.

        Actions:
        - history: OI OHLC per pair (requires symbol as pair BTCUSDT + exchange)
        - aggregated_history: Aggregated OI OHLC across all exchanges for a coin
        - stablecoin_margin: Stablecoin-margined (USDT) OI OHLC
        - coin_margin: Coin-margined OI OHLC
        - exchange_list: OI breakdown by exchange (symbol optional, range required)
        - exchange_chart: Historical OI chart by exchange (symbol only)

        Args:
            action: One of the actions listed above
            symbol: Coin symbol (BTC, ETH, SOL) — auto-converted to pair for 'history' action
            exchange: Exchange name — needed for 'history' action
            interval: Candle interval (1m, 5m, 15m, 1h, 4h, 1d) — for history/aggregated/margin
            limit: Data points (max 4500)
            range: Time range for exchange_list only (1h, 4h, 12h, 24h)
        """
        action = action.strip().lower()
        sym = normalize_symbol(symbol)

        if action == "history":
            pair = to_pair(symbol)
            result = await client.get("/api/futures/open-interest/history", {
                "exchange": exchange,
                "symbol": pair,
                "interval": interval,
                "limit": limit,
            })
            return fmt_parsed(result, f"OI History — {pair} ({exchange})", _fmt_scan_oi)

        elif action == "aggregated_history":
            result = await client.get("/api/futures/open-interest/aggregated-history", {
                "symbol": sym,
                "interval": interval,
                "limit": limit,
            })
            return fmt_parsed(result, f"OI Aggregated — {sym}", _fmt_scan_oi)

        elif action == "stablecoin_margin":
            result = await client.get("/api/futures/open-interest/aggregated-stablecoin-history", {
                "symbol": sym,
                "interval": interval,
                "limit": limit,
            })
            return fmt_parsed(result, f"OI Stablecoin Margin — {sym}", _fmt_scan_oi)

        elif action == "coin_margin":
            result = await client.get("/api/futures/open-interest/aggregated-coin-margin-history", {
                "symbol": sym,
                "interval": interval,
                "limit": limit,
            })
            return fmt_parsed(result, f"OI Coin Margin — {sym}", _fmt_scan_oi)

        elif action == "exchange_list":
            params = {"range": range}
            if symbol:
                params["symbol"] = sym
            result = await client.get("/api/futures/open-interest/exchange-list", params)
            return fmt(result, f"OI Exchange List — {sym or 'All Coins'}")

        elif action == "exchange_chart":
            result = await client.get("/api/futures/open-interest/exchange-history-chart", {
                "symbol": sym,
            })
            return fmt(result, f"OI Exchange Chart — {sym}")

        else:
            return _err(f"Unknown action '{action}'. Available: history, aggregated_history, stablecoin_margin, coin_margin, exchange_list, exchange_chart")

    # ═══════════════════════════════════════════════════════════════════════════
    # CATEGORY TOOL — Funding Rate (6 endpoints)
    # ═══════════════════════════════════════════════════════════════════════════

    @mcp.tool()
    @_tool_envelope()
    async def coinglass_funding_rate_cat(
        action: str = "exchange_list",
        symbol: str = "BTC",
        exchange: str = DEFAULT_EXCHANGE,
        interval: str = "1d",
        limit: int = 100,
        usd: int = 10000,
        range: str = "7d",
    ) -> str:
        """Funding Rate data — 6 endpoints in one tool.

        Actions:
        - history: FR OHLC per pair (requires symbol as pair BTCUSDT + exchange)
        - oi_weight: OI-weighted FR OHLC (pair + exchange) — Professional plan only
        - vol_weight: Volume-weighted FR OHLC (pair + exchange) — Professional plan only
        - exchange_list: Current FR across all exchanges (symbol optional)
        - cumulative: Accumulated/cumulative FR by exchange (range required, symbol optional)
        - arbitrage: FR arbitrage opportunities (usd = position size, exchange optional)

        Args:
            action: One of the actions listed above
            symbol: Coin symbol (BTC, ETH) — auto-converted to pair for history/oi_weight/vol_weight
            exchange: Exchange name — needed for history, oi_weight, vol_weight
            interval: Candle interval (1m, 5m, 15m, 1h, 4h, 1d) — for OHLC actions
            limit: Data points (max 4500)
            usd: Position size in USD for arbitrage FR income calc (default: 10000)
            range: Time range for cumulative (7d, 30d, 90d, 180d, 1y)
        """
        action = action.strip().lower()
        sym = normalize_symbol(symbol)

        if action == "history":
            pair = to_pair(symbol)
            result = await client.get("/api/futures/funding-rate/history", {
                "exchange": exchange,
                "symbol": pair,
                "interval": interval,
                "limit": limit,
            })
            return fmt_parsed(result, f"FR History OHLC — {pair} ({exchange})", _fmt_fr_ohlc)

        elif action == "oi_weight":
            pair = to_pair(symbol)
            result = await client.get("/api/futures/funding-rate/oi-weight-history", {
                "exchange": exchange,
                "symbol": pair,
                "interval": interval,
                "limit": limit,
            })
            _w = []
            if isinstance(result.data, list) and len(result.data) == 0:
                _w.append("Empty result — may require Professional/Enterprise plan. Use 'history' action instead.")
            return fmt(result, f"FR OI-Weighted — {pair} ({exchange})", extra_warnings=_w)

        elif action == "vol_weight":
            pair = to_pair(symbol)
            result = await client.get("/api/futures/funding-rate/vol-weight-history", {
                "exchange": exchange,
                "symbol": pair,
                "interval": interval,
                "limit": limit,
            })
            _w = []
            if isinstance(result.data, list) and len(result.data) == 0:
                _w.append("Empty result — may require Professional/Enterprise plan. Use 'history' action instead.")
            return fmt(result, f"FR Vol-Weighted — {pair} ({exchange})", extra_warnings=_w)

        elif action == "exchange_list":
            result = await client.get("/api/futures/funding-rate/exchange-list")
            # Post-filter by symbol if specified (API returns all coins)
            if sym and isinstance(result.data, list):
                filtered = [c for c in result.data if c.get("symbol", "").upper() == sym]
                if not filtered:
                    return _err(f"No funding rate data found for {sym}.")
                result = FetchResult(
                    data=filtered, age_seconds=result.age_seconds,
                    is_cached=result.is_cached, fetched_at=result.fetched_at,
                )
            return _fmt_fr_exchange_list(result, sym or "All Coins")

        elif action == "cumulative":
            params: dict = {"range": range}
            if symbol:
                params["symbol"] = sym
            result = await client.get("/api/futures/funding-rate/accumulated-exchange-list", params)
            # Post-filter by symbol if specified (API returns all coins)
            if sym and isinstance(result.data, list):
                filtered = [c for c in result.data if c.get("symbol", "").upper() == sym]
                if filtered:
                    result = FetchResult(
                        data=filtered, age_seconds=result.age_seconds,
                        is_cached=result.is_cached, fetched_at=result.fetched_at,
                    )
            return fmt_parsed(result, f"Cumulative FR — {sym or 'All Coins'} ({range})", _fmt_fr_cumulative)

        elif action == "arbitrage":
            params: dict = {"usd": usd}
            if exchange and exchange != DEFAULT_EXCHANGE:
                params["exchange_list"] = exchange
            result = await client.get("/api/futures/funding-rate/arbitrage", params)
            return fmt_parsed(result, f"FR Arbitrage — ${usd} position", _fmt_fr_arbitrage)

        else:
            return _err(f"Unknown action '{action}'. Available: history, oi_weight, vol_weight, exchange_list, cumulative, arbitrage")

    # ═══════════════════════════════════════════════════════════════════════════
    # CATEGORY TOOL — Long/Short Ratio & Net Position (6 endpoints)
    # ═══════════════════════════════════════════════════════════════════════════

    @mcp.tool()
    @_tool_envelope()
    async def coinglass_long_short_cat(
        action: str = "global_account",
        symbol: str = "BTC",
        exchange: str = DEFAULT_EXCHANGE,
        interval: str = "4h",
        limit: int = 100,
        range: str = "4h",
    ) -> str:
        """Long/Short Ratio & Net Position data — 6 endpoints in one tool.

        Actions:
        - global_account: Global L/S account ratio history (pair + exchange)
        - top_account: Top traders L/S account ratio history (pair + exchange)
        - top_position: Top traders L/S position ratio history (pair + exchange)
        - taker_exchange: Taker buy/sell ratio per exchange (coin + range)
        - net_position: Net long/short position history (pair + exchange)
        - net_position_v2: Net position v2 with more detail (pair + exchange)

        Args:
            action: One of the actions listed above
            symbol: Coin symbol (BTC, ETH) — auto-converted to pair for ratio endpoints
            exchange: Exchange name (Binance, OKX, Bybit)
            interval: Candle interval for history actions (1m, 5m, 1h, 4h, 1d)
            limit: Data points (max 1000)
            range: Time range for taker_exchange only (5m, 15m, 30m, 1h, 4h, 12h, 24h)
        """
        action = action.strip().lower()
        sym = normalize_symbol(symbol)
        pair = to_pair(symbol)

        if action == "global_account":
            result = await client.get("/api/futures/global-long-short-account-ratio/history", {
                "exchange": exchange,
                "symbol": pair,
                "interval": interval,
                "limit": limit,
            })
            return fmt_parsed(result, f"Global L/S Account Ratio — {sym} ({exchange})", _fmt_scan_ls_ratio)

        elif action == "top_account":
            result = await client.get("/api/futures/top-long-short-account-ratio/history", {
                "exchange": exchange,
                "symbol": pair,
                "interval": interval,
                "limit": limit,
            })
            return fmt_parsed(result, f"Top Account L/S Ratio — {sym} ({exchange})", _fmt_scan_ls_ratio)

        elif action == "top_position":
            result = await client.get("/api/futures/top-long-short-position-ratio/history", {
                "exchange": exchange,
                "symbol": pair,
                "interval": interval,
                "limit": limit,
            })
            return fmt_parsed(result, f"Top Position L/S Ratio — {sym} ({exchange})", _fmt_p2_top_position_ls)

        elif action == "taker_exchange":
            result = await client.get("/api/futures/taker-buy-sell-volume/exchange-list", {
                "symbol": sym,
                "range": range,
            })
            return fmt(result, f"Taker Buy/Sell Exchange Ratio — {sym} ({range})")

        elif action == "net_position":
            result = await client.get("/api/futures/net-position/history", {
                "exchange": exchange,
                "symbol": pair,
                "interval": interval,
                "limit": limit,
            })
            return fmt(result, f"Net L/S Position — {sym} ({exchange})")

        elif action == "net_position_v2":
            result = await client.get("/api/futures/v2/net-position/history", {
                "exchange": exchange,
                "symbol": pair,
                "interval": interval,
                "limit": limit,
            })
            return fmt(result, f"Net L/S Position v2 — {sym} ({exchange})")

        else:
            return _err(f"Unknown action '{action}'. Available: global_account, top_account, top_position, taker_exchange, net_position, net_position_v2")

    # ═══════════════════════════════════════════════════════════════════════════
    # CATEGORY TOOL — Liquidation (5 endpoints)
    # ═══════════════════════════════════════════════════════════════════════════

    @mcp.tool()
    @_tool_envelope()
    async def coinglass_liquidation_cat(
        action: str = "coin_history",
        symbol: str = "BTC",
        exchange: str = DEFAULT_EXCHANGE,
        interval: str = "1h",
        limit: int = 100,
        range: str = "24h",
        min_amount: int = 10000,
    ) -> str:
        """Liquidation data — 5 endpoints in one tool.

        Actions:
        - pair_history: Liquidation history per pair (pair + exchange + interval)
        - coin_history: Aggregated liq history across exchanges (coin + exchange_list + interval)
        - coin_list: All coins liquidation on an exchange (1h/4h/12h/24h breakdown)
        - exchange_list: Liquidation by exchange for a coin (coin optional + range)
        - order: Individual liquidation orders last 7 days (coin + exchange + min_amount)

        Args:
            action: One of the actions listed above
            symbol: Coin symbol (BTC, ETH) — auto-converted to pair for pair_history
            exchange: Exchange name or comma-separated list for coin_history
            interval: Candle interval for history actions (1m, 5m, 1h, 4h, 1d)
            limit: Data points (max 1000)
            range: Time range for exchange_list (1h, 4h, 12h, 24h)
            min_amount: Minimum USD amount for order action (default 10000)
        """
        action = action.strip().lower()
        sym = normalize_symbol(symbol)

        if action == "pair_history":
            pair = to_pair(symbol)
            result = await client.get("/api/futures/liquidation/history", {
                "exchange": exchange,
                "symbol": pair,
                "interval": interval,
                "limit": limit,
            })
            return fmt_parsed(result, f"Liq Pair History — {pair} ({exchange})", _fmt_liq_history)

        elif action == "coin_history":
            result = await client.get("/api/futures/liquidation/aggregated-history", {
                "exchange_list": exchange,
                "symbol": sym,
                "interval": interval,
                "limit": limit,
            })
            return fmt_parsed(result, f"Liq Aggregated History — {sym}", _fmt_liq_history)

        elif action == "coin_list":
            result = await client.get("/api/futures/liquidation/coin-list", {
                "exchange": exchange,
            })
            return fmt(result, f"Liq Coin List — {exchange}")

        elif action == "exchange_list":
            params = {"range": range}
            if symbol:
                params["symbol"] = sym
            result = await client.get("/api/futures/liquidation/exchange-list", params)
            return fmt(result, f"Liq Exchange List — {sym or 'All Coins'} ({range})")

        elif action == "order":
            result = await client.get("/api/futures/liquidation/order", {
                "exchange": exchange,
                "symbol": sym,
                "min_liquidation_amount": str(min_amount),
            })
            return fmt_parsed(result, f"Liq Orders — {sym} ({exchange}, min ${min_amount:,})", _fmt_scan_liq_orders)

        else:
            return _err(f"Unknown action '{action}'. Available: pair_history, coin_history, coin_list, exchange_list, order")

    # ═══════════════════════════════════════════════════════════════════════════
    # CATEGORY TOOL — Order Book / L2 (5 endpoints)
    # ═══════════════════════════════════════════════════════════════════════════

    @mcp.tool()
    @_tool_envelope()
    async def coinglass_orderbook_cat(
        action: str = "aggregated_bidask",
        symbol: str = "BTC",
        exchange: str = DEFAULT_EXCHANGE,
        interval: str = "1h",
        limit: int = 100,
        range: str = "1",
        state: int = 1,
    ) -> str:
        """Order Book (L2) data — 5 endpoints in one tool.

        Actions:
        - pair_bidask: Bid/Ask history per pair (pair + exchange + interval + depth range)
        - aggregated_bidask: Aggregated bid/ask across exchanges (coin + exchange_list + interval)
        - heatmap: Orderbook heatmap visualization (pair + exchange + interval, max 100 pts)
        - large_orders: Current large open orders (pair + exchange). BTC>=1M, ETH>=500K, Other>=50K
        - large_orders_history: Completed large orders (pair + exchange + time range + state)

        Args:
            action: One of the actions listed above
            symbol: Coin symbol (BTC, ETH) — auto-converted to pair where needed
            exchange: Exchange name or comma-separated list for aggregated_bidask
            interval: Candle interval for bidask/heatmap (1m, 5m, 15m, 1h, 4h, 1d)
            limit: Data points (max 1000 for bidask, max 100 for heatmap)
            range: Depth percentage for bidask (0.25, 0.5, 0.75, 1, 2, 3, 5, 10)
            state: For large_orders_history only — 1=Open, 2=Filled, 3=Cancelled
        """
        action = action.strip().lower()
        sym = normalize_symbol(symbol)
        pair = to_pair(symbol)

        if action == "pair_bidask":
            result = await client.get("/api/futures/orderbook/ask-bids-history", {
                "exchange": exchange,
                "symbol": pair,
                "interval": interval,
                "limit": limit,
                "range": range,
            })
            return fmt_parsed(result, f"OB Pair Bid/Ask — {pair} ({exchange}, ±{range}%)", _fmt_scan_ob_delta)

        elif action == "aggregated_bidask":
            result = await client.get("/api/futures/orderbook/aggregated-ask-bids-history", {
                "exchange_list": exchange,
                "symbol": sym,
                "interval": interval,
                "limit": limit,
                "range": range,
            })
            return fmt_parsed(result, f"OB Aggregated Bid/Ask — {sym} (±{range}%)", _fmt_scan_ob_delta)

        elif action == "heatmap":
            result = await client.get("/api/futures/orderbook/history", {
                "exchange": exchange,
                "symbol": pair,
                "interval": interval,
                "limit": min(limit, 100),
            })
            return fmt_parsed(result, f"OB Heatmap — {pair} ({exchange})", _fmt_ob_heatmap)

        elif action == "large_orders":
            result = await client.get("/api/futures/orderbook/large-limit-order", {
                "exchange": exchange,
                "symbol": pair,
            })
            return fmt_parsed(result, f"Large Orders — {pair} ({exchange})", _fmt_large_orders)

        elif action == "large_orders_history":
            import time as _time
            # Default: last 24h
            end_ms = int(_time.time() * 1000)
            start_ms = end_ms - (24 * 3600 * 1000)
            result = await client.get("/api/futures/orderbook/large-limit-order-history", {
                "exchange": exchange,
                "symbol": pair,
                "start_time": start_ms,
                "end_time": end_ms,
                "state": state,
            })
            state_label = {1: "Open", 2: "Filled", 3: "Cancelled"}.get(state, str(state))
            return fmt_parsed(result, f"Large Orders History — {pair} ({exchange}, {state_label})", _fmt_large_orders)

        else:
            return _err(f"Unknown action '{action}'. Available: pair_bidask, aggregated_bidask, heatmap, large_orders, large_orders_history")

    # ═══════════════════════════════════════════════════════════════════════════
    # CATEGORY TOOL — Hyperliquid (3 endpoints)
    # ═══════════════════════════════════════════════════════════════════════════

    @mcp.tool()
    @_tool_envelope()
    async def coinglass_hyperliquid_cat(
        action: str = "long_short_ratio",
        symbol: str = "BTC",
        interval: str = "1d",
        limit: int = 100,
        current_page: int = 1,
    ) -> str:
        """Hyperliquid on-chain data — 3 endpoints in one tool.

    Actions:
    - long_short_ratio: Global L/S account ratio history on Hyperliquid (symbol + interval)
    - wallet_distribution: Wallet position distribution by tier (Shrimp→Leviathan, no params)
    - positions: Individual wallet positions by coin (symbol + page)

    Args:
        action: One of the actions listed above
        symbol: Coin symbol (BTC, ETH, SOL) — for long_short_ratio and positions
        interval: Time interval for long_short_ratio only (5m, 1h, 1d)
        limit: Data points for long_short_ratio (max 1000)
        current_page: Page number for positions action (paginated results)
        """
        action = action.strip().lower()
        sym = normalize_symbol(symbol)

        if action == "long_short_ratio":
            result = await client.get(
                "/api/hyperliquid/global-long-short-account-ratio/history",
                {"symbol": sym, "interval": interval, "limit": limit},
            )
            return fmt_parsed(result, f"Hyperliquid L/S Ratio — {sym} ({interval})", _fmt_p2_hyperliquid_ls)

        elif action == "wallet_distribution":
            result = await client.get(
                "/api/hyperliquid/wallet/position-distribution", {},
            )
            return fmt(result, "Hyperliquid Wallet Position Distribution (all tiers)")

        elif action == "positions":
            result = await client.get(
                "/api/hyperliquid/position",
                {"symbol": sym, "current_page": str(current_page)},
            )
            return fmt(result, f"Hyperliquid Positions — {sym} (page {current_page})")

        else:
            return _err(f"Unknown action '{action}'. Available: long_short_ratio, wallet_distribution, positions")

    # ═══════════════════════════════════════════════════════════════════════════
    # CATEGORY TOOL — Futures Taker Buy/Sell & Volume (4 endpoints)
    # ═══════════════════════════════════════════════════════════════════════════

    @mcp.tool()
    @_tool_envelope()
    async def coinglass_futures_taker_cat(
        action: str = "coin_taker",
        symbol: str = "BTC",
        exchange: str = DEFAULT_EXCHANGE,
        interval: str = "1h",
        limit: int = 100,
        unit: str = "usd",
    ) -> str:
        """Futures Taker Buy/Sell & Volume data — 4 endpoints in one tool.

    Actions:
    - cvd_pair: CVD per pair on single exchange (pair + exchange + interval)
    - footprint: Footprint chart — buy/sell vol at each price level (pair + exchange, 90d max, Pro+)
    - coin_taker: Aggregated taker buy/sell volume across exchanges (coin + exchange_list + unit)
    - pair_taker: Per-pair taker buy/sell volume on single exchange (pair + exchange + interval)

    Args:
        action: One of the actions listed above
        symbol: Coin symbol (BTC, ETH) — auto-converted to pair for cvd_pair/footprint/pair_taker
        exchange: Exchange name. For coin_taker: comma-separated list (Binance,OKX,Bybit)
        interval: Candle interval (1m, 5m, 15m, 30m, 1h, 4h, 8h, 12h, 1d, 1w)
        limit: Data points (max 1000)
        unit: For coin_taker only — 'usd' or 'coin'
        """
        action = action.strip().lower()
        sym = normalize_symbol(symbol)
        pair = to_pair(symbol)

        if action == "cvd_pair":
            result = await client.get(
                "/api/futures/cvd/history",
                {"exchange": exchange, "symbol": pair, "interval": interval, "limit": limit},
            )
            return fmt_parsed(result, f"Futures CVD (pair) — {pair} ({exchange}, {interval})", lambda d: _fmt_scan_cvd(d, "Futures CVD"))

        elif action == "footprint":
            result = await client.get(
                "/api/futures/volume/footprint-history",
                {"exchange": exchange, "symbol": pair, "interval": interval, "limit": limit},
            )
            return fmt(result, f"Footprint — {pair} ({exchange}, {interval})")

        elif action == "coin_taker":
            result = await client.get(
                "/api/futures/aggregated-taker-buy-sell-volume/history",
                {"exchange_list": exchange, "symbol": sym, "interval": interval,
                 "limit": limit, "unit": unit},
            )
            return fmt_parsed(result, f"Aggregated Taker Buy/Sell — {sym} ({exchange}, {interval})", _fmt_scan_taker)

        elif action == "pair_taker":
            result = await client.get(
                "/api/futures/v2/taker-buy-sell-volume/history",
                {"exchange": exchange, "symbol": pair, "interval": interval, "limit": limit},
            )
            return fmt_parsed(result, f"Pair Taker Buy/Sell — {pair} ({exchange}, {interval})", _fmt_scan_taker)

        else:
            return _err(f"Unknown action '{action}'. Available: cvd_pair, footprint, coin_taker, pair_taker")

    # ═══════════════════════════════════════════════════════════════════════════
    # CATEGORY TOOL — Spots Trading Market (4 endpoints)
    # ═══════════════════════════════════════════════════════════════════════════

    @mcp.tool()
    @_tool_envelope()
    async def coinglass_spot_market_cat(
        action: str = "coins_markets",
        symbol: str = "BTC",
        page: int = 1,
        per_page: int = 20,
    ) -> str:
        """Spot Trading Market data — 4 endpoints in one tool.

    Actions:
    - supported_coins: List all supported spot coin symbols (no params)
    - supported_pairs: List all spot exchanges and their trading pairs (no params)
    - coins_markets: Spot market overview — price, volume, buy/sell, net flow across timeframes (paginated, Standard+)
    - pairs_markets: All spot trading pairs for a coin with volume/flow data (requires symbol)

    Args:
        action: One of the actions listed above
        symbol: Coin symbol (BTC, ETH) — needed for pairs_markets
        page: Page number for coins_markets
        per_page: Results per page for coins_markets
        """
        action = action.strip().lower()
        sym = normalize_symbol(symbol)

        if action == "supported_coins":
            result = await client.get("/api/spot/supported-coins", {})
            return fmt(result, "Spot Supported Coins")

        elif action == "supported_pairs":
            result = await client.get("/api/spot/supported-exchange-pairs", {})
            return fmt(result, "Spot Supported Exchanges & Pairs")

        elif action == "coins_markets":
            result = await client.get(
                "/api/spot/coins-markets",
                {"page": page, "per_page": per_page},
            )
            return fmt_parsed(result, f"Spot Coins Markets (page {page})", _fmt_coins_markets)

        elif action == "pairs_markets":
            result = await client.get(
                "/api/spot/pairs-markets", {"symbol": sym},
            )
            return fmt(result, f"Spot Pairs Markets — {sym}")

        else:
            return _err(f"Unknown action '{action}'. Available: supported_coins, supported_pairs, coins_markets, pairs_markets")

    # ═══════════════════════════════════════════════════════════════════════════
    # CATEGORY TOOL — Spots Order Book (5 endpoints)
    # ═══════════════════════════════════════════════════════════════════════════

    @mcp.tool()
    @_tool_envelope()
    async def coinglass_spot_orderbook_cat(
        action: str = "aggregated_bidask",
        symbol: str = "BTC",
        exchange: str = DEFAULT_EXCHANGE,
        interval: str = "1h",
        limit: int = 100,
        range: str = "1",
        state: int = 1,
    ) -> str:
        """Spot Order Book data — 5 endpoints in one tool.

    Actions:
    - pair_bidask: Bid/Ask history per pair (pair + exchange + interval + depth range)
    - aggregated_bidask: Aggregated bid/ask across exchanges (coin + exchange_list + interval + range)
    - heatmap: Orderbook heatmap visualization (pair + exchange + interval, max 100 pts)
    - large_orders: Current large open orders (pair + exchange). BTC>=350K, ETH>=250K, Other>=10K
    - large_orders_history: Completed large orders (pair + exchange + state). state: 1=Open 2=Filled 3=Cancelled

    Args:
        action: One of the actions listed above
        symbol: Coin symbol (BTC, ETH) — auto-converted to pair where needed
        exchange: Exchange name or comma-separated list for aggregated_bidask
        interval: Candle interval for bidask/heatmap (1m, 5m, 15m, 1h, 4h, 1d)
        limit: Data points (max 1000 for bidask, max 100 for heatmap)
        range: Depth percentage for bidask (0.25, 0.5, 0.75, 1, 2, 3, 5, 10)
        state: For large_orders_history only — 1=Open, 2=Filled, 3=Cancelled
        """
        action = action.strip().lower()
        sym = normalize_symbol(symbol)
        pair = to_pair(symbol)

        if action == "pair_bidask":
            result = await client.get(
                "/api/spot/orderbook/ask-bids-history",
                {"exchange": exchange, "symbol": pair, "interval": interval,
                 "limit": limit, "range": range},
            )
            return fmt_parsed(result, f"Spot OB Pair Bid/Ask — {pair} ({exchange}, ±{range}%)", _fmt_scan_ob_delta)

        elif action == "aggregated_bidask":
            result = await client.get(
                "/api/spot/orderbook/aggregated-ask-bids-history",
                {"exchange_list": exchange, "symbol": sym, "interval": interval,
                 "limit": limit, "range": range},
            )
            return fmt_parsed(result, f"Spot OB Aggregated Bid/Ask — {sym} ({exchange}, ±{range}%)", _fmt_scan_ob_delta)

        elif action == "heatmap":
            hm_limit = min(limit, 100)
            result = await client.get(
                "/api/spot/orderbook/history",
                {"exchange": exchange, "symbol": pair, "interval": interval, "limit": hm_limit},
            )
            return fmt_parsed(result, f"Spot OB Heatmap — {pair} ({exchange}, {interval})", _fmt_ob_heatmap)

        elif action == "large_orders":
            result = await client.get(
                "/api/spot/orderbook/large-limit-order",
                {"exchange": exchange, "symbol": pair},
            )
            return fmt(result, f"Spot Large Orders — {pair} ({exchange})")

        elif action == "large_orders_history":
            import time as _time
            end_ms = int(_time.time() * 1000)
            start_ms = end_ms - 7 * 86400 * 1000  # last 7 days
            result = await client.get(
                "/api/spot/orderbook/large-limit-order-history",
                {"exchange": exchange, "symbol": pair,
                 "start_time": str(start_ms), "end_time": str(end_ms), "state": str(state)},
            )
            state_labels = {1: "Open", 2: "Filled", 3: "Cancelled"}
            return fmt(result, f"Spot Large Orders History — {pair} ({exchange}, {state_labels.get(state, state)})")

        else:
            return _err(f"Unknown action '{action}'. Available: pair_bidask, aggregated_bidask, heatmap, large_orders, large_orders_history")

    # ═══════════════════════════════════════════════════════════════════════════
    # CATEGORY TOOL — Futures Indicators (10 endpoints)
    # ═══════════════════════════════════════════════════════════════════════════

    @mcp.tool()
    @_tool_envelope()
    async def coinglass_indicators_cat(
        action: str = "rsi_list",
        symbol: str = "BTC",
        exchange: str = DEFAULT_EXCHANGE,
        interval: str = "1h",
        limit: int = 100,
        window: int = 14,
        series_type: str = "close",
        fast_window: int = 12,
        slow_window: int = 26,
        signal_window: int = 9,
    ) -> str:
        """Futures Technical Indicators — 8 endpoints in one tool.

    Actions (per-coin snapshot — uses list endpoint, filtered by symbol):
    - pair_rsi: RSI for a coin across all timeframes (15m–1w)
    - pair_ma: Moving Average for a coin across all timeframes
    - pair_ema: Exponential MA for a coin across all timeframes
    - pair_macd: MACD for a coin across all timeframes

    Actions (all-coins snapshot — no params needed):
    - rsi_list: RSI across all coins & timeframes
    - ma_list: MA across all coins & timeframes
    - ema_list: EMA across all coins & timeframes
    - macd_list: MACD across all coins & timeframes

    Args:
        action: One of the actions listed above
        symbol: Coin symbol (BTC, ETH) — auto-converted to pair for pair_* actions
        exchange: Exchange name (Binance, OKX, Bybit)
        interval: Candle interval (1m, 5m, 15m, 1h, 4h, 1d, 1w)
        limit: Data points (max 1000 for most, 4500 for MA/EMA/MACD/RSI)
        window: Lookback window for RSI/MA/EMA/ATR (e.g. 14 for RSI, 10 for MA)
        series_type: Price type — open, high, low, close (default close)
        fast_window: MACD fast period (default 12)
        slow_window: MACD slow period (default 26)
        signal_window: MACD signal period (default 9)
        """
        action = action.strip().lower()
        sym = normalize_symbol(symbol)
        pair = to_pair(symbol)

        # --- All-coins list endpoints (no params) ---
        if action == "rsi_list":
            result = await client.get("/api/futures/rsi/list", {})
            return fmt(result, "Futures RSI List (all coins)")

        elif action == "ma_list":
            result = await client.get("/api/futures/ma/list", {})
            return fmt(result, "Futures MA List (all coins)")

        elif action == "ema_list":
            result = await client.get("/api/futures/ema/list", {})
            return fmt(result, "Futures EMA List (all coins)")

        elif action == "macd_list":
            result = await client.get("/api/futures/macd/list", {})
            return fmt(result, "Futures MACD List (all coins)")

        # --- Per-coin snapshot (uses list endpoint + filter by symbol) ---
        # v4 removed /api/futures/indicators/* per-pair history endpoints.
        # These now fetch the all-coins list and extract the requested symbol.
        elif action == "pair_rsi":
            result = await client.get("/api/futures/rsi/list", {})
            return _indicator_by_symbol(result, sym, "RSI", _fmt_indicator_snapshot)

        elif action == "pair_ma":
            result = await client.get("/api/futures/ma/list", {})
            return _indicator_by_symbol(result, sym, "MA", _fmt_indicator_snapshot)

        elif action == "pair_ema":
            result = await client.get("/api/futures/ema/list", {})
            return _indicator_by_symbol(result, sym, "EMA", _fmt_indicator_snapshot)

        elif action == "pair_macd":
            result = await client.get("/api/futures/macd/list", {})
            return _indicator_by_symbol(result, sym, "MACD", _fmt_indicator_snapshot)

        elif action in ("pair_atr", "whale_index"):
            return _err(f"'{action}' is not available in CoinGlass v4 API. "
                        f"Use rsi_list, ma_list, ema_list, macd_list for indicator snapshots.")

        else:
            return _err(f"Unknown action '{action}'. Available: rsi_list, ma_list, ema_list, macd_list, pair_rsi, pair_ma, pair_ema, pair_macd")

    # ═══════════════════════════════════════════════════════════════════════════
    # CATEGORY TOOL — Index, News & Other (3 endpoints)
    # ═══════════════════════════════════════════════════════════════════════════

    @mcp.tool()
    @_tool_envelope()
    async def coinglass_index_news_cat(
        action: str = "altcoin_season",
        symbol: str = "BTC",
        exchange: str = DEFAULT_EXCHANGE,
        interval: str = "1h",
        limit: int = 100,
        language: str = "en",
        page: int = 1,
        per_page: int = 20,
    ) -> str:
        """Index, Volume Ratio & News — 3 endpoints in one tool.

    Actions:
    - altcoin_season: Altcoin Season Index history (no params needed)
    - futures_spot_ratio: Futures vs Spot volume ratio (exchange_list + coin + interval)
    - news: Latest crypto news articles (language: en/zh/zh-tw, paginated)

    Args:
        action: One of the actions listed above
        symbol: Coin symbol for futures_spot_ratio (BTC, ETH)
        exchange: Exchange or comma-separated list for futures_spot_ratio
        interval: Candle interval for futures_spot_ratio (1m, 5m, 1h, 4h, 1d)
        limit: Data points for futures_spot_ratio (max 1000)
        language: For news — 'en', 'zh', 'zh-tw'
        page: Page number for news
        per_page: Items per page for news
        """
        action = action.strip().lower()
        sym = normalize_symbol(symbol)

        if action == "altcoin_season":
            result = await client.get("/api/index/altcoin-season", {})
            return fmt_parsed(result, "Altcoin Season Index", _fmt_indicator_ts)

        elif action == "futures_spot_ratio":
            result = await client.get(
                "/api/futures_spot_volume_ratio",
                {"exchange_list": exchange, "symbol": sym,
                 "interval": interval, "limit": limit},
            )
            return fmt_parsed(result, f"Futures/Spot Volume Ratio — {sym} ({exchange}, {interval})", _fmt_indicator_ts)

        elif action == "news":
            result = await client.get(
                "/api/article/list",
                {"language": language, "page": page, "per_page": per_page},
            )
            return fmt_parsed(result, f"Crypto News ({language}, page {page})", _fmt_news)

        else:
            return _err(f"Unknown action '{action}'. Available: altcoin_season, futures_spot_ratio, news")
