# McPCG — CoinGlass + Binance MCP Server

Crypto derivatives analytics MCP server optimized for Ricoz Scalping Framework.
Total: **52 tools** (33 CoinGlass + 19 Binance)

## CoinGlass Tools (33 tools)

Require `COINGLASS_API_KEY` env var. All responses include `data_age` and staleness warnings.

### Critical (Ricoz Framework Core)
- `coinglass_spot_cvd` — PRIMARY VETO SIGNAL
- `coinglass_futures_cvd` — Entry filter
- `coinglass_funding_rate` — Crowding indicator
- `coinglass_open_interest` — Position buildup
- `coinglass_liquidation_cat` — Liquidation overview
- `coinglass_taker_buysell` — Net taker flow

### Supporting Tools
- `coinglass_orderbook` / `coinglass_orderbook_cat` / `coinglass_orderbook_heatmap`
- `coinglass_long_short_ratio` / `coinglass_long_short_cat`
- `coinglass_liquidation_history` / `coinglass_liquidation_map`
- `coinglass_funding_rate_cat` / `coinglass_fr_arbitrage`
- `coinglass_futures_taker_cat` / `coinglass_spot_market_cat`
- `coinglass_spot_netflow` / `coinglass_spot_orderbook_cat`
- `coinglass_coins_markets` / `coinglass_trading_market`
- `coinglass_indicators_cat` / `coinglass_index_news_cat`
- `coinglass_hyperliquid_cat` / `coinglass_fear_greed`
- `coinglass_price_ohlc` / `coinglass_footprint`
- `coinglass_whale_alert`

### Aggregate & Utility
- `coinglass_full_scan` — 2-phase 11-endpoint comprehensive scan
- `coinglass_compare` — Compare now vs X hours ago
- `coinglass_trend` — Metric trend over time
- `coinglass_storage_stats` — Cache/storage status

## Binance API Tools (19 tools)

Direct Binance market data — **NO API key required**.

### Spot Market Data (8 tools)
- `binance_spot_price` — Latest price (weight 2)
- `binance_spot_depth` — Order book L2 up to 5000 levels
- `binance_spot_klines` — OHLCV + taker buy volume
- `binance_spot_trades` — Recent trades with buyer/seller maker flag
- `binance_spot_agg_trades` — Aggregated trades
- `binance_spot_ticker_24h` — 24hr statistics
- `binance_spot_book_ticker` — Best bid/ask
- `binance_spot_avg_price` — 5-min average price

### Futures Market Data (11 tools)
- `binance_futures_price` — Mark price + live funding rate
- `binance_futures_funding_rate` — Funding rate history
- `binance_futures_open_interest` — Current OI snapshot
- `binance_futures_oi_history` — OI history (cross-check CoinGlass)
- `binance_futures_long_short_ratio` — Global L/S account ratio
- `binance_futures_top_ls_ratio` — Top trader L/S position ratio
- `binance_futures_taker_volume` — Taker buy/sell ratio (cross-check FutCVD)
- `binance_futures_klines` — Futures OHLCV
- `binance_futures_depth` — Futures order book L2
- `binance_futures_ticker_24h` — Futures 24hr stats
- `binance_futures_liquidation` — Recent forced liquidations (may need API key)

### Binance Rate Limits
- 6000 weight/minute per IP (safe margin: 5500)
- Built-in rate limiter in binance_client.py
- Spot: fallback URLs (api1, api2, api4)
- Futures: retry on timeout

### Cross-Check Matrix

| Data Point     | CoinGlass Tool              | Binance Tool                       |
|----------------|-----------------------------|------------------------------------|
| Funding Rate   | `coinglass_funding_rate`    | `binance_futures_funding_rate`     |
| Open Interest  | `coinglass_open_interest`   | `binance_futures_oi_history`       |
| L/S Ratio      | `coinglass_long_short_ratio`| `binance_futures_long_short_ratio` |
| Taker Flow     | `coinglass_taker_buysell`   | `binance_futures_taker_volume`     |
| Order Book     | `coinglass_orderbook`       | `binance_futures_depth`            |
| Liquidation    | `coinglass_liquidation_cat` | `binance_futures_liquidation`      |
| Price/OHLCV    | `coinglass_price_ohlc`      | `binance_spot_klines`              |

## Architecture

```
src/coinglass_mcp/
├── server.py          — FastMCP server + all tool registration
├── client.py          — CoinGlass HTTP client (httpx)
├── binance_client.py  — Binance HTTP client (httpx, no API key)
├── binance_spot.py    — 8 spot market data tools
├── binance_futures.py — 11 futures market data tools
├── config.py          — CoinGlass config + symbol normalization
└── storage.py         — SQLite persistent cache
```
