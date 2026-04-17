"""CFTC COT tests — symbol mapping + Socrata row normalization."""

from __future__ import annotations

import pytest

from coinglass_mcp.forex.cftc_cot import (
    SYMBOL_MAP,
    _map_symbol,
    _row_to_parsed,
    percentile_of,
)

# Fixture mirrors real Socrata row (keys as returned by publicreporting.cftc.gov).
SAMPLE_ROW = {
    "market_and_exchange_names": "GOLD - COMMODITY EXCHANGE INC.",
    "report_date_as_yyyy_mm_dd": "2026-04-07T00:00:00.000",
    "noncomm_positions_long_all": "205368",
    "noncomm_positions_short_all": "49063",
    "noncomm_postions_spread_all": "42663",
    "comm_positions_long_all": "57729",
    "comm_positions_short_all": "251480",
    "open_interest_all": "354877",
}


def test_symbol_map_has_core_symbols():
    for sym in ["XAUUSD", "GOLD", "EURUSD", "GBPUSD", "USDJPY", "DXY"]:
        assert sym in SYMBOL_MAP


def test_map_symbol_unknown_raises():
    with pytest.raises(ValueError):
        _map_symbol("NOTAREALSYMBOL")


def test_map_symbol_case_insensitive():
    assert _map_symbol("xauusd") == SYMBOL_MAP["XAUUSD"]


def test_row_to_parsed_gold():
    parsed = _row_to_parsed(SAMPLE_ROW, "XAUUSD")
    assert parsed["symbol"] == "XAUUSD"
    assert parsed["noncomm_long"] == 205368
    assert parsed["noncomm_short"] == 49063
    assert parsed["comm_long"] == 57729
    assert parsed["comm_short"] == 251480
    assert parsed["net_position"] == 205368 - 49063
    assert parsed["week_date"] == "2026-04-07"
    assert parsed["open_interest"] == 354877


def test_row_to_parsed_handles_missing_fields():
    parsed = _row_to_parsed({"market_and_exchange_names": "X"}, "XAUUSD")
    assert parsed["noncomm_long"] is None
    assert parsed["net_position"] is None
    assert parsed["week_date"] is None


def test_row_to_parsed_handles_empty_strings():
    row = {**SAMPLE_ROW, "noncomm_positions_long_all": ""}
    parsed = _row_to_parsed(row, "XAUUSD")
    assert parsed["noncomm_long"] is None
    assert parsed["net_position"] is None


def test_percentile_of_basic():
    series = list(range(100))
    assert percentile_of(50, series) == 51.0
    assert percentile_of(99, series) == 100.0
    assert percentile_of(-1, series) == 0.0


def test_percentile_of_empty_returns_neutral():
    assert percentile_of(10, []) == 50.0
