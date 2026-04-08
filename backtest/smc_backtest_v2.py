"""
SMC Money Flow Framework — Backtest Engine v2
Port of Pine Script v6 Decision Engine + CoinGlass Order Flow
By Ricoz87

v2 Changes:
- 90 day support (Binance Futures paginated klines)
- CoinGlass real SpotCVD/FutCVD via McPCG MCP server
- Multi-coin batch mode
- Session-weighted scoring (London boost, NY neutral)
- Scoring max 8 (added SpotCVD layer)
- Comparison summary table

Usage:
  python smc_backtest_v2.py --batch              # run all coins
  python smc_backtest_v2.py --symbol SOLUSDT --days 90
  python smc_backtest_v2.py --symbol SOLUSDT --interval 15m --days 90 --threshold 4
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

COINGLASS_MCP = "https://mcp.guardiansofthetoken.org"

BATCH_CONFIGS = [
    {"symbol": "SOLUSDT",  "interval": "15m", "days": 90, "score_threshold": 4},
    {"symbol": "SOLUSDT",  "interval": "5m",  "days": 90, "score_threshold": 4},
    {"symbol": "ETHUSDT",  "interval": "15m", "days": 90, "score_threshold": 4},
    {"symbol": "SUIUSDT",  "interval": "15m", "days": 90, "score_threshold": 4},
    {"symbol": "AVAXUSDT", "interval": "15m", "days": 90, "score_threshold": 4},
    {"symbol": "HYPEUSDT", "interval": "15m", "days": 90, "score_threshold": 4},
]

DEFAULT_CONFIG = {
    "symbol": "SOLUSDT",
    "interval": "15m",
    "days": 90,
    "struct_lookback": 20,
    "int_lookback": 5,
    "swing_len": 10,
    "nrtr_pct": 0.02,
    "score_threshold": 4,
    "sl_atr_mult": 1.5,
    "tp_atr_mult": 3.0,
    "risk_pct": 0.01,
    "capital": 1000,
}

BACKTEST_DIR = os.path.dirname(os.path.abspath(__file__))


# ═══════════════════════════════════════════════════════════════
# DATA FETCHERS
# ═══════════════════════════════════════════════════════════════

def interval_to_minutes(interval):
    mapping = {
        "1m": 1, "3m": 3, "5m": 5, "15m": 15,
        "30m": 30, "1h": 60, "4h": 240, "1d": 1440
    }
    return mapping.get(interval, 5)


def fetch_ohlcv(symbol="SOLUSDT", interval="5m", days=90):
    """Fetch OHLCV from Binance Futures API (paginated, 1500/req)"""

    total_candles = days * (1440 // interval_to_minutes(interval))
    all_data = []

    end_ms = int(time.time() * 1000)
    start_ms = end_ms - (days * 86400 * 1000)

    print(f"[DATA] Fetching {symbol} {interval} from Binance Futures ({total_candles} candles, {days}d)...")

    current_start = start_ms
    retries = 0
    while current_start < end_ms and retries < 100:
        params = {
            "symbol": symbol,
            "interval": interval,
            "startTime": current_start,
            "limit": 1500,
        }

        try:
            resp = requests.get("https://fapi.binance.com/fapi/v1/klines",
                                params=params, timeout=15)
            batch = resp.json()

            if not batch or isinstance(batch, dict):
                break

            all_data.extend(batch)
            current_start = batch[-1][0] + 1

            if len(batch) < 1500:
                break

            time.sleep(0.15)
            retries += 1

        except Exception as e:
            print(f"[WARN] Binance fetch error: {e}")
            retries += 1
            time.sleep(1)

    if not all_data:
        raise Exception(f"No data returned from Binance for {symbol}")

    # Binance kline format: [ts, o, h, l, c, vol, close_ts, quote_vol, trades, taker_buy_vol, taker_buy_quote, _]
    df = pd.DataFrame(all_data, columns=[
        'ts', 'open', 'high', 'low', 'close', 'volume',
        'close_ts', 'quote_vol', 'trades', 'taker_buy_vol', 'taker_buy_quote', '_'
    ])

    for col in ['open', 'high', 'low', 'close', 'volume', 'quote_vol', 'taker_buy_vol', 'taker_buy_quote']:
        df[col] = df[col].astype(float)

    df['ts'] = pd.to_datetime(df['ts'].astype(int), unit='ms')
    df.set_index('ts', inplace=True)
    df.sort_index(inplace=True)
    df = df[~df.index.duplicated(keep='first')]

    # CVD from real taker buy/sell volume (Binance provides this)
    df['taker_sell_vol'] = df['volume'] - df['taker_buy_vol']
    df['cvd_delta'] = df['taker_buy_vol'] - df['taker_sell_vol']
    df['fut_cvd'] = df['cvd_delta'].cumsum()
    df['cvd_source'] = 'binance_taker'

    print(f"[DATA] Got {len(df)} candles: {df.index[0]} to {df.index[-1]}")
    return df


def fetch_coinglass_cvd(symbol="SOL", interval="5m", cvd_type="spot"):
    """Fetch real SpotCVD or FutCVD from McPCG MCP server."""
    tool_name = f"coinglass_{cvd_type}_cvd"

    payload = {
        "jsonrpc": "2.0",
        "method": "tools/call",
        "params": {
            "name": tool_name,
            "arguments": {"symbol": symbol, "interval": interval}
        },
        "id": 1
    }

    try:
        resp = requests.post(
            f"{COINGLASS_MCP}/mcp",
            json=payload,
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
            },
            timeout=30
        )
        if resp.status_code == 200:
            # Parse SSE response: lines starting with "data: "
            for line in resp.text.strip().split("\n"):
                if line.startswith("data: "):
                    data = json.loads(line[6:])
                    if "result" in data:
                        content = data["result"].get("content", [])
                        for block in content:
                            if block.get("type") == "text":
                                try:
                                    return json.loads(block["text"])
                                except json.JSONDecodeError:
                                    return block["text"]
                    return data
    except Exception as e:
        print(f"[WARN] CoinGlass {cvd_type} CVD fetch failed: {e}")

    return None


def enrich_with_coinglass(df, symbol="SOLUSDT", interval="5m"):
    """Try to enrich with real CoinGlass CVD. Falls back gracefully."""
    base = symbol.replace("USDT", "")

    print(f"[CG] Fetching real SpotCVD for {base}...")
    spot_cvd = fetch_coinglass_cvd(base, interval, "spot")

    print(f"[CG] Fetching real FutCVD for {base}...")
    fut_cvd = fetch_coinglass_cvd(base, interval, "futures")

    spot_ok = False
    fut_ok = False

    for cvd_data_raw, col_name, search_cols, label in [
        (spot_cvd, 'spot_cvd', ['value', 'v', 'cvd', 'spotCvd'], 'SpotCVD'),
        (fut_cvd, 'real_fut_cvd', ['value', 'v', 'cvd', 'futuresCvd'], 'FutCVD'),
    ]:
        if not cvd_data_raw or not isinstance(cvd_data_raw, (list, dict)):
            continue
        try:
            cvd_data = cvd_data_raw
            if isinstance(cvd_data_raw, dict) and "data" in cvd_data_raw:
                cvd_data = cvd_data_raw["data"]

            if not isinstance(cvd_data, list):
                continue

            cvd_df = pd.DataFrame(cvd_data)
            time_col = 'time' if 'time' in cvd_df.columns else ('t' if 't' in cvd_df.columns else None)
            if not time_col:
                continue

            cvd_df['ts'] = pd.to_datetime(cvd_df[time_col], unit='ms')
            cvd_df.set_index('ts', inplace=True)

            val_col = next((c for c in search_cols if c in cvd_df.columns), None)
            if not val_col:
                continue

            df[col_name] = cvd_df[val_col].reindex(df.index, method='nearest')

            if col_name == 'real_fut_cvd':
                df['cvd_delta'] = df['real_fut_cvd'].diff().fillna(0)
                df['fut_cvd'] = df['real_fut_cvd']
                fut_ok = True
            else:
                spot_ok = True

            print(f"[CG] {label} merged!")

        except Exception as e:
            print(f"[WARN] {label} merge failed: {e}")

    if spot_ok or fut_ok:
        df['cvd_source'] = 'coinglass'
        print(f"[CG] Enriched: SpotCVD={'OK' if spot_ok else 'N/A'}, FutCVD={'OK' if fut_ok else 'binance_taker'}")
    else:
        print(f"[CG] CoinGlass unavailable — using Binance taker buy/sell CVD")

    return df


# ═══════════════════════════════════════════════════════════════
# INDICATORS
# ═══════════════════════════════════════════════════════════════

def calc_atr(df, period=14):
    high, low, close = df['high'], df['low'], df['close']
    tr = pd.concat([
        high - low,
        (high - close.shift(1)).abs(),
        (low - close.shift(1)).abs()
    ], axis=1).max(axis=1)
    return tr.rolling(period).mean()


def detect_structure(df, lookback=20):
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
        window_h = df['high'].iloc[i - lookback:i + lookback + 1]
        if df['high'].iloc[i] == window_h.max():
            current_sh = df['high'].iloc[i]

        window_l = df['low'].iloc[i - lookback:i + lookback + 1]
        if df['low'].iloc[i] == window_l.min():
            current_sl = df['low'].iloc[i]

        swing_high.iloc[i] = current_sh
        swing_low.iloc[i] = current_sl

        if not np.isnan(current_sh) and i > 0:
            prev_sh = swing_high.iloc[i - 1]
            if not np.isnan(prev_sh) and df['close'].iloc[i] > prev_sh and df['close'].iloc[i - 1] <= prev_sh:
                bos_bull.iloc[i] = True
                current_trend = True

        if not np.isnan(current_sl) and i > 0:
            prev_sl = swing_low.iloc[i - 1]
            if not np.isnan(prev_sl) and df['close'].iloc[i] < prev_sl and df['close'].iloc[i - 1] >= prev_sl:
                bos_bear.iloc[i] = True
                current_trend = False

        trend_up.iloc[i] = current_trend

    return trend_up, bos_bull, bos_bear, swing_high, swing_low


def detect_nrtr(df, percentage=0.02):
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
    hours = df.index.hour
    is_asia = (hours >= 0) & (hours < 8)
    is_london = (hours >= 7) & (hours < 15)
    is_ny = (hours >= 12) & (hours < 20)
    active_session = is_london | is_ny
    return is_asia, is_london, is_ny, active_session


def detect_key_level_sweeps(df):
    n = len(df)
    pdh_sweep = pd.Series(False, index=df.index)
    pdl_sweep = pd.Series(False, index=df.index)
    pwh_sweep = pd.Series(False, index=df.index)
    pwl_sweep = pd.Series(False, index=df.index)

    df_daily = df.resample('1D').agg({'high': 'max', 'low': 'min'}).dropna()
    df_weekly = df.resample('1W').agg({'high': 'max', 'low': 'min'}).dropna()

    for i in range(1, n):
        dt = df.index[i]
        h = df['high'].iloc[i]
        c = df['close'].iloc[i]
        l = df['low'].iloc[i]

        prev_days = df_daily[df_daily.index < dt.normalize()]
        if len(prev_days) >= 1:
            pdh = prev_days['high'].iloc[-1]
            pdl = prev_days['low'].iloc[-1]
            if h > pdh and c < pdh:
                pdh_sweep.iloc[i] = True
            if l < pdl and c > pdl:
                pdl_sweep.iloc[i] = True

        week_start = dt - timedelta(days=dt.weekday())
        prev_weeks = df_weekly[df_weekly.index < week_start]
        if len(prev_weeks) >= 1:
            pwh = prev_weeks['high'].iloc[-1]
            pwl = prev_weeks['low'].iloc[-1]
            if h > pwh and c < pwh:
                pwh_sweep.iloc[i] = True
            if l < pwl and c > pwl:
                pwl_sweep.iloc[i] = True

    return pdh_sweep, pdl_sweep, pwh_sweep, pwl_sweep


def calc_cvd_bias(df, window=12):
    rolling_cvd = df['cvd_delta'].rolling(window).sum()
    cvd_positive = rolling_cvd > 0
    return cvd_positive, rolling_cvd


def calc_spot_cvd_bias(df, window=12):
    """Use real SpotCVD if available, else mirror FutCVD"""
    if 'spot_cvd' in df.columns:
        rolling = df['spot_cvd'].diff().rolling(window).sum()
        return rolling > 0, rolling
    return calc_cvd_bias(df, window)


# ═══════════════════════════════════════════════════════════════
# DECISION ENGINE v2 (session-weighted, max score 8)
# ═══════════════════════════════════════════════════════════════

def run_decision_engine(df, cfg):
    """
    Decision Engine v2 — session-weighted scoring

    Scoring (max 8):
    1. Sweep direction (+1)
    2. BOS direction (+1)
    3. Swing trend (+1)
    4. Session: London=+1, NY=0, Asia=0
    5. Alignment bonus (+1)
    6. NRTR trend (+1)
    7. FutCVD bias (+1)
    8. SpotCVD bias (+1)
    """

    print("\n[ENGINE] Running Decision Engine v2...")

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
    fut_cvd_positive, fut_cvd_rolling = calc_cvd_bias(df)
    spot_cvd_positive, spot_cvd_rolling = calc_spot_cvd_bias(df)

    cvd_source = df['cvd_source'].iloc[0] if 'cvd_source' in df.columns else 'proxy'
    print(f"[ENGINE] CVD source: {cvd_source}")

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
    threshold = cfg['score_threshold']

    short_raw = short_score >= threshold
    long_raw = long_score >= threshold
    conflict = short_raw & long_raw

    short_bias = short_raw & ~conflict & liq_event
    long_bias = long_raw & ~conflict & liq_event
    trap = liq_event & ((~short_raw & ~long_raw) | conflict)

    # Store
    df['atr'] = atr
    df['sw_trend'] = sw_trend
    df['int_trend'] = int_trend
    df['nrtr'] = nrtr
    df['nrtr_trend'] = nrtr_trend
    df['fut_cvd_rolling'] = fut_cvd_rolling
    df['spot_cvd_rolling'] = spot_cvd_rolling
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
    df['is_london'] = is_london
    df['is_ny'] = is_ny
    df['is_asia'] = is_asia
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
    print(f"  Score threshold:  {threshold}/8")
    print(f"  CVD source:       {cvd_source}")

    return df


# ═══════════════════════════════════════════════════════════════
# BACKTESTER
# ═══════════════════════════════════════════════════════════════

def run_backtest(df, cfg):
    trades = []
    in_trade = False
    trade = None

    for i in range(1, len(df)):
        if in_trade:
            h = df['high'].iloc[i]
            l = df['low'].iloc[i]

            if trade['side'] == 'LONG':
                if l <= trade['sl']:
                    pnl = (trade['sl'] - trade['entry']) / trade['entry'] * 100
                    trade.update({'exit': trade['sl'], 'exit_time': df.index[i],
                                  'pnl': pnl, 'result': 'SL', 'bars': i - trade['entry_idx']})
                    trades.append(trade)
                    in_trade = False
                elif h >= trade['tp']:
                    pnl = (trade['tp'] - trade['entry']) / trade['entry'] * 100
                    trade.update({'exit': trade['tp'], 'exit_time': df.index[i],
                                  'pnl': pnl, 'result': 'TP', 'bars': i - trade['entry_idx']})
                    trades.append(trade)
                    in_trade = False

            elif trade['side'] == 'SHORT':
                if h >= trade['sl']:
                    pnl = (trade['entry'] - trade['sl']) / trade['entry'] * 100
                    trade.update({'exit': trade['sl'], 'exit_time': df.index[i],
                                  'pnl': pnl, 'result': 'SL', 'bars': i - trade['entry_idx']})
                    trades.append(trade)
                    in_trade = False
                elif l <= trade['tp']:
                    pnl = (trade['entry'] - trade['tp']) / trade['entry'] * 100
                    trade.update({'exit': trade['tp'], 'exit_time': df.index[i],
                                  'pnl': pnl, 'result': 'TP', 'bars': i - trade['entry_idx']})
                    trades.append(trade)
                    in_trade = False
            continue

        atr_val = df['atr'].iloc[i]
        if pd.isna(atr_val) or atr_val == 0:
            continue

        hour = df.index[i].hour
        session = 'NY' if hour >= 12 else ('LDN' if hour >= 7 else 'ASIA')

        if df['long_signal'].iloc[i]:
            entry = df['close'].iloc[i]
            trade = {
                'side': 'LONG', 'entry': entry, 'entry_time': df.index[i], 'entry_idx': i,
                'sl': entry - atr_val * cfg['sl_atr_mult'],
                'tp': entry + atr_val * cfg['tp_atr_mult'],
                'score': df['long_score'].iloc[i],
                'session': session,
                'aligned': df['aligned'].iloc[i],
                'sweep': 'PDL' if df['pdl_sweep'].iloc[i] else 'PWL',
            }
            in_trade = True

        elif df['short_signal'].iloc[i]:
            entry = df['close'].iloc[i]
            trade = {
                'side': 'SHORT', 'entry': entry, 'entry_time': df.index[i], 'entry_idx': i,
                'sl': entry + atr_val * cfg['sl_atr_mult'],
                'tp': entry - atr_val * cfg['tp_atr_mult'],
                'score': df['short_score'].iloc[i],
                'session': session,
                'aligned': df['aligned'].iloc[i],
                'sweep': 'PDH' if df['pdh_sweep'].iloc[i] else 'PWH',
            }
            in_trade = True

    return trades


# ═══════════════════════════════════════════════════════════════
# REPORTING
# ═══════════════════════════════════════════════════════════════

def calc_stats(trades):
    if not trades:
        return {
            'total': 0, 'wins': 0, 'losses': 0, 'wr': 0, 'pf': 0,
            'expectancy': 0, 'pnl': 0, 'max_dd': 0, 'rr': 0, 'avg_bars': 0,
            'long_trades': 0, 'long_wr': 0, 'long_pnl': 0,
            'short_trades': 0, 'short_wr': 0, 'short_pnl': 0,
            'asia_trades': 0, 'asia_wr': 0, 'asia_pnl': 0,
            'ldn_trades': 0, 'ldn_wr': 0, 'ldn_pnl': 0,
            'ny_trades': 0, 'ny_wr': 0, 'ny_pnl': 0,
            'aligned_trades': 0, 'aligned_wr': 0,
            'not_aligned_trades': 0, 'not_aligned_wr': 0,
        }

    tdf = pd.DataFrame(trades)
    total = len(tdf)
    wins = len(tdf[tdf['result'] == 'TP'])
    losses = len(tdf[tdf['result'] == 'SL'])
    wr = wins / total * 100 if total > 0 else 0

    avg_win = tdf[tdf['result'] == 'TP']['pnl'].mean() if wins > 0 else 0
    avg_loss = tdf[tdf['result'] == 'SL']['pnl'].mean() if losses > 0 else 0

    gross_profit = tdf[tdf['pnl'] > 0]['pnl'].sum()
    gross_loss = abs(tdf[tdf['pnl'] < 0]['pnl'].sum())
    pf = gross_profit / gross_loss if gross_loss > 0 else 0

    expectancy = (wr / 100 * avg_win) + ((1 - wr / 100) * avg_loss)
    rr = abs(avg_win / avg_loss) if avg_loss != 0 else 0

    cumulative = tdf['pnl'].cumsum()
    peak = cumulative.expanding().max()
    max_dd = (cumulative - peak).min()

    longs = tdf[tdf['side'] == 'LONG']
    shorts = tdf[tdf['side'] == 'SHORT']
    long_wr = len(longs[longs['result'] == 'TP']) / len(longs) * 100 if len(longs) > 0 else 0
    short_wr = len(shorts[shorts['result'] == 'TP']) / len(shorts) * 100 if len(shorts) > 0 else 0

    def session_stats(s):
        st = tdf[tdf['session'] == s]
        if len(st) == 0:
            return 0, 0, 0
        return len(st), len(st[st['result'] == 'TP']) / len(st) * 100, st['pnl'].sum()

    asia_t, asia_wr, asia_pnl = session_stats('ASIA')
    ldn_t, ldn_wr, ldn_pnl = session_stats('LDN')
    ny_t, ny_wr, ny_pnl = session_stats('NY')

    al = tdf[tdf['aligned'] == True]
    nal = tdf[tdf['aligned'] == False]
    al_wr = len(al[al['result'] == 'TP']) / len(al) * 100 if len(al) > 0 else 0
    nal_wr = len(nal[nal['result'] == 'TP']) / len(nal) * 100 if len(nal) > 0 else 0

    return {
        'total': total, 'wins': wins, 'losses': losses,
        'wr': wr, 'pf': pf, 'expectancy': expectancy,
        'pnl': tdf['pnl'].sum(), 'max_dd': max_dd, 'rr': rr,
        'avg_bars': tdf['bars'].mean(),
        'long_trades': len(longs), 'long_wr': long_wr, 'long_pnl': longs['pnl'].sum() if len(longs) > 0 else 0,
        'short_trades': len(shorts), 'short_wr': short_wr, 'short_pnl': shorts['pnl'].sum() if len(shorts) > 0 else 0,
        'asia_trades': asia_t, 'asia_wr': asia_wr, 'asia_pnl': asia_pnl,
        'ldn_trades': ldn_t, 'ldn_wr': ldn_wr, 'ldn_pnl': ldn_pnl,
        'ny_trades': ny_t, 'ny_wr': ny_wr, 'ny_pnl': ny_pnl,
        'aligned_trades': len(al), 'aligned_wr': al_wr,
        'not_aligned_trades': len(nal), 'not_aligned_wr': nal_wr,
    }


def print_single_report(trades, cfg):
    stats = calc_stats(trades)

    if stats['total'] == 0:
        print(f"\n[REPORT] {cfg['symbol']} {cfg['interval']} -- No trades generated.")
        return stats

    print(f"\n{'=' * 60}")
    print(f"  {cfg['symbol']} | {cfg['interval']} | {cfg['days']}d | threshold={cfg['score_threshold']}/8")
    print(f"  SL: {cfg['sl_atr_mult']}x ATR | TP: {cfg['tp_atr_mult']}x ATR")
    print(f"{'=' * 60}")

    print(f"\n  {'Total trades':<20} {stats['total']}")
    print(f"  {'Win Rate':<20} {stats['wr']:.1f}%")
    print(f"  {'Profit Factor':<20} {stats['pf']:.2f}")
    print(f"  {'Expectancy':<20} {stats['expectancy']:.3f}%")
    print(f"  {'Total PnL':<20} {stats['pnl']:.2f}%")
    print(f"  {'Max Drawdown':<20} {stats['max_dd']:.2f}%")
    print(f"  {'R:R':<20} 1:{stats['rr']:.2f}")
    print(f"  {'Avg Hold':<20} {stats['avg_bars']:.0f} bars")

    print(f"\n  LONG  {stats['long_trades']} trades | WR: {stats['long_wr']:.1f}% | PnL: {stats['long_pnl']:.2f}%")
    print(f"  SHORT {stats['short_trades']} trades | WR: {stats['short_wr']:.1f}% | PnL: {stats['short_pnl']:.2f}%")

    print(f"\n  ASIA  {stats['asia_trades']} trades | WR: {stats['asia_wr']:.1f}% | PnL: {stats['asia_pnl']:.2f}%")
    print(f"  LDN   {stats['ldn_trades']} trades | WR: {stats['ldn_wr']:.1f}% | PnL: {stats['ldn_pnl']:.2f}%")
    print(f"  NY    {stats['ny_trades']} trades | WR: {stats['ny_wr']:.1f}% | PnL: {stats['ny_pnl']:.2f}%")

    print(f"\n  Aligned     {stats['aligned_trades']} trades | WR: {stats['aligned_wr']:.1f}%")
    print(f"  Not aligned {stats['not_aligned_trades']} trades | WR: {stats['not_aligned_wr']:.1f}%")

    if trades:
        tdf = pd.DataFrame(trades)
        print(f"\n  TRADE LOG (last 20)")
        print(f"  {'#':<4} {'Side':<6} {'Time':<18} {'Entry':<10} {'Exit':<10} {'R':<3} {'PnL%':<8} {'Sc':<3} {'Sess':<5}")
        print(f"  {'-' * 75}")

        show = tdf.tail(20)
        for idx, t in show.iterrows():
            ts = t['entry_time'].strftime('%m-%d %H:%M') if hasattr(t['entry_time'], 'strftime') else str(t['entry_time'])[:16]
            r = "W" if t['result'] == 'TP' else "L"
            pnl_s = f"+{t['pnl']:.2f}" if t['pnl'] > 0 else f"{t['pnl']:.2f}"
            print(f"  {idx+1:<4} {t['side']:<6} {ts:<18} {t['entry']:<10.4f} {t['exit']:<10.4f} {r:<3} {pnl_s:<8} {t['score']:<3} {t['session']:<5}")

    print(f"{'=' * 60}")
    return stats


def print_batch_summary(results):
    print(f"\n{'=' * 90}")
    print(f"  BATCH SUMMARY -- SMC Backtest v2 (90d, Binance Futures + CoinGlass)")
    print(f"{'=' * 90}")

    labels = [f"{r['cfg']['symbol'].replace('USDT','')} {r['cfg']['interval']}" for r in results]
    header = f"  {'Metric':<18}" + "".join(f"{l:<14}" for l in labels)
    print(header)
    print(f"  {'-' * (18 + 14 * len(labels))}")

    metrics = [
        ('Trades', 'total', '{:.0f}'),
        ('Win Rate', 'wr', '{:.1f}%'),
        ('Profit Factor', 'pf', '{:.2f}'),
        ('Expectancy', 'expectancy', '{:.3f}%'),
        ('Total PnL', 'pnl', '{:.2f}%'),
        ('Max Drawdown', 'max_dd', '{:.2f}%'),
        ('R:R', 'rr', '1:{:.2f}'),
        ('LONG WR', 'long_wr', '{:.1f}%'),
        ('LONG PnL', 'long_pnl', '{:.2f}%'),
        ('SHORT WR', 'short_wr', '{:.1f}%'),
        ('SHORT PnL', 'short_pnl', '{:.2f}%'),
        ('ASIA PnL', 'asia_pnl', '{:.2f}%'),
        ('LDN PnL', 'ldn_pnl', '{:.2f}%'),
        ('NY PnL', 'ny_pnl', '{:.2f}%'),
        ('Aligned WR', 'aligned_wr', '{:.1f}%'),
    ]

    for label, key, fmt in metrics:
        row = f"  {label:<18}"
        for r in results:
            val = r['stats'].get(key, 0)
            row += f"{fmt.format(val):<14}"
        print(row)

    # CVD source row
    row = f"  {'CVD Source':<18}"
    for r in results:
        src = r.get('cvd_source', 'proxy')
        row += f"{src:<14}"
    print(row)

    # Best setting
    valid = [r for r in results if r['stats']['total'] > 0]
    if valid:
        best = max(valid, key=lambda r: r['stats']['pf'])
        print(f"\n  BEST: {best['cfg']['symbol']} {best['cfg']['interval']} -- PF {best['stats']['pf']:.2f}, PnL {best['stats']['pnl']:.2f}%")

        print(f"\n  RECOMMENDATIONS:")
        for r in sorted(valid, key=lambda x: x['stats']['pf'], reverse=True):
            s = r['stats']
            cfg = r['cfg']
            sym = cfg['symbol'].replace('USDT', '')
            if s['pf'] >= 1.3:
                print(f"  + {sym} {cfg['interval']}: PROFITABLE (PF {s['pf']:.2f}, WR {s['wr']:.1f}%, PnL {s['pnl']:.1f}%)")
            elif s['pf'] >= 1.0:
                print(f"  ~ {sym} {cfg['interval']}: BREAKEVEN (PF {s['pf']:.2f}, WR {s['wr']:.1f}%, PnL {s['pnl']:.1f}%)")
            else:
                print(f"  - {sym} {cfg['interval']}: LOSING (PF {s['pf']:.2f}, WR {s['wr']:.1f}%, PnL {s['pnl']:.1f}%)")

    print(f"\n{'=' * 90}")


# ═══════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════

def run_single(cfg):
    print(f"\n{'#' * 60}")
    print(f"  Running: {cfg['symbol']} {cfg['interval']} {cfg['days']}d threshold={cfg['score_threshold']}")
    print(f"{'#' * 60}")

    df = fetch_ohlcv(cfg['symbol'], cfg['interval'], cfg['days'])
    df = enrich_with_coinglass(df, cfg['symbol'], cfg['interval'])
    df = run_decision_engine(df, cfg)
    trades = run_backtest(df, cfg)
    stats = print_single_report(trades, cfg)
    cvd_source = df['cvd_source'].iloc[0] if 'cvd_source' in df.columns else 'proxy'

    # Save CSV
    results_dir = os.path.join(BACKTEST_DIR, 'results')
    os.makedirs(results_dir, exist_ok=True)
    if trades:
        tdf = pd.DataFrame(trades)
        fname = f"{cfg['symbol']}_{cfg['interval']}_{cfg['days']}d_t{cfg['score_threshold']}_v2.csv"
        out_path = os.path.join(results_dir, fname)
        tdf.to_csv(out_path, index=False)
        print(f"[SAVE] {out_path}")

    return {'cfg': cfg, 'stats': stats, 'trades': trades, 'cvd_source': cvd_source}


def main():
    parser = argparse.ArgumentParser(description='SMC Backtest v2')
    parser.add_argument('--symbol', default='SOLUSDT')
    parser.add_argument('--interval', default='15m')
    parser.add_argument('--days', type=int, default=90)
    parser.add_argument('--threshold', type=int, default=4)
    parser.add_argument('--sl', type=float, default=1.5)
    parser.add_argument('--tp', type=float, default=3.0)
    parser.add_argument('--batch', action='store_true', help='Run all coins')
    args = parser.parse_args()

    print("=" * 60)
    print("  SMC Money Flow Framework -- Backtest Engine v2")
    print(f"  Data: Binance Futures | CVD enrich: McPCG CoinGlass")
    print("=" * 60)

    if args.batch:
        results = []
        for batch_cfg in BATCH_CONFIGS:
            cfg = DEFAULT_CONFIG.copy()
            cfg.update(batch_cfg)
            cfg['sl_atr_mult'] = args.sl
            cfg['tp_atr_mult'] = args.tp
            try:
                result = run_single(cfg)
                results.append(result)
            except Exception as e:
                print(f"[ERROR] {batch_cfg['symbol']}: {e}")
                continue
            time.sleep(1)

        if results:
            print_batch_summary(results)
    else:
        cfg = DEFAULT_CONFIG.copy()
        cfg['symbol'] = args.symbol
        cfg['interval'] = args.interval
        cfg['days'] = args.days
        cfg['score_threshold'] = args.threshold
        cfg['sl_atr_mult'] = args.sl
        cfg['tp_atr_mult'] = args.tp
        run_single(cfg)


if __name__ == "__main__":
    main()
