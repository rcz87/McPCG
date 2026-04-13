"""Composite CoinGlass tools — screener, full_scan, compare, trend, storage_stats.

These are the 5 largest tools, extracted from server.py for maintainability.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone, timedelta
from typing import Any

from .client import CoinGlassClient, FetchResult
from .config import (
    normalize_symbol, to_cg_symbol, to_pair,
    DEFAULT_EXCHANGE, make_envelope, Config,
    STALE_WARNING_THRESHOLD,
)
from .binance_client import binance_futures_request, binance_spot_request
from .nansen import nansen_token_flow_intelligence, NANSEN_TOKEN_MAP
from .arkham import arkham_get
from .formatters import (
    _tool_envelope,
    _err,
    _age_banner,
    _fmt_num,
    _ts_wib,
    _get,
    _check_all_zero,
    _format_fr_compact,
    _SCAN_FORMATTERS,
    _fmt_scan_liq_orders,
    _fmt_p2_hyperliquid_ls,
    _fmt_p2_top_position_ls,
    _fmt_p2_spot_large_orders,
    _fmt_nansen_flows,
    _fmt_scan_cvd,
    _fmt_scan_oi,
    _fmt_scan_ob_delta,
    _fmt_scan_price,
    _fmt_scan_taker,
    _fmt_scan_ls_ratio,
    _fmt_scan_fr,
    _fmt_liq_history,
    WIB,
)

# Map metric names to their formatters for compare/trend tools
_METRIC_FORMATTERS = {
    "spot_cvd": lambda data: _fmt_scan_cvd(data, "Spot CVD"),
    "futures_cvd": lambda data: _fmt_scan_cvd(data, "Futures CVD"),
    "open_interest": lambda data: _fmt_scan_oi(data),
    "funding_rate": lambda data: _fmt_scan_fr(data) if isinstance(data, list) and data and isinstance(data[0], dict) and "exchange" in data[0] else json.dumps(data, indent=2, default=str),
    "orderbook": lambda data: _fmt_scan_ob_delta(data),
    "price": lambda data: _fmt_scan_price(data),
    "taker": lambda data: _fmt_scan_taker(data),
    "long_short": lambda data: _fmt_scan_ls_ratio(data),
    "liquidation": lambda data: _fmt_liq_history(data),
}


def _format_metric_data(metric: str, data) -> str:
    """Format metric data using the appropriate formatter, fallback to JSON."""
    formatter = _METRIC_FORMATTERS.get(metric)
    if formatter and isinstance(data, (list, dict)) and data:
        try:
            return formatter(data)
        except Exception:
            pass
    # Fallback: compact JSON (last 5 items for lists)
    if isinstance(data, list) and len(data) > 5:
        return json.dumps(data[-5:], indent=2, default=str) + "\n\n"
    return json.dumps(data, indent=2, default=str) + "\n\n"


def register_coinglass_composite_tools(mcp, client: CoinGlassClient, config: Config):
    """Register 5 composite CoinGlass tools on the MCP server."""

    # ═══════════════════════════════════════════════════════════════════════════════
    # COMPOSITE TOOL — Smart Screener
    # ═══════════════════════════════════════════════════════════════════════════════

    @mcp.tool()
    @_tool_envelope()
    async def coinglass_smart_screener(
        mode: str = "all",
        top_n: int = 15,
        min_oi_usd: float = 10_000_000,
        sensitivity: str = "normal",
        deep_scan: bool = False,
    ) -> str:
        """Screen ALL coins for pump/dump signals BEFORE retail notices.

        v3 — Deep Scan: Phase 1 (coins_markets + whale) → Phase 2 (CVD + OB + Taker for top candidates).

        When deep_scan=False (default): Fast mode, same as v2.
        When deep_scan=True: After initial filter, fetches Spot CVD, Futures CVD,
        Taker flow, and Orderbook for top candidates. Scores 0-100 with 6 dimensions.
        Classifies each coin (ACCUMULATION, DISTRIBUTION, SQUEEZE, etc.)

        EARLY signals (highest weight):
        - Fresh stealth accumulation/distribution (1h OI vs price divergence)
        - Buyer/seller flow transition (L/S ratio flip 4h→1h)
        - Compression detection (price flat + OI rising = breakout imminent)

        MID signals (moderate weight):
        - Building stealth (4h), FR tiered, whale positions, liquidation spikes

        LATE signals + penalties:
        - Bubble, OI exodus, "already moved" penalty, leverage health

        Args:
            mode: "pump" (only pump candidates), "dump" (only dump), "all" (both)
            top_n: Number of coins to return per category (default 15)
            min_oi_usd: Minimum OI in USD to filter noise (default $10M)
            sensitivity: "high" (lower thresholds, more signals), "normal", "low" (higher thresholds, fewer but stronger signals)
            deep_scan: True = fetch CVD/OB/Taker for top candidates, score 0-100 (slower, ~40 extra API calls)
        """
        scan_time = datetime.now(WIB).strftime("%Y-%m-%d %H:%M:%S WIB")

        # ── Sensitivity multipliers ──
        if sensitivity == "high":
            th = 0.7   # lower thresholds by 30%
        elif sensitivity == "low":
            th = 1.5   # raise thresholds by 50%
        else:
            th = 1.0

        # Fetch data in parallel: coins_markets (page1+2) + whale alerts
        tasks = [
            client.get("/api/futures/coins-markets", {"per_page": 200, "page": 1}),
            client.get("/api/hyperliquid/whale-alert"),
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        markets_result, whale_result = results

        # Parse market data
        coins = []
        if isinstance(markets_result, FetchResult) and isinstance(markets_result.data, list):
            coins = markets_result.data
        else:
            return _err(f"Failed to fetch coins-markets data. {markets_result}")

        # Parse whale data — track opens AND closes separately
        whale_longs = {}    # symbol -> total USD long open
        whale_shorts = {}   # symbol -> total USD short open
        whale_closes = {}   # symbol -> total USD close (any direction)
        if isinstance(whale_result, FetchResult) and isinstance(whale_result.data, list):
            for w in whale_result.data:
                sym = w.get("symbol", "").upper()
                action = w.get("position_action")  # 1=open, 2=close
                size = abs(w.get("position_value_usd", 0) or 0)
                is_long = (w.get("position_size", 0) or 0) > 0
                if size < 500_000:
                    continue
                if action == 1:  # Open
                    if is_long:
                        whale_longs[sym] = whale_longs.get(sym, 0) + size
                    else:
                        whale_shorts[sym] = whale_shorts.get(sym, 0) + size
                elif action == 2:  # Close
                    whale_closes[sym] = whale_closes.get(sym, 0) + size

        # Score each coin
        scored = []
        for coin in coins:
            sym = coin.get("symbol", "")
            oi_usd = coin.get("open_interest_usd") or 0
            if oi_usd < min_oi_usd:
                continue

            price = coin.get("current_price") or 0
            pump_score = 0.0
            dump_score = 0.0
            signals = []  # list of (timing, text) tuples
            has_early = False

            # ── Extract all fields ──
            oi_5m = coin.get("open_interest_change_percent_5m") or 0
            oi_1h = coin.get("open_interest_change_percent_1h") or 0
            oi_4h = coin.get("open_interest_change_percent_4h") or 0

            price_1h = coin.get("price_change_percent_1h") or 0
            price_4h = coin.get("price_change_percent_4h") or 0

            vol_1h = coin.get("volume_change_percent_1h") or 0

            fr_raw = coin.get("avg_funding_rate_by_oi") or 0  # CoinGlass already returns %, e.g. -0.028
            fr_pct = fr_raw  # already percentage, e.g. -0.028%

            ls_5m = coin.get("long_short_ratio_5m") or 1.0
            ls_1h = coin.get("long_short_ratio_1h") or 1.0
            ls_4h = coin.get("long_short_ratio_4h") or 1.0
            ls_24h = coin.get("long_short_ratio_24h") or 1.0

            long_liq_4h = coin.get("long_liquidation_usd_4h") or 0
            short_liq_4h = coin.get("short_liquidation_usd_4h") or 0

            oi_mcap = coin.get("open_interest_market_cap_ratio") or 0
            oi_vol_ratio = coin.get("open_interest_volume_ratio") or 0

            # ═══════════════════════════════════════════════════════
            # LAYER 1: EARLY DETECTION (highest priority)
            # ═══════════════════════════════════════════════════════

            # 1A. FRESH STEALTH ACCUMULATION — PUMP
            if oi_1h > 0.3 * th and -0.5 / th <= price_1h <= 0.2 * th:
                s = 3.0
                if oi_5m > 0.1 * th:
                    s += 0.5
                if ls_1h > 1.05:
                    s += 0.5
                pump_score += s
                signals.append(("EARLY", f"⚡ FRESH STEALTH ACCUM: OI 1h +{oi_1h:.1f}% / Price 1h {price_1h:+.1f}%"))
                has_early = True

            # 1B. FRESH STEALTH DISTRIBUTION — DUMP
            if oi_1h > 0.3 * th and -0.2 / th <= price_1h <= 0.5 * th and ls_1h < 0.9 * th:
                s = -3.0
                if ls_5m < 0.85 * th:
                    s -= 0.5
                dump_score += s
                signals.append(("EARLY", f"⚡ STEALTH DISTRIBUTION: OI 1h +{oi_1h:.1f}% / Seller ratio {ls_1h:.2f}"))
                has_early = True

            # 2A. BUYER FLOW TRANSITION — PUMP
            if ls_4h < 0.95 * th and ls_1h > 1.05 / th:
                s = 2.5
                if ls_5m > 1.15 / th:
                    s += 1.0
                pump_score += s
                signals.append(("EARLY", f"⚡ BUYER EMERGING: L/S 4h={ls_4h:.2f} → 1h={ls_1h:.2f}"))
                has_early = True

            # 2B. SELLER FLOW TRANSITION — DUMP
            if ls_4h > 1.05 / th and ls_1h < 0.95 * th:
                s = -2.5
                if ls_5m < 0.85 * th:
                    s -= 1.0
                dump_score += s
                signals.append(("EARLY", f"⚡ SELLER EMERGING: L/S 4h={ls_4h:.2f} → 1h={ls_1h:.2f}"))
                has_early = True

            # 3. COMPRESSION DETECTION — PUMP/DUMP
            if abs(price_1h) < 0.3 * th and oi_1h > 0.3 * th:
                if ls_1h > 1.05:
                    pump_score += 2.0
                    signals.append(("EARLY", f"⚡ COMPRESSION BULLISH: Price 1h {price_1h:+.1f}% / OI +{oi_1h:.1f}%"))
                elif ls_1h < 0.95:
                    dump_score -= 2.0
                    signals.append(("EARLY", f"⚡ COMPRESSION BEARISH: Price 1h {price_1h:+.1f}% / OI +{oi_1h:.1f}%"))
                else:
                    pump_score += 1.0
                    dump_score -= 1.0
                    signals.append(("EARLY", f"⚡ COMPRESSION NEUTRAL: Price 1h {price_1h:+.1f}% / OI +{oi_1h:.1f}% — direction unclear"))
                has_early = True

            # ═══════════════════════════════════════════════════════
            # LAYER 2: EXISTING SIGNALS (reduced weight)
            # ═══════════════════════════════════════════════════════

            # 4. BUILDING STEALTH (4h) — PUMP
            if oi_4h > 1.5 * th and price_4h < 0.5 * th:
                pump_score += 2.0
                signals.append(("MID", f"🟡 BUILDING STEALTH: OI 4h +{oi_4h:.1f}% / Price 4h {price_4h:+.1f}%"))

            # 5. FR TIERED — PUMP/DUMP
            if fr_pct < -0.15 / th:
                pump_score += 2.5
                signals.append(("MID", f"🟡 EXTREME NEG FR: {fr_pct:.4f}%"))
            elif fr_pct < -0.05 / th:
                pump_score += 2.0
                signals.append(("MID", f"🟡 MODERATE NEG FR: {fr_pct:.4f}%"))
            elif fr_pct < -0.01 / th:
                pump_score += 1.0
                signals.append(("MID", f"🟡 MILD NEG FR: {fr_pct:.4f}%"))
            elif fr_pct > 0.15 / th:
                dump_score -= 2.5
                signals.append(("MID", f"🟡 EXTREME HIGH FR: +{fr_pct:.4f}%"))
            elif fr_pct > 0.05 / th:
                dump_score -= 2.0
                signals.append(("MID", f"🟡 MODERATE HIGH FR: +{fr_pct:.4f}%"))
            elif fr_pct > 0.01 / th:
                dump_score -= 1.0
                signals.append(("MID", f"🟡 MILD HIGH FR: +{fr_pct:.4f}%"))

            # 6. WHALE POSITIONS — PUMP/DUMP
            wl = whale_longs.get(sym, 0)
            ws = whale_shorts.get(sym, 0)
            wc = whale_closes.get(sym, 0)
            whale_count = sum(1 for w_sym in [whale_longs, whale_shorts] if sym in w_sym)
            whale_multi = 1.3 if whale_count >= 2 else 1.0

            if wl > 1_000_000:
                pump_score += 3.0 * whale_multi
                signals.append(("MID", f"🐋 WHALE LONG: ${wl/1e6:.1f}M"))
            elif wl > 500_000:
                pump_score += 2.0
                signals.append(("MID", f"🐋 whale long: ${wl/1e6:.1f}M"))

            if ws > 1_000_000:
                dump_score -= 3.0 * whale_multi
                signals.append(("MID", f"🐋 WHALE SHORT: ${ws/1e6:.1f}M"))
            elif ws > 500_000:
                dump_score -= 2.0
                signals.append(("MID", f"🐋 whale short: ${ws/1e6:.1f}M"))

            if wc > 1_000_000:
                # Close = less directional, lower weight
                signals.append(("MID", f"🐋 whale CLOSE: ${wc/1e6:.1f}M"))

            # 7. LIQUIDATION SPIKE
            if short_liq_4h > 500_000 and long_liq_4h > 0 and short_liq_4h > 2 * long_liq_4h:
                pump_score += 1.5
                signals.append(("MID", f"🟡 SHORT LIQ SPIKE: ${short_liq_4h/1e6:.1f}M (4h)"))
            if long_liq_4h > 500_000 and short_liq_4h > 0 and long_liq_4h > 2 * short_liq_4h:
                dump_score -= 1.5
                signals.append(("MID", f"🟡 LONG LIQ SPIKE: ${long_liq_4h/1e6:.1f}M (4h)"))

            # 8. OI EXODUS — DUMP
            if oi_4h < -3.0 * th:
                dump_score -= 2.0
                signals.append(("LATE", f"🔴 OI EXODUS: {oi_4h:.1f}% (4h)"))

            # 9. BUBBLE — DUMP
            if oi_4h > 3.0 * th and price_4h > 3.0 * th and fr_pct > 0.05 / th:
                dump_score -= 3.0
                signals.append(("LATE", f"🔴 BUBBLE: OI+{oi_4h:.1f}% Price+{price_4h:.1f}% FR+{fr_pct:.4f}%"))

            # ═══════════════════════════════════════════════════════
            # LAYER 3: PENALTIES & CONTEXT
            # ═══════════════════════════════════════════════════════

            # 10. "ALREADY MOVED" PENALTY
            if price_4h > 8:
                pump_score -= 4.0
                signals.append(("LATE", f"⛔ ALREADY PUMPED: Price 4h {price_4h:+.1f}% (may be late)"))
            elif price_4h > 5:
                pump_score -= 3.0
                signals.append(("LATE", f"⛔ ALREADY PUMPED: Price 4h {price_4h:+.1f}% (may be late)"))
            elif price_4h > 3:
                pump_score -= 2.0
                signals.append(("LATE", f"⛔ ALREADY PUMPED: Price 4h {price_4h:+.1f}% (may be late)"))

            if price_4h < -8:
                dump_score += 4.0
                signals.append(("LATE", f"⛔ ALREADY DUMPED: Price 4h {price_4h:+.1f}% (may be late)"))
            elif price_4h < -5:
                dump_score += 3.0
                signals.append(("LATE", f"⛔ ALREADY DUMPED: Price 4h {price_4h:+.1f}% (may be late)"))
            elif price_4h < -3:
                dump_score += 2.0
                signals.append(("LATE", f"⛔ ALREADY DUMPED: Price 4h {price_4h:+.1f}% (may be late)"))

            # 11. LEVERAGE HEALTH
            if oi_mcap > 0.25:
                dump_score -= 2.0
                signals.append(("MID", f"📊 VERY OVERLEVERAGED: OI/Mcap {oi_mcap*100:.1f}%"))
            elif oi_mcap > 0.15:
                dump_score -= 1.0
                signals.append(("MID", f"📊 OVERLEVERAGED: OI/Mcap {oi_mcap*100:.1f}%"))

            # 12. L/S CROWDING
            if ls_24h > 3.0:
                dump_score -= 1.5
                signals.append(("MID", f"📊 CROWDED LONG: L/S 24h={ls_24h:.2f}"))

            # 13. VOLUME / LIQUIDITY FLAG
            if oi_vol_ratio > 2.0:
                signals.append(("MID", f"⚠️ LOW LIQUIDITY: OI/Vol ratio {oi_vol_ratio:.1f}x"))

            # ── Final score ──
            total_score = pump_score + dump_score

            if abs(total_score) >= 1 and signals:
                # Determine overall timing label
                timings = [t for t, _ in signals]
                if "EARLY" in timings:
                    timing_label = "EARLY"
                elif "MID" in timings:
                    timing_label = "MID"
                else:
                    timing_label = "LATE"

                scored.append({
                    "symbol": sym,
                    "score": round(total_score, 1),
                    "pump_score": round(pump_score, 1),
                    "dump_score": round(dump_score, 1),
                    "price": price,
                    "oi_usd": oi_usd,
                    "fr_pct": fr_pct,
                    "oi_chg_4h": oi_4h,
                    "price_chg_4h": price_4h,
                    "_ls_1h": ls_1h,
                    "_ls_4h": ls_4h,
                    "_oi_mcap": oi_mcap,
                    "timing": timing_label,
                    "signals": signals,
                })

        # Sort and format output
        pump_list = sorted([c for c in scored if c["score"] > 0], key=lambda x: x["score"], reverse=True)
        dump_list = sorted([c for c in scored if c["score"] < 0], key=lambda x: x["score"])

        filtered_count = len([c for c in coins if (c.get("open_interest_usd") or 0) >= min_oi_usd])
        early_count = len([c for c in scored if c["timing"] == "EARLY"])

        # ═══════════════════════════════════════════════════════════════
        # PHASE 2: DEEP SCAN — CVD + OB + Taker for top candidates
        # ═══════════════════════════════════════════════════════════════
        if deep_scan and (pump_list or dump_list):
            # Select top candidates for deep scan
            deep_n = min(5, top_n)
            candidates = []
            for c in pump_list[:deep_n]:
                candidates.append(c)
            for c in dump_list[:deep_n]:
                candidates.append(c)

            # Fetch 4 metrics per candidate in parallel
            deep_tasks = []
            deep_map = []  # track which task belongs to which coin+metric
            for c in candidates:
                sym = c["symbol"]
                deep_tasks.append(client.get("/api/spot/aggregated-cvd/history",
                    {"exchange_list": "Binance", "symbol": sym, "interval": "5m", "limit": 15}))
                deep_map.append((sym, "spot_cvd"))

                deep_tasks.append(client.get("/api/futures/aggregated-cvd/history",
                    {"exchange_list": "Binance", "symbol": sym, "interval": "5m", "limit": 15}))
                deep_map.append((sym, "fut_cvd"))

                deep_tasks.append(client.get("/api/futures/aggregated-taker-buy-sell-volume/history",
                    {"exchange_list": "Binance", "symbol": sym, "interval": "5m", "limit": 15}))
                deep_map.append((sym, "taker"))

                deep_tasks.append(client.get("/api/futures/orderbook/aggregated-ask-bids-history",
                    {"exchange_list": "Binance", "symbol": sym, "interval": "5m", "limit": 15, "range": "1"}))
                deep_map.append((sym, "ob"))

            deep_results = await asyncio.gather(*deep_tasks, return_exceptions=True)

            # Parse deep scan results per coin
            deep_data = {}  # symbol -> {spot_cvd, fut_cvd, taker, ob}
            for i, res in enumerate(deep_results):
                sym, metric = deep_map[i]
                if sym not in deep_data:
                    deep_data[sym] = {}
                if isinstance(res, FetchResult) and isinstance(res.data, list) and res.data:
                    deep_data[sym][metric] = res.data
                else:
                    deep_data[sym][metric] = None

            # Score 0-100 per coin using 6 dimensions
            def _deep_score(c: dict) -> dict:
                sym = c["symbol"]
                dd = deep_data.get(sym, {})
                dims = {}  # dimension -> score (0-100 for that dimension)

                # --- OI Trend (20%) ---
                oi_4h = c["oi_chg_4h"]
                p4h = c["price_chg_4h"]
                is_pump = c["score"] > 0
                if is_pump:
                    if oi_4h > 3:
                        dims["oi"] = 90
                    elif oi_4h > 1:
                        dims["oi"] = 70
                    elif oi_4h > 0:
                        dims["oi"] = 50
                    else:
                        dims["oi"] = 20
                else:
                    if oi_4h < -3:
                        dims["oi"] = 90
                    elif oi_4h < -1:
                        dims["oi"] = 70
                    elif oi_4h < 0:
                        dims["oi"] = 50
                    else:
                        dims["oi"] = 20

                # --- CVD Alignment (25%) ---
                spot_rows = dd.get("spot_cvd")
                fut_rows = dd.get("fut_cvd")
                spot_dir = 0  # +1 rising, -1 falling
                fut_dir = 0
                spot_summary = "N/A"
                fut_summary = "N/A"

                if spot_rows and len(spot_rows) >= 3:
                    vals = [float(_get(r, "cvd", "cum_vol_delta", "cumVolDelta", default=0) or 0) for r in spot_rows]
                    if vals[-1] > vals[0]:
                        spot_dir = 1
                        spot_summary = f"+{_fmt_num(vals[-1] - vals[0])}"
                    else:
                        spot_dir = -1
                        spot_summary = f"{_fmt_num(vals[-1] - vals[0])}"
                    pos_deltas = sum(1 for i in range(1, len(vals)) if vals[i] > vals[i-1])
                    spot_summary += f" ({pos_deltas}/{len(vals)-1} up)"

                if fut_rows and len(fut_rows) >= 3:
                    vals = [float(_get(r, "cvd", "cum_vol_delta", "cumVolDelta", default=0) or 0) for r in fut_rows]
                    if vals[-1] > vals[0]:
                        fut_dir = 1
                        fut_summary = f"+{_fmt_num(vals[-1] - vals[0])}"
                    else:
                        fut_dir = -1
                        fut_summary = f"{_fmt_num(vals[-1] - vals[0])}"
                    pos_deltas = sum(1 for i in range(1, len(vals)) if vals[i] > vals[i-1])
                    fut_summary += f" ({pos_deltas}/{len(vals)-1} up)"

                if is_pump:
                    if spot_dir == 1 and fut_dir == 1:
                        dims["cvd"] = 95  # both rising = strong
                    elif spot_dir == 1:
                        dims["cvd"] = 65  # spot rising only
                    elif fut_dir == 1:
                        dims["cvd"] = 45  # futures only
                    else:
                        dims["cvd"] = 15  # neither
                else:
                    if spot_dir == -1 and fut_dir == -1:
                        dims["cvd"] = 95
                    elif spot_dir == -1:
                        dims["cvd"] = 65
                    elif fut_dir == -1:
                        dims["cvd"] = 45
                    else:
                        dims["cvd"] = 15

                # --- Taker Flow (15%) ---
                taker_rows = dd.get("taker")
                taker_summary = "N/A"
                if taker_rows and len(taker_rows) >= 3:
                    buy_dominant = 0
                    total_buy = 0
                    total_sell = 0
                    for r in taker_rows:
                        b = float(_get(r, "aggregated_buy_volume_usd", "taker_buy_volume_usd",
                                       "buyVol", "buy", default=0))
                        s = float(_get(r, "aggregated_sell_volume_usd", "taker_sell_volume_usd",
                                       "sellVol", "sell", default=0))
                        total_buy += b
                        total_sell += s
                        if b > s:
                            buy_dominant += 1
                    net = total_buy - total_sell
                    taker_summary = f"Buy {_fmt_num(total_buy)} vs Sell {_fmt_num(total_sell)} | Net {_fmt_num(net, signed=True)}"
                    ratio = buy_dominant / len(taker_rows)
                    if is_pump:
                        dims["taker"] = min(95, int(ratio * 100) + (20 if net > 0 else -10))
                    else:
                        dims["taker"] = min(95, int((1 - ratio) * 100) + (20 if net < 0 else -10))
                else:
                    dims["taker"] = 40  # neutral if missing

                # --- Positioning (15%) — from L/S ratio already in coins_markets ---
                ls = c.get("_ls_1h", 1.0)
                ls_4h = c.get("_ls_4h", 1.0)
                if is_pump:
                    if ls > 1.1 and ls > ls_4h:
                        dims["pos"] = 80
                    elif ls > 1.0:
                        dims["pos"] = 55
                    else:
                        dims["pos"] = 30
                else:
                    if ls < 0.9 and ls < ls_4h:
                        dims["pos"] = 80
                    elif ls < 1.0:
                        dims["pos"] = 55
                    else:
                        dims["pos"] = 30

                # --- Funding Rate (10%) ---
                fr = c["fr_pct"]
                if is_pump:
                    if fr < -0.05:
                        dims["fr"] = 90  # extreme neg = squeeze fuel
                    elif fr < -0.01:
                        dims["fr"] = 70
                    elif fr < 0.01:
                        dims["fr"] = 50  # neutral
                    else:
                        dims["fr"] = 25  # positive = headwind for longs
                else:
                    if fr > 0.05:
                        dims["fr"] = 90
                    elif fr > 0.01:
                        dims["fr"] = 70
                    elif fr > -0.01:
                        dims["fr"] = 50
                    else:
                        dims["fr"] = 25

                # --- Orderbook (15%) ---
                ob_rows = dd.get("ob")
                ob_summary = "N/A"
                if ob_rows and len(ob_rows) >= 3:
                    bid_dom = 0
                    total_bids = 0
                    total_asks = 0
                    for r in ob_rows:
                        b = float(_get(r, "aggregated_bids_usd", "bids_usd", "bids", default=0))
                        a = float(_get(r, "aggregated_asks_usd", "asks_usd", "asks", default=0))
                        total_bids += b
                        total_asks += a
                        if b > a:
                            bid_dom += 1
                    ob_ratio = total_bids / total_asks if total_asks > 0 else 1.0
                    ob_summary = f"Bids {_fmt_num(total_bids)} vs Asks {_fmt_num(total_asks)} | Ratio {ob_ratio:.2f}"
                    if is_pump:
                        if ob_ratio > 1.2:
                            dims["ob"] = 85
                        elif ob_ratio > 1.0:
                            dims["ob"] = 60
                        else:
                            dims["ob"] = 25
                    else:
                        if ob_ratio < 0.8:
                            dims["ob"] = 85
                        elif ob_ratio < 1.0:
                            dims["ob"] = 60
                        else:
                            dims["ob"] = 25
                else:
                    dims["ob"] = 40

                # --- Weighted total score 0-100 ---
                weights = {"oi": 0.20, "cvd": 0.25, "taker": 0.15, "pos": 0.15, "fr": 0.10, "ob": 0.15}
                total = sum(dims.get(k, 40) * w for k, w in weights.items())

                # Penalties
                if abs(p4h) > 5:
                    total *= 0.7  # already moved penalty
                elif abs(p4h) > 3:
                    total *= 0.85

                total = max(0, min(100, round(total)))

                # Classification
                if total >= 70 and is_pump and spot_dir == 1:
                    if c["timing"] == "EARLY":
                        classification = "EARLY ACCUMULATION"
                    elif fr < -0.05:
                        classification = "SQUEEZE SETUP"
                    elif dims.get("ob", 0) >= 70:
                        classification = "PASSIVE ABSORPTION"
                    else:
                        classification = "ACCUMULATION"
                elif total >= 70 and not is_pump and spot_dir == -1:
                    if c["timing"] == "EARLY":
                        classification = "EARLY DISTRIBUTION"
                    else:
                        classification = "DISTRIBUTION"
                elif total >= 50 and is_pump:
                    classification = "ACCUMULATION"
                elif total >= 50 and not is_pump:
                    classification = "DISTRIBUTION"
                elif abs(p4h) > 5:
                    classification = "POST-MOVE"
                else:
                    oi_mcap = c.get("_oi_mcap", 0)
                    if oi_mcap > 0.25:
                        classification = "OI TRAP"
                    elif total < 30:
                        classification = "FAKE STRENGTH" if is_pump else "NEUTRAL"
                    else:
                        classification = "NEUTRAL"

                return {
                    **c,
                    "deep_score": total,
                    "dims": dims,
                    "classification": classification,
                    "spot_cvd_summary": spot_summary,
                    "fut_cvd_summary": fut_summary,
                    "taker_summary": taker_summary,
                    "ob_summary": ob_summary,
                }

            # Apply deep scoring
            pump_list = [_deep_score(c) for c in pump_list[:deep_n]]
            dump_list = [_deep_score(c) for c in dump_list[:deep_n]]
            pump_list.sort(key=lambda x: x["deep_score"], reverse=True)
            dump_list.sort(key=lambda x: x["deep_score"], reverse=True)

            # ── Format deep scan output ──
            output = f"# SMART SCREENER v3 — Deep Scan\n"
            output += f"**Scan time:** {scan_time} | **Sensitivity:** {sensitivity} | **Mode:** DEEP\n"
            output += f"**Phase 1:** {len(coins)} coins → {filtered_count} filtered → {len(pump_list)+len(dump_list)} deep scanned\n"
            output += f"**Phase 2:** CVD + Taker + Orderbook fetched ({len(candidates)*4} API calls)\n"
            output += f"**Derivatives:** ✅ | **Whale:** {'✅' if whale_longs or whale_shorts else '⚠️ None'}\n\n"

            # Large cap vs small/mid cap split
            large_cap = [c for c in pump_list + dump_list if c["oi_usd"] >= 40_000_000]
            small_mid = [c for c in pump_list + dump_list if 5_000_000 <= c["oi_usd"] < 100_000_000]

            if large_cap:
                output += "## 🔵 LARGE CAP — MARKET DIRECTION (OI ≥ $40M)\n\n"
                output += "| # | Coin | Bias | Score | Classification | OI | FR | Key Signal |\n"
                output += "|---|------|------|-------|----------------|----|----|------------|\n"
                for i, c in enumerate(sorted(large_cap, key=lambda x: x["deep_score"], reverse=True), 1):
                    bias = "PUMP" if c["score"] > 0 else "DUMP"
                    sig0 = c["signals"][0][1] if c["signals"] else ""
                    output += (f"| {i} | **{c['symbol']}** | {bias} | {c['deep_score']}/100 | "
                               f"{c['classification']} | ${c['oi_usd']/1e6:,.0f}M | "
                               f"{c['fr_pct']:.4f}% | {sig0} |\n")
                output += "\n"

            if small_mid:
                output += "## 🟡 SMALL/MID CAP — EARLY SETUPS ($5M–$100M)\n\n"
                output += "| # | Coin | Type | Score | OI | Vol Δ4h | Key Signal |\n"
                output += "|---|------|------|-------|----|---------|-----------|\n"
                for i, c in enumerate(sorted(small_mid, key=lambda x: x["deep_score"], reverse=True), 1):
                    output += (f"| {i} | **{c['symbol']}** | {c['classification']} | {c['deep_score']}/100 | "
                               f"${c['oi_usd']/1e6:,.0f}M | {c['oi_chg_4h']:+.1f}% | "
                               f"{c['signals'][0][1] if c['signals'] else ''} |\n")
                output += "\n"

            # Detailed breakdown per coin
            output += "## 📊 DEEP SCAN BREAKDOWN\n\n"
            all_deep = pump_list + dump_list
            all_deep.sort(key=lambda x: x["deep_score"], reverse=True)
            for c in all_deep:
                bias = "⬆️ PUMP" if c["score"] > 0 else "⬇️ DUMP"
                output += f"### {c['symbol']} — {c['deep_score']}/100 [{c['classification']}] {bias}\n"
                output += f"Price: ${c['price']:,.4f} | OI: ${c['oi_usd']/1e6:,.0f}M | FR: {c['fr_pct']:.4f}%\n\n"

                # Dimension scores
                d = c["dims"]
                output += "```\n"
                output += f" OI Trend   (20%): {d.get('oi', 0):>3}/100  |  CVD Align (25%): {d.get('cvd', 0):>3}/100\n"
                output += f" Taker Flow (15%): {d.get('taker', 0):>3}/100  |  Positioning(15%): {d.get('pos', 0):>3}/100\n"
                output += f" Funding    (10%): {d.get('fr', 0):>3}/100  |  Orderbook  (15%): {d.get('ob', 0):>3}/100\n"
                output += "```\n\n"

                output += f"- **Spot CVD:** {c['spot_cvd_summary']}\n"
                output += f"- **Futures CVD:** {c['fut_cvd_summary']}\n"
                output += f"- **Taker:** {c['taker_summary']}\n"
                output += f"- **Orderbook:** {c['ob_summary']}\n"

                # Signals
                for timing, sig_text in c["signals"]:
                    output += f"- {sig_text}\n"
                output += "\n"

            # Distribution / Trap / Watchlist
            distro = [c for c in all_deep if "DISTRIBUTION" in c["classification"]]
            traps = [c for c in all_deep if c["classification"] in ("OI TRAP", "FAKE STRENGTH", "POST-MOVE")]

            if distro:
                output += "## 🔴 DISTRIBUTION / SHORT BIAS\n\n"
                output += "| Coin | Score | Classification | Reason |\n"
                output += "|------|-------|----------------|--------|\n"
                for c in distro:
                    output += f"| {c['symbol']} | {c['deep_score']}/100 | {c['classification']} | {c['signals'][0][1] if c['signals'] else ''} |\n"
                output += "\n"

            if traps:
                output += "## ⚠️ TRAP / NOISE\n\n"
                output += "| Coin | Flag | Reason |\n"
                output += "|------|------|--------|\n"
                for c in traps:
                    output += f"| {c['symbol']} | {c['classification']} | {c['signals'][0][1] if c['signals'] else ''} |\n"
                output += "\n"

            # Sniper watchlist
            watch = [c for c in all_deep if c["deep_score"] >= 55 and abs(c["price_chg_4h"]) <= 3]
            if watch:
                output += "## 👀 SNIPER WATCHLIST\n\n"
                output += "| Coin | Score | Trigger Condition |\n"
                output += "|------|-------|-------------------|\n"
                for c in watch:
                    if c["score"] > 0:
                        trigger = f"Confirm Spot CVD rising + OB bid dominant → long"
                    else:
                        trigger = f"Confirm Spot CVD falling + OB ask dominant → short"
                    output += f"| **{c['symbol']}** | {c['deep_score']}/100 | {trigger} |\n"
                output += "\n"

            # Final summary
            best_pump = pump_list[0] if pump_list else None
            best_dump = dump_list[0] if dump_list else None
            most_trap = max(traps, key=lambda x: x.get("_oi_mcap", 0)) if traps else None

            output += "## 🧾 FINAL SUMMARY\n\n"
            output += "| Category | Pick |\n"
            output += "|----------|------|\n"
            if best_pump:
                output += f"| Best Clean Setup | **{best_pump['symbol']}** — {best_pump['deep_score']}/100 [{best_pump['classification']}] |\n"
            if best_dump:
                output += f"| Strongest Distribution | **{best_dump['symbol']}** — {best_dump['deep_score']}/100 [{best_dump['classification']}] |\n"
            squeeze = [c for c in all_deep if c["classification"] == "SQUEEZE SETUP"]
            if squeeze:
                output += f"| Best Squeeze Setup | **{squeeze[0]['symbol']}** — {squeeze[0]['deep_score']}/100 |\n"
            if most_trap:
                output += f"| Most Dangerous Trap | **{most_trap['symbol']}** — {most_trap['classification']} |\n"

            # Market phase
            pump_count = len([c for c in scored if c["score"] > 0])
            dump_count = len([c for c in scored if c["score"] < 0])
            if dump_count > pump_count * 2:
                phase = "DISTRIBUTION — majority dump signals"
            elif pump_count > dump_count * 2:
                phase = "ACCUMULATION — majority pump signals"
            else:
                phase = "MIXED — no clear macro bias"
            output += f"| Market Phase | {phase} |\n"
            output += "\n"

            return make_envelope("success", "coinglass", output)

        # ═══════════════════════════════════════════════════════════════
        # FAST MODE (v2) — No deep scan
        # ═══════════════════════════════════════════════════════════════
        output = f"# SMART SCREENER v2 — Early Detection\n"
        output += f"**Scan time:** {scan_time} | **Sensitivity:** {sensitivity}\n"
        output += f"**Coins scanned:** {len(coins)} | **Filtered (OI>${min_oi_usd/1e6:.0f}M):** {filtered_count}\n"
        output += f"**Signals:** {len(pump_list)} pump, {len(dump_list)} dump | **⚡ Early:** {early_count}\n\n"

        def _fmt_coin(i: int, c: dict) -> str:
            """Format a single coin entry."""
            sign = "+" if c["score"] > 0 else ""
            block = (
                f"### {i}. {c['symbol']} — Score: {sign}{c['score']} [{c['timing']}]\n"
                f"Price: ${c['price']:,.4f} | OI: ${c['oi_usd']/1e6:,.0f}M | "
                f"FR: {c['fr_pct']:.4f}% | OI 4h: {c['oi_chg_4h']:+.1f}% | "
                f"Price 4h: {c['price_chg_4h']:+.1f}%\n"
            )
            for timing, sig_text in c["signals"]:
                block += f"  {sig_text}  [{timing}]\n"
            # Status line
            p4h = c["price_chg_4h"]
            if abs(p4h) <= 3:
                block += f"  ✅ NOT moved yet: Price 4h {p4h:+.1f}%\n"
            block += "\n"
            return block

        if mode in ("all", "pump"):
            output += f"## ⬆️ PUMP CANDIDATES (top {top_n})\n\n"
            if pump_list:
                for i, c in enumerate(pump_list[:top_n], 1):
                    output += _fmt_coin(i, c)
            else:
                output += "*No pump signals detected right now.*\n\n"

        if mode in ("all", "dump"):
            output += f"## ⬇️ DUMP CANDIDATES (top {top_n})\n\n"
            if dump_list:
                for i, c in enumerate(dump_list[:top_n], 1):
                    output += _fmt_coin(i, c)
            else:
                output += "*No dump signals detected right now.*\n\n"

        output += (
            "---\n\n"
            "## Cara Pakai\n\n"
            "1. Prioritaskan coin berlabel **[EARLY]** — sinyal paling fresh\n"
            "2. Coin dengan ⛔ ALREADY MOVED sudah jalan — hati-hati chase\n"
            "3. Jalankan `coinglass_full_scan` pada coin pilihan untuk konfirmasi detail\n"
            "4. Atau jalankan `coinglass_smart_screener` dengan `deep_scan=true` untuk analisis 6 dimensi\n"
        )

        return make_envelope("success", "coinglass", output)


    # ═══════════════════════════════════════════════════════════════════════════════
    # COMPOSITE TOOL — Ricoz Full Scan (HARDENED)
    # ═══════════════════════════════════════════════════════════════════════════════

    # Critical metrics — if ANY fails, analysis is BLOCKED
    CRITICAL_METRICS = {"Spot CVD", "Futures CVD", "Open Interest"}

    # Arkham chain mapping for exchange flow (Phase 4)
    ARKHAM_CHAIN_MAP = {
        "SOL": "solana", "BTC": "bitcoin", "ETH": "ethereum",
        "BNB": "bnb", "AVAX": "avalanche", "SUI": "sui",
        "HYPE": "ethereum", "XRP": "",
    }

    # Tokens NOT supported by Nansen flows (skip API call)
    NANSEN_UNSUPPORTED = {"BTC", "HYPE"}
    # Supplementary metrics — partial failure is OK
    SUPPLEMENTARY_METRICS = {
        "Funding Rate", "Orderbook Delta", "Liquidation Map",
        "Price OHLC", "Taker Buy/Sell", "Long/Short Ratio",
    }
    PRECISION_METRICS = {"Whale Alert", "OB Bidask ±1%", "Footprint", "RSI"}


    @mcp.tool()
    @_tool_envelope()
    async def coinglass_full_scan(
        symbol: str = "BTC",
        interval: str = "5m",
        exchange: str = DEFAULT_EXCHANGE,
    ) -> str:
        """ALL-IN-ONE 4-phase scan — Ricoz Scalping Framework (~24 endpoints).

        Optimized for speed: per-task timeout caps (12s), reduced data limits.
        Targets <30s total execution — safe for Claude AI MCP.

        Phase 1 — Quick Scan (8 core CoinGlass endpoints)
        Phase 2 — Precision Check (7 endpoints: Whale, OB, Footprint, RSI, etc.)
        Phase 3 — Binance Direct Cross-Check (6 endpoints)
        Phase 4 — On-Chain Layer (Nansen + Arkham)

        Args:
            symbol: Coin symbol (BTC, ETH, SOL, HYPE, AVAX, etc.)
            interval: Candle interval for time-series data (5m recommended for scalping)
            exchange: Exchange name (Binance, OKX, Bybit, etc.)
        """
        async def _capped(coro, timeout=12):
            """Cap per-task execution time — prevents slow retries from blocking scan."""
            try:
                return await asyncio.wait_for(coro, timeout=timeout)
            except asyncio.TimeoutError:
                return TimeoutError(f"Timed out after {timeout}s")

        sym = to_cg_symbol(symbol)  # PEPE→1000PEPE, BTC→BTC
        raw_sym = normalize_symbol(symbol)  # Always base symbol for display
        pair = to_pair(symbol)
        limit = 30  # formatters only show last 15 — keep lean for speed

        # Define all endpoints with their labels and criticality
        # V4 API: some endpoints need coin-level (1000PEPE), some need pair-level (1000PEPEUSDT)
        calls = [
            ("Spot CVD", "/api/spot/aggregated-cvd/history",
             {"exchange_list": exchange, "symbol": sym, "interval": interval, "limit": limit}),
            ("Futures CVD", "/api/futures/aggregated-cvd/history",
             {"exchange_list": exchange, "symbol": sym, "interval": interval, "limit": limit}),
            ("Open Interest", "/api/futures/open-interest/aggregated-history",
             {"exchange_list": exchange, "symbol": sym, "interval": interval, "limit": limit}),
            ("Funding Rate", "/api/futures/funding-rate/exchange-list",
             {}),
            ("Orderbook Delta", "/api/futures/orderbook/ask-bids-history",
             {"exchange": exchange, "symbol": pair, "interval": interval, "range": "1", "limit": limit}),
            ("Price OHLC", "/api/futures/price/history",
             {"exchange": exchange, "symbol": pair, "interval": interval, "limit": limit}),
            ("Taker Buy/Sell", "/api/futures/v2/taker-buy-sell-volume/history",
             {"exchange": exchange, "symbol": pair, "interval": interval, "limit": limit}),
            ("Long/Short Ratio", "/api/futures/global-long-short-account-ratio/history",
             {"exchange": exchange, "symbol": pair, "interval": interval, "limit": limit}),
        ]
        # Only include Liquidation Heatmap if plan supports it
        if config.has_feature("liquidation_heatmap"):
            calls.append(("Liquidation Heatmap", "/api/futures/liquidation/heatmap/model1",
                           {"exchange": exchange, "symbol": pair, "range": "3d"}))

        # ── Phase 2: Precision endpoints ──
        precision_calls = [
            ("Whale Alert", "/api/hyperliquid/whale-alert", {}),
            ("OB Bidask ±1%", "/api/futures/orderbook/ask-bids-history", {
                "exchange": exchange, "symbol": pair,
                "interval": interval, "limit": 10, "range": "1",
            }),
        ]
        if config.has_feature("footprint"):
            precision_calls.append(
                ("Footprint", "/api/futures/volume/footprint-history", {
                    "exchange": exchange, "symbol": pair,
                    "interval": "5m", "limit": 10,
                })
            )
        # Liquidation Orders — cluster data for TP/SL placement
        precision_calls.append(
            ("Liq Orders", "/api/futures/liquidation/order", {
                "exchange": exchange, "symbol": sym,
                "min_liquidation_amount": "10000",
            })
        )
        # Hyperliquid L/S Ratio — on-chain sophisticated trader sentiment
        precision_calls.append(
            ("Hyperliquid L/S", "/api/hyperliquid/global-long-short-account-ratio/history", {
                "symbol": sym, "interval": "1h", "limit": 15,
            })
        )
        # Top Trader Position Ratio — smart money positioning (not retail)
        precision_calls.append(
            ("Top Position L/S", "/api/futures/top-long-short-position-ratio/history", {
                "exchange": exchange, "symbol": pair, "interval": interval, "limit": 10,
            })
        )
        # Spot Large Orders — detect bid/ask walls, spoofing identification
        precision_calls.append(
            ("Spot Large Orders", "/api/spot/orderbook/large-limit-order", {
                "exchange": exchange, "symbol": pair,
            })
        )
        # RSI — use rsi/list (all coins) then filter, because per-coin endpoint is broken
        precision_calls.append(
            ("RSI", "/api/futures/rsi/list", {})
        )

        # ── Prepare ALL phases for parallel execution ──
        # Phase 1+2: CoinGlass endpoints — capped at 12s per task (prevents 20s retry chains)
        all_calls = calls + precision_calls
        cg_tasks = [_capped(client.get(ep, params), timeout=12) for _, ep, params in all_calls]

        # Phase 3: Binance direct (no API key, separate rate limiter) — capped at 10s
        BINANCE_SPOT_MAP = {"HYPE": "HYPER"}
        bn_spot_sym = BINANCE_SPOT_MAP.get(sym, sym) + "USDT"
        bn_fut_sym = pair  # e.g. SOLUSDT
        bn_tasks = [
            _capped(binance_futures_request("/fapi/v1/premiumIndex", {"symbol": bn_fut_sym}), timeout=10),
            _capped(binance_futures_request("/fapi/v1/openInterest", {"symbol": bn_fut_sym}), timeout=10),
            _capped(binance_futures_request(
                "/futures/data/takerlongshortRatio",
                {"symbol": bn_fut_sym, "period": interval if interval in ("5m","15m","30m","1h","2h","4h") else "5m", "limit": 10},
            ), timeout=10),
            _capped(binance_futures_request(
                "/futures/data/globalLongShortAccountRatio",
                {"symbol": bn_fut_sym, "period": interval if interval in ("5m","15m","30m","1h","2h","4h") else "5m", "limit": 10},
            ), timeout=10),
            _capped(binance_spot_request("/api/v3/klines", {"symbol": bn_spot_sym, "interval": interval, "limit": 15}, weight=2), timeout=10),
            _capped(binance_futures_request("/fapi/v1/klines", {"symbol": bn_fut_sym, "interval": interval, "limit": 30}, weight=5), timeout=10),
        ]

        # Phase 4: Nansen + Arkham (external APIs) — capped at 12s
        p4_task_names = []
        p4_coros = []
        nansen_skip = raw_sym.upper() in NANSEN_UNSUPPORTED
        nansen_has_map = raw_sym.upper() in NANSEN_TOKEN_MAP
        if nansen_has_map and not nansen_skip:
            p4_task_names.append("nansen")
            p4_coros.append(_capped(nansen_token_flow_intelligence(raw_sym), timeout=12))
        else:
            async def _noop():
                return None
            p4_task_names.append("nansen")
            p4_coros.append(_noop())

        arkham_chain = ARKHAM_CHAIN_MAP.get(raw_sym.upper(), "ethereum")
        arkham_params: dict = {}
        if arkham_chain:
            arkham_params["chains"] = arkham_chain
        p4_task_names.append("arkham")
        p4_coros.append(_capped(arkham_get(f"/flow/entity/{exchange.lower()}", arkham_params), timeout=12))

        # ══ FIRE ALL PHASES IN PARALLEL — biggest speed win ══
        # Per-task timeouts cap slow endpoints. Total scan targets <30s.
        _cg_results, _bn_results, _p4_results = await asyncio.gather(
            asyncio.gather(*cg_tasks, return_exceptions=True),
            asyncio.gather(*bn_tasks, return_exceptions=True),
            asyncio.gather(*p4_coros, return_exceptions=True),
        )
        raw_results = list(_cg_results)
        bn_premium, bn_oi, bn_taker, bn_ls, bn_klines, bn_fut_klines = _bn_results
        p4 = dict(zip(p4_task_names, _p4_results))

        # ── Auto-fallback: if Spot CVD is empty, try OKX then Bybit ──
        spot_cvd_idx = 0  # first call is always Spot CVD
        spot_result = raw_results[spot_cvd_idx]
        if (isinstance(spot_result, FetchResult)
                and isinstance(spot_result.data, list)
                and len(spot_result.data) == 0):
            for fallback_ex in ("OKX", "Bybit"):
                if fallback_ex == exchange:
                    continue
                try:
                    fb_result = await client.get(
                        "/api/spot/aggregated-cvd/history",
                        {"exchange_list": fallback_ex, "symbol": sym,
                         "interval": interval, "limit": limit},
                    )
                    if (isinstance(fb_result.data, list)
                            and len(fb_result.data) > 0):
                        raw_results[spot_cvd_idx] = fb_result
                        # Update call label to show fallback exchange
                        calls[spot_cvd_idx] = (
                            f"Spot CVD (fallback: {fallback_ex})",
                            calls[spot_cvd_idx][1],
                            calls[spot_cvd_idx][2],
                        )
                        break
                except Exception:
                    continue

        # Split Phase 2 results for later use
        phase2_results = list(zip(precision_calls, raw_results[len(calls):]))

        # Post-filter: FR exchange-list returns all coins, extract only target symbol
        for i, (label, _, _) in enumerate(calls):
            if label == "Funding Rate" and isinstance(raw_results[i], FetchResult):
                fr_result = raw_results[i]
                if isinstance(fr_result.data, list):
                    filtered = [c for c in fr_result.data
                                if c.get("symbol", "").upper() in (sym, raw_sym)]
                    raw_results[i] = FetchResult(
                        data=_format_fr_compact(filtered, raw_sym),
                        age_seconds=fr_result.age_seconds,
                        is_cached=fr_result.is_cached,
                        fetched_at=fr_result.fetched_at,
                    )

        # Build status map
        scan_time = datetime.now(WIB).strftime("%Y-%m-%d %H:%M:%S WIB")
        output = f"# FULL SCAN — {raw_sym} ({interval})\n"
        output += f"**Scan time:** {scan_time}\n\n"

        # Status checklist
        output += "## Phase 1 — Quick Scan Status\n"
        critical_failed = []
        supplementary_failed = []
        max_age = 0.0

        for (label, _, _), result in zip(calls, raw_results):
            # Match "Spot CVD (fallback: OKX)" to CRITICAL set "Spot CVD"
            base_label = label.split(" (fallback")[0]
            is_critical = base_label in CRITICAL_METRICS
            tag = "CRITICAL" if is_critical else "SUPPORT"

            if isinstance(result, Exception):
                status = f"FAILED ({client._mask_key(str(result))})"
                icon = "X"
                if is_critical:
                    critical_failed.append(label)
                else:
                    supplementary_failed.append(label)
            elif isinstance(result, FetchResult):
                # Detect empty dataset (API succeeded but returned no data or all $0)
                is_empty = (isinstance(result.data, list) and len(result.data) == 0)
                if not is_empty and isinstance(result.data, list) and result.data:
                    is_empty = _check_all_zero(base_label, result.data)
                if is_empty:
                    status = "NO DATA (empty — coin may not be listed on this exchange)"
                    icon = "X"
                    if is_critical:
                        critical_failed.append(f"{label} (NO DATA)")
                    else:
                        supplementary_failed.append(f"{label} (NO DATA)")
                elif result.is_expired:
                    status = f"EXPIRED ({result.age_seconds:.0f}s old)"
                    icon = "X"
                    if is_critical:
                        critical_failed.append(label)
                    else:
                        supplementary_failed.append(label)
                elif result.is_stale:
                    status = f"STALE ({result.age_seconds:.0f}s)"
                    icon = "~"
                    if is_critical:
                        critical_failed.append(f"{label} (STALE)")
                else:
                    status = f"OK ({result.age_label})"
                    icon = "+"
                if not is_empty:
                    max_age = max(max_age, result.age_seconds)
            else:
                status = "UNKNOWN"
                icon = "?"

            output += f"- [{icon}] **{label}** [{tag}]: {status}\n"

        # ── Phase 2 Precision Status ──
        output += "\n**Phase 2 — Precision Endpoints:**\n"
        for (p2_label, _, _), p2_result in phase2_results:
            if isinstance(p2_result, Exception):
                p2_status = f"FAILED ({client._mask_key(str(p2_result))})"
                p2_icon = "X"
            elif isinstance(p2_result, FetchResult):
                if p2_result.is_expired:
                    p2_status = f"EXPIRED ({p2_result.age_seconds:.0f}s old)"
                    p2_icon = "X"
                elif p2_result.is_stale:
                    p2_status = f"STALE ({p2_result.age_seconds:.0f}s)"
                    p2_icon = "~"
                else:
                    p2_status = f"OK ({p2_result.age_label})"
                    p2_icon = "+"
                max_age = max(max_age, p2_result.age_seconds)
            else:
                p2_status = "UNKNOWN"
                p2_icon = "?"
            output += f"- [{p2_icon}] **{p2_label}** [PRECISION]: {p2_status}\n"

        output += "\n"

        # HARD BLOCK if critical metrics failed
        if critical_failed:
            output += (
                f"## ANALYSIS BLOCKED\n\n"
                f"**CRITICAL METRICS FAILED: {', '.join(critical_failed)}**\n\n"
                f"SpotCVD + FutCVD + OI are the MINIMUM required dataset for "
                f"Ricoz Scalping Framework analysis. Without ALL three, any "
                f"conclusion would be unreliable.\n\n"
                f"**ACTION: DO NOT TRADE based on this scan. Re-run or check individual tools.**\n\n"
            )
            if supplementary_failed:
                output += f"Also failed (supplementary): {', '.join(supplementary_failed)}\n\n"
            return make_envelope("failed", "coinglass", output,
                                 data_age_seconds=max_age,
                                 warnings=[f"CRITICAL METRICS FAILED: {', '.join(critical_failed)}"],
                                 failed_endpoints=critical_failed + supplementary_failed,
                                 fallback_suggestion="Try data_binance for Binance-only scan")

        # Data sections — formatted as readable tables (data values unchanged)
        for (label, _, _), result in zip(calls, raw_results):
            output += f"---\n\n## {label}\n\n"
            if isinstance(result, Exception):
                output += f"**FAILED:** {client._mask_key(str(result))}\n\n"
            elif isinstance(result, FetchResult):
                output += _age_banner(result)
                data = result.data
                # Match label to formatter (handle fallback labels like "Spot CVD (fallback: OKX)")
                base_label = label.split(" (fallback")[0]
                formatter = _SCAN_FORMATTERS.get(base_label)
                if formatter and isinstance(data, (list, dict)) and data:
                    output += formatter(data, label)
                elif isinstance(data, list) and len(data) == 0:
                    output += "**Empty dataset**\n\n"
                else:
                    # Fallback: compact JSON for unknown formats
                    if isinstance(data, list) and len(data) > 15:
                        output += f"*(last 15 of {len(data)})*\n"
                        output += json.dumps(data[-15:], indent=2, default=str) + "\n\n"
                    else:
                        output += json.dumps(data, indent=2, default=str) + "\n\n"

        # Warnings
        if supplementary_failed:
            output += (
                f"---\n\n## DATA GAPS\n\n"
                f"**Missing supplementary metrics:** {', '.join(supplementary_failed)}\n"
                f"Core analysis (SpotCVD/FutCVD/OI) is valid but be cautious "
                f"with incomplete supporting data.\n\n"
            )

        if max_age > STALE_WARNING_THRESHOLD:
            output += (
                f"## STALENESS WARNING\n\n"
                f"**Oldest data in this scan: {max_age:.0f}s**. "
                f"Some metrics may not reflect current market conditions.\n\n"
            )

        # Analysis checklist
        output += (
            "---\n\n"
            "## Ricoz Framework Checklist\n\n"
            f"1. **SpotCVD**: If negative → DO NOT LONG {raw_sym}\n"
            "2. **FutCVD**: Must align with SpotCVD direction\n"
            "3. **OI**: Rising + price direction = trend strength\n"
            "4. **FR**: Extreme = contrarian signal\n"
            "5. **OBDelta**: Confirm buy/sell pressure\n"
            "6. **Liq Map**: Set TP near liquidation clusters\n"
            "7. **Taker**: Confirm aggressor side\n"
            "8. **L/S Ratio**: Contrarian indicator\n"
        )

        # ═════════════════════════════════════════════════════════════════
        # PHASE 2 — PRE-ENTRY PRECISION CHECK
        # ═════════════════════════════════════════════════════════════════
        output += "\n---\n\n"
        output += f"# PHASE 2 — PRE-ENTRY PRECISION CHECK ({raw_sym})\n\n"
        output += "## 🐋 WHALE CHECK\n\n"

        # Build lookup for Phase 2 results
        p2 = {}
        for (p2_lbl, _, _), p2_res in phase2_results:
            p2[p2_lbl] = p2_res

        # ── Whale Alert ──
        whale_result = p2.get("Whale Alert")
        whale_entries = []
        if isinstance(whale_result, FetchResult) and not whale_result.is_expired:
            output += _age_banner(whale_result)
            whale_data = whale_result.data
            if isinstance(whale_data, list):
                sym_whales = [
                    w for w in whale_data
                    if w.get("symbol", "").upper() in (sym, raw_sym)
                ]
                if sym_whales:
                    for w in sym_whales[:10]:
                        direction = "LONG" if w.get("position_size", 0) > 0 else "SHORT"
                        action_type = "OPEN" if w.get("position_action") == 1 else "CLOSE"
                        size_usd = w.get("position_value_usd", 0)
                        entry = w.get("entry_price", 0)
                        whale_entries.append({
                            "direction": direction, "action": action_type,
                            "size_usd": size_usd, "entry": entry,
                        })
                        output += (
                            f"- **Whale {action_type} {direction}**: "
                            f"${size_usd:,.0f} @ ${entry:,.2f}\n"
                        )
                    output += "\n"
                else:
                    output += (
                        f"**Whale Alert:** No {raw_sym} whale positions "
                        f"(>$1M) on Hyperliquid\n\n"
                    )
            else:
                output += "**Whale Alert:** No data available\n\n"
        elif isinstance(whale_result, Exception):
            output += (
                f"**Whale Alert:** FAILED — "
                f"{client._mask_key(str(whale_result))}\n\n"
            )
        else:
            output += "**Whale Alert:** Data expired or unavailable\n\n"

        # ── OB Bidask ±1% ──
        ob_result = p2.get("OB Bidask ±1%")
        ob_bids = 0.0
        ob_asks = 0.0
        if isinstance(ob_result, FetchResult) and not ob_result.is_expired:
            output += _age_banner(ob_result)
            ob_data = ob_result.data
            if isinstance(ob_data, list) and ob_data:
                latest = ob_data[-1]
                ob_bids = latest.get("bids_usd", 0)
                ob_asks = latest.get("asks_usd", 0)
                dominant = "BIDS (buyers)" if ob_bids > ob_asks else "ASKS (sellers)"
                ratio = ob_bids / ob_asks if ob_asks > 0 else float("inf")
                output += f"**OB Bidask ±1% (latest):** Bids ${ob_bids / 1e6:.1f}M vs Asks ${ob_asks / 1e6:.1f}M → **{dominant}** (ratio {ratio:.2f})\n\n"
                # Trend table — show dominance shift over time
                if len(ob_data) > 1:
                    output += "```\n"
                    output += f"{'Time':>6} | {'Bids':>10} | {'Asks':>10} | {'Ratio':>6} | {'Dominant':>8}\n"
                    output += f"{'─'*6} | {'─'*10} | {'─'*10} | {'─'*6} | {'─'*8}\n"
                    bid_dom_count = 0
                    for ob_row in ob_data:
                        if not isinstance(ob_row, dict):
                            continue
                        ts = _get(ob_row, "t", "time", "timestamp", "createTime", default=0)
                        b = float(_get(ob_row, "aggregated_bids_usd", "bids_usd", "bids", default=0))
                        a = float(_get(ob_row, "aggregated_asks_usd", "asks_usd", "asks", default=0))
                        r = b / a if a > 0 else 0
                        dom = "BIDS" if b > a else "ASKS"
                        if b > a:
                            bid_dom_count += 1
                        ts_str = datetime.fromtimestamp(ts / 1000, tz=WIB).strftime("%H:%M") if ts > 1e9 else "?"
                        output += f"{ts_str:>6} | {_fmt_num(b):>10} | {_fmt_num(a):>10} | {r:>6.2f} | {dom:>8}\n"
                    output += "```\n"
                    output += f"**Trend:** {bid_dom_count}/{len(ob_data)} bid-dominant\n\n"
            else:
                output += "**OB Bidask ±1%:** No data\n\n"
        elif isinstance(ob_result, Exception):
            output += (
                f"**OB Bidask ±1%:** FAILED — "
                f"{client._mask_key(str(ob_result))}\n\n"
            )
        else:
            output += "**OB Bidask ±1%:** Data expired or unavailable\n\n"

        # ── Footprint ──
        fp_result = p2.get("Footprint")
        fp_direction = None
        if fp_result is not None:
            if isinstance(fp_result, FetchResult) and not fp_result.is_expired:
                output += _age_banner(fp_result)
                fp_data = fp_result.data
                if isinstance(fp_data, list) and fp_data:
                    # Multi-candle footprint table
                    output += "**Footprint (per-candle absorption):**\n```\n"
                    output += f"{'Time':>6} | {'Buy $':>10} | {'Sell $':>10} | {'Net':>10} | {'Top Imbalance':>20}\n"
                    output += f"{'─'*6} | {'─'*10} | {'─'*10} | {'─'*10} | {'─'*20}\n"
                    overall_buy = 0.0
                    overall_sell = 0.0
                    buy_candles = 0
                    for candle in fp_data[-10:]:
                        if not isinstance(candle, list) or len(candle) < 2:
                            continue
                        ts = candle[0]
                        levels = candle[1]
                        ts_str = datetime.fromtimestamp(ts / 1000, tz=WIB).strftime("%H:%M") if ts > 1e9 else "?"
                        c_buy = 0.0
                        c_sell = 0.0
                        c_max_imb = 0.0
                        c_imb_price = 0.0
                        c_imb_side = "BUY"
                        for level in levels:
                            if isinstance(level, list) and len(level) >= 8:
                                buy_usdt = level[6]
                                sell_usdt = level[7]
                                price_mid = (level[0] + level[1]) / 2
                                c_buy += buy_usdt
                                c_sell += sell_usdt
                                imb = abs(buy_usdt - sell_usdt)
                                if imb > c_max_imb:
                                    c_max_imb = imb
                                    c_imb_price = price_mid
                                    c_imb_side = "BUY" if buy_usdt > sell_usdt else "SELL"
                        overall_buy += c_buy
                        overall_sell += c_sell
                        net = c_buy - c_sell
                        if net > 0:
                            buy_candles += 1
                        imb_str = f"{c_imb_side} ${c_imb_price:,.0f}"
                        output += f"{ts_str:>6} | {_fmt_num(c_buy):>10} | {_fmt_num(c_sell):>10} | {_fmt_num(net, signed=True):>10} | {imb_str:>20}\n"
                    output += "```\n"
                    fp_direction = "BUY" if overall_buy > overall_sell else "SELL"
                    n_candles = min(len(fp_data), 10)
                    output += (
                        f"**Summary:** {buy_candles}/{n_candles} buy-dominant | "
                        f"total buy ${overall_buy / 1e6:.2f}M vs sell ${overall_sell / 1e6:.2f}M → **{fp_direction}**\n"
                    )
                    # Top 5 imbalance levels from latest candle
                    latest_candle = fp_data[-1]
                    if isinstance(latest_candle, list) and len(latest_candle) >= 2:
                        level_imbalances = []
                        for level in latest_candle[1]:
                            if isinstance(level, list) and len(level) >= 8:
                                buy_u = level[6]
                                sell_u = level[7]
                                price_m = (level[0] + level[1]) / 2
                                level_imbalances.append((abs(buy_u - sell_u), price_m, buy_u, sell_u))
                        if level_imbalances:
                            level_imbalances.sort(reverse=True)
                            output += "\n**Top 5 imbalance levels (latest candle):**\n"
                            for imb_val, price, b, s in level_imbalances[:5]:
                                side = "BUY" if b > s else "SELL"
                                output += f"- ${price:,.2f}: {side} imbalance ${imb_val:,.0f} (buy ${b:,.0f} vs sell ${s:,.0f})\n"
                    output += "\n"
                else:
                    output += "**Footprint:** No data\n\n"
            elif isinstance(fp_result, Exception):
                output += (
                    f"**Footprint:** FAILED — "
                    f"{client._mask_key(str(fp_result))}\n\n"
                )
            else:
                output += "**Footprint:** Data expired or unavailable\n\n"
        else:
            output += "**Footprint:** Not available (requires Standard+ plan)\n\n"

        # ── Liquidation Orders ──
        liq_result = p2.get("Liq Orders")
        if isinstance(liq_result, FetchResult) and not liq_result.is_expired:
            output += _age_banner(liq_result)
            liq_data = liq_result.data
            if isinstance(liq_data, list) and liq_data:
                output += f"## Liquidation Orders — {raw_sym} | {exchange} | 24h\n\n"
                output += _fmt_scan_liq_orders(liq_data)
            else:
                output += f"**Liq Orders:** No {raw_sym} liquidation orders in last 24h\n\n"
        elif isinstance(liq_result, Exception):
            output += (
                f"**Liq Orders:** FAILED — "
                f"{client._mask_key(str(liq_result))}\n\n"
            )
        else:
            output += "**Liq Orders:** Data expired or unavailable\n\n"

        # ── Hyperliquid L/S Ratio ──
        hl_result = p2.get("Hyperliquid L/S")
        if isinstance(hl_result, FetchResult) and not hl_result.is_expired:
            output += _age_banner(hl_result)
            hl_data = hl_result.data
            if isinstance(hl_data, list) and hl_data:
                output += f"## Hyperliquid L/S Ratio — {raw_sym} | 1h\n\n"
                output += _fmt_p2_hyperliquid_ls(hl_data)
            else:
                output += f"**Hyperliquid L/S:** No data for {raw_sym}\n\n"
        elif isinstance(hl_result, Exception):
            output += f"**Hyperliquid L/S:** FAILED — {client._mask_key(str(hl_result))}\n\n"
        else:
            output += "**Hyperliquid L/S:** Data expired or unavailable\n\n"

        # ── Top Trader Position L/S Ratio ──
        tp_result = p2.get("Top Position L/S")
        if isinstance(tp_result, FetchResult) and not tp_result.is_expired:
            output += _age_banner(tp_result)
            tp_data = tp_result.data
            if isinstance(tp_data, list) and tp_data:
                output += f"## Top Position L/S — {raw_sym} | {exchange} | {interval}\n\n"
                output += _fmt_p2_top_position_ls(tp_data)
            else:
                output += f"**Top Position L/S:** No data for {raw_sym}\n\n"
        elif isinstance(tp_result, Exception):
            output += f"**Top Position L/S:** FAILED — {client._mask_key(str(tp_result))}\n\n"
        else:
            output += "**Top Position L/S:** Data expired or unavailable\n\n"

        # ── Spot Large Orders (filtered ±20% from current price) ──
        slo_result = p2.get("Spot Large Orders")
        if isinstance(slo_result, FetchResult) and not slo_result.is_expired:
            output += _age_banner(slo_result)
            slo_data = slo_result.data
            if isinstance(slo_data, list) and slo_data:
                # Get current price from Phase 1 Price OHLC for filtering
                current_price = 0.0
                for (lbl, _, _), res in zip(calls, raw_results):
                    if lbl == "Price OHLC" and isinstance(res, FetchResult):
                        pdata = res.data
                        if isinstance(pdata, list) and pdata:
                            last_c = pdata[-1]
                            if isinstance(last_c, dict):
                                current_price = float(_get(last_c, "c", "close", default=0))
                        break
                # Filter orders within ±20% of current price
                if current_price > 0:
                    lo = current_price * 0.8
                    hi = current_price * 1.2
                    slo_data = [
                        r for r in slo_data
                        if isinstance(r, dict) and lo <= float(_get(r, "limit_price", "price", default=0)) <= hi
                    ]
                output += f"## Spot Large Orders — {raw_sym} | {exchange} | ±20% from ${current_price:,.2f}\n\n"
                output += _fmt_p2_spot_large_orders(slo_data)
            else:
                output += f"**Spot Large Orders:** No large orders for {raw_sym}\n\n"
        elif isinstance(slo_result, Exception):
            output += f"**Spot Large Orders:** FAILED — {client._mask_key(str(slo_result))}\n\n"
        else:
            output += "**Spot Large Orders:** Data expired or unavailable\n\n"

        # ── RSI (from rsi/list — filter by symbol) ──
        rsi_result = p2.get("RSI")
        rsi_value = None
        rsi_label = ""
        if isinstance(rsi_result, FetchResult) and not rsi_result.is_expired:
            output += _age_banner(rsi_result)
            rsi_data = rsi_result.data
            if isinstance(rsi_data, list) and rsi_data:
                # rsi/list returns all coins — filter for our symbol
                coin_rsi = None
                for entry in rsi_data:
                    if isinstance(entry, dict) and entry.get("symbol", "").upper() in (sym, raw_sym):
                        coin_rsi = entry
                        break
                if coin_rsi:
                    # Pick RSI for the closest matching interval
                    interval_map = {"1m": "15m", "3m": "15m", "5m": "15m", "15m": "15m",
                                    "30m": "1h", "1h": "1h", "2h": "4h", "4h": "4h",
                                    "8h": "12h", "12h": "12h", "1d": "24h"}
                    rsi_key = f"rsi_{interval_map.get(interval, '1h')}"
                    rsi_value = coin_rsi.get(rsi_key)

                    # Show all available RSI timeframes
                    rsi_parts = []
                    for tf in ("15m", "1h", "4h", "12h", "24h"):
                        v = coin_rsi.get(f"rsi_{tf}")
                        if v is not None:
                            rsi_parts.append(f"{tf}={v:.1f}")
                    output += f"**RSI:** {' | '.join(rsi_parts)}\n"

                    if rsi_value is None:
                        output += f"*(no RSI data for {interval} interval)*\n"
                    output += "\n"
                else:
                    output += f"**RSI:** {raw_sym} not found in RSI list\n\n"
            else:
                output += "**RSI:** No data\n\n"
        elif isinstance(rsi_result, Exception):
            output += (
                f"**RSI:** FAILED — "
                f"{client._mask_key(str(rsi_result))}\n\n"
            )
        else:
            output += "**RSI:** Data expired or unavailable\n\n"


        # ═════════════════════════════════════════════════════════════════
        # PHASE 3 — BINANCE DIRECT CROSS-CHECK (data already fetched above)
        # ═════════════════════════════════════════════════════════════════
        output += "\n---\n\n"
        output += f"# PHASE 3 — BINANCE DIRECT CROSS-CHECK ({raw_sym})\n\n"

        # ── Futures Price + Funding Rate ──
        if isinstance(bn_premium, dict) and "markPrice" in bn_premium:
            mark = float(bn_premium["markPrice"])
            fr = float(bn_premium.get("lastFundingRate", 0))
            fr_pct = fr * 100
            fr_label = "NEGATIF (shorts pay)" if fr < 0 else "POSITIF (longs pay)"
            output += f"**Binance Mark Price:** ${mark:,.2f}\n"
            output += f"**Binance Funding Rate:** {fr_pct:+.4f}% — {fr_label}\n\n"
        elif isinstance(bn_premium, dict) and "error" in bn_premium:
            output += f"**Binance Price:** {bn_premium['error']}\n\n"

        # ── Open Interest ──
        if isinstance(bn_oi, dict) and "openInterest" in bn_oi:
            oi_val = float(bn_oi["openInterest"])
            if isinstance(bn_premium, dict) and "markPrice" in bn_premium:
                oi_usd = oi_val * float(bn_premium["markPrice"])
                output += f"**Binance OI:** {oi_val:,.0f} {raw_sym} (${oi_usd/1e6:,.1f}M)\n\n"
            else:
                output += f"**Binance OI:** {oi_val:,.0f} {raw_sym}\n\n"

        # ── Taker Buy/Sell Volume (cross-check FutCVD) ──
        if isinstance(bn_taker, list) and bn_taker:
            # Get mark price for USD conversion
            mark_price = 0.0
            if isinstance(bn_premium, dict) and "markPrice" in bn_premium:
                mark_price = float(bn_premium["markPrice"])
            output += "**Binance Futures Taker Buy/Sell:**\n```\n"
            output += f"{'Time':>6} | {'Buy':>10} | {'Sell':>10} | {'Net':>10} | {'Ratio':>6}\n"
            output += f"{'─'*6} | {'─'*10} | {'─'*10} | {'─'*10} | {'─'*6}\n"
            bn_buy_dom = 0
            bn_total_net = 0.0
            bn_taker_show = bn_taker[-10:]
            for t in bn_taker_show:
                ratio = float(t.get("buySellRatio", 0))
                buy_qty = float(t.get("buyVol", 0))
                sell_qty = float(t.get("sellVol", 0))
                buy_usd = buy_qty * mark_price if mark_price else buy_qty
                sell_usd = sell_qty * mark_price if mark_price else sell_qty
                net_usd = buy_usd - sell_usd
                bn_total_net += net_usd
                if net_usd > 0:
                    bn_buy_dom += 1
                ts_ms = t.get("timestamp", 0)
                ts_str = datetime.fromtimestamp(ts_ms / 1000, tz=WIB).strftime("%H:%M") if ts_ms else "?"
                output += f"{ts_str:>6} | {_fmt_num(buy_usd):>10} | {_fmt_num(sell_usd):>10} | {_fmt_num(net_usd, signed=True):>10} | {ratio:>6.3f}\n"
            output += "```\n"
            output += f"**Stats:** {bn_buy_dom}/{len(bn_taker_show)} buy-dominant | total net: {_fmt_num(bn_total_net, signed=True)}\n\n"

        # ── Long/Short Account Ratio ──
        if isinstance(bn_ls, list) and bn_ls:
            output += "**Binance L/S Ratio:**\n```\n"
            output += f"{'Time':>6} | {'Long %':>8} | {'Short %':>8} | {'Ratio':>8}\n"
            output += f"{'─'*6} | {'─'*8} | {'─'*8} | {'─'*8}\n"
            for ls_row in bn_ls[-10:]:
                ts_ms = ls_row.get("timestamp", 0)
                ts_str = datetime.fromtimestamp(ts_ms / 1000, tz=WIB).strftime("%H:%M") if ts_ms else "?"
                long_pct = float(ls_row.get("longAccount", 0)) * 100
                short_pct = float(ls_row.get("shortAccount", 0)) * 100
                ratio = float(ls_row.get("longShortRatio", 0))
                output += f"{ts_str:>6} | {long_pct:>7.1f}% | {short_pct:>7.1f}% | {ratio:>8.3f}\n"
            output += "```\n"
            if len(bn_ls) > 1:
                latest_ratio = float(bn_ls[-1].get("longShortRatio", 0))
                first_ratio = float(bn_ls[0].get("longShortRatio", 0))
                shift = latest_ratio - first_ratio
                shift_label = "more long" if shift > 0 else "more short"
                output += f"**Shift:** {shift:+.3f} ({shift_label})\n\n"
            else:
                output += "\n"

        # ── Spot Klines CVD (taker buy vs sell) ──
        if isinstance(bn_klines, list) and bn_klines:
            bn_klines_show = bn_klines[-10:]
            output += f"**Binance Spot CVD ({bn_spot_sym}):**\n"
            cumulative = 0.0
            buy_candles = 0
            for c in bn_klines_show:
                ts_str = datetime.fromtimestamp(c[0] / 1000, tz=WIB).strftime("%H:%M")
                vol = float(c[5])
                buy = float(c[9])
                sell = vol - buy
                delta = buy - sell
                cumulative += delta
                bias = "BUY" if delta > 0 else "SELL"
                if delta > 0:
                    buy_candles += 1
                output += f"- {ts_str}: buy={buy:,.0f} sell={sell:,.0f} delta={delta:+,.0f} → **{bias}**\n"
            output += f"- **Cumulative ({len(bn_klines_show)} candles): {cumulative:+,.0f} {sym} | {buy_candles}/{len(bn_klines_show)} buy-dominant**\n\n"
        elif isinstance(bn_klines, dict) and "error" in bn_klines:
            output += f"**Binance Spot:** {bn_spot_sym} — {bn_klines.get('error', 'not available')}\n\n"

        # ── VWAP (Volume Weighted Average Price) ──
        # Calculated from futures klines: VWAP = Σ(TP × Vol) / Σ(Vol)
        # where TP (Typical Price) = (High + Low + Close) / 3
        # Binance klines: [openTime, O, H, L, C, vol, closeTime, quoteVol, trades, takerBuyBaseVol, takerBuyQuoteVol, ...]
        if isinstance(bn_fut_klines, list) and len(bn_fut_klines) >= 3:
            try:
                sum_tp_vol = 0.0
                sum_vol = 0.0
                candle_count = len(bn_fut_klines)
                first_ts = bn_fut_klines[0][0]
                last_ts = bn_fut_klines[-1][0]

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
                    current_price = float(bn_fut_klines[-1][4])  # last close
                    diff = current_price - vwap
                    diff_pct = (diff / vwap) * 100
                    position = "ABOVE" if diff > 0 else "BELOW"
                    first_str = datetime.fromtimestamp(first_ts / 1000, tz=WIB).strftime("%H:%M")
                    last_str = datetime.fromtimestamp(last_ts / 1000, tz=WIB).strftime("%H:%M")

                    output += (
                        f"**Futures VWAP ({candle_count}x {interval}, {first_str}-{last_str} WIB):**\n"
                        f"- VWAP: ${vwap:,.2f}\n"
                        f"- Price: ${current_price:,.2f}\n"
                        f"- Price {position} VWAP by ${abs(diff):,.2f} ({diff_pct:+.2f}%)\n"
                        f"- Total volume: {_fmt_num(sum_vol, '$')}\n\n"
                    )
            except (IndexError, ValueError, TypeError):
                pass  # silently skip if klines format unexpected
        elif isinstance(bn_fut_klines, dict) and "error" in bn_fut_klines:
            output += f"**VWAP:** {bn_fut_klines.get('error', 'futures klines not available')}\n\n"

        output += "\n"

        # ═════════════════════════════════════════════════════════════════
        # PHASE 4 — ON-CHAIN LAYER (Nansen + Arkham)
        # ═════════════════════════════════════════════════════════════════
        output += "---\n\n"
        output += f"# PHASE 4 — ON-CHAIN LAYER ({raw_sym})\n\n"

        # Phase 4 data already fetched in parallel above

        # ── Nansen On-Chain Flows ──
        nansen_result = p4.get("nansen")
        if nansen_skip:
            output += (
                f"## Nansen On-Chain Flows — {raw_sym} | 1h\n\n"
                f"*(Not supported — BTC and Hyperliquid chain excluded)*\n\n"
            )
        elif isinstance(nansen_result, Exception):
            output += (
                f"## Nansen On-Chain Flows — {raw_sym} | 1h\n\n"
                f"**FAILED:** {str(nansen_result)[:200]}\n\n"
            )
        elif isinstance(nansen_result, dict) and nansen_result:
            output += f"## Nansen On-Chain Flows — {raw_sym} | 1h\n\n"
            output += _fmt_nansen_flows(nansen_result, raw_sym)
        elif nansen_has_map:
            output += f"## Nansen On-Chain Flows — {raw_sym} | 1h\n\n"
            output += f"**No data returned for {raw_sym}**\n\n"
        else:
            output += (
                f"## Nansen On-Chain Flows — {raw_sym} | 1h\n\n"
                f"*(Token not in address map — add to NANSEN_TOKEN_MAP)*\n\n"
            )

        # ── Arkham Exchange Flow ──
        arkham_result = p4.get("arkham")
        output += f"## Arkham Exchange Flow — {exchange} | {arkham_chain or 'ALL'}\n\n"
        if isinstance(arkham_result, Exception):
            output += f"**FAILED:** {str(arkham_result)[:200]}\n\n"
        elif isinstance(arkham_result, dict):
            if arkham_result.get("status") == "error":
                output += f"**ERROR:** {arkham_result.get('error', 'unknown')}\n\n"
            else:
                data = arkham_result.get("data")
                # Arkham returns {chain_name: [time_series]} — extract the series
                series = []
                if isinstance(data, dict):
                    # Try chain-specific key first, then fallback to any list value
                    if arkham_chain and arkham_chain in data:
                        series = data[arkham_chain]
                    else:
                        for v in data.values():
                            if isinstance(v, list):
                                series = v
                                break
                elif isinstance(data, list):
                    series = data

                if isinstance(series, list) and series:
                    # Show last 7 days (last 7 entries since it's daily data)
                    recent = series[-7:]
                    # Summary from last entry
                    total_in = sum(float(p.get("inflow", 0)) for p in recent)
                    total_out = sum(float(p.get("outflow", 0)) for p in recent)
                    total_net = total_in - total_out
                    direction = "NET INFLOW" if total_net > 0 else "NET OUTFLOW"
                    output += (
                        f"**7d Summary:** In {_fmt_num(total_in)} | "
                        f"Out {_fmt_num(total_out)} | "
                        f"Net {_fmt_num(abs(total_net))} ({direction})\n\n"
                    )
                    output += "```\n"
                    output += f"{'Date':>12} | {'Inflow':>12} | {'Outflow':>12} | {'Net':>12}\n"
                    output += f"{'─'*12} | {'─'*12} | {'─'*12} | {'─'*12}\n"
                    for point in recent:
                        ts = point.get("time", "")
                        # Arkham returns ISO date string "2026-03-20T00:00:00Z"
                        if isinstance(ts, str) and len(ts) >= 10:
                            ts_str = ts[:10]  # YYYY-MM-DD
                        else:
                            ts_str = _ts_wib(ts)
                        p_in = float(point.get("inflow", 0))
                        p_out = float(point.get("outflow", 0))
                        p_net = p_in - p_out
                        output += (
                            f"{ts_str:>12} | {_fmt_num(p_in):>12} | "
                            f"{_fmt_num(p_out):>12} | {_fmt_num(p_net, signed=True):>12}\n"
                        )
                    output += "```\n\n"
                else:
                    output += "**No flow data available**\n\n"
        else:
            output += "**No data**\n\n"

        output += "\n"

        # Build envelope with partial failure tracking
        _scan_warnings = []
        if supplementary_failed:
            _scan_warnings.append(f"Missing supplementary metrics: {', '.join(supplementary_failed)}")
        if max_age > STALE_WARNING_THRESHOLD:
            _scan_warnings.append(f"Oldest data in scan: {max_age:.0f}s — some metrics may be stale")
        _scan_status = "partial" if supplementary_failed else "success"
        return make_envelope(_scan_status, "coinglass", output,
                             data_age_seconds=max_age, warnings=_scan_warnings,
                             failed_endpoints=supplementary_failed or None)


    # ═══════════════════════════════════════════════════════════════════════════════
    # HISTORICAL & TREND TOOLS — Persistent Storage
    # ═══════════════════════════════════════════════════════════════════════════════

    ENDPOINT_MAP = {
        "spot_cvd": "/api/spot/aggregated-cvd/history",
        "futures_cvd": "/api/futures/aggregated-cvd/history",
        "funding_rate": "/api/futures/funding-rate/exchange-list",
        "open_interest": "/api/futures/open-interest/aggregated-history",
        "orderbook": "/api/futures/orderbook/aggregated-ask-bids-history",
        "liquidation": "/api/futures/liquidation/history",
        "price": "/api/futures/price/history",
        "taker": "/api/futures/aggregated-taker-buy-sell-volume/history",
        "long_short": "/api/futures/global-long-short-account-ratio/history",
    }


    @mcp.tool()
    @_tool_envelope()
    async def coinglass_compare(
        symbol: str = "BTC",
        metric: str = "spot_cvd",
        hours_ago: float = 1.0,
        interval: str = "5m",
        exchange: str = DEFAULT_EXCHANGE,
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
            exchange: Exchange name (Binance, OKX, Bybit, etc.)
        """
        endpoint = ENDPOINT_MAP.get(metric)
        if not endpoint:
            available = ", ".join(ENDPOINT_MAP.keys())
            return _err(f"Unknown metric '{metric}'. Available: {available}")

        sym = normalize_symbol(symbol)
        pair = to_pair(symbol)

        # Build params per V4 API requirements
        if metric == "funding_rate":
            params = {}
        elif metric == "orderbook":
            params = {"exchange_list": exchange, "symbol": sym, "interval": interval, "range": "1", "limit": 20}
        elif metric in ("spot_cvd", "futures_cvd"):
            # Aggregated CVD: exchange_list + coin
            params = {"exchange_list": exchange, "symbol": sym, "interval": interval, "limit": 20}
        elif metric == "taker":
            # Aggregated taker: exchange_list + coin
            params = {"exchange_list": exchange, "symbol": sym, "interval": interval, "limit": 20}
        elif metric in ("price", "long_short", "liquidation"):
            # Pair-level: exchange + pair
            params = {"exchange": exchange, "symbol": pair, "interval": interval, "limit": 20}
        else:
            # Aggregated coin-level (open_interest): exchange_list + coin
            params = {"exchange_list": exchange, "symbol": sym, "interval": interval, "limit": 20}

        current: FetchResult | None = None
        current_err = ""
        try:
            current = await client.get(endpoint, params)
        except Exception as e:
            current_err = str(e)

        # Get historical from storage
        historical = await client.storage.aget_historical(endpoint, sym, hours_ago, interval)

        output = f"## Compare {metric.upper()} — {sym}\n"

        if current is not None:
            output += f"### Current\n"
            output += _age_banner(current)
            output += _format_metric_data(metric, current.data)
        else:
            output += f"### Current\n**ERROR fetching live data:** {current_err}\n\n"

        if historical is not None:
            age = historical["age_minutes"]
            hist_data = historical["data"]
            ts = datetime.fromtimestamp(
                historical["fetched_at"], tz=WIB
            ).strftime("%Y-%m-%d %H:%M:%S WIB")

            # Accurate header: show actual snapshot age, not requested
            actual_h = age / 60
            output = output.replace(
                f"## Compare {metric.upper()} — {sym}\n",
                f"## Compare {metric.upper()} — {sym}\n"
                f"**Now vs {actual_h:.1f}h ago** "
                f"(requested {hours_ago}h, closest snapshot: {age:.0f}min ago)\n\n",
            )

            output += f"### Historical (from storage)\n"
            output += f"**Stored at:** {ts} ({age:.0f} minutes ago)\n\n"

            expected_age_min = hours_ago * 60
            drift = abs(age - expected_age_min)
            if drift > 30:
                output += (
                    f"**WARNING:** No snapshot from {hours_ago}h ago. "
                    f"Using closest available: {age:.0f}min ago "
                    f"(drift {drift:.0f}min). Interpret with caution.\n\n"
                )

            output += _format_metric_data(metric, hist_data)
        else:
            output += (
                f"### Historical\n"
                f"**NO DATA** from ~{hours_ago}h ago in storage.\n\n"
                f"Data is stored automatically each time you query a metric. "
                f"Keep querying periodically to build history for comparison.\n\n"
            )

        _cmp_age = current.age_seconds if current else 0
        _cmp_warnings = []
        if current and current.is_stale:
            _cmp_warnings.append(f"Current data is stale ({current.age_seconds:.0f}s old)")
        if historical is None:
            _cmp_warnings.append(f"No historical snapshot from ~{hours_ago}h ago")
        return make_envelope(
            "partial" if historical is None else "success",
            "coinglass", output, data_age_seconds=_cmp_age, warnings=_cmp_warnings)


    @mcp.tool()
    @_tool_envelope()
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
            return _err(f"Unknown metric '{metric}'. Available: {available}")

        sym = normalize_symbol(symbol)
        snapshots = await client.storage.aget_trend(endpoint, sym, hours, interval)

        output = f"## Trend {metric.upper()} — {sym} (last {hours}h)\n\n"

        if not snapshots:
            output += (
                f"*No stored snapshots found for {sym} {metric} in the last {hours}h.*\n\n"
                "**Tip:** Data is stored automatically each time you query a metric. "
                "Use the individual tools periodically to build up historical "
                "snapshots for trend analysis.\n"
            )
            return make_envelope("partial", "coinglass", output,
                                 warnings=[f"No stored snapshots for {sym} {metric} in last {hours}h"])

        output += f"**{len(snapshots)} snapshots found**\n\n"

        # Extract CVD/value from each snapshot for reset detection
        cvd_values = []
        for snap in snapshots:
            ts = datetime.fromtimestamp(
                snap["fetched_at"], tz=WIB
            ).strftime("%H:%M:%S WIB")
            age = snap["age_minutes"]
            data = snap["data"]
            age_label = f"{age:.0f}min ago"
            output += f"### {ts} ({age_label})\n"
            output += _format_metric_data(metric, data)
            # Track CVD values for reset detection
            if isinstance(data, list) and data:
                last = data[-1]
                if isinstance(last, dict):
                    for key in ("cum_vol_delta", "cvd", "v", "value"):
                        if key in last:
                            cvd_values.append((ts, float(last[key])))
                            break

        # Detect CVD resets (sudden drops >40% between consecutive snapshots)
        if "cvd" in metric and len(cvd_values) >= 2:
            for i in range(1, len(cvd_values)):
                prev_ts, prev_val = cvd_values[i - 1]
                curr_ts, curr_val = cvd_values[i]
                if prev_val != 0:
                    change_pct = (curr_val - prev_val) / abs(prev_val) * 100
                    if change_pct < -40:
                        output += (
                            f"**CVD RESET DETECTED** between {prev_ts} → {curr_ts} "
                            f"(drop {change_pct:.0f}%)\n"
                            f"This is likely a daily CVD reset, NOT a bearish signal. "
                            f"Verify against raw chart before interpreting.\n\n"
                        )

        return make_envelope("success", "coinglass", output)


    @mcp.tool()
    @_tool_envelope()
    async def coinglass_storage_stats(symbol: str = "") -> str:
        """Show storage statistics — how much historical data is stored.

        Shows total records, oldest/newest data, top symbols tracked.
        Use this to check if historical data is available for trend analysis.

        Args:
            symbol: Ignored — shows all stats. Accepted for compatibility.
        """
        stats = await client.storage.aget_stats()
        output = "## Storage Statistics\n\n"
        output += json.dumps(stats, indent=2, default=str) + "\n\n"

        # Rate limit info
        if client._rate_limiter:
            rl = client._rate_limiter.usage
            output += (
                f"## Rate Limit Status\n\n"
                f"- Used: {rl['used']}/{rl['limit']} req/min\n"
                f"- Remaining: {rl['remaining']}\n\n"
            )

        output += (
            "**How it works:**\n"
            "- Every API call is automatically stored in SQLite\n"
            "- Data is kept for 48 hours, then auto-cleaned\n"
            "- Use `coinglass_compare` to compare now vs X hours ago\n"
            "- Use `coinglass_trend` to see how a metric changed over time\n"
            "- More queries = more history = better trend analysis\n"
        )
        return make_envelope("success", "coinglass", output)
