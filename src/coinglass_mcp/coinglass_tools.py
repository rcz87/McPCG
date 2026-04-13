"""17 core CoinGlass endpoint tools — extracted from server.py.

CRITICAL (1-6):  spot_cvd, futures_cvd, funding_rate, open_interest,
                 liquidation_map, orderbook
IMPORTANT (7-12): price_ohlc, liquidation_history, long_short_ratio,
                  taker_buysell, fr_arbitrage, coins_markets
NICE-TO-HAVE (13-17): whale_alert, fear_greed, footprint, spot_netflow,
                      orderbook_heatmap
"""

from __future__ import annotations

from datetime import datetime, timezone, timedelta

from .client import CoinGlassClient, FetchResult
from .config import (
    Config,
    DEFAULT_EXCHANGE,
    make_envelope,
    normalize_symbol,
    to_pair,
)
from .formatters import (
    _tool_envelope,
    _err,
    fmt,
    fmt_parsed,
    _fmt_scan_cvd,
    _fmt_scan_oi,
    _fmt_scan_ob_delta,
    _fmt_scan_price,
    _fmt_scan_taker,
    _fmt_scan_ls_ratio,
    _fmt_funding_rate_all,
    _fmt_liq_history,
    _fmt_fr_arbitrage,
    _fmt_coins_markets,
    _fmt_whale_alert,
    _fmt_fear_greed,
    _fmt_spot_netflow,
)

WIB = timezone(timedelta(hours=7))

PLAN_FALLBACK_MAP: dict[str, dict[str, str]] = {
    "coinglass_footprint": {
        "required_plan": "standard+",
        "fallback_tool": "coinglass_orderbook",
    },
    "coinglass_liquidation_map": {
        "required_plan": "professional+",
        "fallback_tool": "coinglass_liquidation_cat",
    },
    "coinglass_orderbook_heatmap": {
        "required_plan": "standard+",
        "fallback_tool": "coinglass_orderbook",
    },
}


def register_coinglass_tools(mcp, client: CoinGlassClient, config: Config):
    """Register 17 core CoinGlass tools on the MCP server."""

    # ═══════════════════════════════════════════════════════════════════════════
    # CRITICAL TOOLS (1-6) — Ricoz Scalping Framework Core
    # ═══════════════════════════════════════════════════════════════════════════

    @mcp.tool()
    @_tool_envelope()
    async def coinglass_spot_cvd(
        symbol: str = "BTC",
        interval: str = "5m",
        limit: int = 100,
        exchange: str = DEFAULT_EXCHANGE,
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
            exchange: Exchange name (Binance, OKX, Bybit, etc.)
        """
        sym = normalize_symbol(symbol)
        result = await client.get("/api/spot/aggregated-cvd/history", {
            "exchange_list": exchange,
            "symbol": sym,
            "interval": interval,
            "limit": limit,
        })
        return fmt_parsed(result, f"Spot CVD — {sym}", lambda d: _fmt_scan_cvd(d, "Spot CVD"))

    @mcp.tool()
    @_tool_envelope()
    async def coinglass_futures_cvd(
        symbol: str = "BTC",
        interval: str = "5m",
        limit: int = 100,
        exchange: str = DEFAULT_EXCHANGE,
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
            exchange: Exchange name (Binance, OKX, Bybit, etc.)
        """
        sym = normalize_symbol(symbol)
        result = await client.get("/api/futures/aggregated-cvd/history", {
            "exchange_list": exchange,
            "symbol": sym,
            "interval": interval,
            "limit": limit,
        })
        return fmt_parsed(result, f"Futures CVD — {sym}", lambda d: _fmt_scan_cvd(d, "Futures CVD"))

    @mcp.tool()
    @_tool_envelope()
    async def coinglass_funding_rate(symbol: str = "") -> str:
        """Get current Funding Rate for ALL coins across all exchanges.

        Funding Rate indicates market sentiment:
        - Positive FR = longs pay shorts (market bullish/overleveraged long)
        - Negative FR = shorts pay longs (market bearish/overleveraged short)
        - Extreme FR (>0.05%) = potential reversal zone
        - Near zero = neutral, good for directional trades

        Args:
            symbol: Ignored — always returns all coins. Accepted for compatibility.

        Returns data for all coins — only coins with active FR data shown.
        """
        _sym_warn = []
        if symbol:
            _sym_warn.append("Symbol parameter is ignored — this endpoint returns global data for all coins.")
        result = await client.get("/api/futures/funding-rate/exchange-list")
        # Flatten to clean format: one row per coin+exchange with active FR
        if isinstance(result.data, list):
            clean = []
            for coin in result.data:
                sym = coin.get("symbol", "")
                margin_list = (coin.get("stablecoin_margin_list")
                               or coin.get("stablecoinMarginList") or [])
                for ex in margin_list:
                    fr = ex.get("funding_rate", ex.get("fundingRate"))
                    if fr is not None:
                        clean.append({
                            "symbol": sym,
                            "exchange": ex.get("exchange", "?"),
                            "funding_rate": fr,
                            "interval_h": ex.get("funding_rate_interval",
                                                 ex.get("fundingRateInterval", 8)),
                            "next_funding": ex.get("next_funding_time",
                                                   ex.get("nextFundingTime")),
                        })
            if clean:
                # Sort by extreme FR first (highest absolute value)
                clean.sort(key=lambda x: abs(x.get("funding_rate", 0)), reverse=True)
                total = len(clean)
                top50 = clean[:50]
                result = FetchResult(
                    data=top50, age_seconds=result.age_seconds,
                    is_cached=result.is_cached, fetched_at=result.fetched_at,
                )
        return fmt_parsed(result, f"Funding Rate — Top 50 Extreme FR (of {total} active)",
                          _fmt_funding_rate_all, extra_warnings=_sym_warn)

    @mcp.tool()
    @_tool_envelope()
    async def coinglass_open_interest(
        symbol: str = "BTC",
        interval: str = "5m",
        limit: int = 100,
        exchange: str = DEFAULT_EXCHANGE,
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
            exchange: Exchange name (Binance, OKX, Bybit, etc.)
        """
        sym = normalize_symbol(symbol)
        params: dict = {
            "symbol": sym,
            "interval": interval,
            "limit": limit,
        }
        if exchange:
            params["exchange_list"] = exchange
        result = await client.get("/api/futures/open-interest/aggregated-history", params)
        return fmt_parsed(result, f"Open Interest — {sym}", _fmt_scan_oi)

    @mcp.tool()
    @_tool_envelope()
    async def coinglass_liquidation_map(
        symbol: str = "BTC",
        exchange: str = DEFAULT_EXCHANGE,
        range: str = "3d",
    ) -> str:
        """Get Liquidation Heatmap — shows where liquidation clusters are.

        ⚠️ Requires Professional or Enterprise plan.

        Critical for identifying:
        - Magnetic zones (price tends to move toward liquidation clusters)
        - Stop loss clusters that market makers target
        - Potential reversal zones after liquidation sweeps

        Args:
            symbol: Coin symbol (BTC, ETH, SOL, etc.) — auto-converted to pair (BTCUSDT)
            exchange: Exchange name (Binance, OKX, Bybit, etc.)
            range: Time range (12h, 24h, 3d, 7d, 30d, 90d, 180d, 1y)
        """
        if not config.has_feature("liquidation_heatmap"):
            return _err(
                f"Liquidation Heatmap requires Professional or Enterprise plan. Current plan: {config.plan}",
                access=PLAN_FALLBACK_MAP["coinglass_liquidation_map"],
                fallback="Try coinglass_liquidation_cat")
        pair = to_pair(symbol)
        result = await client.get("/api/futures/liquidation/heatmap/model1", {
            "exchange": exchange,
            "symbol": pair,
            "range": range,
        })
        return fmt(result, f"Liquidation Heatmap — {pair} ({exchange}, {range})")

    @mcp.tool()
    @_tool_envelope()
    async def coinglass_orderbook(
        symbol: str = "BTC",
        exchange: str = DEFAULT_EXCHANGE,
        interval: str = "5m",
        limit: int = 100,
        range: str = "1",
    ) -> str:
        """Get Aggregated Orderbook Ask/Bids History — OBDelta (bid/ask imbalance).

        Shows orderbook depth imbalance over time:
        - More bids than asks = buying pressure (bullish)
        - More asks than bids = selling pressure (bearish)
        - Sudden bid wall = potential support
        - Sudden ask wall = potential resistance

        Args:
            symbol: Coin symbol (BTC, ETH, SOL, etc.)
            exchange: Exchange name or comma-separated list (Binance, OKX, Bybit or ALL)
            interval: Candle interval (1m, 5m, 15m, 1h, 4h, 1d)
            limit: Number of data points (max 1000)
            range: Depth percentage (0.25, 0.5, 0.75, 1, 2, 3, 5, 10)
        """
        sym = normalize_symbol(symbol)
        result = await client.get("/api/futures/orderbook/aggregated-ask-bids-history", {
            "exchange_list": exchange,
            "symbol": sym,
            "interval": interval,
            "limit": limit,
            "range": range,
        })
        return fmt_parsed(result, f"Orderbook Delta — {normalize_symbol(symbol)}", _fmt_scan_ob_delta)

    # ═══════════════════════════════════════════════════════════════════════════
    # IMPORTANT TOOLS (7-12) — Supporting Analytics
    # ═══════════════════════════════════════════════════════════════════════════

    @mcp.tool()
    @_tool_envelope()
    async def coinglass_price_ohlc(
        symbol: str = "BTC",
        interval: str = "5m",
        limit: int = 100,
        exchange: str = DEFAULT_EXCHANGE,
    ) -> str:
        """Get price OHLC history from futures markets.

        Args:
            symbol: Coin symbol (BTC, ETH, SOL, etc.) — auto-converted to pair (BTCUSDT)
            interval: Candle interval (1m, 5m, 15m, 30m, 1h, 4h, 12h, 1d)
            limit: Number of data points
            exchange: Exchange name (Binance, OKX, Bybit, etc.)
        """
        pair = to_pair(symbol)
        result = await client.get("/api/futures/price/history", {
            "exchange": exchange,
            "symbol": pair,
            "interval": interval,
            "limit": limit,
        })
        return fmt_parsed(result, f"Price OHLC — {normalize_symbol(symbol)} ({interval})", _fmt_scan_price)

    @mcp.tool()
    @_tool_envelope()
    async def coinglass_liquidation_history(
        symbol: str = "BTC",
        interval: str = "1h",
        limit: int = 100,
        exchange: str = DEFAULT_EXCHANGE,
    ) -> str:
        """Get pair liquidation history — volume of liquidations over time.

        Shows when and how much was liquidated:
        - High liquidation volume = volatile period
        - Long liquidations at support = potential bottom
        - Short liquidations at resistance = potential top

        Args:
            symbol: Coin symbol (BTC, ETH, SOL, etc.) — auto-converted to pair (BTCUSDT)
            interval: Candle interval (1m, 5m, 15m, 30m, 1h, 4h, 12h, 1d)
            limit: Number of data points
            exchange: Exchange name (Binance, OKX, Bybit, etc.)
        """
        pair = to_pair(symbol)
        result = await client.get("/api/futures/liquidation/history", {
            "exchange": exchange,
            "symbol": pair,
            "interval": interval,
            "limit": limit,
        })
        return fmt_parsed(result, f"Liquidation History — {normalize_symbol(symbol)}", _fmt_liq_history)

    @mcp.tool()
    @_tool_envelope()
    async def coinglass_long_short_ratio(
        symbol: str = "BTC",
        interval: str = "1h",
        limit: int = 100,
        exchange: str = DEFAULT_EXCHANGE,
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
            exchange: Exchange name (Binance, OKX, Bybit, etc.)
        """
        pair = to_pair(symbol)
        result = await client.get("/api/futures/global-long-short-account-ratio/history", {
            "exchange": exchange,
            "symbol": pair,
            "interval": interval,
            "limit": limit,
        })
        return fmt_parsed(result, f"Long/Short Ratio — {normalize_symbol(symbol)}", _fmt_scan_ls_ratio)

    @mcp.tool()
    @_tool_envelope()
    async def coinglass_taker_buysell(
        symbol: str = "BTC",
        interval: str = "5m",
        limit: int = 100,
        exchange: str = DEFAULT_EXCHANGE,
    ) -> str:
        """Get Taker Buy/Sell Volume — shows aggressor side.

        Taker = market orders (aggressive traders):
        - Taker buy > sell = aggressive buyers, bullish
        - Taker sell > buy = aggressive sellers, bearish
        - Supports CVD signals

        Args:
            symbol: Coin symbol (BTC, ETH, SOL, etc.) — auto-converted to pair (BTCUSDT)
            interval: Candle interval (1m, 5m, 15m, 30m, 1h, 4h, 12h, 1d)
            limit: Number of data points
            exchange: Exchange name (Binance, OKX, Bybit, etc.)
        """
        sym = normalize_symbol(symbol)
        result = await client.get("/api/futures/aggregated-taker-buy-sell-volume/history", {
            "exchange_list": exchange,
            "symbol": sym,
            "interval": interval,
            "limit": limit,
        })
        return fmt_parsed(result, f"Taker Buy/Sell — {sym}", _fmt_scan_taker)

    @mcp.tool()
    @_tool_envelope()
    async def coinglass_fr_arbitrage(
        usd: int = 10000,
        exchange: str = "",
    ) -> str:
        """Get Funding Rate Arbitrage — find extreme funding rates across coins.

        Shows coins with highest/lowest funding rates:
        - Extreme positive FR = potential short opportunity
        - Extreme negative FR = potential long opportunity
        - Good for finding FR arbitrage trades

        Args:
            usd: Position size in USD for calculating FR income (default: 10000)
            exchange: Comma-separated exchange filter (e.g. Binance,OKX)
        """
        params: dict = {"usd": usd}
        if exchange:
            params["exchange_list"] = exchange
        result = await client.get("/api/futures/funding-rate/arbitrage", params)
        # Sort by APR descending, show top 20 best opportunities
        if isinstance(result.data, list) and result.data:
            sorted_data = sorted(result.data, key=lambda x: abs(x.get("apr", 0)), reverse=True)
            total = len(sorted_data)
            top20 = sorted_data[:20]
            result = FetchResult(
                data=top20, age_seconds=result.age_seconds,
                is_cached=result.is_cached, fetched_at=result.fetched_at,
            )
            return fmt_parsed(result, f"Funding Rate Arbitrage — top 20 of {total} by APR (${usd} position)", _fmt_fr_arbitrage)
        return fmt_parsed(result, f"Funding Rate Arbitrage — ${usd} position", _fmt_fr_arbitrage)

    @mcp.tool()
    @_tool_envelope()
    async def coinglass_coins_markets(
        exchange: str = "",
        page: int = 1,
        per_page: int = 20,
    ) -> str:
        """Get market overview for all futures coins.

        Returns comprehensive market data including:
        - Price, 24h change, volume
        - Open Interest, FR, Long/Short ratio
        - Good for market scanning and finding opportunities

        Args:
            exchange: Comma-separated exchange filter (e.g. Binance,OKX)
            page: Page number (default: 1)
            per_page: Results per page (default: 20)
        """
        params: dict = {"page": page, "per_page": per_page}
        if exchange:
            params["exchange_list"] = exchange
        result = await client.get("/api/futures/coins-markets", params)
        return fmt_parsed(result, f"Futures Market Overview — page {page}", _fmt_coins_markets)

    # ═══════════════════════════════════════════════════════════════════════════
    # NICE-TO-HAVE TOOLS (13-17)
    # ═══════════════════════════════════════════════════════════════════════════

    @mcp.tool()
    @_tool_envelope()
    async def coinglass_whale_alert(symbol: str = "") -> str:
        """Get Hyperliquid whale position alerts (~200 most recent, positions > $1M).

        Shows large trader positions on Hyperliquid:
        - Whale opening large long = bullish signal
        - Whale opening large short = bearish signal
        - Track whale PnL for sentiment
        - position_action: 1=open, 2=close | position_size: positive=long, negative=short

        Args:
            symbol: Ignored — returns all whales. Accepted for compatibility.
        """
        _sym_warn = []
        if symbol:
            _sym_warn.append("Symbol parameter is ignored — this endpoint returns all whale positions globally.")
        result = await client.get("/api/hyperliquid/whale-alert")
        return fmt_parsed(result, "Whale Alerts — Hyperliquid", _fmt_whale_alert,
                          extra_warnings=_sym_warn)

    @mcp.tool()
    @_tool_envelope()
    async def coinglass_fear_greed(symbol: str = "") -> str:
        """Get Fear & Greed Index history.

        Market sentiment indicator:
        - 0-25 = Extreme Fear (contrarian BUY zone)
        - 25-45 = Fear
        - 45-55 = Neutral
        - 55-75 = Greed
        - 75-100 = Extreme Greed (contrarian SELL zone)

        Args:
            symbol: Ignored — index is market-wide. Accepted for compatibility.
        """
        _sym_warn = []
        if symbol:
            _sym_warn.append("Symbol parameter is ignored — Fear & Greed Index is market-wide.")
        result = await client.get("/api/index/fear-greed-history")
        return fmt_parsed(result, "Fear & Greed Index", _fmt_fear_greed,
                          extra_warnings=_sym_warn)

    @mcp.tool()
    @_tool_envelope()
    async def coinglass_footprint(
        symbol: str = "BTC",
        interval: str = "5m",
        limit: int = 10,
        exchange: str = DEFAULT_EXCHANGE,
    ) -> str:
        """Get Footprint chart — buy/sell volume at each price level.

        Shows WHERE buyers and sellers are active:
        - High buy imbalance at a level = support zone
        - High sell imbalance at a level = resistance zone
        - Total buy > sell = buyers absorbing (bullish)
        - Requires Standard plan or higher

        Args:
            symbol: Coin symbol (BTC, ETH, SOL, etc.)
            interval: Candle interval (5m, 15m, 1h, etc.)
            limit: Number of candles (default 10)
            exchange: Exchange name (Binance, OKX, Bybit, etc.)

        Response format per level:
        [price_low, price_high, buy_qty, sell_qty, buy_quote, sell_quote,
         buy_usdt, sell_usdt, buy_count, sell_count]
        """
        pair = to_pair(symbol)
        sym = normalize_symbol(symbol)
        result = await client.get("/api/futures/volume/footprint-history", {
            "exchange": exchange,
            "symbol": pair,
            "interval": interval,
            "limit": limit,
        })

        header = f"## Footprint — {sym} ({interval}, {exchange})\n\n"
        _fp_warnings = []
        if result.is_expired:
            _fp_warnings.append(f"DATA EXPIRED ({result.age_seconds:.0f}s old) — DO NOT USE FOR ENTRY")
        elif result.is_stale:
            _fp_warnings.append(f"DATA STALE ({result.age_seconds:.0f}s old)")

        data = result.data
        if not isinstance(data, list) or len(data) == 0:
            return make_envelope("failed", "coinglass",
                                 header + "**No footprint data available.**",
                                 data_age_seconds=result.age_seconds, warnings=_fp_warnings)

        # Filter out null candles (still forming)
        valid = [c for c in data if c is not None and isinstance(c, list) and len(c) >= 2]
        if not valid:
            return make_envelope("failed", "coinglass",
                                 header + "**All candles still forming (null). Try larger limit.**",
                                 data_age_seconds=result.age_seconds, warnings=_fp_warnings)

        output = header
        for candle in valid[-5:]:  # Show last 5 completed candles
            ts = candle[0]
            levels = candle[1]
            if not isinstance(levels, list) or not levels:
                continue

            ts_str = datetime.fromtimestamp(ts, tz=WIB).strftime("%H:%M")
            total_buy = sum(l[6] for l in levels if len(l) > 7)
            total_sell = sum(l[7] for l in levels if len(l) > 7)
            delta = total_buy - total_sell
            dominant = "BUYERS" if delta > 0 else "SELLERS"
            buy_count = sum(l[8] for l in levels if len(l) > 8)
            sell_count = sum(l[9] for l in levels if len(l) > 9)

            output += f"### {ts_str} — **{dominant}** (delta ${delta:+,.0f})\n"
            output += f"Buy: ${total_buy:,.0f} ({buy_count} trades) | Sell: ${total_sell:,.0f} ({sell_count} trades)\n"

            # Top 3 imbalance levels
            ranked = sorted(levels, key=lambda l: abs(l[6] - l[7]) if len(l) > 7 else 0, reverse=True)
            for l in ranked[:3]:
                if len(l) > 7:
                    mid = (l[0] + l[1]) / 2
                    side = "BUY wall" if l[6] > l[7] else "SELL wall"
                    output += f"- ${mid:,.2f}: buy ${l[6]:,.0f} vs sell ${l[7]:,.0f} → **{side}** ${abs(l[6]-l[7]):,.0f}\n"
            output += "\n"

        _fp_status = "failed" if result.is_expired else "success"
        return make_envelope(_fp_status, "coinglass", output,
                             data_age_seconds=result.age_seconds, warnings=_fp_warnings)

    @mcp.tool()
    @_tool_envelope()
    async def coinglass_spot_netflow(
        symbol: str = "BTC",
        exchange: str = "Binance, Bybit, OKX, Bitget, Gate",
    ) -> str:
        """Get spot exchange net flow — coins moving in/out of exchanges.

        Returns net flow across multiple timeframes (5m to 1y) in a single response.
        - Net inflow (positive) = more buy volume (bullish)
        - Net outflow (negative) = more sell volume (bearish)

        Args:
            symbol: Coin symbol (BTC, ETH, SOL, etc.)
            exchange: Comma-separated exchange list (default: Binance, Bybit, OKX, Bitget, Gate)
        """
        sym = normalize_symbol(symbol)
        result = await client.get("/api/spot/coin/netflow", {
            "symbol": sym,
            "exchange_list": exchange,
        })
        return fmt_parsed(result, f"Spot Net Flow — {sym}", _fmt_spot_netflow)

    @mcp.tool()
    @_tool_envelope()
    async def coinglass_orderbook_heatmap(
        symbol: str = "BTC",
        exchange: str = DEFAULT_EXCHANGE,
        interval: str = "1h",
        limit: int = 100,
    ) -> str:
        """Get Orderbook Heatmap data — visual representation of order depth.

        Shows where large orders are placed:
        - Dense bid zones = potential support levels
        - Dense ask zones = potential resistance levels
        - Requires Standard plan or higher
        - History: 1m=3days, 5m=15days, others=150days

        Args:
            symbol: Coin symbol (BTC, ETH, SOL, etc.) — auto-converted to pair (BTCUSDT)
            exchange: Exchange name (Binance, OKX, Bybit, etc.)
            interval: Candle interval (1m, 5m, 15m, 1h, 4h, 1d)
            limit: Data points (max 100)
        """
        pair = to_pair(symbol)
        result = await client.get("/api/futures/orderbook/history", {
            "exchange": exchange,
            "symbol": pair,
            "interval": interval,
            "limit": min(limit, 100),
        })
        return fmt(result, f"Orderbook Heatmap — {normalize_symbol(symbol)}")
