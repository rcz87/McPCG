"""CFTC Commitments of Traders (COT) client — uses Socrata JSON API.

Data source: https://publicreporting.cftc.gov/resource/6dca-aqww.json
(Legacy COT — Futures Only, public domain, weekly release Friday ~15:30 ET).
The legacy report gives us the classic Non-Comm (speculator) vs Commercial
(hedger) split that retail traders are used to.

We query by `market_and_exchange_names` and grab the latest row per contract.
Previous version parsed the text report at /dea/newcot/deacot.txt but that URL
was deprecated by CFTC; the Socrata API is stable and well-documented.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

logger = logging.getLogger("coinglass-mcp.forex")

SOCRATA_URL = "https://publicreporting.cftc.gov/resource/6dca-aqww.json"
TIMEOUT = 15.0

SYMBOL_MAP: dict[str, str] = {
    "XAUUSD":  "GOLD - COMMODITY EXCHANGE INC.",
    "GOLD":    "GOLD - COMMODITY EXCHANGE INC.",
    "XAGUSD":  "SILVER - COMMODITY EXCHANGE INC.",
    "SILVER":  "SILVER - COMMODITY EXCHANGE INC.",
    "DXY":     "USD INDEX - ICE FUTURES U.S.",
    "USDX":    "USD INDEX - ICE FUTURES U.S.",
    "EURUSD":  "EURO FX - CHICAGO MERCANTILE EXCHANGE",
    "GBPUSD":  "BRITISH POUND - CHICAGO MERCANTILE EXCHANGE",
    "USDJPY":  "JAPANESE YEN - CHICAGO MERCANTILE EXCHANGE",
    "USDCHF":  "SWISS FRANC - CHICAGO MERCANTILE EXCHANGE",
    "AUDUSD":  "AUSTRALIAN DOLLAR - CHICAGO MERCANTILE EXCHANGE",
    "USDCAD":  "CANADIAN DOLLAR - CHICAGO MERCANTILE EXCHANGE",
    "NZDUSD":  "NEW ZEALAND DOLLAR - CHICAGO MERCANTILE EXCHANGE",
    "USDMXN":  "MEXICAN PESO - CHICAGO MERCANTILE EXCHANGE",
    "WTI":     "CRUDE OIL, LIGHT SWEET - NEW YORK MERCANTILE EXCHANGE",
}


def _map_symbol(symbol: str) -> str:
    s = symbol.upper().strip()
    if s not in SYMBOL_MAP:
        raise ValueError(
            f"Unknown COT symbol '{symbol}'. Supported: {', '.join(sorted(SYMBOL_MAP))}"
        )
    return SYMBOL_MAP[s]


def _row_to_parsed(row: dict[str, Any], symbol: str) -> dict[str, Any]:
    """Convert a Socrata JSON row to our normalized schema."""
    def _int(key: str) -> int | None:
        v = row.get(key)
        if v is None or v == "":
            return None
        try:
            return int(float(v))
        except (ValueError, TypeError):
            return None

    week_raw = row.get("report_date_as_yyyy_mm_dd", "") or ""
    # Socrata returns "2026-04-07T00:00:00.000" — trim to date.
    week_date = week_raw.split("T", 1)[0] if week_raw else None

    ncl = _int("noncomm_positions_long_all")
    ncs = _int("noncomm_positions_short_all")
    net = None
    if ncl is not None and ncs is not None:
        net = ncl - ncs

    return {
        "symbol": symbol.upper(),
        "contract_name": row.get("market_and_exchange_names"),
        "week_date": week_date,
        "noncomm_long": ncl,
        "noncomm_short": ncs,
        "noncomm_spread": _int("noncomm_postions_spread_all"),
        "comm_long": _int("comm_positions_long_all"),
        "comm_short": _int("comm_positions_short_all"),
        "net_position": net,
        "open_interest": _int("open_interest_all"),
    }


async def _fetch_latest_row(contract: str, client: httpx.AsyncClient) -> dict | None:
    """Query Socrata for the most recent row matching a contract name."""
    params = {
        "market_and_exchange_names": contract,
        "$limit": 1,
        "$order": "report_date_as_yyyy_mm_dd DESC",
    }
    resp = await client.get(SOCRATA_URL, params=params)
    resp.raise_for_status()
    rows = resp.json() or []
    return rows[0] if rows else None


async def fetch_cot_for_symbol(symbol: str) -> dict | None:
    """Fetch and parse the latest COT record for a single symbol."""
    contract = _map_symbol(symbol)
    async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=True) as client:
        row = await _fetch_latest_row(contract, client)
    if not row:
        return None
    return _row_to_parsed(row, symbol)


async def fetch_cot_all_mapped() -> dict[str, dict]:
    """Fetch COT data for every symbol in SYMBOL_MAP in parallel.

    Returns {symbol: parsed_row}. Skips symbols whose query fails.
    """
    # De-dupe contract names (some symbols map to same contract, e.g. GOLD + XAUUSD)
    unique_contracts: dict[str, list[str]] = {}
    for sym, contract in SYMBOL_MAP.items():
        unique_contracts.setdefault(contract, []).append(sym)

    out: dict[str, dict] = {}
    async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=True) as client:
        async def fetch_one(contract: str):
            try:
                return contract, await _fetch_latest_row(contract, client)
            except Exception as e:
                logger.warning("COT fetch failed for %s: %s", contract, e)
                return contract, None

        results = await asyncio.gather(
            *(fetch_one(c) for c in unique_contracts),
            return_exceptions=False,
        )

    for contract, row in results:
        if not row:
            continue
        for sym in unique_contracts[contract]:
            out[sym] = _row_to_parsed(row, sym)
    return out


def percentile_of(value: float, series: list[float]) -> float:
    """Percentile rank of value within series (0-100). Empty series → 50."""
    if not series:
        return 50.0
    sorted_s = sorted(series)
    below = sum(1 for v in sorted_s if v <= value)
    return (below / len(sorted_s)) * 100.0
