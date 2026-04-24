"""Binance USDT-M Futures Market Data Tools for McPCG.

These are the MOST RELEVANT for trading framework —
OI, funding rate, taker ratio, long/short ratio.
All PUBLIC endpoints — no API key required.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone, timedelta
from typing import Any

from .binance_client import binance_futures_request
from .config import make_envelope
from . import multi_exchange as _mx

WIB = timezone(timedelta(hours=7))


# ═══════════════════════════════════════════════════════════════════════════════
# MULTI-EXCHANGE HELPERS (Binance + OKX aggregation)
# ═══════════════════════════════════════════════════════════════════════════════


def _multi_header(title: str, ok: list[str], failed: dict[str, str]) -> str:
    """Header for multi-exchange aggregated output."""
    src = "+".join(ok) if ok else "none"
    tag = f"Source: {src}"
    if failed:
        tag += f" | Failed: {', '.join(failed.keys())}"
    return f"## {title}\n\nData: LIVE | {tag} | {_ts()}\n\n"


def _multi_source_tag(ok: list[str]) -> str:
    """Envelope source tag. Joins all successful exchanges (e.g. 'binance+okx+bybit').

    Preserves canonical order: binance → okx → bybit.
    """
    order = ["binance", "okx", "bybit", "hyperliquid"]
    parts = [e for e in order if e in ok]
    if parts:
        return "+".join(parts)
    return "binance"  # fallback (even on total failure, we came from binance tool)


def _build_warnings(ok: list[str], failed: dict[str, str], total_possible: int = 4) -> list[str]:
    """Build informative warnings[] for envelope when some sources fail.

    Downstream consumers (TELEGLAS, dashboards) can use these to decide
    whether to surface the partial-coverage state to users.
    """
    warnings: list[str] = []
    if failed:
        failed_names = ", ".join(failed.keys())
        ok_names = "+".join(ok) if ok else "none"
        n_ok = len(ok)
        warnings.append(
            f"Source(s) failed: {failed_names}. Computed from {n_ok}/{total_possible} "
            f"exchanges ({ok_names})."
        )
        # Per-exchange error detail (useful for debugging transient vs persistent)
        for name, err in failed.items():
            short_err = (err[:60] + "…") if len(err) > 60 else err
            warnings.append(f"{name}: {short_err}")
    return warnings


def _multi_err_check(result: dict, hdr: str) -> str | None:
    """Emit failed-envelope if no exchange responded successfully."""
    if result.get("status") == "failed":
        fe = result.get("exchanges_failed", {})
        detail = "; ".join(f"{k}: {v}" for k, v in fe.items())
        return make_envelope(
            "failed", _multi_source_tag(result.get("exchanges_ok", [])),
            hdr + f"**ERROR:** {detail}",
            fallback_suggestion="Try coinglass equivalent tool",
        )
    return None


# ═══════════════════════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def _ts() -> str:
    return datetime.now(WIB).strftime("%H:%M:%S WIB")


def _header(title: str) -> str:
    return f"## {title}\n\nData: LIVE | Source: Binance Futures | {_ts()}\n\n"


def _dt(ts) -> str:
    """Smart timestamp: HH:MM if today, else MM-DD HH:MM."""
    if not ts:
        return "N/A"
    dt_obj = datetime.fromtimestamp(int(ts) / 1000, WIB)
    now = datetime.now(WIB)
    if dt_obj.date() == now.date():
        return dt_obj.strftime("%H:%M")
    return dt_obj.strftime("%m-%d %H:%M")


def _f(val) -> float:
    """Safe float conversion."""
    try:
        return float(val)
    except (TypeError, ValueError):
        return 0.0


def _dollar(val: float) -> str:
    """Format dollar value with appropriate suffix."""
    av = abs(val)
    sign = "" if val >= 0 else "-"
    if av >= 1_000_000_000:
        return f"{sign}${av / 1e9:,.2f}B"
    if av >= 1_000_000:
        return f"{sign}${av / 1e6:,.2f}M"
    if av >= 1_000:
        return f"{sign}${av / 1e3:,.1f}K"
    return f"{sign}${av:,.2f}"


def _price(val: float) -> str:
    """Smart price formatting based on magnitude."""
    if val == 0:
        return "$0.00"
    if val >= 10:
        return f"${val:,.2f}"
    if val >= 0.01:
        return f"${val:.4f}"
    if val >= 0.0001:
        return f"${val:.6f}"
    return f"${val:.8f}"


def _err_check(data: Any, hdr: str) -> str | None:
    """Return envelope error string if data is error/empty, else None."""
    if isinstance(data, dict) and "error" in data:
        fallback = "Try coinglass equivalent tools"
        return make_envelope("failed", "binance", hdr + f"**ERROR:** {data['error']}",
                             fallback_suggestion=fallback)
    if isinstance(data, list) and len(data) == 0:
        return make_envelope("failed", "binance", hdr + "**WARNING: Empty dataset.**")
    return None


def _ok(content: str) -> str:
    """Wrap success content in Binance envelope."""
    return make_envelope("success", "binance", content)


# ═══════════════════════════════════════════════════════════════════════════════
# TOOL 9: binance_futures_price
# ═══════════════════════════════════════════════════════════════════════════════

async def binance_futures_price(symbol: str = "") -> str:
    """Get futures mark price + FR aggregated across Binance + OKX.

    With symbol: vol-weighted mark price + per-exchange breakdown.
    Without symbol: all Binance perps (OKX multi-symbol aggregation not supported — single-source).

    Args:
        symbol: Futures pair (e.g. SOLUSDT). Leave empty for all (Binance-only).
    """
    # Multi-symbol path: OKX doesn't support bulk ticker the same way — keep Binance-only
    if not symbol:
        params: dict = {}
        data = await binance_futures_request("/fapi/v1/premiumIndex", params, 10)
        label = "Futures Prices (all, Binance only)"
        hdr = _header(label)
        e = _err_check(data, hdr)
        if e:
            return e
        items = data if isinstance(data, list) else [data]
        if len(items) > 30:
            total = len(items)
            items = items[:30]
            hdr += f"*(showing first 30 of {total})*\n\n"
        table = "```\n"
        table += f" {'Symbol':<14} | {'Mark Price':>13} | {'FR':>9} | Next\n"
        table += f" {'─' * 14} | {'─' * 13} | {'─' * 9} | ─────\n"
        for d in items:
            sym = d.get("symbol", "")
            mark = _f(d.get("markPrice"))
            fr = _f(d.get("lastFundingRate")) * 100
            next_t = _dt(d.get("nextFundingTime", 0))
            table += f" {sym:<14} | {_price(mark):>13} | {fr:>+8.4f}% | {next_t}\n"
        table += "```\n"
        return _ok(hdr + table)

    # Single symbol: aggregate from Binance + OKX
    sym_upper = symbol.upper()
    result = await _mx.aggregate_mark_price(sym_upper)
    title = f"Futures Price (agg) — {sym_upper}"
    ok = result["exchanges_ok"]
    failed = result["exchanges_failed"]

    hdr = _multi_header(title, ok, failed)
    e = _multi_err_check(result, hdr)
    if e:
        return e

    agg = result["aggregated"]
    by = result["by_exchange"]

    lines = [
        f"- **VWAP Mark Price:** {_price(agg['vwap_mark_price'])} "
        f"(weighted by 24h vol)",
        f"- **Total 24h Volume:** {_dollar(agg['total_vol_24h_usd'])}",
        "",
        "**By exchange:**",
    ]
    for ex in ("binance", "okx", "bybit", "hyperliquid"):
        d = by.get(ex)
        if not d:
            continue
        pct = (d["vol_24h_usd"] / agg["total_vol_24h_usd"] * 100) if agg["total_vol_24h_usd"] else 0
        fr_str = ""
        if "funding_rate" in d:
            fr_pct = d["funding_rate"] * 100
            fr_tag = "negatif" if fr_pct < 0 else "positif" if fr_pct > 0 else "neutral"
            fr_str = f" | FR {fr_pct:+.4f}% ({fr_tag})"
        lines.append(
            f"- **{ex.capitalize()}:** mark {_price(d['mark_price'])} | "
            f"vol24h {_dollar(d['vol_24h_usd'])} ({pct:.1f}%){fr_str}"
        )

    if failed:
        lines.append("")
        lines.append(f"⚠️ **Failed:** {', '.join(failed.keys())}")

    status = "success" if not failed else "partial"
    return make_envelope(
        status, _multi_source_tag(ok), hdr + "\n".join(lines) + "\n",
        warnings=_build_warnings(ok, failed, total_possible=4),
    )


# ═══════════════════════════════════════════════════════════════════════════════
# TOOL 10: binance_futures_funding_rate
# ═══════════════════════════════════════════════════════════════════════════════

async def binance_futures_funding_rate(symbol: str, limit: int = 100) -> str:
    """Get funding rate from Binance + OKX.

    Shows current OI-weighted FR (primary signal) + Binance FR history (8h periods).
    OKX FR history is side-by-side when timestamps align; Binance is authoritative timeline
    because its FR epoch is the standard 8h tick.

    Args:
        symbol: Futures pair (e.g. SOLUSDT)
        limit: Number of Binance history records (default 100, max 1000)
    """
    sym_upper = symbol.upper()
    limit = min(limit, 1000)

    # Parallel: current aggregate + Binance FR history + OKX FR history + Bybit FR history
    okx_sym = _mx.okx_client.to_okx_swap(sym_upper)
    current_task = _mx.aggregate_funding_current(sym_upper)
    bnb_hist_task = binance_futures_request(
        "/fapi/v1/fundingRate",
        {"symbol": sym_upper, "limit": limit},
        1,
    )
    okx_hist_task = _mx.okx_client.okx_request(
        "/api/v5/public/funding-rate-history",
        {"instId": okx_sym, "limit": "100"},
    )
    bybit_hist_task = _mx.bybit_client.bybit_request(
        "/v5/market/funding/history",
        {"category": "linear", "symbol": sym_upper, "limit": min(limit, 200)},
    )

    current, bnb_hist, okx_hist, bybit_hist = await asyncio.gather(
        current_task, bnb_hist_task, okx_hist_task, bybit_hist_task,
        return_exceptions=True,
    )

    title = f"Funding Rate — {sym_upper}"
    ok_current = current["exchanges_ok"] if isinstance(current, dict) else []
    failed_current = current["exchanges_failed"] if isinstance(current, dict) else {}

    hdr = _multi_header(title, ok_current, failed_current)

    # Build OKX history lookup by fundingTime bucket (8h buckets)
    okx_by_ts: dict[int, float] = {}
    if (not isinstance(okx_hist, Exception)
            and not _mx._is_err(okx_hist)
            and isinstance(okx_hist, list)):
        for d in okx_hist:
            ts = _mx._floor_ts(d.get("fundingTime", 0), "8h")
            okx_by_ts[ts] = _f(d.get("fundingRate"))

    # Build Bybit history lookup by fundingRateTimestamp (8h buckets)
    bybit_by_ts: dict[int, float] = {}
    if (not isinstance(bybit_hist, Exception)
            and not _mx._is_err(bybit_hist)
            and isinstance(bybit_hist, dict)
            and isinstance(bybit_hist.get("list"), list)):
        for d in bybit_hist["list"]:
            ts = _mx._floor_ts(d.get("fundingRateTimestamp", 0), "8h")
            bybit_by_ts[ts] = _f(d.get("fundingRate"))

    # Binance history as authoritative timeline
    if isinstance(bnb_hist, Exception) or _mx._is_err(bnb_hist):
        # No history — fallback to current only
        if isinstance(current, dict) and current.get("status") != "failed":
            agg = current["aggregated"]
            by = current["by_exchange"]
            lines = [
                f"- **Weighted FR (current):** {agg['weighted_funding_rate']*100:+.4f}%",
                f"- **Total OI:** {_dollar(agg['total_oi_usd'])}",
                "",
                "**By exchange:**",
            ]
            for ex in ("binance", "okx"):
                d = by.get(ex)
                if not d:
                    continue
                lines.append(
                    f"- **{ex.capitalize()}:** FR {d['funding_rate']*100:+.4f}% | "
                    f"OI {_dollar(d['oi_usd'])}"
                )
            lines.append("")
            lines.append("⚠️ Binance FR history unavailable; showing current snapshot only.")
            return make_envelope("partial", _multi_source_tag(ok_current),
                                 hdr + "\n".join(lines) + "\n")
        err = _mx._err_msg(bnb_hist) if isinstance(bnb_hist, dict) else str(bnb_hist)
        return make_envelope("failed", "binance",
                             hdr + f"**ERROR:** Binance FR history failed: {err}")

    items = bnb_hist if isinstance(bnb_hist, list) else [bnb_hist]

    # Current weighted FR header block
    preamble = ""
    if isinstance(current, dict) and current.get("status") != "failed":
        agg = current["aggregated"]
        by = current["by_exchange"]
        parts = []
        for ex in ("binance", "okx", "bybit", "hyperliquid"):
            d = by.get(ex)
            if d:
                interval_hrs = d.get("funding_interval_hours", 8)
                suffix = f" (per {interval_hrs}h)" if interval_hrs != 8 else ""
                parts.append(f"{ex.capitalize()} {d['funding_rate']*100:+.4f}%{suffix}")
        preamble = (
            f"**Current (weighted by OI):** {agg['weighted_funding_rate']*100:+.4f}% | "
            f"{' | '.join(parts)}\n\n"
        )

    if len(items) > 30:
        total = len(items)
        items = items[-30:]
        hdr += f"*(showing last 30 of {total})*\n\n"

    table = "```\n"
    table += f" {'Time':>16} | {'Binance':>9} | {'OKX':>9} | {'Bybit':>9} | {'Mark Price':>13}\n"
    table += f" {'─' * 16} | {'─' * 9} | {'─' * 9} | {'─' * 9} | {'─' * 13}\n"
    for d in items:
        ts_raw = d.get("fundingTime", 0)
        t = _dt(ts_raw)
        bnb_fr = _f(d.get("fundingRate")) * 100
        ts_bucket = _mx._floor_ts(ts_raw, "8h")
        okx_fr_val = okx_by_ts.get(ts_bucket)
        okx_str = f"{okx_fr_val*100:+.4f}%" if okx_fr_val is not None else "—"
        bybit_fr_val = bybit_by_ts.get(ts_bucket)
        bybit_str = f"{bybit_fr_val*100:+.4f}%" if bybit_fr_val is not None else "—"
        mark = _f(d.get("markPrice"))
        table += (
            f" {t:>16} | {bnb_fr:>+8.4f}% | {okx_str:>9} | {bybit_str:>9} | "
            f"{_price(mark):>13}\n"
        )
    table += "```\n"

    bnb_rates = [_f(d.get("fundingRate")) * 100 for d in items]
    avg_bnb = sum(bnb_rates) / len(bnb_rates) if bnb_rates else 0
    matched_okx = [
        okx_by_ts[_mx._floor_ts(d.get("fundingTime", 0), "8h")]
        for d in items
        if _mx._floor_ts(d.get("fundingTime", 0), "8h") in okx_by_ts
    ]
    avg_okx = (sum(matched_okx) / len(matched_okx) * 100) if matched_okx else 0
    matched_bybit = [
        bybit_by_ts[_mx._floor_ts(d.get("fundingTime", 0), "8h")]
        for d in items
        if _mx._floor_ts(d.get("fundingTime", 0), "8h") in bybit_by_ts
    ]
    avg_bybit = (sum(matched_bybit) / len(matched_bybit) * 100) if matched_bybit else 0
    table += (
        f"\n**Summary:** Avg Binance {avg_bnb:+.4f}% | "
        f"Avg OKX {avg_okx:+.4f}% ({len(matched_okx)}/{len(items)}) | "
        f"Avg Bybit {avg_bybit:+.4f}% ({len(matched_bybit)}/{len(items)})"
    )

    status = "success" if not failed_current and (okx_by_ts or bybit_by_ts) else "partial"
    return make_envelope(
        status, _multi_source_tag(ok_current or ["binance"]),
        hdr + preamble + table,
        warnings=_build_warnings(ok_current, failed_current, total_possible=4),
    )


# ═══════════════════════════════════════════════════════════════════════════════
# TOOL 11: binance_futures_open_interest
# ═══════════════════════════════════════════════════════════════════════════════

async def binance_futures_open_interest(symbol: str) -> str:
    """Get current Open Interest aggregated across Binance + OKX.

    Returns total OI (USD), plus per-exchange breakdown.
    Aggregation: SUM across exchanges (cross-exchange perp exposure).

    Args:
        symbol: Futures pair (e.g. SOLUSDT, BTCUSDT)
    """
    sym_upper = symbol.upper()
    result = await _mx.aggregate_oi_current(sym_upper)
    title = f"Open Interest — {sym_upper}"
    ok = result["exchanges_ok"]
    failed = result["exchanges_failed"]

    hdr = _multi_header(title, ok, failed)
    e = _multi_err_check(result, hdr)
    if e:
        return e

    agg = result["aggregated"]
    by = result["by_exchange"]

    lines = [
        f"- **Total OI (aggregated):** {_dollar(agg['total_oi_usd'])}",
        "",
        "**By exchange:**",
    ]
    for ex in ("binance", "okx", "bybit", "hyperliquid"):
        d = by.get(ex)
        if not d:
            continue
        pct = (d["oi_usd"] / agg["total_oi_usd"] * 100) if agg["total_oi_usd"] else 0
        lines.append(
            f"- **{ex.capitalize()}:** {_dollar(d['oi_usd'])} ({pct:.1f}%) — "
            f"{d['oi_contracts']:,.3f} contracts"
        )
    if failed:
        lines.append("")
        lines.append(f"⚠️ **Failed sources:** {', '.join(failed.keys())}")

    status = "success" if not failed else "partial"
    return make_envelope(
        status, _multi_source_tag(ok), hdr + "\n".join(lines) + "\n",
        warnings=_build_warnings(ok, failed, total_possible=4),
    )


# ═══════════════════════════════════════════════════════════════════════════════
# TOOL 12: binance_futures_oi_history
# ═══════════════════════════════════════════════════════════════════════════════

async def binance_futures_oi_history(symbol: str, period: str = "5m", limit: int = 100) -> str:
    """Get OI history aggregated across Binance + OKX.

    Binance: per-symbol (BTCUSDT perp only).
    OKX rubik: ccy-aggregated (all BTC contracts) — broader scope but useful for total.
    Aggregation: SUM per timestamp bucket.

    Args:
        symbol: Futures pair (e.g. SOLUSDT, BTCUSDT)
        period: 5m, 15m, 30m, 1h, 2h, 4h, 6h, 12h, 1d
        limit: Number of records (default 100, max 500)
    """
    sym_upper = symbol.upper()
    result = await _mx.aggregate_oi_history(sym_upper, period, min(limit, 500))
    title = f"OI History — {sym_upper} {period}"
    ok = result["exchanges_ok"]
    failed = result["exchanges_failed"]

    hdr = _multi_header(title, ok, failed)
    e = _multi_err_check(result, hdr)
    if e:
        return e

    rows = result["aggregated"]
    if len(rows) > 30:
        total = len(rows)
        rows = rows[-30:]
        hdr += f"*(showing last 30 of {total})*\n\n"

    table = "```\n"
    table += f" {'Time':>16} | {'Total':>10} | {'Binance':>10} | {'OKX':>10} | {'Bybit':>10} | src\n"
    table += f" {'─' * 16} | {'─' * 10} | {'─' * 10} | {'─' * 10} | {'─' * 10} | ───\n"
    for r in rows:
        t = _dt(r["ts_ms"])
        # Mark partial bucket: suppress Total (misleading SUM) + tag "partial"
        if r.get("partial"):
            total_display = f"(partial)"
        else:
            total_display = _dollar(r["total_oi_usd"])
        bnb = _dollar(r["binance_oi_usd"]) if r["binance_oi_usd"] else "—"
        okx = _dollar(r["okx_oi_usd"]) if r["okx_oi_usd"] else "—"
        bybit = _dollar(r.get("bybit_oi_usd", 0)) if r.get("bybit_oi_usd") else "—"
        src_cnt = r.get("source_count", 0)
        table += f" {t:>16} | {total_display:>10} | {bnb:>10} | {okx:>10} | {bybit:>10} | {src_cnt}/3\n"
    table += "```\n"

    # Summary excludes partial buckets — avoids contaminated stats
    complete = [r for r in rows if not r.get("partial") and r.get("source_count", 0) >= 2]
    if len(complete) >= 2:
        first = complete[0]["total_oi_usd"]
        last = complete[-1]["total_oi_usd"]
        change = last - first
        pct = (change / first * 100) if first else 0
        table += (
            f"\n**Summary** (excluding partial buckets): {_dollar(first)} → {_dollar(last)} "
            f"(change: {_dollar(change)}, {pct:+.2f}%)"
        )

    # Scope disclaimer — prevent cross-tool comparison misread
    notes = result.get("notes", {})
    if notes.get("okx_scope"):
        table += (
            f"\n**Scope note:** OKX = {notes['okx_scope']}; Binance/Bybit per-symbol. "
            f"HL excluded (no OI history endpoint). "
            f"Totals here NOT directly comparable to `binance_futures_open_interest` (4-way current snapshot)."
        )

    if failed:
        table += f"\n⚠️ **Failed:** {', '.join(failed.keys())}"

    status = "success" if not failed else "partial"
    return make_envelope(
        status, _multi_source_tag(ok), hdr + table,
        warnings=_build_warnings(ok, failed, total_possible=3),
    )


# ═══════════════════════════════════════════════════════════════════════════════
# TOOL 13 & 14: Long/Short Ratio (shared formatter)
# ═══════════════════════════════════════════════════════════════════════════════

def _fmt_ls(data: Any, title: str) -> str:
    """Shared formatter for L/S ratio tools (global + top trader)."""
    hdr = _header(title)
    e = _err_check(data, hdr)
    if e:
        return e

    items = data if isinstance(data, list) else [data]
    if len(items) > 30:
        total = len(items)
        items = items[-30:]
        hdr += f"*(showing last 30 of {total})*\n\n"

    table = "```\n"
    table += f" {'Time':>16} | {'Long %':>8} | {'Short %':>8} | {'Ratio':>8}\n"
    table += f" {'─' * 16} | {'─' * 8} | {'─' * 8} | {'─' * 8}\n"
    for d in items:
        t = _dt(d.get("timestamp", 0))
        long_pct = _f(d.get("longAccount")) * 100
        short_pct = _f(d.get("shortAccount")) * 100
        ratio = _f(d.get("longShortRatio"))
        table += f" {t:>16} | {long_pct:>7.1f}% | {short_pct:>7.1f}% | {ratio:>8.3f}\n"
    table += "```\n"

    ratios = [_f(d.get("longShortRatio")) for d in items]
    avg_r = sum(ratios) / len(ratios) if ratios else 0
    if len(items) >= 2:
        shift = ratios[-1] - ratios[0]
        direction = "more long" if shift > 0 else "more short"
        table += f"\n**Summary:** Avg ratio: {avg_r:.3f}, range {min(ratios):.3f}-{max(ratios):.3f} | Shift: {shift:+.3f} ({direction})"
    else:
        table += f"\n**Summary:** Ratio: {avg_r:.3f}"

    return _ok(hdr + table)


async def binance_futures_long_short_ratio(symbol: str, period: str = "5m", limit: int = 100) -> str:
    """Get global long/short ACCOUNT ratio aggregated across Binance + OKX.

    Ratio > 1 = more accounts are long.
    Aggregation: simple average of per-exchange ratios (equal weight).

    Args:
        symbol: Futures pair (e.g. SOLUSDT)
        period: 5m, 15m, 30m, 1h, 2h, 4h, 6h, 12h, 1d
        limit: Number of records (default 100, max 500)
    """
    sym_upper = symbol.upper()
    result = await _mx.aggregate_ls_ratio(sym_upper, period, min(limit, 500))
    title = f"Global L/S Account Ratio (agg) — {sym_upper} {period}"
    ok = result["exchanges_ok"]
    failed = result["exchanges_failed"]

    hdr = _multi_header(title, ok, failed)
    e = _multi_err_check(result, hdr)
    if e:
        return e

    rows = result["aggregated"]
    if len(rows) > 30:
        total = len(rows)
        rows = rows[-30:]
        hdr += f"*(showing last 30 of {total})*\n\n"

    table = "```\n"
    table += f" {'Time':>16} | {'Avg':>7} | {'Binance':>7} | {'OKX':>7} | {'Bybit':>7} | src\n"
    table += f" {'─' * 16} | {'─' * 7} | {'─' * 7} | {'─' * 7} | {'─' * 7} | ───\n"
    for r in rows:
        t = _dt(r["ts_ms"])
        # Partial bucket: Avg is noisy (only 1 source), suppress display
        avg_display = "(partial)" if r.get("partial") else f"{r['avg_ratio']:.3f}"
        bnb = f"{r['binance_ratio']:.3f}" if r["binance_ratio"] else "—"
        okx = f"{r['okx_ratio']:.3f}" if r["okx_ratio"] else "—"
        bybit = f"{r.get('bybit_ratio'):.3f}" if r.get("bybit_ratio") else "—"
        src_cnt = r.get("source_count", 0)
        table += f" {t:>16} | {avg_display:>7} | {bnb:>7} | {okx:>7} | {bybit:>7} | {src_cnt}/3\n"
    table += "```\n"

    # Summary stats exclude partial buckets to avoid contamination from
    # stale tail bucket (e.g., only Bybit has reported while Binance/OKX lag).
    complete = [r for r in rows if not r.get("partial") and r.get("avg_ratio", 0) > 0]
    ratios = [r["avg_ratio"] for r in complete]
    if len(ratios) >= 2:
        shift = ratios[-1] - ratios[0]
        direction = "more long" if shift > 0 else "more short" if shift < 0 else "flat"
        avg_all = sum(ratios) / len(ratios)
        table += (
            f"\n**Summary** (excluding partial buckets): Avg {avg_all:.3f}, "
            f"range {min(ratios):.3f}-{max(ratios):.3f} | "
            f"Shift: {shift:+.3f} ({direction})"
        )
    elif ratios:
        table += f"\n**Summary:** Ratio: {ratios[0]:.3f} (single complete bucket)"
    else:
        table += "\n**Summary:** No complete buckets yet — all rows partial, wait for next flush"

    if failed:
        table += f"\n⚠️ **Failed:** {', '.join(failed.keys())}"

    status = "success" if not failed else "partial"
    return make_envelope(
        status, _multi_source_tag(ok), hdr + table,
        warnings=_build_warnings(ok, failed, total_possible=3),
    )


# ═══════════════════════════════════════════════════════════════════════════════
# TOOL 14: binance_futures_top_ls_ratio
# ═══════════════════════════════════════════════════════════════════════════════

async def binance_futures_top_ls_ratio(symbol: str, period: str = "5m", limit: int = 100) -> str:
    """Get top trader long/short POSITION ratio from Binance.

    More insightful than global ratio — positions from top traders.

    Args:
        symbol: Futures pair (e.g. SOLUSDT)
        period: 5m, 15m, 30m, 1h, 2h, 4h, 6h, 12h, 1d
        limit: Number of records (default 100, max 500)
    """
    params = {"symbol": symbol.upper(), "period": period, "limit": min(limit, 500)}
    data = await binance_futures_request("/futures/data/topLongShortPositionRatio", params, 1)
    return _fmt_ls(data, f"Top Trader L/S Position Ratio — {symbol.upper()} {period}")


# ═══════════════════════════════════════════════════════════════════════════════
# TOOL 15: binance_futures_taker_volume
# ═══════════════════════════════════════════════════════════════════════════════

async def binance_futures_taker_volume(symbol: str, period: str = "5m", limit: int = 100) -> str:
    """Get taker buy/sell volume aggregated across Binance + OKX.

    Aggregation: SUM buy / SUM sell per timestamp bucket (both converted to USD).
    Cross-check with CoinGlass FutCVD.

    Args:
        symbol: Futures pair (e.g. SOLUSDT, BTCUSDT)
        period: 5m, 15m, 30m, 1h, 2h, 4h, 6h, 12h, 1d
        limit: Number of records (default 100, max 500)
    """
    sym_upper = symbol.upper()
    result = await _mx.aggregate_taker_volume(sym_upper, period, min(limit, 500))
    title = f"Taker Buy/Sell (agg) — {sym_upper} {period}"
    ok = result["exchanges_ok"]
    failed = result["exchanges_failed"]

    hdr = _multi_header(title, ok, failed)
    e = _multi_err_check(result, hdr)
    if e:
        return e

    rows = result["aggregated"]
    if len(rows) > 30:
        total = len(rows)
        rows = rows[-30:]
        hdr += f"*(showing last 30 of {total})*\n\n"

    table = "```\n"
    table += f" {'Time':>16} | {'Buy $':>10} | {'Sell $':>10} | {'Net':>10} | {'Ratio':>6} | Side | src\n"
    table += f" {'─' * 16} | {'─' * 10} | {'─' * 10} | {'─' * 10} | {'─' * 6} | ──── | ───\n"
    for r in rows:
        t = _dt(r["ts_ms"])
        buy = r["total_buy_usd"]
        sell = r["total_sell_usd"]
        net = r["net_usd"]
        ratio = r["buy_sell_ratio"]
        side = "BUY" if ratio >= 1 else "SELL"
        net_str = f"+{_dollar(net)}" if net >= 0 else _dollar(net)
        src_cnt = r.get("source_count", 0)
        # Mark partial bucket (SUM is incomplete)
        tag = "*" if r.get("partial") else " "
        table += (
            f" {t:>16} | {_dollar(buy):>10} | {_dollar(sell):>10} | {net_str:>10} | "
            f"{ratio:>6.3f} | {side:>4}{tag}| {src_cnt}/2\n"
        )
    table += "```\n"
    if any(r.get("partial") for r in rows):
        table += "*= partial bucket (some exchanges haven't reported yet — SUM understated)\n"

    # Exclude partial buckets from aggregate summary to avoid understated totals
    complete = [r for r in rows if not r.get("partial")]
    buy_count = sum(1 for r in complete if r["buy_sell_ratio"] >= 1)
    total_buy = sum(r["total_buy_usd"] for r in complete)
    total_sell = sum(r["total_sell_usd"] for r in complete)
    total_net = total_buy - total_sell
    net_str = f"+{_dollar(total_net)}" if total_net >= 0 else _dollar(total_net)
    excl_note = f" (excluded {len(rows) - len(complete)} partial)" if len(complete) < len(rows) else ""
    table += (
        f"\n**Summary:** {buy_count}/{len(complete)} buy-dominant{excl_note} | "
        f"Buy {_dollar(total_buy)} vs Sell {_dollar(total_sell)} | Net {net_str}"
    )

    if failed:
        table += f"\n⚠️ **Failed:** {', '.join(failed.keys())}"

    status = "success" if not failed else "partial"
    return make_envelope(
        status, _multi_source_tag(ok), hdr + table,
        warnings=_build_warnings(ok, failed, total_possible=2),
    )


# ═══════════════════════════════════════════════════════════════════════════════
# TOOL 16: binance_futures_klines
# ═══════════════════════════════════════════════════════════════════════════════

async def binance_futures_klines(symbol: str, interval: str = "5m", limit: int = 100) -> str:
    """Get futures OHLCV candlestick data from Binance.

    Includes taker buy volume — can derive aggressor side.

    Args:
        symbol: Futures pair (e.g. SOLUSDT)
        interval: 1m,3m,5m,15m,30m,1h,2h,4h,6h,8h,12h,1d,3d,1w,1M
        limit: Number of candles (default 100, max 1500)

    Response per candle: [openTime, O, H, L, C, volume, closeTime,
    quoteVolume, trades, takerBuyBaseVol, takerBuyQuoteVol, ignore]
    """
    if limit <= 100:
        weight = 1
    elif limit <= 500:
        weight = 2
    elif limit <= 1000:
        weight = 5
    else:
        weight = 10
        limit = min(limit, 1500)
    params = {"symbol": symbol.upper(), "interval": interval, "limit": limit}
    data = await binance_futures_request("/fapi/v1/klines", params, weight)
    title = f"Futures Klines — {symbol.upper()} {interval}"

    hdr = _header(title)
    e = _err_check(data, hdr)
    if e:
        return e

    items = data if isinstance(data, list) else []
    if not items:
        return make_envelope("failed", "binance", hdr + "**WARNING: Empty dataset.**")

    if len(items) > 30:
        total = len(items)
        items = items[-30:]
        hdr += f"*(showing last 30 of {total})*\n\n"

    table = "```\n"
    table += f" {'Time':>16} | {'Open':>13} | {'High':>13} | {'Low':>13} | {'Close':>13} | {'Volume':>10} | {'Trades':>6}\n"
    table += f" {'─' * 16} | {'─' * 13} | {'─' * 13} | {'─' * 13} | {'─' * 13} | {'─' * 10} | {'─' * 6}\n"
    for c in items:
        t = _dt(c[0])
        o, h, l, cl = _f(c[1]), _f(c[2]), _f(c[3]), _f(c[4])
        vol = _f(c[7])  # quoteVolume (USD)
        trades = int(c[8]) if len(c) > 8 else 0
        table += f" {t:>16} | {_price(o):>13} | {_price(h):>13} | {_price(l):>13} | {_price(cl):>13} | {_dollar(vol):>10} | {trades:>6}\n"
    table += "```\n"

    first_o = _f(items[0][1])
    last_c = _f(items[-1][4])
    high = max(_f(c[2]) for c in items)
    low = min(_f(c[3]) for c in items)
    pct = ((last_c - first_o) / first_o * 100) if first_o else 0
    total_vol = sum(_f(c[7]) for c in items)
    table += f"\n**Summary:** Range {_price(low)}-{_price(high)} | {_price(first_o)} → {_price(last_c)} ({pct:+.2f}%) | Total vol: {_dollar(total_vol)}"

    return _ok(hdr + table)


# ═══════════════════════════════════════════════════════════════════════════════
# TOOL 17: binance_futures_depth
# ═══════════════════════════════════════════════════════════════════════════════

async def binance_futures_depth(symbol: str, limit: int = 100) -> str:
    """Get futures order book (L2) from Binance.

    Cross-check with CoinGlass OBDelta — compare bid vs ask volume.

    Args:
        symbol: Futures pair (e.g. SOLUSDT)
        limit: 5, 10, 20, 50, 100, 500, 1000
    """
    weight_map = {5: 2, 10: 2, 20: 2, 50: 5, 100: 10, 500: 20, 1000: 40}
    valid_limits = sorted(weight_map.keys())
    # Snap to nearest valid limit
    limit = min(valid_limits, key=lambda x: abs(x - limit))
    weight = weight_map[limit]
    params = {"symbol": symbol.upper(), "limit": limit}
    data = await binance_futures_request("/fapi/v1/depth", params, weight)
    title = f"Futures Order Book — {symbol.upper()} (top {limit})"

    hdr = _header(title)
    e = _err_check(data, hdr)
    if e:
        return e

    if not isinstance(data, dict) or "bids" not in data:
        return make_envelope("failed", "binance", hdr + "**WARNING: Unexpected response format.**")

    bids = data.get("bids", [])
    asks = data.get("asks", [])

    total_bid_val = sum(_f(b[0]) * _f(b[1]) for b in bids)
    total_ask_val = sum(_f(a[0]) * _f(a[1]) for a in asks)

    top_n = min(15, len(bids), len(asks))

    table = "```\n"
    table += f" {'Bid Price':>13} | {'Bid Qty':>10} | {'Bid $':>10} | {'Ask Price':>13} | {'Ask Qty':>10} | {'Ask $':>10}\n"
    table += f" {'─' * 13} | {'─' * 10} | {'─' * 10} | {'─' * 13} | {'─' * 10} | {'─' * 10}\n"
    for i in range(top_n):
        bp, bq = _f(bids[i][0]), _f(bids[i][1])
        ap, aq = _f(asks[i][0]), _f(asks[i][1])
        table += f" {_price(bp):>13} | {bq:>10,.4f} | {_dollar(bp * bq):>10} | {_price(ap):>13} | {aq:>10,.4f} | {_dollar(ap * aq):>10}\n"
    table += "```\n"

    ratio = total_bid_val / total_ask_val if total_ask_val else 0
    dominant = "BIDS (buyers)" if ratio > 1 else "ASKS (sellers)"
    spread = _f(asks[0][0]) - _f(bids[0][0]) if bids and asks else 0
    spread_pct = (spread / _f(bids[0][0]) * 100) if bids and _f(bids[0][0]) else 0

    table += f"\n**Summary:** {len(bids)} bids ({_dollar(total_bid_val)}) vs {len(asks)} asks ({_dollar(total_ask_val)}) → **{dominant}** (ratio {ratio:.2f})"
    table += f"\n**Spread:** {_price(spread)} ({spread_pct:.4f}%)"

    return _ok(hdr + table)


# ═══════════════════════════════════════════════════════════════════════════════
# TOOL 18: binance_futures_ticker_24h
# ═══════════════════════════════════════════════════════════════════════════════

async def binance_futures_ticker_24h(symbol: str = "") -> str:
    """Get 24hr futures ticker statistics from Binance.

    Includes volume, priceChange, highPrice, lowPrice, lastPrice, weightedAvgPrice.

    Args:
        symbol: Futures pair. Leave empty for all.
    """
    params = {}
    weight = 40
    if symbol:
        params["symbol"] = symbol.upper()
        weight = 1
    data = await binance_futures_request("/fapi/v1/ticker/24hr", params, weight)
    label = f"Futures 24h Ticker — {symbol.upper()}" if symbol else "Futures 24h Tickers (all)"

    hdr = _header(label)
    e = _err_check(data, hdr)
    if e:
        return e

    items = data if isinstance(data, list) else [data]

    # Single symbol — detailed view
    if len(items) == 1:
        d = items[0]
        last = _f(d.get("lastPrice"))
        change = _f(d.get("priceChange"))
        change_pct = _f(d.get("priceChangePercent"))
        high = _f(d.get("highPrice"))
        low = _f(d.get("lowPrice"))
        vol = _f(d.get("quoteVolume"))
        vwap = _f(d.get("weightedAvgPrice"))
        trades = int(_f(d.get("count")))

        return _ok(hdr + "\n".join([
            f"- **Last Price:** {_price(last)}",
            f"- **24h Change:** {change_pct:+.2f}% ({_price(abs(change))})",
            f"- **24h High:** {_price(high)}",
            f"- **24h Low:** {_price(low)}",
            f"- **24h Volume:** {_dollar(vol)}",
            f"- **VWAP:** {_price(vwap)}",
            f"- **Trades:** {trades:,}",
        ]) + "\n")

    # Multi-ticker: sort by volume, show top 30
    if len(items) > 30:
        total = len(items)
        items.sort(key=lambda x: _f(x.get("quoteVolume")), reverse=True)
        items = items[:30]
        hdr += f"*(top 30 by volume of {total})*\n\n"

    table = "```\n"
    table += f" {'Symbol':<14} | {'Last Price':>13} | {'24h %':>8} | {'Volume':>12}\n"
    table += f" {'─' * 14} | {'─' * 13} | {'─' * 8} | {'─' * 12}\n"
    for d in items:
        sym = d.get("symbol", "")
        last = _f(d.get("lastPrice"))
        pct = _f(d.get("priceChangePercent"))
        vol = _f(d.get("quoteVolume"))
        table += f" {sym:<14} | {_price(last):>13} | {pct:>+7.2f}% | {_dollar(vol):>12}\n"
    table += "```\n"
    return _ok(hdr + table)


# ═══════════════════════════════════════════════════════════════════════════════
# TOOL 19: binance_futures_liquidation
# ═══════════════════════════════════════════════════════════════════════════════

async def binance_futures_liquidation(symbol: str = "", limit: int = 100) -> str:
    """Get recent forced liquidation orders from Binance.

    Cross-check with CoinGlass liquidation data.
    NOTE: This endpoint may require API key on some regions.

    Args:
        symbol: Futures pair. Leave empty for all.
        limit: Max 1000, default 100
    """
    params = {"limit": min(limit, 1000)}
    weight = 50
    if symbol:
        params["symbol"] = symbol.upper()
        weight = 20
    data = await binance_futures_request("/fapi/v1/forceOrders", params, weight)
    label = f"Forced Liquidations — {symbol.upper()}" if symbol else "Forced Liquidations (all)"

    hdr = _header(label)
    e = _err_check(data, hdr)
    if e:
        return e

    items = data if isinstance(data, list) else [data]
    if len(items) > 30:
        total = len(items)
        items = items[-30:]
        hdr += f"*(showing last 30 of {total})*\n\n"

    total_long_val = 0.0
    total_short_val = 0.0

    table = "```\n"
    table += f" {'Symbol':<14} | {'Price':>13} | {'Qty':>10} | {'Value':>10} | {'Side':>5} | Time\n"
    table += f" {'─' * 14} | {'─' * 13} | {'─' * 10} | {'─' * 10} | {'─' * 5} | ─────\n"
    for d in items:
        sym = d.get("symbol", "")
        price = _f(d.get("averagePrice") or d.get("price"))
        qty = _f(d.get("executedQty") or d.get("origQty"))
        val = price * qty
        side = d.get("side", "")
        # SELL side = long liquidated, BUY side = short liquidated
        liq_side = "LONG" if side == "SELL" else "SHORT"
        t = _dt(d.get("time", 0))

        if liq_side == "LONG":
            total_long_val += val
        else:
            total_short_val += val

        table += f" {sym:<14} | {_price(price):>13} | {qty:>10,.4f} | {_dollar(val):>10} | {liq_side:>5} | {t}\n"
    table += "```\n"

    table += f"\n**Summary:** LONG liq: {_dollar(total_long_val)} | SHORT liq: {_dollar(total_short_val)}"

    return _ok(hdr + table)


# ═══════════════════════════════════════════════════════════════════════════════
# TOOL: binance_futures_cvd (Cumulative Volume Delta — perp market)
# ═══════════════════════════════════════════════════════════════════════════════


async def binance_futures_cvd(symbol: str, interval: str = "5m", limit: int = 100) -> str:
    """Compute Futures CVD (Cumulative Volume Delta) from Binance perp klines.

    CVD = running sum of (takerBuy − takerSell) per candle, in USD (quote vol).
    Futures CVD = aggressive futures taker flow (perp orderflow).

    Use alongside binance_spot_cvd:
      - Both rising → strong buyer conviction across spot + perp
      - Spot CVD rising + Futures CVD falling → spot-led pump (stronger signal)
      - Futures CVD rising + Spot CVD falling → leveraged-only pump (fragile)

    Args:
        symbol: Futures pair (e.g. BTCUSDT, SOLUSDT)
        interval: 1m,3m,5m,15m,30m,1h,2h,4h,6h,8h,12h,1d,3d,1w,1M
        limit: Number of candles (default 100, max 1500)
    """
    from .binance_spot import _compute_cvd
    # Binance futures klines: max limit is 1500
    return await _compute_cvd(
        venue="futures",
        endpoint="/fapi/v1/klines",
        symbol=symbol,
        interval=interval,
        limit=min(limit, 1500),
    )
