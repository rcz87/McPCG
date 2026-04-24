"""ALL-IN-ONE Binance direct scan tool — 10 endpoints, NO CoinGlass, NO API key.

Extracted from server.py (lines 4947-5302) to reduce file size.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone, timedelta

from .binance_client import binance_futures_request, binance_spot_request
from .config import make_envelope
from .formatters import _tool_envelope, _fmt_num
from . import okx_client
from . import multi_exchange

WIB = timezone(timedelta(hours=7))


def register_data_binance_tool(mcp):
    """Register the data_binance all-in-one tool."""

    @mcp.tool()
    @_tool_envelope(source="binance")
    async def data_binance(
        symbol: str = "BTC",
        interval: str = "5m",
        limit: int = 30,
    ) -> str:
        """ALL-IN-ONE Binance direct scan — 10 endpoints, NO CoinGlass, NO API key.

        Comprehensive market analysis using only Binance public API:

        Phase 1 — Price & Overview:
          Mark price, funding rate, 24h stats, VWAP

        Phase 2 — Derivatives Flow:
          Open Interest, L/S ratio, top trader L/S, taker buy/sell

        Phase 3 — Order Book & Liquidations:
          Futures depth (bid/ask balance), forced liquidations

        Phase 4 — Spot Cross-Check:
          Spot klines CVD (taker buy vs sell from candle data)

        Args:
            symbol: Coin symbol (BTC, ETH, SOL, HYPE, etc.)
            interval: Candle interval for klines/taker/LS (5m, 15m, 1h, 4h)
            limit: Data points for time-series (default 30, max 100)
        """
        import asyncio

        async def _capped(coro, timeout=10):
            """Cap per-task execution time."""
            try:
                return await asyncio.wait_for(coro, timeout=timeout)
            except asyncio.TimeoutError:
                return TimeoutError(f"Timed out after {timeout}s")

        sym = symbol.strip().upper()
        # Build Binance symbol pairs
        SPOT_MAP = {"HYPE": "HYPER"}
        spot_sym = SPOT_MAP.get(sym, sym) + "USDT"
        fut_sym = sym + "USDT"
        limit = min(limit, 100)

        # Normalize interval for endpoints that only accept certain periods
        valid_periods = ("5m", "15m", "30m", "1h", "2h", "4h", "6h", "12h", "1d")
        period = interval if interval in valid_periods else "5m"

        scan_time = datetime.now(WIB).strftime("%Y-%m-%d %H:%M:%S WIB")

        # ── Fire ALL requests in parallel ──
        tasks = [
            _capped(binance_futures_request("/fapi/v1/premiumIndex", {"symbol": fut_sym}), timeout=10),
            _capped(binance_futures_request("/fapi/v1/ticker/24hr", {"symbol": fut_sym}), timeout=10),
            _capped(binance_futures_request("/fapi/v1/openInterest", {"symbol": fut_sym}), timeout=10),
            _capped(binance_futures_request(
                "/futures/data/openInterestHist",
                {"symbol": fut_sym, "period": period, "limit": limit},
            ), timeout=10),
            _capped(binance_futures_request(
                "/futures/data/globalLongShortAccountRatio",
                {"symbol": fut_sym, "period": period, "limit": limit},
            ), timeout=10),
            _capped(binance_futures_request(
                "/futures/data/topLongShortPositionRatio",
                {"symbol": fut_sym, "period": period, "limit": limit},
            ), timeout=10),
            _capped(binance_futures_request(
                "/futures/data/takerlongshortRatio",
                {"symbol": fut_sym, "period": period, "limit": limit},
            ), timeout=10),
            _capped(binance_futures_request("/fapi/v1/depth", {"symbol": fut_sym, "limit": 100}, weight=10), timeout=10),
            _capped(binance_futures_request("/fapi/v1/klines", {"symbol": fut_sym, "interval": interval, "limit": limit}, weight=5), timeout=10),
            _capped(binance_spot_request("/api/v3/klines", {"symbol": spot_sym, "interval": interval, "limit": limit}, weight=2), timeout=10),
            # Liquidations: use OKX public endpoint (Binance /forceOrders requires API key since 2022)
            _capped(okx_client.okx_request(
                "/api/v5/public/liquidation-orders",
                {"instType": "SWAP", "uly": f"{sym}-USDT", "state": "filled", "limit": "30"},
            ), timeout=10),
        ]

        results = await asyncio.gather(*tasks, return_exceptions=True)
        (bn_premium, bn_ticker24h, bn_oi_snap, bn_oi_hist,
         bn_ls, bn_top_ls, bn_taker, bn_depth,
         bn_fut_klines, bn_spot_klines, okx_liqs) = results

        # ═════════════════════════════════════════════════════════════════
        output = f"# DATA BINANCE — {sym} ({interval})\n"
        output += f"**Scan time:** {scan_time}\n"
        output += f"**Source:** Binance Public API (no API key)\n\n"

        # Helper
        def _safe_float(d, *keys, default=0.0):
            if not isinstance(d, dict):
                return default
            for k in keys:
                if k in d:
                    try:
                        return float(d[k])
                    except (TypeError, ValueError):
                        pass
            return default

        # ═════════════════════════════════════════════════════════════════
        # PHASE 1 — PRICE & OVERVIEW
        # ═════════════════════════════════════════════════════════════════
        output += "---\n\n## Phase 1 — Price & Overview\n\n"

        mark_price = 0.0
        if isinstance(bn_premium, dict) and "markPrice" in bn_premium:
            mark_price = _safe_float(bn_premium, "markPrice")
            index_price = _safe_float(bn_premium, "indexPrice")
            fr = _safe_float(bn_premium, "lastFundingRate")
            fr_pct = fr * 100
            fr_label = "NEGATIF (shorts pay)" if fr < 0 else "POSITIF (longs pay)" if fr > 0 else "NEUTRAL"
            interest_rate = _safe_float(bn_premium, "interestRate") * 100
            next_ts = bn_premium.get("nextFundingTime", 0)
            next_t = datetime.fromtimestamp(int(next_ts) / 1000, tz=WIB).strftime("%H:%M") if next_ts else "N/A"

            output += f"- **Mark Price:** ${mark_price:,.2f}\n"
            output += f"- **Index Price:** ${index_price:,.2f}\n"
            output += f"- **Funding Rate:** {fr_pct:+.4f}% — {fr_label}\n"
            output += f"- **Interest Rate:** {interest_rate:.4f}%\n"
            output += f"- **Next Funding:** {next_t}\n\n"
        elif isinstance(bn_premium, dict) and "error" in bn_premium:
            output += f"**ERROR:** {bn_premium['error']}\n\n"
        else:
            output += "**Mark Price:** Failed to fetch\n\n"

        # 24h Ticker
        if isinstance(bn_ticker24h, dict) and "lastPrice" in bn_ticker24h:
            last = _safe_float(bn_ticker24h, "lastPrice")
            change_pct = _safe_float(bn_ticker24h, "priceChangePercent")
            high = _safe_float(bn_ticker24h, "highPrice")
            low = _safe_float(bn_ticker24h, "lowPrice")
            vol = _safe_float(bn_ticker24h, "quoteVolume")
            vwap = _safe_float(bn_ticker24h, "weightedAvgPrice")
            trades = int(_safe_float(bn_ticker24h, "count"))

            output += f"**24h Stats:**\n"
            output += f"- Change: {change_pct:+.2f}% | High: ${high:,.2f} | Low: ${low:,.2f}\n"
            output += f"- Volume: {_fmt_num(vol)} | VWAP: ${vwap:,.2f} | Trades: {trades:,}\n\n"

        # ═════════════════════════════════════════════════════════════════
        # PHASE 2 — DERIVATIVES FLOW
        # ═════════════════════════════════════════════════════════════════
        output += "---\n\n## Phase 2 — Derivatives Flow\n\n"

        # Open Interest snapshot
        if isinstance(bn_oi_snap, dict) and "openInterest" in bn_oi_snap:
            oi_contracts = _safe_float(bn_oi_snap, "openInterest")
            oi_usd = oi_contracts * mark_price if mark_price else 0
            output += f"**Open Interest:** {oi_contracts:,.0f} {sym} ({_fmt_num(oi_usd)})\n\n"

        # OI History
        if isinstance(bn_oi_hist, list) and bn_oi_hist:
            show = bn_oi_hist[-15:]
            output += f"**OI History ({period}):**\n```\n"
            output += f" {'Time':>16} | {'OI (contracts)':>14} | {'OI (USD)':>12}\n"
            output += f" {'─' * 16} | {'─' * 14} | {'─' * 12}\n"
            for d in show:
                ts = d.get("timestamp", 0)
                ts_str = datetime.fromtimestamp(int(ts) / 1000, tz=WIB).strftime("%H:%M") if ts else "?"
                oi = float(d.get("sumOpenInterest", 0))
                oi_val = float(d.get("sumOpenInterestValue", 0))
                output += f" {ts_str:>16} | {oi:>14,.2f} | {_fmt_num(oi_val):>12}\n"
            output += "```\n"
            first_val = float(bn_oi_hist[0].get("sumOpenInterestValue", 0))
            last_val = float(bn_oi_hist[-1].get("sumOpenInterestValue", 0))
            change = last_val - first_val
            pct = (change / first_val * 100) if first_val else 0
            output += f"**OI Change:** {_fmt_num(first_val)} → {_fmt_num(last_val)} ({pct:+.2f}%)\n\n"

        # L/S Ratio
        if isinstance(bn_ls, list) and bn_ls:
            show = bn_ls[-15:]
            output += f"**Global L/S Account Ratio ({period}):**\n```\n"
            output += f" {'Time':>6} | {'Long %':>8} | {'Short %':>8} | {'Ratio':>8}\n"
            output += f" {'─' * 6} | {'─' * 8} | {'─' * 8} | {'─' * 8}\n"
            for d in show:
                ts = d.get("timestamp", 0)
                ts_str = datetime.fromtimestamp(int(ts) / 1000, tz=WIB).strftime("%H:%M") if ts else "?"
                lp = float(d.get("longAccount", 0)) * 100
                sp = float(d.get("shortAccount", 0)) * 100
                r = float(d.get("longShortRatio", 0))
                output += f" {ts_str:>6} | {lp:>7.1f}% | {sp:>7.1f}% | {r:>8.3f}\n"
            output += "```\n"
            shift = float(bn_ls[-1].get("longShortRatio", 0)) - float(bn_ls[0].get("longShortRatio", 0))
            output += f"**Shift:** {shift:+.3f} ({'more long' if shift > 0 else 'more short'})\n\n"

        # Top Trader L/S
        if isinstance(bn_top_ls, list) and bn_top_ls:
            show = bn_top_ls[-10:]
            output += f"**Top Trader L/S Position ({period}):**\n```\n"
            output += f" {'Time':>6} | {'Long %':>8} | {'Short %':>8} | {'Ratio':>8}\n"
            output += f" {'─' * 6} | {'─' * 8} | {'─' * 8} | {'─' * 8}\n"
            for d in show:
                ts = d.get("timestamp", 0)
                ts_str = datetime.fromtimestamp(int(ts) / 1000, tz=WIB).strftime("%H:%M") if ts else "?"
                lp = float(d.get("longAccount", 0)) * 100
                sp = float(d.get("shortAccount", 0)) * 100
                r = float(d.get("longShortRatio", 0))
                output += f" {ts_str:>6} | {lp:>7.1f}% | {sp:>7.1f}% | {r:>8.3f}\n"
            output += "```\n\n"

        # Taker Buy/Sell
        if isinstance(bn_taker, list) and bn_taker:
            show = bn_taker[-15:]
            output += f"**Futures Taker Buy/Sell ({period}):**\n```\n"
            output += f" {'Time':>6} | {'Buy':>10} | {'Sell':>10} | {'Net':>10} | {'Ratio':>6} | Side\n"
            output += f" {'─' * 6} | {'─' * 10} | {'─' * 10} | {'─' * 10} | {'─' * 6} | ────\n"
            buy_dom = 0
            total_net = 0.0
            for d in show:
                ts = d.get("timestamp", 0)
                ts_str = datetime.fromtimestamp(int(ts) / 1000, tz=WIB).strftime("%H:%M") if ts else "?"
                ratio = float(d.get("buySellRatio", 0))
                buy = float(d.get("buyVol", 0))
                sell = float(d.get("sellVol", 0))
                # Convert to USD if mark_price available
                buy_usd = buy * mark_price if mark_price and buy < 1e6 else buy
                sell_usd = sell * mark_price if mark_price and sell < 1e6 else sell
                net = buy_usd - sell_usd
                total_net += net
                side = "BUY" if ratio >= 1 else "SELL"
                if ratio >= 1:
                    buy_dom += 1
                net_str = f"+{_fmt_num(net)}" if net >= 0 else _fmt_num(net)
                output += f" {ts_str:>6} | {_fmt_num(buy_usd):>10} | {_fmt_num(sell_usd):>10} | {net_str:>10} | {ratio:>6.3f} | {side:>4}\n"
            output += "```\n"
            net_str = f"+{_fmt_num(total_net)}" if total_net >= 0 else _fmt_num(total_net)
            output += f"**Stats:** {buy_dom}/{len(show)} buy-dominant | Net: {net_str}\n\n"

        # ═════════════════════════════════════════════════════════════════
        # PHASE 3 — ORDER BOOK & LIQUIDATIONS
        # ═════════════════════════════════════════════════════════════════
        output += "---\n\n## Phase 3 — Order Book & Liquidations\n\n"

        # Futures Depth
        if isinstance(bn_depth, dict) and "bids" in bn_depth:
            bids = bn_depth.get("bids", [])
            asks = bn_depth.get("asks", [])
            total_bid_val = sum(float(b[0]) * float(b[1]) for b in bids)
            total_ask_val = sum(float(a[0]) * float(a[1]) for a in asks)
            ratio = total_bid_val / total_ask_val if total_ask_val else 0
            dominant = "BIDS (buyers)" if ratio > 1 else "ASKS (sellers)"
            spread = float(asks[0][0]) - float(bids[0][0]) if bids and asks else 0
            spread_pct = (spread / float(bids[0][0]) * 100) if bids and float(bids[0][0]) else 0

            top_n = min(10, len(bids), len(asks))
            output += f"**Futures Order Book (top {len(bids)} levels):**\n```\n"
            output += f" {'Bid Price':>13} | {'Bid $':>10} | {'Ask Price':>13} | {'Ask $':>10}\n"
            output += f" {'─' * 13} | {'─' * 10} | {'─' * 13} | {'─' * 10}\n"
            for i in range(top_n):
                bp, bq = float(bids[i][0]), float(bids[i][1])
                ap, aq = float(asks[i][0]), float(asks[i][1])
                output += f" ${bp:>12,.2f} | {_fmt_num(bp * bq):>10} | ${ap:>12,.2f} | {_fmt_num(ap * aq):>10}\n"
            output += "```\n"
            output += f"**Summary:** Bids {_fmt_num(total_bid_val)} vs Asks {_fmt_num(total_ask_val)} → **{dominant}** (ratio {ratio:.2f})\n"
            output += f"**Spread:** ${spread:,.2f} ({spread_pct:.4f}%)\n\n"

        # Liquidations — OKX public endpoint (Binance /forceOrders requires API key)
        # OKX response shape: {data: [{details: [{bkPx, posSide, sz, ts, ...}]}]}
        # sz is in CONTRACTS → multiply by ct_val (OKX SWAP) for base units, then × bkPx for USD.
        okx_liq_details = []
        if isinstance(okx_liqs, list):
            for group in okx_liqs:
                if isinstance(group, dict):
                    okx_liq_details.extend(group.get("details", []) or [])

        if okx_liq_details:
            # Newest first → chronological (oldest→newest) for display, take last 15
            okx_liq_details.sort(key=lambda d: int(d.get("ts", 0)))
            show = okx_liq_details[-15:]
            ct_val = await multi_exchange._okx_ct_val(fut_sym)  # base per contract
            total_long = 0.0
            total_short = 0.0
            output += f"**Recent Forced Liquidations (OKX public):**\n```\n"
            output += f" {'Price':>13} | {'Qty':>10} | {'Value':>10} | {'Side':>5} | Time\n"
            output += f" {'─' * 13} | {'─' * 10} | {'─' * 10} | {'─' * 5} | ─────\n"
            for d in show:
                bk_px = float(d.get("bkPx") or 0)
                sz_contracts = float(d.get("sz") or 0)
                base_qty = sz_contracts * ct_val
                val = bk_px * base_qty
                # OKX posSide = position that got liquidated
                pos_side = (d.get("posSide") or "").lower()
                liq_side = "LONG" if pos_side == "long" else "SHORT"
                ts = int(d.get("ts") or 0)
                ts_str = datetime.fromtimestamp(ts / 1000, tz=WIB).strftime("%H:%M") if ts else "?"
                if liq_side == "LONG":
                    total_long += val
                else:
                    total_short += val
                output += f" ${bk_px:>12,.2f} | {base_qty:>10,.4f} | {_fmt_num(val):>10} | {liq_side:>5} | {ts_str}\n"
            output += "```\n"
            output += f"**Summary:** LONG liq: {_fmt_num(total_long)} | SHORT liq: {_fmt_num(total_short)} (source: OKX)\n\n"
        elif isinstance(okx_liqs, dict) and "error" in okx_liqs:
            output += f"**Liquidations:** {okx_liqs.get('error', 'unavailable')} (OKX public)\n\n"
        else:
            output += f"**Liquidations:** No recent liquidations for {sym} on OKX\n\n"

        # ═════════════════════════════════════════════════════════════════
        # PHASE 4 — SPOT CROSS-CHECK + VWAP
        # ═════════════════════════════════════════════════════════════════
        output += "---\n\n## Phase 4 — Spot Cross-Check\n\n"

        # Spot Klines CVD
        if isinstance(bn_spot_klines, list) and bn_spot_klines:
            show = bn_spot_klines[-15:]
            output += f"**Spot CVD ({spot_sym}, {interval}):**\n```\n"
            output += f" {'Time':>6} | {'Buy':>10} | {'Sell':>10} | {'Delta':>10} | Side\n"
            output += f" {'─' * 6} | {'─' * 10} | {'─' * 10} | {'─' * 10} | ────\n"
            cum_delta = 0.0
            buy_candles = 0
            for c in show:
                ts_str = datetime.fromtimestamp(c[0] / 1000, tz=WIB).strftime("%H:%M")
                vol = float(c[5])  # base volume
                taker_buy = float(c[9])  # taker buy base volume
                taker_sell = vol - taker_buy
                # Convert to USD
                close_price = float(c[4])
                buy_usd = taker_buy * close_price
                sell_usd = taker_sell * close_price
                delta = buy_usd - sell_usd
                cum_delta += delta
                side = "BUY" if delta > 0 else "SELL"
                if delta > 0:
                    buy_candles += 1
                delta_str = f"+{_fmt_num(delta)}" if delta >= 0 else _fmt_num(delta)
                output += f" {ts_str:>6} | {_fmt_num(buy_usd):>10} | {_fmt_num(sell_usd):>10} | {delta_str:>10} | {side:>4}\n"
            output += "```\n"
            cum_str = f"+{_fmt_num(cum_delta)}" if cum_delta >= 0 else _fmt_num(cum_delta)
            direction = "BUYERS dominant" if cum_delta > 0 else "SELLERS dominant"
            output += f"**Summary:** {buy_candles}/{len(show)} buy-dominant | Cum delta: {cum_str} → **{direction}**\n\n"
        elif isinstance(bn_spot_klines, dict) and "error" in bn_spot_klines:
            output += f"**Spot CVD:** {spot_sym} — {bn_spot_klines.get('error', 'not available')}\n\n"

        # VWAP from futures klines
        if isinstance(bn_fut_klines, list) and len(bn_fut_klines) >= 3:
            try:
                sum_tp_vol = 0.0
                sum_vol = 0.0
                for c in bn_fut_klines:
                    h = float(c[2])
                    l = float(c[3])
                    cl = float(c[4])
                    vol = float(c[5])
                    tp = (h + l + cl) / 3
                    sum_tp_vol += tp * vol
                    sum_vol += vol
                if sum_vol > 0:
                    vwap = sum_tp_vol / sum_vol
                    current = float(bn_fut_klines[-1][4])
                    diff = current - vwap
                    diff_pct = (diff / vwap) * 100
                    position = "ABOVE" if diff > 0 else "BELOW"
                    first_ts = bn_fut_klines[0][0]
                    last_ts = bn_fut_klines[-1][0]
                    first_str = datetime.fromtimestamp(first_ts / 1000, tz=WIB).strftime("%H:%M")
                    last_str = datetime.fromtimestamp(last_ts / 1000, tz=WIB).strftime("%H:%M")
                    total_vol_usd = sum(float(c[7]) for c in bn_fut_klines)
                    output += (
                        f"**Futures VWAP ({len(bn_fut_klines)}x {interval}, {first_str}-{last_str} WIB):**\n"
                        f"- VWAP: ${vwap:,.2f}\n"
                        f"- Price: ${current:,.2f}\n"
                        f"- Price **{position}** VWAP by ${abs(diff):,.2f} ({diff_pct:+.2f}%)\n"
                        f"- Total volume: {_fmt_num(total_vol_usd)}\n\n"
                    )
            except (IndexError, TypeError, ValueError):
                pass

        return make_envelope("success", "binance", output)
