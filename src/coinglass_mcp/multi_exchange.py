"""Multi-exchange aggregator: Binance + OKX.

Fetches the same market data from multiple exchanges in parallel, then
produces an aggregated view (SUM / weighted-avg) plus per-exchange breakdown.

Aggregation strategy by metric type:
  - Open Interest      → SUM (cross-exchange total exposure)
  - Taker volume       → SUM buy / SUM sell
  - Funding rate       → WEIGHTED AVG by OI (crowd-weighted crowding)
  - L/S ratio          → WEIGHTED AVG by OI
  - Mark price         → WEIGHTED AVG by 24h volume
  - Funding history    → side-by-side (no aggregate — timelines differ)

Each function returns a dict:
  {
    "status":          "success" | "partial" | "failed",
    "exchanges_ok":    ["binance", "okx"],
    "exchanges_failed": {"okx": "error msg"},
    "aggregated":      <method-specific>,
    "by_exchange":     {"binance": <...>, "okx": <...>},
  }

Timestamp alignment: we align by flooring to the period bucket
(5m→300s, 1h→3600s). OKX and Binance both snap to bucket boundaries,
so aligned series generally match 1:1 within ±1 bucket.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from . import okx_client
from . import bybit_client
from .binance_client import binance_futures_request

logger = logging.getLogger("multi-exchange")


# ─── Period → seconds ─────────────────────────────────────────────────────────

_PERIOD_SECONDS = {
    "5m": 300, "15m": 900, "30m": 1800,
    "1h": 3600, "2h": 7200, "4h": 14400,
    "6h": 21600, "8h": 28800, "12h": 43200,
    "1d": 86400, "3d": 259200, "1w": 604800,
}


def _floor_ts(ts_ms: int, period: str) -> int:
    """Floor a ms timestamp to the period bucket."""
    secs = _PERIOD_SECONDS.get(period.lower(), 300)
    return (int(ts_ms) // (secs * 1000)) * (secs * 1000)


def _f(v: Any) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _is_err(data: Any) -> bool:
    return isinstance(data, dict) and "error" in data


def _err_msg(data: Any) -> str:
    if isinstance(data, dict):
        return str(data.get("error", "unknown"))
    return "unknown"


def _status(ok: list[str], failed: dict[str, str]) -> str:
    if not ok and failed:
        return "failed"
    if ok and failed:
        return "partial"
    return "success"


# ─── 1. Open Interest — SNAPSHOT ──────────────────────────────────────────────


async def aggregate_oi_current(symbol: str) -> dict:
    """Fetch current OI from Binance + OKX + Bybit, return SUM and per-exchange.

    Binance returns OI in contracts only (enrich with mark price).
    OKX returns oi, oiCcy, oiUsd directly.
    Bybit ticker returns openInterest + openInterestValue (USD) directly.
    """
    bnb_sym = symbol.upper()
    okx_sym = okx_client.to_okx_swap(bnb_sym)

    # Parallel fetch from all 3 exchanges
    bnb_oi, bnb_mark, okx_oi, bybit_tkr = await asyncio.gather(
        binance_futures_request("/fapi/v1/openInterest", {"symbol": bnb_sym}, 1),
        binance_futures_request("/fapi/v1/premiumIndex", {"symbol": bnb_sym}, 1),
        okx_client.okx_request("/api/v5/public/open-interest",
                               {"instType": "SWAP", "instId": okx_sym}),
        bybit_client.bybit_request("/v5/market/tickers",
                                   {"category": "linear", "symbol": bnb_sym}),
        return_exceptions=True,
    )

    ok: list[str] = []
    failed: dict[str, str] = {}
    by_exchange: dict[str, dict] = {}

    # Binance
    if isinstance(bnb_oi, Exception):
        failed["binance"] = str(bnb_oi)
    elif _is_err(bnb_oi):
        failed["binance"] = _err_msg(bnb_oi)
    else:
        oi_contracts = _f(bnb_oi.get("openInterest"))
        mark_px = 0.0
        if not isinstance(bnb_mark, Exception) and not _is_err(bnb_mark):
            mark_px = _f(bnb_mark.get("markPrice"))
        by_exchange["binance"] = {
            "oi_contracts": oi_contracts,
            "oi_usd": oi_contracts * mark_px,
            "mark_price": mark_px,
            "ts_ms": int(bnb_oi.get("time", 0)),
        }
        ok.append("binance")

    # OKX
    if isinstance(okx_oi, Exception):
        failed["okx"] = str(okx_oi)
    elif _is_err(okx_oi):
        failed["okx"] = _err_msg(okx_oi)
    elif isinstance(okx_oi, list) and okx_oi:
        d = okx_oi[0]
        by_exchange["okx"] = {
            "oi_contracts": _f(d.get("oi")),
            "oi_ccy": _f(d.get("oiCcy")),
            "oi_usd": _f(d.get("oiUsd")),
            "ts_ms": int(d.get("ts", 0)),
        }
        ok.append("okx")
    else:
        failed["okx"] = "empty response"

    # Bybit
    if isinstance(bybit_tkr, Exception):
        failed["bybit"] = str(bybit_tkr)
    elif _is_err(bybit_tkr):
        failed["bybit"] = _err_msg(bybit_tkr)
    elif isinstance(bybit_tkr, dict) and bybit_tkr.get("list"):
        d = bybit_tkr["list"][0]
        by_exchange["bybit"] = {
            "oi_contracts": _f(d.get("openInterest")),
            "oi_usd": _f(d.get("openInterestValue")),
            "mark_price": _f(d.get("markPrice")),
            "ts_ms": 0,
        }
        ok.append("bybit")
    else:
        failed["bybit"] = "empty response"

    total_oi_usd = sum(by_exchange[e]["oi_usd"] for e in ok)

    return {
        "status": _status(ok, failed),
        "exchanges_ok": ok,
        "exchanges_failed": failed,
        "aggregated": {"total_oi_usd": total_oi_usd},
        "by_exchange": by_exchange,
    }


# ─── 2. Open Interest — HISTORY ───────────────────────────────────────────────


async def aggregate_oi_history(symbol: str, period: str, limit: int = 100) -> dict:
    """Fetch OI history from Binance + OKX + Bybit, align by period bucket, SUM.

    Binance: /futures/data/openInterestHist → [{timestamp, sumOpenInterest, sumOpenInterestValue}]
    OKX rubik: /api/v5/rubik/stat/contracts/open-interest-volume → [[ts, oi_usd, vol_usd]]
      NOTE: OKX rubik is ccy-aggregated (all BTC contracts), not per-instId.
    Bybit: /v5/market/open-interest → [{openInterest, timestamp}] (contracts only,
      multiplied by Binance mark price for USD; Bybit has no USD in this endpoint).
    """
    bnb_sym = symbol.upper()
    ccy = okx_client.to_okx_ccy(bnb_sym)
    okx_period = okx_client.to_okx_rubik_period(period)
    bybit_interval = bybit_client.to_bybit_interval(period)

    bnb_task = binance_futures_request(
        "/futures/data/openInterestHist",
        {"symbol": bnb_sym, "period": period, "limit": min(limit, 500)},
        1,
    )
    # Need Binance mark price to USD-ify Bybit's contract-only OI response
    mark_task = binance_futures_request("/fapi/v1/premiumIndex", {"symbol": bnb_sym}, 1)
    if okx_period:
        okx_task = okx_client.okx_request(
            "/api/v5/rubik/stat/contracts/open-interest-volume",
            {"ccy": ccy, "period": okx_period},
        )
    else:
        okx_task = asyncio.sleep(0, result={"error": f"OKX period {period} unsupported"})
    if bybit_interval:
        bybit_task = bybit_client.bybit_request(
            "/v5/market/open-interest",
            {"category": "linear", "symbol": bnb_sym,
             "intervalTime": bybit_interval, "limit": min(limit, 200)},
        )
    else:
        bybit_task = asyncio.sleep(0, result={"error": f"Bybit interval {period} unsupported"})

    bnb_data, mark_data, okx_data, bybit_data = await asyncio.gather(
        bnb_task, mark_task, okx_task, bybit_task, return_exceptions=True,
    )

    mark_px = 0.0
    if not isinstance(mark_data, Exception) and not _is_err(mark_data):
        mark_px = _f(mark_data.get("markPrice"))

    ok: list[str] = []
    failed: dict[str, str] = {}

    # Binance
    bnb_series: dict[int, dict] = {}
    if isinstance(bnb_data, Exception):
        failed["binance"] = str(bnb_data)
    elif _is_err(bnb_data):
        failed["binance"] = _err_msg(bnb_data)
    elif isinstance(bnb_data, list):
        for d in bnb_data:
            ts = _floor_ts(d.get("timestamp", 0), period)
            bnb_series[ts] = {
                "oi_contracts": _f(d.get("sumOpenInterest")),
                "oi_usd": _f(d.get("sumOpenInterestValue")),
            }
        ok.append("binance")
    else:
        failed["binance"] = "unexpected response"

    # OKX
    okx_series: dict[int, dict] = {}
    if okx_period:
        if isinstance(okx_data, Exception):
            failed["okx"] = str(okx_data)
        elif _is_err(okx_data):
            failed["okx"] = _err_msg(okx_data)
        elif isinstance(okx_data, list):
            for row in okx_data:
                if len(row) < 2:
                    continue
                ts = _floor_ts(row[0], period)
                okx_series[ts] = {
                    "oi_usd": _f(row[1]),
                    "vol_usd": _f(row[2]) if len(row) > 2 else 0.0,
                }
            ok.append("okx")
        else:
            failed["okx"] = "unexpected response"
    else:
        failed["okx"] = f"period {period} not supported on OKX rubik"

    # Bybit — contracts × mark_px = USD
    bybit_series: dict[int, dict] = {}
    if bybit_interval:
        if isinstance(bybit_data, Exception):
            failed["bybit"] = str(bybit_data)
        elif _is_err(bybit_data):
            failed["bybit"] = _err_msg(bybit_data)
        elif isinstance(bybit_data, dict) and isinstance(bybit_data.get("list"), list):
            for d in bybit_data["list"]:
                ts = _floor_ts(d.get("timestamp", 0), period)
                oi_c = _f(d.get("openInterest"))
                bybit_series[ts] = {
                    "oi_contracts": oi_c,
                    "oi_usd": oi_c * mark_px,
                }
            ok.append("bybit")
        else:
            failed["bybit"] = "unexpected response"
    else:
        failed["bybit"] = f"period {period} not supported on Bybit"

    # Union timestamps, SUM across all 3 exchanges
    all_ts = sorted(set(bnb_series.keys()) | set(okx_series.keys()) | set(bybit_series.keys()))
    all_ts = all_ts[-limit:]
    aggregated = []
    for ts in all_ts:
        b = bnb_series.get(ts, {})
        o = okx_series.get(ts, {})
        y = bybit_series.get(ts, {})
        total = _f(b.get("oi_usd")) + _f(o.get("oi_usd")) + _f(y.get("oi_usd"))
        aggregated.append({
            "ts_ms": ts,
            "total_oi_usd": total,
            "binance_oi_usd": _f(b.get("oi_usd")),
            "okx_oi_usd": _f(o.get("oi_usd")),
            "bybit_oi_usd": _f(y.get("oi_usd")),
        })

    return {
        "status": _status(ok, failed),
        "exchanges_ok": ok,
        "exchanges_failed": failed,
        "aggregated": aggregated,
        "by_exchange": {
            "binance": sorted(
                [{"ts_ms": k, **v} for k, v in bnb_series.items()],
                key=lambda x: x["ts_ms"],
            )[-limit:],
            "okx": sorted(
                [{"ts_ms": k, **v} for k, v in okx_series.items()],
                key=lambda x: x["ts_ms"],
            )[-limit:],
            "bybit": sorted(
                [{"ts_ms": k, **v} for k, v in bybit_series.items()],
                key=lambda x: x["ts_ms"],
            )[-limit:],
        },
        "notes": {"okx_scope": "ccy-aggregated (all contracts)" if "okx" in ok else None},
    }


# ─── 3. Taker Volume — HISTORY ────────────────────────────────────────────────


async def aggregate_taker_volume(symbol: str, period: str, limit: int = 100) -> dict:
    """Fetch taker buy/sell from Binance + OKX, SUM per timestamp.

    Binance: /futures/data/takerlongshortRatio → [{timestamp, buyVol, sellVol, buySellRatio}]
             Vols are in BASE currency units (not USD).
    OKX rubik: /api/v5/rubik/stat/taker-volume-contract?instId → [[ts, sellVol, buyVol]]
               Vols are in CONTRACTS. For USDT-SWAP, 1 contract = 0.01 base (BTC) or 0.1 (ETH).

    ⚠️  Different units between exchanges — we convert everything to USD
        by multiplying by recent price. For SUM to be meaningful both must be USD.
    """
    bnb_sym = symbol.upper()
    okx_sym = okx_client.to_okx_swap(bnb_sym)
    okx_period = okx_client.to_okx_rubik_period(period)

    bnb_task = binance_futures_request(
        "/futures/data/takerlongshortRatio",
        {"symbol": bnb_sym, "period": period, "limit": min(limit, 500)},
        1,
    )
    # Parallel: fetch mark price for USD conversion
    mark_task = binance_futures_request("/fapi/v1/premiumIndex", {"symbol": bnb_sym}, 1)
    if okx_period:
        okx_task = okx_client.okx_request(
            "/api/v5/rubik/stat/taker-volume-contract",
            {"instId": okx_sym, "period": okx_period},
        )
    else:
        okx_task = asyncio.sleep(0, result={"error": f"OKX period {period} unsupported"})

    bnb_data, mark_data, okx_data = await asyncio.gather(
        bnb_task, mark_task, okx_task, return_exceptions=True,
    )

    # Mark price for USD conversion
    mark_px = 0.0
    if not isinstance(mark_data, Exception) and not _is_err(mark_data):
        mark_px = _f(mark_data.get("markPrice"))

    ok: list[str] = []
    failed: dict[str, str] = {}

    # Binance normalize — buyVol/sellVol are in BASE units; convert to USD
    bnb_series: dict[int, dict] = {}
    if isinstance(bnb_data, Exception):
        failed["binance"] = str(bnb_data)
    elif _is_err(bnb_data):
        failed["binance"] = _err_msg(bnb_data)
    elif isinstance(bnb_data, list):
        for d in bnb_data:
            ts = _floor_ts(d.get("timestamp", 0), period)
            buy_base = _f(d.get("buyVol"))
            sell_base = _f(d.get("sellVol"))
            bnb_series[ts] = {
                "buy_usd": buy_base * mark_px,
                "sell_usd": sell_base * mark_px,
                "buy_base": buy_base,
                "sell_base": sell_base,
            }
        ok.append("binance")
    else:
        failed["binance"] = "unexpected response"

    # OKX normalize — contracts. Need contract size. For linear USDT SWAP:
    # BTC-USDT-SWAP = 0.01 BTC per contract, ETH-USDT-SWAP = 0.1 ETH per contract.
    # For a generic converter we'd call instruments endpoint; for now rely on
    # parallel mark_px and assume contract size = 0.01 for BTC-like, 0.1 for ETH, 1 for alts.
    # Best practice: just use mark_px × contracts × ct_val (see _okx_ct_val).
    okx_series: dict[int, dict] = {}
    if okx_period:
        if isinstance(okx_data, Exception):
            failed["okx"] = str(okx_data)
        elif _is_err(okx_data):
            failed["okx"] = _err_msg(okx_data)
        elif isinstance(okx_data, list):
            ct_val = await _okx_ct_val(bnb_sym)  # base units per contract (lazy-loaded)
            for row in okx_data:
                if len(row) < 3:
                    continue
                ts = _floor_ts(row[0], period)
                # OKX order: [ts, sellVol, buyVol] (contracts)
                sell_contracts = _f(row[1])
                buy_contracts = _f(row[2])
                sell_base = sell_contracts * ct_val
                buy_base = buy_contracts * ct_val
                okx_series[ts] = {
                    "buy_usd": buy_base * mark_px,
                    "sell_usd": sell_base * mark_px,
                    "buy_base": buy_base,
                    "sell_base": sell_base,
                }
            ok.append("okx")
        else:
            failed["okx"] = "unexpected response"
    else:
        failed["okx"] = f"period {period} not supported on OKX rubik"

    all_ts = sorted(set(bnb_series.keys()) | set(okx_series.keys()))[-limit:]
    aggregated = []
    for ts in all_ts:
        b = bnb_series.get(ts, {})
        o = okx_series.get(ts, {})
        total_buy = _f(b.get("buy_usd")) + _f(o.get("buy_usd"))
        total_sell = _f(b.get("sell_usd")) + _f(o.get("sell_usd"))
        ratio = total_buy / total_sell if total_sell > 0 else 0.0
        aggregated.append({
            "ts_ms": ts,
            "total_buy_usd": total_buy,
            "total_sell_usd": total_sell,
            "net_usd": total_buy - total_sell,
            "buy_sell_ratio": ratio,
        })

    return {
        "status": _status(ok, failed),
        "exchanges_ok": ok,
        "exchanges_failed": failed,
        "aggregated": aggregated,
        "by_exchange": {
            "binance": sorted(
                [{"ts_ms": k, **v} for k, v in bnb_series.items()],
                key=lambda x: x["ts_ms"],
            )[-limit:],
            "okx": sorted(
                [{"ts_ms": k, **v} for k, v in okx_series.items()],
                key=lambda x: x["ts_ms"],
            )[-limit:],
        },
    }


# ─── Contract size lookup for OKX USDT-SWAP linear contracts ─────────────────
#
# OKX contract sizes vary per instrument (BTC=0.01, ARB=10, WLD=1, XRP=100...).
# We lazy-load from /api/v5/public/instruments and cache in memory for the
# process lifetime. This avoids hardcoding a list that drifts over time.

_ct_val_cache: dict[str, float] = {}
_ct_val_lock = asyncio.Lock()


async def _okx_ct_val(binance_symbol: str) -> float:
    """Return OKX contract size (base units per contract) for a symbol.

    Caches result in-memory. Falls back to 1.0 on error (conservative — will
    understate volume rather than inflate it).
    """
    sym = binance_symbol.upper()
    if sym in _ct_val_cache:
        return _ct_val_cache[sym]

    async with _ct_val_lock:
        # Double-check after acquiring lock
        if sym in _ct_val_cache:
            return _ct_val_cache[sym]

        okx_inst_id = okx_client.to_okx_swap(sym)
        resp = await okx_client.okx_request(
            "/api/v5/public/instruments",
            {"instType": "SWAP", "instId": okx_inst_id},
        )
        ct_val = 1.0  # fallback
        if isinstance(resp, list) and resp:
            raw = resp[0].get("ctVal")
            try:
                ct_val = float(raw) if raw else 1.0
            except (TypeError, ValueError):
                ct_val = 1.0
        _ct_val_cache[sym] = ct_val
        return ct_val


# ─── 4. Funding Rate — CURRENT (weighted by OI) ───────────────────────────────


async def aggregate_funding_current(symbol: str) -> dict:
    """Fetch current FR + OI from Binance + OKX + Bybit. Produces OI-weighted FR."""
    bnb_sym = symbol.upper()
    okx_sym = okx_client.to_okx_swap(bnb_sym)

    bnb_fr_task = binance_futures_request("/fapi/v1/premiumIndex", {"symbol": bnb_sym}, 1)
    bnb_oi_task = binance_futures_request("/fapi/v1/openInterest", {"symbol": bnb_sym}, 1)
    okx_fr_task = okx_client.okx_request(
        "/api/v5/public/funding-rate", {"instId": okx_sym},
    )
    okx_oi_task = okx_client.okx_request(
        "/api/v5/public/open-interest", {"instType": "SWAP", "instId": okx_sym},
    )
    # Bybit ticker has both FR + OI + mark in one call
    bybit_task = bybit_client.bybit_request(
        "/v5/market/tickers", {"category": "linear", "symbol": bnb_sym},
    )

    bnb_fr, bnb_oi, okx_fr, okx_oi, bybit_tkr = await asyncio.gather(
        bnb_fr_task, bnb_oi_task, okx_fr_task, okx_oi_task, bybit_task,
        return_exceptions=True,
    )

    ok: list[str] = []
    failed: dict[str, str] = {}
    by_exchange: dict[str, dict] = {}

    # Binance
    if (not isinstance(bnb_fr, Exception) and not _is_err(bnb_fr)
            and not isinstance(bnb_oi, Exception) and not _is_err(bnb_oi)):
        mark = _f(bnb_fr.get("markPrice"))
        oi_contracts = _f(bnb_oi.get("openInterest"))
        by_exchange["binance"] = {
            "funding_rate": _f(bnb_fr.get("lastFundingRate")),
            "mark_price": mark,
            "index_price": _f(bnb_fr.get("indexPrice")),
            "oi_usd": oi_contracts * mark,
            "next_funding_ts_ms": int(bnb_fr.get("nextFundingTime", 0)),
        }
        ok.append("binance")
    else:
        msg_parts = []
        if isinstance(bnb_fr, Exception):
            msg_parts.append(f"fr:{bnb_fr}")
        elif _is_err(bnb_fr):
            msg_parts.append(f"fr:{_err_msg(bnb_fr)}")
        if isinstance(bnb_oi, Exception):
            msg_parts.append(f"oi:{bnb_oi}")
        elif _is_err(bnb_oi):
            msg_parts.append(f"oi:{_err_msg(bnb_oi)}")
        failed["binance"] = "; ".join(msg_parts) or "unknown"

    # OKX
    if (not isinstance(okx_fr, Exception) and not _is_err(okx_fr)
            and not isinstance(okx_oi, Exception) and not _is_err(okx_oi)
            and isinstance(okx_fr, list) and okx_fr
            and isinstance(okx_oi, list) and okx_oi):
        fr_d = okx_fr[0]
        oi_d = okx_oi[0]
        by_exchange["okx"] = {
            "funding_rate": _f(fr_d.get("fundingRate")),
            "next_funding_rate": _f(fr_d.get("nextFundingRate")) if fr_d.get("nextFundingRate") else None,
            "oi_usd": _f(oi_d.get("oiUsd")),
            "next_funding_ts_ms": int(fr_d.get("nextFundingTime", 0)),
        }
        ok.append("okx")
    else:
        failed["okx"] = "fr or oi missing/error"

    # Bybit — ticker returns FR + OI_USD + mark in one response
    if (not isinstance(bybit_tkr, Exception) and not _is_err(bybit_tkr)
            and isinstance(bybit_tkr, dict) and bybit_tkr.get("list")):
        d = bybit_tkr["list"][0]
        by_exchange["bybit"] = {
            "funding_rate": _f(d.get("fundingRate")),
            "mark_price": _f(d.get("markPrice")),
            "oi_usd": _f(d.get("openInterestValue")),
            "next_funding_ts_ms": int(d.get("nextFundingTime", 0)),
        }
        ok.append("bybit")
    else:
        failed["bybit"] = _err_msg(bybit_tkr) if isinstance(bybit_tkr, dict) else str(bybit_tkr)

    # Weighted aggregation across all successful exchanges
    total_oi = sum(by_exchange[e]["oi_usd"] for e in ok)
    weighted_fr = 0.0
    if total_oi > 0:
        weighted_fr = sum(
            by_exchange[e]["funding_rate"] * by_exchange[e]["oi_usd"]
            for e in ok
        ) / total_oi

    return {
        "status": _status(ok, failed),
        "exchanges_ok": ok,
        "exchanges_failed": failed,
        "aggregated": {
            "weighted_funding_rate": weighted_fr,
            "total_oi_usd": total_oi,
            "weight_basis": "oi_usd",
        },
        "by_exchange": by_exchange,
    }


# ─── 5. L/S Ratio — HISTORY (weighted per-ts by current OI) ──────────────────


async def aggregate_ls_ratio(symbol: str, period: str, limit: int = 100) -> dict:
    """Fetch L/S account ratio from Binance + OKX + Bybit.

    Binance: /futures/data/globalLongShortAccountRatio → {longAccount, shortAccount, longShortRatio}
    OKX rubik: /contracts/long-short-account-ratio-contract → [[ts, ratio]]
    Bybit: /v5/market/account-ratio → [{buyRatio, sellRatio, timestamp}] (compute ratio=buy/sell)
    """
    bnb_sym = symbol.upper()
    okx_sym = okx_client.to_okx_swap(bnb_sym)
    okx_period = okx_client.to_okx_rubik_period(period)
    bybit_interval = bybit_client.to_bybit_interval(period)

    bnb_task = binance_futures_request(
        "/futures/data/globalLongShortAccountRatio",
        {"symbol": bnb_sym, "period": period, "limit": min(limit, 500)},
        1,
    )
    if okx_period:
        okx_task = okx_client.okx_request(
            "/api/v5/rubik/stat/contracts/long-short-account-ratio-contract",
            {"instId": okx_sym, "period": okx_period},
        )
    else:
        okx_task = asyncio.sleep(0, result={"error": f"OKX period {period} unsupported"})
    if bybit_interval:
        bybit_task = bybit_client.bybit_request(
            "/v5/market/account-ratio",
            {"category": "linear", "symbol": bnb_sym,
             "period": bybit_interval, "limit": min(limit, 500)},
        )
    else:
        bybit_task = asyncio.sleep(0, result={"error": f"Bybit interval {period} unsupported"})

    bnb_data, okx_data, bybit_data = await asyncio.gather(
        bnb_task, okx_task, bybit_task, return_exceptions=True,
    )

    ok: list[str] = []
    failed: dict[str, str] = {}

    bnb_series: dict[int, dict] = {}
    if isinstance(bnb_data, Exception):
        failed["binance"] = str(bnb_data)
    elif _is_err(bnb_data):
        failed["binance"] = _err_msg(bnb_data)
    elif isinstance(bnb_data, list):
        for d in bnb_data:
            ts = _floor_ts(d.get("timestamp", 0), period)
            bnb_series[ts] = {
                "ratio": _f(d.get("longShortRatio")),
                "long_pct": _f(d.get("longAccount")) * 100,
                "short_pct": _f(d.get("shortAccount")) * 100,
            }
        ok.append("binance")
    else:
        failed["binance"] = "unexpected response"

    okx_series: dict[int, dict] = {}
    if okx_period:
        if isinstance(okx_data, Exception):
            failed["okx"] = str(okx_data)
        elif _is_err(okx_data):
            failed["okx"] = _err_msg(okx_data)
        elif isinstance(okx_data, list):
            for row in okx_data:
                if len(row) < 2:
                    continue
                ts = _floor_ts(row[0], period)
                r = _f(row[1])
                long_pct = (r / (1 + r)) * 100 if r > 0 else 0.0
                short_pct = 100.0 - long_pct
                okx_series[ts] = {"ratio": r, "long_pct": long_pct, "short_pct": short_pct}
            ok.append("okx")
        else:
            failed["okx"] = "unexpected response"
    else:
        failed["okx"] = f"period {period} not supported"

    bybit_series: dict[int, dict] = {}
    if bybit_interval:
        if isinstance(bybit_data, Exception):
            failed["bybit"] = str(bybit_data)
        elif _is_err(bybit_data):
            failed["bybit"] = _err_msg(bybit_data)
        elif isinstance(bybit_data, dict) and isinstance(bybit_data.get("list"), list):
            for d in bybit_data["list"]:
                ts = _floor_ts(d.get("timestamp", 0), period)
                buy = _f(d.get("buyRatio"))
                sell = _f(d.get("sellRatio"))
                r = buy / sell if sell > 0 else 0.0
                bybit_series[ts] = {
                    "ratio": r,
                    "long_pct": buy * 100,
                    "short_pct": sell * 100,
                }
            ok.append("bybit")
        else:
            failed["bybit"] = "unexpected response"
    else:
        failed["bybit"] = f"interval {period} not supported"

    all_ts = sorted(
        set(bnb_series.keys()) | set(okx_series.keys()) | set(bybit_series.keys())
    )[-limit:]
    aggregated = []
    for ts in all_ts:
        b = bnb_series.get(ts)
        o = okx_series.get(ts)
        y = bybit_series.get(ts)
        parts = [x["ratio"] for x in (b, o, y) if x is not None and x.get("ratio", 0) > 0]
        avg = sum(parts) / len(parts) if parts else 0.0
        aggregated.append({
            "ts_ms": ts,
            "avg_ratio": avg,
            "binance_ratio": b["ratio"] if b else None,
            "okx_ratio": o["ratio"] if o else None,
            "bybit_ratio": y["ratio"] if y else None,
        })

    return {
        "status": _status(ok, failed),
        "exchanges_ok": ok,
        "exchanges_failed": failed,
        "aggregated": aggregated,
        "by_exchange": {
            "binance": sorted(
                [{"ts_ms": k, **v} for k, v in bnb_series.items()],
                key=lambda x: x["ts_ms"],
            )[-limit:],
            "okx": sorted(
                [{"ts_ms": k, **v} for k, v in okx_series.items()],
                key=lambda x: x["ts_ms"],
            )[-limit:],
            "bybit": sorted(
                [{"ts_ms": k, **v} for k, v in bybit_series.items()],
                key=lambda x: x["ts_ms"],
            )[-limit:],
        },
        "notes": {"aggregation": "simple avg (equal weight)"},
    }


# ─── 6. Mark Price — CURRENT (vol-weighted) ──────────────────────────────────


async def aggregate_mark_price(symbol: str) -> dict:
    """Fetch mark + 24h vol from Binance + OKX + Bybit. Produces vol-weighted VWAP."""
    bnb_sym = symbol.upper()
    okx_sym = okx_client.to_okx_swap(bnb_sym)

    bnb_mark_task = binance_futures_request("/fapi/v1/premiumIndex", {"symbol": bnb_sym}, 1)
    bnb_ticker_task = binance_futures_request("/fapi/v1/ticker/24hr", {"symbol": bnb_sym}, 1)
    okx_ticker_task = okx_client.okx_request("/api/v5/market/ticker", {"instId": okx_sym})
    okx_mark_task = okx_client.okx_request("/api/v5/public/mark-price",
                                           {"instType": "SWAP", "instId": okx_sym})
    bybit_task = bybit_client.bybit_request(
        "/v5/market/tickers", {"category": "linear", "symbol": bnb_sym},
    )

    bnb_mark, bnb_ticker, okx_ticker, okx_mark, bybit_tkr = await asyncio.gather(
        bnb_mark_task, bnb_ticker_task, okx_ticker_task, okx_mark_task, bybit_task,
        return_exceptions=True,
    )

    ok: list[str] = []
    failed: dict[str, str] = {}
    by_exchange: dict[str, dict] = {}

    if (not isinstance(bnb_mark, Exception) and not _is_err(bnb_mark)
            and not isinstance(bnb_ticker, Exception) and not _is_err(bnb_ticker)):
        by_exchange["binance"] = {
            "mark_price": _f(bnb_mark.get("markPrice")),
            "index_price": _f(bnb_mark.get("indexPrice")),
            "funding_rate": _f(bnb_mark.get("lastFundingRate")),
            "vol_24h_usd": _f(bnb_ticker.get("quoteVolume")),
            "last_price": _f(bnb_ticker.get("lastPrice")),
        }
        ok.append("binance")
    else:
        failed["binance"] = "mark or ticker failed"

    if (isinstance(okx_ticker, list) and okx_ticker
            and isinstance(okx_mark, list) and okx_mark):
        t = okx_ticker[0]
        m = okx_mark[0]
        last = _f(t.get("last"))
        vol_base = _f(t.get("volCcy24h"))
        by_exchange["okx"] = {
            "mark_price": _f(m.get("markPx")) or last,
            "last_price": last,
            "vol_24h_usd": vol_base * last,
        }
        ok.append("okx")
    else:
        failed["okx"] = "ticker or mark failed"

    # Bybit — ticker has turnover24h (already in USD) and markPrice + fundingRate
    if (not isinstance(bybit_tkr, Exception) and not _is_err(bybit_tkr)
            and isinstance(bybit_tkr, dict) and bybit_tkr.get("list")):
        d = bybit_tkr["list"][0]
        by_exchange["bybit"] = {
            "mark_price": _f(d.get("markPrice")),
            "index_price": _f(d.get("indexPrice")),
            "funding_rate": _f(d.get("fundingRate")),
            "last_price": _f(d.get("lastPrice")),
            "vol_24h_usd": _f(d.get("turnover24h")),
        }
        ok.append("bybit")
    else:
        failed["bybit"] = _err_msg(bybit_tkr) if isinstance(bybit_tkr, dict) else str(bybit_tkr)

    total_vol = sum(by_exchange[e]["vol_24h_usd"] for e in ok)
    vwap_mark = 0.0
    if total_vol > 0:
        vwap_mark = sum(
            by_exchange[e]["mark_price"] * by_exchange[e]["vol_24h_usd"]
            for e in ok
        ) / total_vol

    return {
        "status": _status(ok, failed),
        "exchanges_ok": ok,
        "exchanges_failed": failed,
        "aggregated": {
            "vwap_mark_price": vwap_mark,
            "total_vol_24h_usd": total_vol,
            "weight_basis": "vol_24h_usd",
        },
        "by_exchange": by_exchange,
    }
