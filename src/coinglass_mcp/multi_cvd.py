"""Multi-exchange CVD from REST trades endpoints (Level 1).

Fetches last N trades from Binance + OKX + Bybit + Hyperliquid in parallel,
normalizes to USD delta per trade (buy − sell), buckets by time interval,
produces per-exchange CVD table + aggregate total.

Window limitation: each exchange only returns last 500-1000 trades via REST,
so the effective window depends on symbol activity:
  - BTC/ETH on Binance: ~5-15 minutes
  - Mid-cap alts: ~30-60 minutes
  - Low-cap: several hours

Trade side conventions:
  Binance aggTrades: `m` = isBuyerMaker.
    m=false → taker is BUYER (aggressive buy) → +delta
    m=true  → taker is SELLER (aggressive sell) → -delta
  OKX trades:    side = "buy"  → +delta,  "sell" → -delta
  Bybit trades:  side = "Buy"  → +delta,  "Sell" → -delta
  HL trades:     side = "B"    → +delta,  "A" (ask/sell) → -delta
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone, timedelta
from typing import Any

from . import okx_client, bybit_client, hyperliquid_client, multi_exchange
from .binance_client import binance_futures_request
from .config import make_envelope

WIB = timezone(timedelta(hours=7))

# Trades per exchange — REST max limits:
BINANCE_LIMIT = 1000   # max aggTrades
OKX_LIMIT = 500        # max 500
BYBIT_LIMIT = 1000     # max 1000
HL_TRADES_BACK = 500   # approximate


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _f(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _dt(ts_ms: int) -> str:
    """HH:MM smart format."""
    if not ts_ms:
        return "N/A"
    dt_obj = datetime.fromtimestamp(int(ts_ms) / 1000, WIB)
    if dt_obj.date() == datetime.now(WIB).date():
        return dt_obj.strftime("%H:%M")
    return dt_obj.strftime("%m-%d %H:%M")


def _dollar(v: float) -> str:
    av = abs(v)
    sign = "" if v >= 0 else "-"
    if av >= 1_000_000_000:
        return f"{sign}${av / 1e9:,.2f}B"
    if av >= 1_000_000:
        return f"{sign}${av / 1e6:,.2f}M"
    if av >= 1_000:
        return f"{sign}${av / 1e3:,.1f}K"
    return f"{sign}${av:,.0f}"


# ─── Per-exchange trade fetchers + normalizers ──────────────────────────────


async def _fetch_binance_trades(symbol: str) -> list[dict]:
    """Binance futures aggTrades → [{ts_ms, delta_usd}]."""
    resp = await binance_futures_request(
        "/fapi/v1/aggTrades", {"symbol": symbol, "limit": BINANCE_LIMIT}, 20,
    )
    if not isinstance(resp, list):
        return []
    out = []
    for t in resp:
        price = _f(t.get("p"))
        qty = _f(t.get("q"))
        is_buyer_maker = t.get("m", False)
        # m=false → taker is buyer (aggressive buy), +delta
        sign = -1.0 if is_buyer_maker else 1.0
        out.append({
            "ts_ms": int(t.get("T", 0)),
            "delta_usd": sign * price * qty,
        })
    return out


async def _fetch_okx_trades(symbol: str) -> list[dict]:
    """OKX trades → [{ts_ms, delta_usd}]. sz is in contracts (apply ctVal)."""
    okx_sym = okx_client.to_okx_swap(symbol)
    resp = await okx_client.okx_request(
        "/api/v5/market/trades", {"instId": okx_sym, "limit": str(OKX_LIMIT)},
    )
    if not isinstance(resp, list):
        return []
    ct_val = await multi_exchange._okx_ct_val(symbol)
    out = []
    for t in resp:
        price = _f(t.get("px"))
        sz_contracts = _f(t.get("sz"))
        base_qty = sz_contracts * ct_val
        side = t.get("side", "").lower()
        sign = 1.0 if side == "buy" else -1.0
        out.append({
            "ts_ms": int(t.get("ts", 0)),
            "delta_usd": sign * price * base_qty,
        })
    return out


async def _fetch_bybit_trades(symbol: str) -> list[dict]:
    """Bybit trades → [{ts_ms, delta_usd}]. size is in base units."""
    resp = await bybit_client.bybit_request(
        "/v5/market/recent-trade",
        {"category": "linear", "symbol": symbol, "limit": BYBIT_LIMIT},
    )
    if not isinstance(resp, dict) or not isinstance(resp.get("list"), list):
        return []
    out = []
    for t in resp["list"]:
        price = _f(t.get("price"))
        size = _f(t.get("size"))
        side = t.get("side", "").lower()
        sign = 1.0 if side == "buy" else -1.0
        out.append({
            "ts_ms": int(t.get("time", 0)),
            "delta_usd": sign * price * size,
        })
    return out


async def _fetch_hl_trades(symbol: str) -> list[dict]:
    """HL recentTrades → [{ts_ms, delta_usd}]. Side: B=buy taker, A=ask/sell taker."""
    hl_coin = hyperliquid_client.to_hl_coin(symbol)
    resp = await hyperliquid_client.hl_request(
        {"type": "recentTrades", "coin": hl_coin},
    )
    if not isinstance(resp, list):
        return []
    out = []
    for t in resp:
        price = _f(t.get("px"))
        size = _f(t.get("sz"))
        side = t.get("side", "")
        sign = 1.0 if side == "B" else -1.0
        out.append({
            "ts_ms": int(t.get("time", 0)),
            "delta_usd": sign * price * size,
        })
    return out


# ─── Bucketing + aggregation ─────────────────────────────────────────────────


def _bucket_delta(
    trades: list[dict], interval_ms: int, since_ms: int,
) -> dict[int, float]:
    """Group trades into time buckets, return {bucket_start_ms: sum_delta_usd}."""
    out: dict[int, float] = {}
    for t in trades:
        ts = t["ts_ms"]
        if ts < since_ms:
            continue
        bucket = (ts // interval_ms) * interval_ms
        out[bucket] = out.get(bucket, 0.0) + t["delta_usd"]
    return out


# ─── Coverage transparency ──────────────────────────────────────────────────


def _compute_coverage(
    raw_trades_by_exchange: dict[str, list[dict]], now_ms: int,
) -> dict[str, dict]:
    """Coverage per exchange = (now − oldest trade ts) in minutes.

    Computed from raw REST response BEFORE since_ms filter, so it reflects
    the actual reach of the REST endpoint regardless of requested window.
    Empty exchange (failed or zero trades) → minutes=0.0, status="empty".
    """
    coverage: dict[str, dict] = {}
    for exch, trades in raw_trades_by_exchange.items():
        if not trades:
            coverage[exch] = {"minutes": 0.0, "trade_count": 0, "status": "empty"}
            continue
        oldest_ts = min(t["ts_ms"] for t in trades if t.get("ts_ms"))
        coverage_min = round((now_ms - oldest_ts) / 60000, 1) if oldest_ts else 0.0
        coverage[exch] = {
            "minutes": max(coverage_min, 0.0),
            "trade_count": len(trades),
            "status": "ok",
        }
    return coverage


def _build_coverage_warning(coverage: dict[str, dict], window_min: int) -> str | None:
    """Emit a warning when min coverage across active exchanges < 50% of requested.

    Returns None when coverage is sufficient.
    """
    valid = [c["minutes"] for c in coverage.values() if c["status"] == "ok"]
    if not valid:
        return "All exchanges returned empty trades. Check symbol or API status."
    min_cov = min(valid)
    if min_cov < window_min * 0.5:
        return (
            f"Requested window={window_min}min but actual REST coverage ~{min_cov}min "
            f"(limited by exchange trade history caps). "
            f"For deeper history use binance_spot_cvd / binance_futures_cvd "
            f"(kline-based, supports hours/days)."
        )
    return None


def _format_coverage_section(coverage: dict[str, dict], window_min: int) -> str:
    """Render coverage as a markdown block appended to the response data."""
    lines = ["**REST coverage (per exchange):**"]
    for exch in ("binance", "okx", "bybit", "hyperliquid"):
        c = coverage.get(exch, {"minutes": 0.0, "trade_count": 0, "status": "empty"})
        if c["status"] == "ok":
            lines.append(
                f"- {exch.capitalize():<12}: {c['minutes']:>5.1f} min "
                f"({c['trade_count']} trades)"
            )
        else:
            lines.append(f"- {exch.capitalize():<12}: — (empty / failed)")
    lines.append(f"- Requested window: {window_min} min")
    return "\n".join(lines)


# ─── Main tool ──────────────────────────────────────────────────────────────


async def multi_exchange_cvd_live(
    symbol: str,
    interval_min: int = 5,
    window_min: int = 30,
) -> str:
    """Multi-exchange CVD from REST trades (last N minutes, 4 exchanges).

    Aggregates aggressive taker flow per time bucket across
    Binance + OKX + Bybit + Hyperliquid. Useful for detecting
    cross-exchange divergence (e.g., Binance buying but OKX selling).

    Window is limited by REST trades endpoint (typically ~30min for BTC on
    Binance, longer for lower-volume symbols). For deeper history use
    binance_spot_cvd / binance_futures_cvd (single-source, klines-based).

    Args:
        symbol: Perp symbol (e.g. BTCUSDT)
        interval_min: Bucket size in minutes (default 5)
        window_min: Lookback window in minutes (default 30)
    """
    sym_upper = symbol.upper()
    interval_ms = interval_min * 60 * 1000
    since_ms = int((datetime.now(timezone.utc).timestamp() - window_min * 60) * 1000)

    # Parallel fetch 4 exchanges
    bnb_raw, okx_raw, bybit_raw, hl_raw = await asyncio.gather(
        _fetch_binance_trades(sym_upper),
        _fetch_okx_trades(sym_upper),
        _fetch_bybit_trades(sym_upper),
        _fetch_hl_trades(sym_upper),
        return_exceptions=True,
    )

    ok: list[str] = []
    failed: dict[str, str] = {}

    def _process(name: str, raw) -> dict[int, float]:
        if isinstance(raw, Exception):
            failed[name] = str(raw)
            return {}
        if not raw:
            failed[name] = "empty response"
            return {}
        ok.append(name)
        return _bucket_delta(raw, interval_ms, since_ms)

    bnb = _process("binance", bnb_raw)
    okx = _process("okx", okx_raw)
    bybit = _process("bybit", bybit_raw)
    hl = _process("hyperliquid", hl_raw)

    # Coverage transparency: compute from raw trades BEFORE since_ms filter
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    raw_lists = {
        "binance": bnb_raw if isinstance(bnb_raw, list) else [],
        "okx": okx_raw if isinstance(okx_raw, list) else [],
        "bybit": bybit_raw if isinstance(bybit_raw, list) else [],
        "hyperliquid": hl_raw if isinstance(hl_raw, list) else [],
    }
    coverage = _compute_coverage(raw_lists, now_ms)
    cov_warning = _build_coverage_warning(coverage, window_min)
    coverage_section = _format_coverage_section(coverage, window_min)

    title = f"Multi-Exchange CVD (live) — {sym_upper} (last {window_min}min, {interval_min}m buckets)"
    src_tag = "+".join(ok) if ok else "none"
    # Note: timestamp is canonical via envelope.timestamp — markdown stays format-only
    hdr = (
        f"## {title}\n\n"
        f"Data: LIVE | Source: {src_tag}\n\n"
        f"{coverage_section}\n\n"
    )

    if not ok:
        warns = [cov_warning] if cov_warning else []
        return make_envelope(
            "failed", "binance",
            hdr + "**ERROR:** All exchanges failed\n" +
            "\n".join(f"- {k}: {v}" for k, v in failed.items()),
            warnings=warns,
        )

    # All buckets present across any exchange
    all_buckets = sorted(
        set(bnb.keys()) | set(okx.keys()) | set(bybit.keys()) | set(hl.keys())
    )
    if not all_buckets:
        warns = [cov_warning] if cov_warning else []
        return make_envelope(
            "failed", src_tag,
            hdr + "**WARNING:** No trades in window (try larger window_min)",
            warnings=warns,
        )

    # Per-exchange running CVD (cumulative sum over time)
    rows = []
    run = {"binance": 0.0, "okx": 0.0, "bybit": 0.0, "hyperliquid": 0.0, "total": 0.0}
    for b in all_buckets:
        d_bnb = bnb.get(b, 0.0)
        d_okx = okx.get(b, 0.0)
        d_byb = bybit.get(b, 0.0)
        d_hl = hl.get(b, 0.0)
        d_total = d_bnb + d_okx + d_byb + d_hl

        run["binance"] += d_bnb
        run["okx"] += d_okx
        run["bybit"] += d_byb
        run["hyperliquid"] += d_hl
        run["total"] += d_total

        rows.append({
            "ts_ms": b,
            "delta": {"binance": d_bnb, "okx": d_okx, "bybit": d_byb,
                      "hyperliquid": d_hl, "total": d_total},
            "cvd": dict(run),
        })

    # Delta table — Time left-aligned, numeric columns right-aligned
    table = "**Delta per bucket (taker buy − sell, USD):**\n```\n"
    table += f" {'Time':<8} | {'Binance':>9} | {'OKX':>9} | {'Bybit':>9} | {'HL':>9} | {'Total':>10}\n"
    table += f" {'─'*8} | {'─'*9} | {'─'*9} | {'─'*9} | {'─'*9} | {'─'*10}\n"
    for r in rows:
        t = _dt(r["ts_ms"])
        d = r["delta"]
        def _s(v):
            return f"+{_dollar(v)}" if v > 0 else _dollar(v) if v < 0 else "—"
        table += (
            f" {t:<8} | {_s(d['binance']):>9} | {_s(d['okx']):>9} | "
            f"{_s(d['bybit']):>9} | {_s(d['hyperliquid']):>9} | {_s(d['total']):>10}\n"
        )
    table += "```\n\n"

    # Cumulative CVD summary
    last = rows[-1]
    summary_lines = ["**Cumulative CVD over window:**"]
    for ex in ("binance", "okx", "bybit", "hyperliquid"):
        if ex in ok:
            v = last["cvd"][ex]
            tag = "BUY" if v > 0 else "SELL" if v < 0 else "flat"
            summary_lines.append(f"- {ex.capitalize():<12}: {_dollar(v):>10} ({tag})")
        else:
            summary_lines.append(f"- {ex.capitalize():<12}: {'—':>10} (failed)")
    total = last["cvd"]["total"]
    total_tag = "NET BUY" if total > 0 else "NET SELL" if total < 0 else "flat"
    summary_lines.append(f"- **Total**      : **{_dollar(total)}** ({total_tag})")

    # Divergence flag: are any exchanges opposite signed to total?
    per_ex = {e: last["cvd"][e] for e in ok}
    total_sign = 1 if total > 0 else -1 if total < 0 else 0
    outliers = [e for e, v in per_ex.items()
                if total_sign != 0 and (v > 0) != (total > 0) and abs(v) > 0]
    if outliers:
        summary_lines.append("")
        summary_lines.append(
            f"⚠️ **Exchange divergence:** {', '.join(outliers)} positioned opposite to total — "
            f"possible profit-taking or venue-specific flow"
        )
    else:
        summary_lines.append("")
        summary_lines.append("✅ **Consensus:** all active exchanges aligned with total direction")

    if failed:
        summary_lines.append("")
        summary_lines.append(f"⚠️ Failed sources: {', '.join(failed.keys())}")

    warns: list[str] = []
    if cov_warning:
        warns.append(cov_warning)

    # Structured payload for programmatic / analytical use
    data_struct = {
        "symbol": sym_upper,
        "interval_min": interval_min,
        "window_min": window_min,
        "active_exchanges": ok,
        "failed_exchanges": list(failed.keys()),
        "buckets": [
            {
                "ts_ms": r["ts_ms"],
                "time": _dt(r["ts_ms"]),
                "delta": r["delta"],
                "cvd": r["cvd"],
            }
            for r in rows
        ],
        "totals": {ex: last["cvd"][ex] for ex in ("binance", "okx", "bybit", "hyperliquid", "total")},
        "net_direction": "buy" if total > 0 else "sell" if total < 0 else "flat",
        "divergence_outliers": outliers,
        "coverage": coverage,
    }

    status = "success" if not failed else "partial"
    return make_envelope(
        status, src_tag, hdr + table + "\n".join(summary_lines),
        warnings=warns, data_struct=data_struct,
    )
