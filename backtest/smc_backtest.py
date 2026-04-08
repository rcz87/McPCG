"""
SMC Money Flow Framework — Backtest Engine
Port of Pine Script v6 Decision Engine + CoinGlass Order Flow
By Ricoz87

Data Sources:
- OKX API: OHLCV (klines)
- CVD proxy from candle direction (standalone, no MCP dependency)

Usage:
  python smc_backtest.py
  python smc_backtest.py --symbol SOLUSDT --interval 5m --days 30
"""

import requests
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import json
import argparse
import time
import os

# ═══════════════════════════════════════════════════════════════
# CONFIG
# ═══════════════════════════════════════════════════════════════

DEFAULT_CONFIG = {
    "symbol": "SOLUSDT",
    "interval": "5m",
    "days": 14,
    "struct_lookback": 20,
    "int_lookback": 5,
    "swing_len": 10,
    "nrtr_pct": 0.02,
    "score_threshold": 5,
    "sl_atr_mult": 1.5,
    "tp_atr_mult": 3.0,
    "risk_pct": 0.01,       # 1% risk per trade
    "capital": 1000,
}

BACKTEST_DIR = os.path.dirname(os.path.abspath(__file__))


# ═══════════════════════════════════════════════════════════════
# DATA FETCHERS
# ═══════════════════════════════════════════════════════════════

def interval_to_minutes(interval):
    """Convert interval string to minutes"""
    mapping = {"1m": 1, "3m": 3, "5m": 5, "15m": 15, "30m": 30, "1h": 60, "4h": 240, "1d": 1440}
    return mapping.get(interval, 5)


def fetch_ohlcv(symbol="SOLUSDT", interval="5m", days=14):
    """Fetch OHLCV from Binance Futures API (paginated, 1500/req)"""

    total_candles = days * (1440 // interval_to_minutes(interval))
    all_data = []

    # Calculate start time
    end_ms = int(time.time() * 1000)
    start_ms = end_ms - (days * 86400 * 1000)

    print(f"[DATA] Fetching {symbol} {interval} from Binance Futures ({total_candles} candles target)...")

    current_start = start_ms
    while current_start < end_ms:
        params = {
            "symbol": symbol,
            "interval": interval,
            "startTime": current_start,
            "limit": 1500,
        }

        resp = requests.get("https://fapi.binance.com/fapi/v1/klines", params=params, timeout=15)
        batch = resp.json()

        if not batch or isinstance(batch, dict):
            break

        all_data.extend(batch)
        current_start = batch[-1][0] + 1  # next ms after last candle

        if len(batch) < 1500:
            break

        time.sleep(0.15)

    if not all_data:
        raise Exception("No data returned from Binance")

    # Binance format: [ts, o, h, l, c, vol, close_ts, quote_vol, trades, taker_buy_vol, taker_buy_quote, ignore]
    df = pd.DataFrame(all_data, columns=[
        'ts', 'open', 'high', 'low', 'close', 'volume',
        'close_ts', 'quote_vol', 'trades', 'taker_buy_vol', 'taker_buy_quote', '_'
    ])

    for col in ['open', 'high', 'low', 'close', 'volume', 'quote_vol', 'taker_buy_vol', 'taker_buy_quote']:
        df[col] = df[col].astype(float)

    df['ts'] = pd.to_datetime(df['ts'].astype(int), unit='ms')
    df.set_index('ts', inplace=True)
    df.sort_index(inplace=True)

    # CVD from actual taker buy/sell data (better than candle-color proxy)
    df['taker_sell_vol'] = df['volume'] - df['taker_buy_vol']
    df['cvd_delta'] = df['taker_buy_vol'] - df['taker_sell_vol']
    df['fut_cvd'] = df['cvd_delta'].cumsum()

    print(f"[DATA] Got {len(df)} candles: {df.index[0]} to {df.index[-1]}")
    return df


# ═══════════════════════════════════════════════════════════════
# INDICATORS
# ═══════════════════════════════════════════════════════════════

def calc_atr(df, period=14):
    """ATR calculation"""
    high = df['high']
    low = df['low']
    close = df['close']

    tr1 = high - low
    tr2 = abs(high - close.shift(1))
    tr3 = abs(low - close.shift(1))
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

    return tr.rolling(period).mean()


def detect_structure(df, lookback=20):
    """
    Detect BOS/CHoCH — port of Pine Script structure logic
    Returns: trend_up series, bos_bull events, bos_bear events
    """
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
        # Pivot high
        window_h = df['high'].iloc[i - lookback:i + lookback + 1]
        if df['high'].iloc[i] == window_h.max():
            current_sh = df['high'].iloc[i]

        # Pivot low
        window_l = df['low'].iloc[i - lookback:i + lookback + 1]
        if df['low'].iloc[i] == window_l.min():
            current_sl = df['low'].iloc[i]

        swing_high.iloc[i] = current_sh
        swing_low.iloc[i] = current_sl

        # BOS Bull
        if not np.isnan(current_sh) and i > 0:
            prev_sh = swing_high.iloc[i - 1]
            if not np.isnan(prev_sh) and df['close'].iloc[i] > prev_sh and df['close'].iloc[i - 1] <= prev_sh:
                bos_bull.iloc[i] = True
                current_trend = True

        # BOS Bear
        if not np.isnan(current_sl) and i > 0:
            prev_sl = swing_low.iloc[i - 1]
            if not np.isnan(prev_sl) and df['close'].iloc[i] < prev_sl and df['close'].iloc[i - 1] >= prev_sl:
                bos_bear.iloc[i] = True
                current_trend = False

        trend_up.iloc[i] = current_trend

    return trend_up, bos_bull, bos_bear, swing_high, swing_low


def detect_nrtr(df, percentage=0.02):
    """NRTR — Nick Rypock Trailing Reverse"""
    n = len(df)
    nrtr = pd.Series(np.nan, index=df.index)
    nrtr_trend = pd.Series(0, index=df.index)

    trend = 0
    hp = df['close'].iloc[0]
    lp = df['close'].iloc[0]

    for i in range(n):
        c = df['close'].iloc[i]

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


def detect_sessions(df):
    """Detect trading sessions (UTC)"""
    hours = df.index.hour

    is_asia = (hours >= 0) & (hours < 8)
    is_london = (hours >= 7) & (hours < 15)
    is_ny = (hours >= 12) & (hours < 20)
    active_session = is_london | is_ny

    return is_asia, is_london, is_ny, active_session


def detect_key_level_sweeps(df):
    """Detect PDH/PDL/PWH/PWL sweeps"""
    n = len(df)
    pdh_sweep = pd.Series(False, index=df.index)
    pdl_sweep = pd.Series(False, index=df.index)
    pwh_sweep = pd.Series(False, index=df.index)
    pwl_sweep = pd.Series(False, index=df.index)

    # Compute daily and weekly highs/lows
    df_daily = df.resample('1D').agg({'high': 'max', 'low': 'min'})
    df_weekly = df.resample('1W').agg({'high': 'max', 'low': 'min'})

    for i in range(1, n):
        dt = df.index[i]

        # Previous day H/L
        prev_days = df_daily[df_daily.index < dt.normalize()]
        if len(prev_days) >= 1:
            pdh = prev_days['high'].iloc[-1]
            pdl = prev_days['low'].iloc[-1]

            h = df['high'].iloc[i]
            c = df['close'].iloc[i]
            l = df['low'].iloc[i]

            if h > pdh and c < pdh:
                pdh_sweep.iloc[i] = True
            if l < pdl and c > pdl:
                pdl_sweep.iloc[i] = True

        # Previous week H/L
        week_start = dt - timedelta(days=dt.weekday())
        prev_weeks = df_weekly[df_weekly.index < week_start]
        if len(prev_weeks) >= 1:
            pwh = prev_weeks['high'].iloc[-1]
            pwl = prev_weeks['low'].iloc[-1]

            h = df['high'].iloc[i]
            c = df['close'].iloc[i]
            l = df['low'].iloc[i]

            if h > pwh and c < pwh:
                pwh_sweep.iloc[i] = True
            if l < pwl and c > pwl:
                pwl_sweep.iloc[i] = True

    return pdh_sweep, pdl_sweep, pwh_sweep, pwl_sweep


def calc_cvd_bias(df, window=12):
    """
    CVD bias from taker data (proxy for SpotCVD/FutCVD alignment).
    Positive cumulative delta = bullish, negative = bearish.
    """
    rolling_cvd = df['cvd_delta'].rolling(window).sum()
    cvd_positive = rolling_cvd > 0
    return cvd_positive, rolling_cvd


# ═══════════════════════════════════════════════════════════════
# DECISION ENGINE (port from Pine Script)
# ═══════════════════════════════════════════════════════════════

def run_decision_engine(df, cfg):
    """
    Decision Engine v2 — ported from Pine Script

    Scoring (max 7):
    1. Sweep direction (PDH/PWH -> short, PDL/PWL -> long)
    2. BOS direction
    3. Swing trend
    4. Active session (London/NY)
    5. Alignment (swing + internal trend same direction)
    6. NRTR trend
    7. CVD bias (order flow)
    """

    print("\n[ENGINE] Running Decision Engine...")

    # --- Indicators ---
    atr = calc_atr(df)

    print("[ENGINE] Detecting swing structure...")
    sw_trend, sw_bos_bull, sw_bos_bear, sw_high, sw_low = detect_structure(df, cfg['struct_lookback'])

    print("[ENGINE] Detecting internal structure...")
    int_trend, int_bos_bull, int_bos_bear, _, _ = detect_structure(df, cfg['int_lookback'])

    print("[ENGINE] Computing NRTR...")
    nrtr, nrtr_trend = detect_nrtr(df, cfg['nrtr_pct'])

    print("[ENGINE] Detecting sessions...")
    is_asia, is_london, is_ny, active_session = detect_sessions(df)

    print("[ENGINE] Detecting key level sweeps...")
    pdh_sweep, pdl_sweep, pwh_sweep, pwl_sweep = detect_key_level_sweeps(df)

    print("[ENGINE] Computing CVD bias...")
    cvd_positive, cvd_rolling = calc_cvd_bias(df)

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

    # 4. Active session
    short_score += active_session.astype(int)
    long_score += active_session.astype(int)

    # 5. Alignment
    aligned_bear = (~int_trend & ~sw_trend)
    aligned_bull = (int_trend & sw_trend)
    short_score += aligned_bear.astype(int)
    long_score += aligned_bull.astype(int)

    # 6. NRTR
    short_score += (nrtr_trend == -1).astype(int)
    long_score += (nrtr_trend == 1).astype(int)

    # 7. CVD bias (order flow layer)
    short_score += (~cvd_positive).astype(int)
    long_score += cvd_positive.astype(int)

    # --- Signals ---
    liq_event = pdh_sweep | pdl_sweep | pwh_sweep | pwl_sweep
    threshold = cfg['score_threshold']

    short_raw = short_score >= threshold
    long_raw = long_score >= threshold
    conflict = short_raw & long_raw

    short_bias = short_raw & ~conflict & liq_event
    long_bias = long_raw & ~conflict & liq_event
    trap = liq_event & ((~short_raw & ~long_raw) | conflict)

    # Store results
    df['atr'] = atr
    df['sw_trend'] = sw_trend
    df['int_trend'] = int_trend
    df['nrtr'] = nrtr
    df['nrtr_trend'] = nrtr_trend
    df['cvd_rolling'] = cvd_rolling
    df['short_score'] = short_score
    df['long_score'] = long_score
    df['short_signal'] = short_bias
    df['long_signal'] = long_bias
    df['trap_signal'] = trap
    df['liq_event'] = liq_event
    df['pdh_sweep'] = pdh_sweep
    df['pdl_sweep'] = pdl_sweep
    df['pwh_sweep'] = pwh_sweep
    df['pwl_sweep'] = pwl_sweep
    df['active_session'] = active_session
    df['aligned'] = sw_trend == int_trend

    longs = long_bias.sum()
    shorts = short_bias.sum()
    traps = trap.sum()
    events = liq_event.sum()

    print(f"\n[ENGINE] Results:")
    print(f"  Liquidity events: {events}")
    print(f"  LONG signals:     {longs}")
    print(f"  SHORT signals:    {shorts}")
    print(f"  TRAP/WEAK:        {traps}")
    print(f"  Score threshold:  {threshold}/7")

    return df


# ═══════════════════════════════════════════════════════════════
# BACKTESTER
# ═══════════════════════════════════════════════════════════════

def run_backtest(df, cfg):
    """
    Simple backtest:
    - Entry on signal bar close
    - SL = ATR * sl_mult
    - TP = ATR * tp_mult
    - Track each trade to completion
    """

    print("\n[BACKTEST] Running backtest...")

    trades = []
    in_trade = False
    trade = None

    for i in range(1, len(df)):
        if in_trade:
            # Check SL/TP
            h = df['high'].iloc[i]
            l = df['low'].iloc[i]

            if trade['side'] == 'LONG':
                if l <= trade['sl']:
                    pnl = trade['sl'] - trade['entry']
                    pnl_pct = pnl / trade['entry'] * 100
                    trade['exit'] = trade['sl']
                    trade['exit_time'] = df.index[i]
                    trade['pnl'] = pnl_pct
                    trade['result'] = 'SL'
                    trade['bars'] = i - trade['entry_idx']
                    trades.append(trade)
                    in_trade = False
                elif h >= trade['tp']:
                    pnl = trade['tp'] - trade['entry']
                    pnl_pct = pnl / trade['entry'] * 100
                    trade['exit'] = trade['tp']
                    trade['exit_time'] = df.index[i]
                    trade['pnl'] = pnl_pct
                    trade['result'] = 'TP'
                    trade['bars'] = i - trade['entry_idx']
                    trades.append(trade)
                    in_trade = False

            elif trade['side'] == 'SHORT':
                if h >= trade['sl']:
                    pnl = trade['entry'] - trade['sl']
                    pnl_pct = pnl / trade['entry'] * 100
                    trade['exit'] = trade['sl']
                    trade['exit_time'] = df.index[i]
                    trade['pnl'] = pnl_pct
                    trade['result'] = 'SL'
                    trade['bars'] = i - trade['entry_idx']
                    trades.append(trade)
                    in_trade = False
                elif l <= trade['tp']:
                    pnl = trade['entry'] - trade['tp']
                    pnl_pct = pnl / trade['entry'] * 100
                    trade['exit'] = trade['tp']
                    trade['exit_time'] = df.index[i]
                    trade['pnl'] = pnl_pct
                    trade['result'] = 'TP'
                    trade['bars'] = i - trade['entry_idx']
                    trades.append(trade)
                    in_trade = False
            continue

        # Check for new signals
        atr_val = df['atr'].iloc[i]
        if pd.isna(atr_val) or atr_val == 0:
            continue

        if df['long_signal'].iloc[i]:
            entry = df['close'].iloc[i]
            trade = {
                'side': 'LONG',
                'entry': entry,
                'entry_time': df.index[i],
                'entry_idx': i,
                'sl': entry - atr_val * cfg['sl_atr_mult'],
                'tp': entry + atr_val * cfg['tp_atr_mult'],
                'score': df['long_score'].iloc[i],
                'session': 'NY' if df.index[i].hour >= 12 else ('LDN' if df.index[i].hour >= 7 else 'ASIA'),
                'aligned': df['aligned'].iloc[i],
                'sweep': 'PDL' if df['pdl_sweep'].iloc[i] else 'PWL',
            }
            in_trade = True

        elif df['short_signal'].iloc[i]:
            entry = df['close'].iloc[i]
            trade = {
                'side': 'SHORT',
                'entry': entry,
                'entry_time': df.index[i],
                'entry_idx': i,
                'sl': entry + atr_val * cfg['sl_atr_mult'],
                'tp': entry - atr_val * cfg['tp_atr_mult'],
                'score': df['short_score'].iloc[i],
                'session': 'NY' if df.index[i].hour >= 12 else ('LDN' if df.index[i].hour >= 7 else 'ASIA'),
                'aligned': df['aligned'].iloc[i],
                'sweep': 'PDH' if df['pdh_sweep'].iloc[i] else 'PWH',
            }
            in_trade = True

    return trades


# ═══════════════════════════════════════════════════════════════
# REPORTING
# ═══════════════════════════════════════════════════════════════

def print_report(trades, cfg):
    """Print comprehensive backtest report"""

    if not trades:
        print("\n[REPORT] No trades generated. Try lowering score_threshold or increasing days.")
        return None

    tdf = pd.DataFrame(trades)

    total = len(tdf)
    wins = len(tdf[tdf['result'] == 'TP'])
    losses = len(tdf[tdf['result'] == 'SL'])
    win_rate = wins / total * 100 if total > 0 else 0

    avg_win = tdf[tdf['result'] == 'TP']['pnl'].mean() if wins > 0 else 0
    avg_loss = tdf[tdf['result'] == 'SL']['pnl'].mean() if losses > 0 else 0

    total_pnl = tdf['pnl'].sum()
    avg_bars = tdf['bars'].mean()

    # Risk-reward
    rr = abs(avg_win / avg_loss) if avg_loss != 0 else 0

    # Expectancy
    expectancy = (win_rate / 100 * avg_win) + ((1 - win_rate / 100) * avg_loss)

    # Profit factor
    gross_profit = tdf[tdf['pnl'] > 0]['pnl'].sum()
    gross_loss = abs(tdf[tdf['pnl'] < 0]['pnl'].sum())
    pf = gross_profit / gross_loss if gross_loss > 0 else float('inf')

    # Max drawdown
    cumulative = tdf['pnl'].cumsum()
    peak = cumulative.expanding().max()
    drawdown = cumulative - peak
    max_dd = drawdown.min()

    # By side
    longs = tdf[tdf['side'] == 'LONG']
    shorts = tdf[tdf['side'] == 'SHORT']
    long_wr = len(longs[longs['result'] == 'TP']) / len(longs) * 100 if len(longs) > 0 else 0
    short_wr = len(shorts[shorts['result'] == 'TP']) / len(shorts) * 100 if len(shorts) > 0 else 0

    # By session
    sessions = {}
    for s in ['ASIA', 'LDN', 'NY']:
        s_trades = tdf[tdf['session'] == s]
        if len(s_trades) > 0:
            s_wins = len(s_trades[s_trades['result'] == 'TP'])
            sessions[s] = {
                'trades': len(s_trades),
                'win_rate': s_wins / len(s_trades) * 100,
                'pnl': s_trades['pnl'].sum()
            }

    # By alignment
    aligned = tdf[tdf['aligned'] == True]
    not_aligned = tdf[tdf['aligned'] == False]
    aligned_wr = len(aligned[aligned['result'] == 'TP']) / len(aligned) * 100 if len(aligned) > 0 else 0
    not_aligned_wr = len(not_aligned[not_aligned['result'] == 'TP']) / len(not_aligned) * 100 if len(not_aligned) > 0 else 0

    # Print
    print("\n" + "=" * 60)
    print(f"  SMC BACKTEST REPORT -- {cfg['symbol']} {cfg['interval']}")
    print(f"  Period: {cfg['days']} days | Threshold: {cfg['score_threshold']}/7")
    print(f"  SL: {cfg['sl_atr_mult']}x ATR | TP: {cfg['tp_atr_mult']}x ATR")
    print("=" * 60)

    print(f"\n  OVERVIEW")
    print(f"  {'Total trades':<25} {total}")
    print(f"  {'Wins':<25} {wins}")
    print(f"  {'Losses':<25} {losses}")
    print(f"  {'Win Rate':<25} {win_rate:.1f}%")
    print(f"  {'Avg Win':<25} +{avg_win:.2f}%")
    print(f"  {'Avg Loss':<25} {avg_loss:.2f}%")
    print(f"  {'Risk:Reward':<25} 1:{rr:.2f}")
    print(f"  {'Expectancy':<25} {expectancy:.3f}%")
    print(f"  {'Profit Factor':<25} {pf:.2f}")
    print(f"  {'Total PnL':<25} {total_pnl:.2f}%")
    print(f"  {'Max Drawdown':<25} {max_dd:.2f}%")
    print(f"  {'Avg Hold (bars)':<25} {avg_bars:.0f}")

    print(f"\n  BY SIDE")
    print(f"  {'LONG':<10} {len(longs)} trades | WR: {long_wr:.1f}% | PnL: {longs['pnl'].sum():.2f}%")
    print(f"  {'SHORT':<10} {len(shorts)} trades | WR: {short_wr:.1f}% | PnL: {shorts['pnl'].sum():.2f}%")

    print(f"\n  BY SESSION")
    for s, data in sessions.items():
        print(f"  {s:<10} {data['trades']} trades | WR: {data['win_rate']:.1f}% | PnL: {data['pnl']:.2f}%")

    print(f"\n  ALIGNMENT EDGE")
    print(f"  {'Aligned':<15} {len(aligned)} trades | WR: {aligned_wr:.1f}%")
    print(f"  {'Not aligned':<15} {len(not_aligned)} trades | WR: {not_aligned_wr:.1f}%")

    # Trade log
    print(f"\n  TRADE LOG")
    print(f"  {'#':<4} {'Side':<6} {'Entry Time':<20} {'Entry':<10} {'Exit':<10} {'Result':<6} {'PnL%':<8} {'Score':<6} {'Sweep':<6} {'Session':<6}")
    print(f"  {'-'*90}")

    for idx, t in tdf.iterrows():
        entry_str = t['entry_time'].strftime('%m-%d %H:%M') if hasattr(t['entry_time'], 'strftime') else str(t['entry_time'])[:16]
        result_mark = "WIN" if t['result'] == 'TP' else "LOSS"
        pnl_str = f"+{t['pnl']:.2f}" if t['pnl'] > 0 else f"{t['pnl']:.2f}"
        print(f"  {idx+1:<4} {t['side']:<6} {entry_str:<20} {t['entry']:<10.4f} {t['exit']:<10.4f} {result_mark:<6} {pnl_str:<8} {t['score']:<6} {t['sweep']:<6} {t['session']:<6}")

    print("\n" + "=" * 60)

    return tdf


# ═══════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description='SMC Money Flow Backtest')
    parser.add_argument('--symbol', default='SOLUSDT', help='Trading pair')
    parser.add_argument('--interval', default='5m', help='Timeframe')
    parser.add_argument('--days', type=int, default=14, help='Days of data')
    parser.add_argument('--threshold', type=int, default=5, help='Score threshold (out of 7)')
    parser.add_argument('--sl', type=float, default=1.5, help='SL ATR multiplier')
    parser.add_argument('--tp', type=float, default=3.0, help='TP ATR multiplier')
    args = parser.parse_args()

    cfg = DEFAULT_CONFIG.copy()
    cfg['symbol'] = args.symbol
    cfg['interval'] = args.interval
    cfg['days'] = args.days
    cfg['score_threshold'] = args.threshold
    cfg['sl_atr_mult'] = args.sl
    cfg['tp_atr_mult'] = args.tp

    print("=" * 60)
    print("  SMC Money Flow Framework -- Backtest Engine")
    print(f"  {cfg['symbol']} | {cfg['interval']} | {cfg['days']}d | threshold={cfg['score_threshold']}/7")
    print("=" * 60)

    # Fetch data
    df = fetch_ohlcv(cfg['symbol'], cfg['interval'], cfg['days'])

    # Run Decision Engine
    df = run_decision_engine(df, cfg)

    # Run backtest
    trades = run_backtest(df, cfg)

    # Report
    tdf = print_report(trades, cfg)

    # Save trades to CSV
    if tdf is not None:
        results_dir = os.path.join(BACKTEST_DIR, 'results')
        os.makedirs(results_dir, exist_ok=True)
        fname = f"{cfg['symbol']}_{cfg['interval']}_{cfg['days']}d_t{cfg['score_threshold']}.csv"
        out_path = os.path.join(results_dir, fname)
        tdf.to_csv(out_path, index=False)
        print(f"\n[SAVE] Results saved to {out_path}")


if __name__ == "__main__":
    main()
