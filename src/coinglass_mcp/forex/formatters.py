"""Markdown formatters for forex tool responses.

Matches the existing Binance/CoinGlass tool style — human-readable tables +
context headers, not raw JSON. Data values never modified; only presentation.
"""

from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Any

WIB = timezone(timedelta(hours=7))


# ─── Primitives ─────────────────────────────────────────────────────────────


def _pct(v: float | None, decimals: int = 1) -> str:
    if v is None:
        return "—"
    return f"{v:.{decimals}f}%"


def _int(v: int | None) -> str:
    if v is None:
        return "—"
    return f"{v:,}"


def _signed(v: int | float | None) -> str:
    if v is None:
        return "—"
    sign = "+" if v > 0 else ""
    if isinstance(v, float):
        return f"{sign}{v:,.2f}"
    return f"{sign}{v:,}"


def _usd(v: float | None) -> str:
    if v is None or v == 0:
        return "—"
    av = abs(v)
    sign = "-" if v < 0 else ""
    if av >= 1e9:
        return f"{sign}${av / 1e9:,.2f}B"
    if av >= 1e6:
        return f"{sign}${av / 1e6:,.2f}M"
    if av >= 1e3:
        return f"{sign}${av / 1e3:,.1f}K"
    return f"{sign}${av:,.2f}"


def _num(v: float | None, decimals: int = 1) -> str:
    """Raw number with K/M suffix but NO currency prefix (for lots/contracts)."""
    if v is None or v == 0:
        return "—"
    av = abs(v)
    sign = "-" if v < 0 else ""
    if av >= 1e9:
        return f"{sign}{av / 1e9:,.2f}B"
    if av >= 1e6:
        return f"{sign}{av / 1e6:,.2f}M"
    if av >= 1e3:
        return f"{sign}{av / 1e3:,.{decimals}f}K"
    return f"{sign}{av:,.{decimals}f}"


def _price(v: float | None) -> str:
    if v is None or v == 0:
        return "—"
    if v >= 100:
        return f"{v:,.2f}"
    if v >= 1:
        return f"{v:.4f}"
    return f"{v:.5f}"


def _wib_banner(label: str) -> str:
    ts = datetime.now(WIB).strftime("%Y-%m-%d %H:%M:%S WIB")
    return f"## {label}\n*Fetched: {ts}*\n\n"


# ─── MyFXBook formatters ────────────────────────────────────────────────────


def fmt_sentiment(row: dict) -> str:
    """Single-symbol retail sentiment block."""
    sym = row.get("symbol", "?")
    lp, sp = row.get("long_pct", 0), row.get("short_pct", 0)
    bias = "LONG-heavy" if lp > sp else "SHORT-heavy" if sp > lp else "balanced"
    lean = max(lp, sp)
    tag = "🔥 EXTREME" if lean >= 70 else "⚠ crowded" if lean >= 60 else "neutral"

    out = _wib_banner(f"MyFXBook Retail Sentiment — {sym}")
    out += f"**Retail: {bias}** ({tag})\n\n"
    out += "```\n"
    out += f" Side       | Pct     | Positions | Volume (lot) | Avg Entry\n"
    out += f" ─────────  | ─────── | ───────── | ──────────── | ──────────\n"
    out += (f" LONG       | {_pct(lp):>7} | {_int(row.get('long_positions')):>9} "
            f"| {_num(row.get('long_volume_usd')):>12} | {_price(row.get('avg_long_entry')):>10}\n")
    out += (f" SHORT      | {_pct(sp):>7} | {_int(row.get('short_positions')):>9} "
            f"| {_num(row.get('short_volume_usd')):>12} | {_price(row.get('avg_short_entry')):>10}\n")
    out += "```\n\n"

    out += "**Contrarian read:**\n"
    if lp >= 70:
        out += f"- Retail heavily LONG → bias SHORT. Stop-hunt zone: {_price(row.get('avg_long_entry'))}\n"
    elif sp >= 70:
        out += f"- Retail heavily SHORT → bias LONG. Stop-hunt zone: {_price(row.get('avg_short_entry'))}\n"
    else:
        out += "- Not crowded enough for a contrarian signal\n"
    return out


def fmt_sentiment_all(rows: list[dict], limit: int = 25) -> str:
    out = _wib_banner(f"MyFXBook Community Outlook — All Symbols ({len(rows)})")
    out += "*Sorted by most-crowded positioning (max of long%/short%).*\n\n"
    out += "```\n"
    out += f" Symbol      | Long%   | Short%  | Crowded | Positions (L/S)\n"
    out += f" ──────────  | ─────── | ─────── | ─────── | ─────────────────\n"
    shown = rows[:limit]
    for r in shown:
        lp, sp = r.get("long_pct", 0), r.get("short_pct", 0)
        lean = max(lp, sp)
        tag = "🔥" if lean >= 70 else "⚠" if lean >= 60 else " "
        out += (f" {r.get('symbol', '?'):<10} | {_pct(lp):>7} | {_pct(sp):>7} "
                f"| {_pct(lean):>5} {tag} | {_int(r.get('long_positions'))}/{_int(r.get('short_positions'))}\n")
    out += "```\n"
    if len(rows) > limit:
        out += f"\n*({len(rows) - limit} more symbols omitted.)*\n"
    return out


def fmt_extreme_flags(flagged: list[dict], threshold: int, scanned: int) -> str:
    out = _wib_banner(f"MyFXBook Extreme Scanner — threshold ≥ {threshold}%")
    out += f"*Scanned {scanned} symbols, flagged {len(flagged)}.*\n\n"
    if not flagged:
        out += "**No crowded positions above threshold.** Retail relatively balanced.\n"
        return out
    out += "```\n"
    out += f" Symbol   | Crowded    | Pct     | Bias        | Stop-hunt zone\n"
    out += f" ───────  | ────────── | ─────── | ─────────── | ───────────────\n"
    for r in flagged:
        out += (f" {r.get('symbol', '?'):<7} | {r.get('crowded_side', ''):<10} "
                f"| {_pct(r.get('crowded_pct')):>7} | Contrarian {r.get('contrarian_bias', ''):<4} "
                f"| {_price(r.get('stop_hunt_zone'))}\n")
    out += "```\n\n"
    out += "**How to read:** Retail heavily on one side → contrarian bias = opposite direction. "
    out += "Stop-hunt zone = avg retail entry (likely liquidation cluster).\n"
    return out


def fmt_sentiment_change(row: dict) -> str:
    sym = row.get("symbol", "?")
    out = _wib_banner(f"MyFXBook Sentiment Change — {sym}")

    cur_l, cur_s = row.get("current_long_pct", 0), row.get("current_short_pct", 0)
    past_l, past_s = row.get("past_long_pct"), row.get("past_short_pct")
    dL, dS = row.get("delta_long_pct"), row.get("delta_short_pct")
    lookback = row.get("lookback_hours", row.get("requested_lookback_hours", "?"))

    if past_l is None:
        out += f"**Insufficient history** for {lookback}h lookback. Background poller "
        out += "needs time to build up snapshots. Try again in a few hours.\n\n"
        out += f"**Current sentiment:** Long {_pct(cur_l)} / Short {_pct(cur_s)}\n"
        return out

    out += "```\n"
    out += f" Side    | Now      | {lookback}h ago   | Delta\n"
    out += f" ──────  | ──────── | ──────── | ──────────\n"
    out += f" LONG    | {_pct(cur_l):>8} | {_pct(past_l):>8} | {_signed(dL):>10}\n"
    out += f" SHORT   | {_pct(cur_s):>8} | {_pct(past_s):>8} | {_signed(dS):>10}\n"
    out += "```\n\n"
    out += f"**Momentum:** {row.get('momentum', 'FLAT')}\n"
    if age := row.get("past_snapshot_age_sec"):
        out += f"*Past snapshot age: {age // 60}min*\n"
    return out


def fmt_historical_query(payload: dict) -> str:
    sym, days = payload.get("symbol", "?"), payload.get("days", 0)
    snaps = payload.get("snapshots", [])
    out = _wib_banner(f"MyFXBook Historical — {sym} (last {days}d)")
    if not snaps:
        out += "**No snapshots in DB yet** for this symbol. Background poller is running "
        out += f"hourly — data accumulates over time. Call `myfxbook_sentiment({sym})` "
        out += "a few times to seed history.\n"
        return out
    out += f"*{len(snaps)} snapshot(s) found.*\n\n```\n"
    out += f" Time (WIB)          | Long%   | Short%  | Pos (L/S)\n"
    out += f" ──────────────────  | ─────── | ─────── | ─────────────\n"
    for s in snaps[-30:]:  # last 30 rows max to keep output compact
        ts = datetime.fromtimestamp(s["ts"], tz=WIB).strftime("%Y-%m-%d %H:%M")
        out += (f" {ts:<19} | {_pct(s.get('long_pct')):>7} | {_pct(s.get('short_pct')):>7} "
                f"| {_int(s.get('long_positions'))}/{_int(s.get('short_positions'))}\n")
    out += "```\n"
    if len(snaps) > 30:
        out += f"\n*(Showing last 30 of {len(snaps)} snapshots.)*\n"
    return out


# ─── CFTC COT formatters ────────────────────────────────────────────────────


def fmt_cot_snapshot(row: dict) -> str:
    sym = row.get("symbol", "?")
    week = row.get("week_date", "?")
    out = _wib_banner(f"CFTC COT — {sym} ({week})")

    ncl, ncs = row.get("noncomm_long"), row.get("noncomm_short")
    cl, cs = row.get("comm_long"), row.get("comm_short")
    net = row.get("net_position")
    oi = row.get("open_interest")

    inst_bias = "LONG" if (net or 0) > 0 else "SHORT" if (net or 0) < 0 else "NEUTRAL"
    out += f"**Institutional bias (Non-Comm net): {inst_bias}**\n\n"

    out += "```\n"
    out += f" Category        | Long     | Short    | Net\n"
    out += f" ──────────────  | ──────── | ──────── | ──────────\n"
    nc_net = (ncl or 0) - (ncs or 0) if ncl is not None and ncs is not None else None
    c_net = (cl or 0) - (cs or 0) if cl is not None and cs is not None else None
    out += f" Non-Comm (spec) | {_int(ncl):>8} | {_int(ncs):>8} | {_signed(nc_net):>10}\n"
    out += f" Commercial      | {_int(cl):>8} | {_int(cs):>8} | {_signed(c_net):>10}\n"
    out += "```\n\n"
    out += f"**Open Interest:** {_int(oi)} contracts\n"
    out += f"**Contract:** {row.get('contract_name', '—')}\n\n"
    out += "*Non-Comm (speculators / hedge funds) is the 'smart money' group to watch. "
    out += "Commercials are hedgers and usually on the opposite side.*\n"
    return out


def fmt_cot_scanner(payload: dict) -> str:
    threshold = payload.get("threshold", 85)
    flagged = payload.get("results", [])
    out = _wib_banner(f"CFTC COT Extreme Scanner — percentile ≥ {threshold}")
    if not flagged:
        out += "**No symbols at extreme positioning** (or insufficient history).\n"
        out += "*The background poller needs ≥10 weeks of history per symbol to flag extremes.*\n"
        return out
    out += f"*{len(flagged)} symbol(s) at extremes.*\n\n```\n"
    out += f" Symbol    | Net Pos     | Percentile | Side\n"
    out += f" ───────── | ─────────── | ────────── | ───────────\n"
    for f in flagged:
        out += (f" {f.get('symbol', '?'):<9} | {_signed(f.get('current_net')):>11} "
                f"| {_pct(f.get('percentile')):>8}   | {f.get('side', '')}\n")
    out += "```\n"
    return out


def fmt_cot_history(payload: dict) -> str:
    sym = payload.get("symbol", "?")
    weeks = payload.get("weeks_requested", 0)
    history = payload.get("history", [])
    out = _wib_banner(f"CFTC COT Historical — {sym} (last {weeks}w requested)")
    if not history:
        out += f"**No history in DB yet.** Run `cftc_cot_snapshot({sym})` a few times "
        out += "or wait for weekly poller (Fridays).\n"
        return out
    out += f"*{len(history)} weekly record(s).*\n\n```\n"
    out += f" Week        | NC Long  | NC Short | NC Net\n"
    out += f" ──────────  | ──────── | ──────── | ──────────\n"
    for h in history:
        out += (f" {h.get('week_date', '?'):<10} | {_int(h.get('noncomm_long')):>8} "
                f"| {_int(h.get('noncomm_short')):>8} | {_signed(h.get('net_position')):>10}\n")
    out += "```\n"
    return out


def fmt_divergence(cot: dict, retail: dict, diverge: bool, symbol: str) -> str:
    out = _wib_banner(f"CFTC vs MyFXBook Divergence — {symbol}")
    if diverge:
        out += f"## 🎯 HIGH CONVICTION — institutions and retail disagree\n\n"
    else:
        out += "## ⚖ AGREEMENT — no contrarian edge\n\n"

    cot_dir = cot.get("direction", "?")
    retail_dir = retail.get("direction", "?")

    out += "```\n"
    out += f" Source                  | Direction | Detail\n"
    out += f" ──────────────────────  | ───────── | ─────────────────────────────\n"
    net = cot.get("net_position")
    out += (f" CFTC Non-Comm ({cot.get('week_date', '?')}) | {cot_dir:<9} "
            f"| Net {_signed(net)} contracts\n")
    out += (f" MyFXBook retail          | {retail_dir:<9} "
            f"| L {_pct(retail.get('long_pct'))} / S {_pct(retail.get('short_pct'))}\n")
    out += "```\n\n"
    if diverge:
        out += (f"**Signal:** Institutions {cot_dir}, retail {retail_dir}. "
                "Classic smart-money-vs-retail split. Favor the institutional side, "
                "time entries on retail capitulation.\n")
    else:
        out += "**Signal:** Both sides aligned — no contrarian edge here. "
        out += "Wait for divergence or rely on other setups.\n"
    return out
