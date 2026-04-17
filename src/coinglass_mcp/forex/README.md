# Forex Module — McPCG

Retail sentiment (MyFXBook) + institutional positioning (CFTC COT) buat FX/gold
scalping. Modul additive — gak ngubah tool crypto yang udah jalan.

## Tools (10)

### MyFXBook (retail sentiment — contrarian signal)
| Tool | Fungsi |
|------|--------|
| `myfxbook_sentiment(symbol)` | Retail long/short % + volume + avg entry utk 1 symbol |
| `myfxbook_sentiment_all()` | Semua pair sekaligus, sorted paling crowded dulu |
| `myfxbook_sentiment_by_country(symbol)` | ⚠ Not supported via public API — returns clear fallback |
| `myfxbook_extreme_scanner(threshold, watchlist)` | Scan pair dgn long% / short% ≥ threshold |
| `myfxbook_sentiment_change(symbol, hours)` | Delta sentiment vs N jam lalu (butuh history) |
| `myfxbook_historical_query(symbol, days)` | Time-series dari DB lokal |

### CFTC COT (institutional positioning)
| Tool | Fungsi |
|------|--------|
| `cftc_cot_snapshot(symbol)` | COT report terbaru (Non-Comm vs Commercial) |
| `cftc_cot_extreme_scanner(percentile_threshold)` | Symbol dgn net position di ekstrem (butuh ≥10 minggu history) |
| `cftc_cot_historical(symbol, weeks)` | Time-series Non-Comm net |
| `cftc_cot_vs_retail_divergence(symbol)` | **Killer**: CFTC institutional direction vs MyFXBook retail direction |

## Supported Symbols

**MyFXBook**: semua pair yang ditrack MyFXBook (~100+ major/minor/cross + metals)

**CFTC COT**: `XAUUSD` `GOLD` `XAGUSD` `SILVER` `DXY` `USDX` `EURUSD` `GBPUSD`
`USDJPY` `USDCHF` `AUDUSD` `USDCAD` `NZDUSD` `USDMXN` `WTI`

## Env Vars

Tambahin ke `/root/McPCG/.env`:

```bash
MYFXBOOK_EMAIL=your@email.com
MYFXBOOK_PASSWORD=your-password
```

Kalau gak di-set, tool MyFXBook return envelope error yang jelas (auth missing).
CFTC tools **gak butuh creds** — public domain data.

## Rate Limits

- **MyFXBook**: 100 req/day hard cap (official limit). Tracked di SQLite persisten
  (`forex.db`) — tahan PM2 restart. Warn at 80, hard-block at 95.
- **CFTC**: gak ada limit resmi tapi cached 24h (release mingguan Jumat).

## Background Polling

Otomatis jalan dari `lifespan` server:
- **MyFXBook**: hourly poll community outlook → isi tabel `myfxbook_snapshot` untuk
  `sentiment_change` + `historical_query`.
- **CFTC**: cek tiap jam, fetch ulang kalau udah >7 hari atau Jumat ≥21:00 UTC
  (setelah release resmi).

Polling skip silent kalau:
- MyFXBook creds missing (gak error, cuma log info)
- Rate limit near cap

## Storage

`~/.coinglass-mcp/forex.db` — SQLite terpisah dari `cache.db` biar gak ganggu
main cache. Tabel: `myfxbook_session`, `myfxbook_snapshot`, `cot_snapshot`,
`forex_rate_limit`.

## Example Prompts (buat Claude chat)

```
Scan semua FX pair, cari yang retail crowded >75%
→ myfxbook_extreme_scanner(threshold=75)

XAUUSD retail lagi gimana? Bandingin sama COT institutional.
→ cftc_cot_vs_retail_divergence(symbol="XAUUSD")

Retail EURUSD capitulating gak? Lihat 6 jam terakhir.
→ myfxbook_sentiment_change(symbol="EURUSD", hours=6)

COT gold ekstrem gak minggu ini?
→ cftc_cot_snapshot(symbol="XAUUSD")
```

## Disable Module

Modul additive — gak bisa di-disable via env flag secara kasar, tapi dua cara:

1. **Recommended**: comment satu baris di `server.py`:
   ```python
   # register_forex_tools(mcp)
   ```
   restart server → 10 tool forex hilang, sisanya utuh.

2. **Partial**: hapus creds MyFXBook dari `.env` → tool MyFXBook auto-return error,
   CFTC tools tetap jalan (gak butuh creds).

## Testing

```bash
.venv/bin/python -m pytest tests/forex/ -v
```
