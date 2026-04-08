"""SMC Money Flow Framework — MCP Backtest Engine.

Port of smc_backtest_v2.py into FastMCP tool format.
Uses Binance Futures API (async httpx) for OHLCV data.
All indicator computation identical to Pine Script v6 Decision Engine.

Tools:
  smc_backtest       — Single coin backtest with full report
  smc_backtest_batch — Multi-coin comparison summary
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import httpx
import numpy as np
import pandas as pd

WIB = timezone(timedelta(hours=7))

# ═══════════════════════════════════════════════════════════════
# CONFIG DEFAULTS
# ═══════════════════════════════════════════════════════════════

DEFAULT_CFG = {
    "struct_lookback": 20,
    "int_lookback": 5,
    "swing_len": 10,
    "nrtr_pct": 0.02,
    "risk_pct": 0.01,
    "capital": 1000,
}

BINANCE_FAPI = "https://fapi.binance.com"


# ═══════════════════════════════════════════════════════════════
# DATA FETCHER (async httpx)
# ═══════════════════════════════════════════════════════════════

def _interval_to_minutes(interval: str) -> int:
    mapping = {
        "1m": 1, "3m": 3, "5m": 5, "15m": 15,
        "30m": 30, "1h": 60, "4h": 240, "1d": 1440,
    }
    return mapping.get(interval, 5)


async def fetch_ohlcv_async(
    symbol: str = "SOLUSDT",
    interval: str = "15m",
    days: int = 90,
) -> pd.DataFrame:
    """Fetch OHLCV from Binance Futures API (paginated, 1500/req)."""

    import time as _time

    end_ms = int(_time.time() * 1000)
    start_ms = end_ms - (days * 86400 * 1000)

    all_data: list = []
    current_start = start_ms
    retries = 0

    async with httpx.AsyncClient(timeout=20.0) as client:
        while current_start < end_ms and retries < 100:
            params = {
                "symbol": symbol,
                "interval": interval,
                "startTime": current_start,
                "limit": 1500,
            }

            try:
                resp = await client.get(
                    f"{BINANCE_FAPI}/fapi/v1/klines", params=params
                )
                batch = resp.json()

                if not batch or isinstance(batch, dict):
                    break

                all_data.extend(batch)
                current_start = batch[-1][0] + 1

                if len(batch) < 1500:
                    break

                await asyncio.sleep(0.15)
                retries += 1

            except Exception:
                retries += 1
                await asyncio.sleep(1)

    if not all_data:
        raise Exception(f"No data returned from Binance for {symbol}")

    # Binance kline columns
    df = pd.DataFrame(all_data, columns=[
        "ts", "open", "high", "low", "close", "volume",
        "close_ts", "quote_vol", "trades", "taker_buy_vol",
        "taker_buy_quote", "_",
    ])

    for col in ["open", "high", "low", "close", "volume",
                "quote_vol", "taker_buy_vol", "taker_buy_quote"]:
        df[col] = df[col].astype(float)

    df["ts"] = pd.to_datetime(df["ts"].astype(int), unit="ms")
    df.set_index("ts", inplace=True)
    df.sort_index(inplace=True)
    df = df[~df.index.duplicated(keep="first")]

    # CVD from real taker buy/sell volume
    df["taker_sell_vol"] = df["volume"] - df["taker_buy_vol"]
    df["cvd_delta"] = df["taker_buy_vol"] - df["taker_sell_vol"]
    df["fut_cvd"] = df["cvd_delta"].cumsum()
    df["cvd_source"] = "binance_taker"

    return df


# ═══════════════════════════════════════════════════════════════
# INDICATORS (sync — CPU-intensive, called via run_in_executor)
# ═══════════════════════════════════════════════════════════════

def calc_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high, low, close = df["high"], df["low"], df["close"]
    tr = pd.concat([
        high - low,
        (high - close.shift(1)).abs(),
        (low - close.shift(1)).abs(),
    ], axis=1).max(axis=1)
    return tr.rolling(period).mean()


def detect_structure(
    df: pd.DataFrame, lookback: int = 20
) -> tuple[pd.Series, pd.Series, pd.Series, pd.Series, pd.Series]:
    n = len(df)
    trend_up = pd.Series(True, index=df.index)
    swing_high = pd.Series(np.nan, index=df.index)
    swing_low = pd.Series(np.nan, index=df.index)
    bos_bull = pd.Series(False, index=df.index)
    bos_bear = pd.Series(False, index=df.index)

    current_sh = np.nan
    current_sl = np.nan
    current_trend = True

    for i in range(lookback, n - lookback):
        window_h = df["high"].iloc[i - lookback : i + lookback + 1]
        if df["high"].iloc[i] == window_h.max():
            current_sh = df["high"].iloc[i]

        window_l = df["low"].iloc[i - lookback : i + lookback + 1]
        if df["low"].iloc[i] == window_l.min():
            current_sl = df["low"].iloc[i]

        swing_high.iloc[i] = current_sh
        swing_low.iloc[i] = current_sl

        if not np.isnan(current_sh) and i > 0:
            prev_sh = swing_high.iloc[i - 1]
            if (
                not np.isnan(prev_sh)
                and df["close"].iloc[i] > prev_sh
                and df["close"].iloc[i - 1] <= prev_sh
            ):
                bos_bull.iloc[i] = True
                current_trend = True

        if not np.isnan(current_sl) and i > 0:
            prev_sl = swing_low.iloc[i - 1]
            if (
                not np.isnan(prev_sl)
                and df["close"].iloc[i] < prev_sl
                and df["close"].iloc[i - 1] >= prev_sl
            ):
                bos_bear.iloc[i] = True
                current_trend = False

        trend_up.iloc[i] = current_trend

    return trend_up, bos_bull, bos_bear, swing_high, swing_low


def detect_nrtr(
    df: pd.DataFrame, percentage: float = 0.02
) -> tuple[pd.Series, pd.Series]:
    n = len(df)
    nrtr = pd.Series(np.nan, index=df.index)
    nrtr_trend = pd.Series(0, index=df.index)
    trend = 0
    hp = df["close"].iloc[0]
    lp = df["close"].iloc[0]

    for i in range(n):
        c = df["close"].iloc[i]
        if trend >= 0:
            if c > hp:
                hp = c
            nr = hp * (1 - percentage)
            if c <= nr:
                trend = -1
                lp = c
                nr = lp * (1 + percentage)
        else:
            if c < lp:
                lp = c
            nr = lp * (1 + percentage)
            if c > nr:
                trend = 1
                hp = c
                nr = hp * (1 - percentage)
        nrtr.iloc[i] = nr
        nrtr_trend.iloc[i] = trend

    return nrtr, nrtr_trend


def detect_sessions(
    df: pd.DataFrame,
) -> tuple[pd.Series, pd.Series, pd.Series, pd.Series]:
    hours = df.index.hour
    is_asia = (hours >= 0) & (hours < 8)
    is_london = (hours >= 7) & (hours < 15)
    is_ny = (hours >= 12) & (hours < 20)
    active_session = is_london | is_ny
    return is_asia, is_london, is_ny, active_session


def detect_key_level_sweeps(
    df: pd.DataFrame,
) -> tuple[pd.Series, pd.Series, pd.Series, pd.Series]:
    n = len(df)
    pdh_sweep = pd.Series(False, index=df.index)
    pdl_sweep = pd.Series(False, index=df.index)
    pwh_sweep = pd.Series(False, index=df.index)
    pwl_sweep = pd.Series(False, index=df.index)

    df_daily = df.resample("1D").agg({"high": "max", "low": "min"}).dropna()
    df_weekly = df.resample("1W").agg({"high": "max", "low": "min"}).dropna()

    for i in range(1, n):
        dt = df.index[i]
        h = df["high"].iloc[i]
        c = df["close"].iloc[i]
        low_val = df["low"].iloc[i]

        prev_days = df_daily[df_daily.index < dt.normalize()]
        if len(prev_days) >= 1:
            pdh = prev_days["high"].iloc[-1]
            pdl = prev_days["low"].iloc[-1]
            if h > pdh and c < pdh:
                pdh_sweep.iloc[i] = True
            if low_val < pdl and c > pdl:
                pdl_sweep.iloc[i] = True

        week_start = dt - timedelta(days=dt.weekday())
        prev_weeks = df_weekly[df_weekly.index < week_start]
        if len(prev_weeks) >= 1:
            pwh = prev_weeks["high"].iloc[-1]
            pwl = prev_weeks["low"].iloc[-1]
            if h > pwh and c < pwh:
                pwh_sweep.iloc[i] = True
            if low_val < pwl and c > pwl:
                pwl_sweep.iloc[i] = True

    return pdh_sweep, pdl_sweep, pwh_sweep, pwl_sweep


def calc_cvd_bias(
    df: pd.DataFrame, window: int = 12
) -> tuple[pd.Series, pd.Series]:
    rolling_cvd = df["cvd_delta"].rolling(window).sum()
    cvd_positive = rolling_cvd > 0
    return cvd_positive, rolling_cvd


def calc_spot_cvd_bias(
    df: pd.DataFrame, window: int = 12
) -> tuple[pd.Series, pd.Series]:
    """Use real SpotCVD if available, else mirror FutCVD."""
    if "spot_cvd" in df.columns:
        rolling = df["spot_cvd"].diff().rolling(window).sum()
        return rolling > 0, rolling
    return calc_cvd_bias(df, window)


# ═══════════════════════════════════════════════════════════════
# DECISION ENGINE v2 (session-weighted, max score 8)
# ═══════════════════════════════════════════════════════════════

def run_decision_engine(df: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """
    Decision Engine v2 -- session-weighted scoring (max 8):
    1. Sweep direction (+1)
    2. BOS direction (+1)
    3. Swing trend (+1)
    4. Session: London=+1
    5. Alignment bonus (+1)
    6. NRTR trend (+1)
    7. FutCVD bias (+1)
    8. SpotCVD bias (+1)
    """
    atr = calc_atr(df)

    sw_trend, sw_bos_bull, sw_bos_bear, sw_high, sw_low = detect_structure(
        df, cfg["struct_lookback"]
    )
    int_trend, int_bos_bull, int_bos_bear, _, _ = detect_structure(
        df, cfg["int_lookback"]
    )
    nrtr, nrtr_trend = detect_nrtr(df, cfg["nrtr_pct"])
    is_asia, is_london, is_ny, active_session = detect_sessions(df)
    pdh_sweep, pdl_sweep, pwh_sweep, pwl_sweep = detect_key_level_sweeps(df)
    fut_cvd_positive, fut_cvd_rolling = calc_cvd_bias(df)
    spot_cvd_positive, spot_cvd_rolling = calc_spot_cvd_bias(df)

    # --- Scoring ---
    short_score = pd.Series(0, index=df.index)
    long_score = pd.Series(0, index=df.index)

    # 1. Sweep
    short_score += (pdh_sweep | pwh_sweep).astype(int)
    long_score += (pdl_sweep | pwl_sweep).astype(int)

    # 2. BOS
    short_score += sw_bos_bear.astype(int)
    long_score += sw_bos_bull.astype(int)

    # 3. Swing trend
    short_score += (~sw_trend).astype(int)
    long_score += sw_trend.astype(int)

    # 4. Session (London=+1 only)
    short_score += is_london.astype(int)
    long_score += is_london.astype(int)

    # 5. Alignment
    short_score += (~int_trend & ~sw_trend).astype(int)
    long_score += (int_trend & sw_trend).astype(int)

    # 6. NRTR
    short_score += (nrtr_trend == -1).astype(int)
    long_score += (nrtr_trend == 1).astype(int)

    # 7. FutCVD bias
    short_score += (~fut_cvd_positive).astype(int)
    long_score += fut_cvd_positive.astype(int)

    # 8. SpotCVD bias
    short_score += (~spot_cvd_positive).astype(int)
    long_score += spot_cvd_positive.astype(int)

    # --- Signals ---
    liq_event = pdh_sweep | pdl_sweep | pwh_sweep | pwl_sweep
    threshold = cfg["score_threshold"]

    short_raw = short_score >= threshold
    long_raw = long_score >= threshold
    conflict = short_raw & long_raw

    short_bias = short_raw & ~conflict & liq_event
    long_bias = long_raw & ~conflict & liq_event
    trap = liq_event & ((~short_raw & ~long_raw) | conflict)

    # Store
    df["atr"] = atr
    df["sw_trend"] = sw_trend
    df["int_trend"] = int_trend
    df["nrtr"] = nrtr
    df["nrtr_trend"] = nrtr_trend
    df["fut_cvd_rolling"] = fut_cvd_rolling
    df["spot_cvd_rolling"] = spot_cvd_rolling
    df["short_score"] = short_score
    df["long_score"] = long_score
    df["short_signal"] = short_bias
    df["long_signal"] = long_bias
    df["trap_signal"] = trap
    df["liq_event"] = liq_event
    df["pdh_sweep"] = pdh_sweep
    df["pdl_sweep"] = pdl_sweep
    df["pwh_sweep"] = pwh_sweep
    df["pwl_sweep"] = pwl_sweep
    df["is_london"] = is_london
    df["is_ny"] = is_ny
    df["is_asia"] = is_asia
    df["active_session"] = active_session
    df["aligned"] = sw_trend == int_trend

    return df


# ═══════════════════════════════════════════════════════════════
# BACKTESTER
# ═══════════════════════════════════════════════════════════════

def run_backtest(df: pd.DataFrame, cfg: dict) -> list[dict]:
    trades: list[dict] = []
    in_trade = False
    trade: dict = {}

    for i in range(1, len(df)):
        if in_trade:
            h = df["high"].iloc[i]
            low_val = df["low"].iloc[i]

            if trade["side"] == "LONG":
                if low_val <= trade["sl"]:
                    pnl = (trade["sl"] - trade["entry"]) / trade["entry"] * 100
                    trade.update({
                        "exit": trade["sl"],
                        "exit_time": df.index[i],
                        "pnl": pnl,
                        "result": "SL",
                        "bars": i - trade["entry_idx"],
                    })
                    trades.append(trade)
                    in_trade = False
                elif h >= trade["tp"]:
                    pnl = (trade["tp"] - trade["entry"]) / trade["entry"] * 100
                    trade.update({
                        "exit": trade["tp"],
                        "exit_time": df.index[i],
                        "pnl": pnl,
                        "result": "TP",
                        "bars": i - trade["entry_idx"],
                    })
                    trades.append(trade)
                    in_trade = False

            elif trade["side"] == "SHORT":
                if h >= trade["sl"]:
                    pnl = (trade["entry"] - trade["sl"]) / trade["entry"] * 100
                    trade.update({
                        "exit": trade["sl"],
                        "exit_time": df.index[i],
                        "pnl": pnl,
                        "result": "SL",
                        "bars": i - trade["entry_idx"],
                    })
                    trades.append(trade)
                    in_trade = False
                elif low_val <= trade["tp"]:
                    pnl = (
                        (trade["entry"] - trade["tp"]) / trade["entry"] * 100
                    )
                    trade.update({
                        "exit": trade["tp"],
                        "exit_time": df.index[i],
                        "pnl": pnl,
                        "result": "TP",
                        "bars": i - trade["entry_idx"],
                    })
                    trades.append(trade)
                    in_trade = False
            continue

        atr_val = df["atr"].iloc[i]
        if pd.isna(atr_val) or atr_val == 0:
            continue

        hour = df.index[i].hour
        session = "NY" if hour >= 12 else ("LDN" if hour >= 7 else "ASIA")

        if df["long_signal"].iloc[i]:
            entry = df["close"].iloc[i]
            trade = {
                "side": "LONG",
                "entry": entry,
                "entry_time": df.index[i],
                "entry_idx": i,
                "sl": entry - atr_val * cfg["sl_atr_mult"],
                "tp": entry + atr_val * cfg["tp_atr_mult"],
                "score": df["long_score"].iloc[i],
                "session": session,
                "aligned": df["aligned"].iloc[i],
                "sweep": "PDL" if df["pdl_sweep"].iloc[i] else "PWL",
            }
            in_trade = True

        elif df["short_signal"].iloc[i]:
            entry = df["close"].iloc[i]
            trade = {
                "side": "SHORT",
                "entry": entry,
                "entry_time": df.index[i],
                "entry_idx": i,
                "sl": entry + atr_val * cfg["sl_atr_mult"],
                "tp": entry - atr_val * cfg["tp_atr_mult"],
                "score": df["short_score"].iloc[i],
                "session": session,
                "aligned": df["aligned"].iloc[i],
                "sweep": "PDH" if df["pdh_sweep"].iloc[i] else "PWH",
            }
            in_trade = True

    return trades


# ═══════════════════════════════════════════════════════════════
# STATS
# ═══════════════════════════════════════════════════════════════

def calc_stats(trades: list[dict]) -> dict:
    if not trades:
        return {
            "total": 0, "wins": 0, "losses": 0, "wr": 0, "pf": 0,
            "expectancy": 0, "pnl": 0, "max_dd": 0, "rr": 0, "avg_bars": 0,
            "long_trades": 0, "long_wr": 0, "long_pnl": 0,
            "short_trades": 0, "short_wr": 0, "short_pnl": 0,
            "asia_trades": 0, "asia_wr": 0, "asia_pnl": 0,
            "ldn_trades": 0, "ldn_wr": 0, "ldn_pnl": 0,
            "ny_trades": 0, "ny_wr": 0, "ny_pnl": 0,
            "aligned_trades": 0, "aligned_wr": 0,
            "not_aligned_trades": 0, "not_aligned_wr": 0,
        }

    tdf = pd.DataFrame(trades)
    total = len(tdf)
    wins = len(tdf[tdf["result"] == "TP"])
    losses = len(tdf[tdf["result"] == "SL"])
    wr = wins / total * 100 if total > 0 else 0

    avg_win = tdf[tdf["result"] == "TP"]["pnl"].mean() if wins > 0 else 0
    avg_loss = tdf[tdf["result"] == "SL"]["pnl"].mean() if losses > 0 else 0

    gross_profit = tdf[tdf["pnl"] > 0]["pnl"].sum()
    gross_loss = abs(tdf[tdf["pnl"] < 0]["pnl"].sum())
    pf = gross_profit / gross_loss if gross_loss > 0 else 0

    expectancy = (wr / 100 * avg_win) + ((1 - wr / 100) * avg_loss)
    rr = abs(avg_win / avg_loss) if avg_loss != 0 else 0

    cumulative = tdf["pnl"].cumsum()
    peak = cumulative.expanding().max()
    max_dd = (cumulative - peak).min()

    longs = tdf[tdf["side"] == "LONG"]
    shorts = tdf[tdf["side"] == "SHORT"]
    long_wr = (
        len(longs[longs["result"] == "TP"]) / len(longs) * 100
        if len(longs) > 0
        else 0
    )
    short_wr = (
        len(shorts[shorts["result"] == "TP"]) / len(shorts) * 100
        if len(shorts) > 0
        else 0
    )

    def _session_stats(s: str) -> tuple[int, float, float]:
        st = tdf[tdf["session"] == s]
        if len(st) == 0:
            return 0, 0.0, 0.0
        return (
            len(st),
            len(st[st["result"] == "TP"]) / len(st) * 100,
            st["pnl"].sum(),
        )

    asia_t, asia_wr_, asia_pnl = _session_stats("ASIA")
    ldn_t, ldn_wr_, ldn_pnl = _session_stats("LDN")
    ny_t, ny_wr_, ny_pnl = _session_stats("NY")

    al = tdf[tdf["aligned"] == True]  # noqa: E712
    nal = tdf[tdf["aligned"] == False]  # noqa: E712
    al_wr = (
        len(al[al["result"] == "TP"]) / len(al) * 100 if len(al) > 0 else 0
    )
    nal_wr = (
        len(nal[nal["result"] == "TP"]) / len(nal) * 100
        if len(nal) > 0
        else 0
    )

    return {
        "total": total,
        "wins": wins,
        "losses": losses,
        "wr": wr,
        "pf": pf,
        "expectancy": expectancy,
        "pnl": tdf["pnl"].sum(),
        "max_dd": max_dd,
        "rr": rr,
        "avg_bars": tdf["bars"].mean(),
        "long_trades": len(longs),
        "long_wr": long_wr,
        "long_pnl": longs["pnl"].sum() if len(longs) > 0 else 0,
        "short_trades": len(shorts),
        "short_wr": short_wr,
        "short_pnl": shorts["pnl"].sum() if len(shorts) > 0 else 0,
        "asia_trades": asia_t,
        "asia_wr": asia_wr_,
        "asia_pnl": asia_pnl,
        "ldn_trades": ldn_t,
        "ldn_wr": ldn_wr_,
        "ldn_pnl": ldn_pnl,
        "ny_trades": ny_t,
        "ny_wr": ny_wr_,
        "ny_pnl": ny_pnl,
        "aligned_trades": len(al),
        "aligned_wr": al_wr,
        "not_aligned_trades": len(nal),
        "not_aligned_wr": nal_wr,
    }


# ═══════════════════════════════════════════════════════════════
# FULL PIPELINE (sync — run in executor)
# ═══════════════════════════════════════════════════════════════

def _run_pipeline_sync(
    df: pd.DataFrame, cfg: dict
) -> tuple[list[dict], dict]:
    """Run decision engine + backtest. CPU-heavy, must run in thread."""
    df = run_decision_engine(df, cfg)
    trades = run_backtest(df, cfg)
    stats = calc_stats(trades)
    cvd_source = (
        df["cvd_source"].iloc[0] if "cvd_source" in df.columns else "proxy"
    )
    return trades, stats, cvd_source


# ═══════════════════════════════════════════════════════════════
# REPORT FORMATTERS
# ═══════════════════════════════════════════════════════════════

def _format_single_report(
    trades: list[dict],
    stats: dict,
    cfg: dict,
    cvd_source: str,
    candle_count: int,
) -> str:
    """Format markdown report for a single backtest."""
    ts = datetime.now(WIB).strftime("%H:%M:%S WIB")
    sym = cfg["symbol"]
    out = f"## SMC Backtest — {sym} {cfg['interval']} {cfg['days']}d\n\n"
    out += (
        f"**Source:** Binance Futures | **CVD:** {cvd_source} "
        f"| **Time:** {ts}\n"
    )
    out += (
        f"**Config:** threshold={cfg['score_threshold']}/8 | "
        f"SL={cfg['sl_atr_mult']}x ATR | TP={cfg['tp_atr_mult']}x ATR | "
        f"Candles: {candle_count:,}\n\n"
    )

    if stats["total"] == 0:
        out += "**No trades generated.** Try lowering the threshold.\n"
        return out

    # --- Overview ---
    out += "### Overview\n"
    out += f"| Metric | Value |\n|---|---|\n"
    out += f"| Total Trades | {stats['total']} |\n"
    out += f"| Win Rate | {stats['wr']:.1f}% |\n"
    out += f"| Profit Factor | {stats['pf']:.2f} |\n"
    out += f"| Expectancy | {stats['expectancy']:.3f}% |\n"
    out += f"| Total PnL | {stats['pnl']:.2f}% |\n"
    out += f"| Max Drawdown | {stats['max_dd']:.2f}% |\n"
    out += f"| R:R | 1:{stats['rr']:.2f} |\n"
    out += f"| Avg Hold | {stats['avg_bars']:.0f} bars |\n\n"

    # --- By Side ---
    out += "### By Side\n"
    out += "| Side | Trades | WR | PnL |\n|---|---|---|---|\n"
    out += (
        f"| LONG | {stats['long_trades']} | "
        f"{stats['long_wr']:.1f}% | {stats['long_pnl']:.2f}% |\n"
    )
    out += (
        f"| SHORT | {stats['short_trades']} | "
        f"{stats['short_wr']:.1f}% | {stats['short_pnl']:.2f}% |\n\n"
    )

    # --- By Session ---
    out += "### By Session (UTC)\n"
    out += "| Session | Trades | WR | PnL |\n|---|---|---|---|\n"
    out += (
        f"| ASIA (00-08) | {stats['asia_trades']} | "
        f"{stats['asia_wr']:.1f}% | {stats['asia_pnl']:.2f}% |\n"
    )
    out += (
        f"| LDN (07-15) | {stats['ldn_trades']} | "
        f"{stats['ldn_wr']:.1f}% | {stats['ldn_pnl']:.2f}% |\n"
    )
    out += (
        f"| NY (12-20) | {stats['ny_trades']} | "
        f"{stats['ny_wr']:.1f}% | {stats['ny_pnl']:.2f}% |\n\n"
    )

    # --- Alignment Edge ---
    out += "### Alignment Edge\n"
    out += "| Alignment | Trades | WR |\n|---|---|---|\n"
    out += (
        f"| Aligned | {stats['aligned_trades']} | "
        f"{stats['aligned_wr']:.1f}% |\n"
    )
    out += (
        f"| Not Aligned | {stats['not_aligned_trades']} | "
        f"{stats['not_aligned_wr']:.1f}% |\n\n"
    )

    # --- Last 20 Trades ---
    if trades:
        tdf = pd.DataFrame(trades)
        show = tdf.tail(20)
        out += f"### Last {len(show)} Trades\n"
        out += "| # | Side | Time | Entry | Exit | W/L | PnL% | Score | Sess |\n"
        out += "|---|---|---|---|---|---|---|---|---|\n"
        for idx, t in show.iterrows():
            ts_str = (
                t["entry_time"].strftime("%m-%d %H:%M")
                if hasattr(t["entry_time"], "strftime")
                else str(t["entry_time"])[:16]
            )
            r = "W" if t["result"] == "TP" else "L"
            pnl_s = (
                f"+{t['pnl']:.2f}" if t["pnl"] > 0 else f"{t['pnl']:.2f}"
            )
            out += (
                f"| {idx + 1} | {t['side']} | {ts_str} | "
                f"{t['entry']:.4f} | {t['exit']:.4f} | {r} | "
                f"{pnl_s} | {t['score']} | {t['session']} |\n"
            )
        out += "\n"

    # --- Verdict ---
    out += "### Verdict\n"
    if stats["pf"] >= 1.3:
        out += (
            f"**PROFITABLE** — PF {stats['pf']:.2f}, "
            f"WR {stats['wr']:.1f}%, PnL {stats['pnl']:.1f}%\n"
        )
    elif stats["pf"] >= 1.0:
        out += (
            f"**BREAKEVEN** — PF {stats['pf']:.2f}, "
            f"WR {stats['wr']:.1f}%, PnL {stats['pnl']:.1f}%\n"
        )
    else:
        out += (
            f"**LOSING** — PF {stats['pf']:.2f}, "
            f"WR {stats['wr']:.1f}%, PnL {stats['pnl']:.1f}%\n"
        )

    return out


def _format_batch_report(results: list[dict]) -> str:
    """Format markdown comparison table for batch backtest."""
    ts = datetime.now(WIB).strftime("%H:%M:%S WIB")
    out = f"## SMC Backtest — Batch Comparison\n\n"
    out += f"**Source:** Binance Futures | **Time:** {ts}\n\n"

    if not results:
        return out + "**No results.**\n"

    # Build comparison table
    labels = [r["label"] for r in results]
    header = "| Metric | " + " | ".join(labels) + " |\n"
    sep = "|---|" + "|".join(["---"] * len(labels)) + "|\n"
    out += header + sep

    metrics = [
        ("Trades", "total", "{:.0f}"),
        ("Win Rate", "wr", "{:.1f}%"),
        ("Profit Factor", "pf", "{:.2f}"),
        ("Expectancy", "expectancy", "{:.3f}%"),
        ("Total PnL", "pnl", "{:.2f}%"),
        ("Max Drawdown", "max_dd", "{:.2f}%"),
        ("R:R", "rr", "1:{:.2f}"),
        ("LONG WR", "long_wr", "{:.1f}%"),
        ("LONG PnL", "long_pnl", "{:.2f}%"),
        ("SHORT WR", "short_wr", "{:.1f}%"),
        ("SHORT PnL", "short_pnl", "{:.2f}%"),
        ("ASIA PnL", "asia_pnl", "{:.2f}%"),
        ("LDN PnL", "ldn_pnl", "{:.2f}%"),
        ("NY PnL", "ny_pnl", "{:.2f}%"),
        ("Aligned WR", "aligned_wr", "{:.1f}%"),
    ]

    for label, key, fmt in metrics:
        row = f"| {label} |"
        for r in results:
            val = r["stats"].get(key, 0)
            row += f" {fmt.format(val)} |"
        out += row + "\n"

    # CVD source row
    row = "| CVD Source |"
    for r in results:
        row += f" {r.get('cvd_source', 'proxy')} |"
    out += row + "\n\n"

    # Best + recommendations
    valid = [r for r in results if r["stats"]["total"] > 0]
    if valid:
        best = max(valid, key=lambda r: r["stats"]["pf"])
        out += (
            f"**BEST:** {best['label']} — "
            f"PF {best['stats']['pf']:.2f}, PnL {best['stats']['pnl']:.2f}%\n\n"
        )

        out += "### Recommendations\n"
        for r in sorted(valid, key=lambda x: x["stats"]["pf"], reverse=True):
            s = r["stats"]
            if s["pf"] >= 1.3:
                out += (
                    f"- **{r['label']}:** PROFITABLE "
                    f"(PF {s['pf']:.2f}, WR {s['wr']:.1f}%, "
                    f"PnL {s['pnl']:.1f}%)\n"
                )
            elif s["pf"] >= 1.0:
                out += (
                    f"- **{r['label']}:** BREAKEVEN "
                    f"(PF {s['pf']:.2f}, WR {s['wr']:.1f}%, "
                    f"PnL {s['pnl']:.1f}%)\n"
                )
            else:
                out += (
                    f"- **{r['label']}:** LOSING "
                    f"(PF {s['pf']:.2f}, WR {s['wr']:.1f}%, "
                    f"PnL {s['pnl']:.1f}%)\n"
                )

    return out


# ═══════════════════════════════════════════════════════════════
# MCP TOOL REGISTRATION
# ═══════════════════════════════════════════════════════════════

def register_backtest_tools(mcp):
    """Register SMC backtest tools on the FastMCP instance."""

    @mcp.tool()
    async def smc_backtest(
        symbol: str = "SOLUSDT",
        interval: str = "15m",
        days: int = 30,
        threshold: int = 4,
        sl_mult: float = 1.5,
        tp_mult: float = 3.0,
    ) -> str:
        """Run SMC Money Flow backtest on a single coin.

        Fetches OHLCV from Binance Futures, computes SMC indicators
        (structure BOS/CHoCH, NRTR, key level sweeps, sessions, CVD bias),
        runs the 8-factor decision engine, and backtests with ATR-based SL/TP.

        Returns full report: overview stats, by side, by session,
        alignment edge, and last 20 trades.

        Args:
            symbol: Binance Futures pair (e.g. SOLUSDT, ETHUSDT, BTCUSDT)
            interval: Candle timeframe — 5m, 15m, 1h
            days: Lookback period in days (max 90)
            threshold: Minimum score to trigger signal (out of 8)
            sl_mult: Stop loss ATR multiplier
            tp_mult: Take profit ATR multiplier
        """
        # Cap days
        days = min(max(days, 1), 90)
        threshold = min(max(threshold, 1), 8)

        # Normalize symbol
        sym = symbol.upper().strip()
        if not sym.endswith("USDT"):
            sym = sym + "USDT"

        cfg = {
            **DEFAULT_CFG,
            "symbol": sym,
            "interval": interval,
            "days": days,
            "score_threshold": threshold,
            "sl_atr_mult": sl_mult,
            "tp_atr_mult": tp_mult,
        }

        try:
            # 1. Fetch data (async)
            df = await fetch_ohlcv_async(sym, interval, days)
            candle_count = len(df)

            # 2. Run pipeline in thread (CPU-heavy)
            trades, stats, cvd_source = await asyncio.to_thread(
                _run_pipeline_sync, df, cfg
            )

            # 3. Format report
            return _format_single_report(
                trades, stats, cfg, cvd_source, candle_count
            )

        except Exception as e:
            return (
                f"## SMC Backtest — {sym} ERROR\n\n"
                f"**Error:** {str(e)[:500]}\n\n"
                f"Check that `{sym}` is a valid Binance Futures pair."
            )

    @mcp.tool()
    async def smc_backtest_batch(
        coins: str = "SOL,ETH,SUI",
        interval: str = "15m",
        days: int = 30,
        threshold: int = 4,
    ) -> str:
        """Run SMC backtest for multiple coins and return comparison table.

        Runs the full SMC Money Flow backtest on each coin sequentially
        and presents a side-by-side comparison of all key metrics.

        Args:
            coins: Comma-separated coin list (e.g. "SOL,ETH,SUI,AVAX,BTC")
                   USDT suffix is added automatically.
            interval: Candle timeframe — 5m, 15m, 1h
            days: Lookback period in days (max 90)
            threshold: Minimum score to trigger signal (out of 8)
        """
        days = min(max(days, 1), 90)
        threshold = min(max(threshold, 1), 8)

        coin_list = [
            c.strip().upper().replace("USDT", "")
            for c in coins.split(",")
            if c.strip()
        ]

        if not coin_list:
            return "**Error:** No coins specified."

        results: list[dict] = []
        errors: list[str] = []

        for coin in coin_list:
            sym = coin + "USDT"
            cfg = {
                **DEFAULT_CFG,
                "symbol": sym,
                "interval": interval,
                "days": days,
                "score_threshold": threshold,
                "sl_atr_mult": 1.5,
                "tp_atr_mult": 3.0,
            }

            try:
                df = await fetch_ohlcv_async(sym, interval, days)
                trades, stats, cvd_source = await asyncio.to_thread(
                    _run_pipeline_sync, df, cfg
                )
                results.append({
                    "label": f"{coin} {interval}",
                    "cfg": cfg,
                    "stats": stats,
                    "trades": trades,
                    "cvd_source": cvd_source,
                })
            except Exception as e:
                errors.append(f"{coin}: {str(e)[:100]}")

            # Small delay between coins to respect rate limits
            await asyncio.sleep(0.5)

        out = _format_batch_report(results)

        if errors:
            out += "\n### Errors\n"
            for err in errors:
                out += f"- {err}\n"

        return out
