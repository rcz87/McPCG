# Ricoz Trading Analysis Guide
### Cheatsheet Lengkap — "Coin Ini Mau Naik atau Turun?"

---

## DAFTAR ISI

1. [Quick Check (30 Detik)](#1-quick-check-30-detik)
2. [Signal Matrix — SpotCVD x FutCVD x OBDelta](#2-signal-matrix)
3. [OI vs Price Matrix](#3-oi-vs-price-matrix)
4. [Heatmap Reading Guide](#4-heatmap-reading-guide)
5. [Proven Patterns](#5-proven-patterns)
6. [Top-Down Flow (5 Menit Pre-Trade)](#6-top-down-flow)
7. [Common Traps](#7-common-traps)
8. [Entry Checklist](#8-entry-checklist)

---

## 1. Quick Check (30 Detik)

**5 sinyal yang harus dicek SEBELUM ambil posisi:**

```
CHECK 1: SpotCVD naik atau turun?
         ↑ Naik  = Ada real buying (BULLISH)
         ↓ Turun = Real selling pressure (BEARISH)

CHECK 2: Futures CVD searah dengan Spot?
         ✅ Searah   = Konfirmasi kuat
         ❌ Berlawanan = HATI-HATI, divergence!

CHECK 3: OBDelta (Order Book) mendukung?
         Bid > Ask = Buyer dominan (support naik)
         Ask > Bid = Seller dominan (support turun)

CHECK 4: OI bergerak kemana?
         OI naik + Harga naik  = Trend sehat
         OI naik + Harga turun = Akan ada squeeze

CHECK 5: Heatmap — Ada dinding besar di mana?
         Cluster tebal di bawah = Support kuat
         Cluster tebal di atas  = Resistance kuat
```

### Quick Decision:
```
3-5 sinyal BULLISH  → Cari LONG entry
3-5 sinyal BEARISH  → Cari SHORT entry
Campur/tidak jelas  → SKIP, tunggu clarity
```

---

## 2. Signal Matrix

### SpotCVD x FuturesCVD x OBDelta — 8 Kombinasi

| # | SpotCVD | FutCVD | OBDelta | Arti | Action | Confidence |
|---|---------|--------|---------|------|--------|------------|
| 1 | ↑ Naik | ↑ Naik | Bid > Ask | **Full bullish alignment** — real buying + leverage ikut + orderbook support | **LONG** | ⭐⭐⭐⭐⭐ |
| 2 | ↑ Naik | ↑ Naik | Ask > Bid | Buying kuat tapi ada wall di atas — mungkin perlu breakout dulu | **LONG** (tunggu break wall) | ⭐⭐⭐⭐ |
| 3 | ↑ Naik | ↓ Turun | Bid > Ask | **Spot beli, futures jual** — smart money accumulate, futures akan catch up | **LONG** (high conviction) | ⭐⭐⭐⭐ |
| 4 | ↑ Naik | ↓ Turun | Ask > Bid | Mixed signal — spot beli tapi futures + OB melawan | **SKIP** | ⭐⭐ |
| 5 | ↓ Turun | ↓ Turun | Ask > Bid | **Full bearish alignment** — real selling + leverage ikut + OB confirm | **SHORT** | ⭐⭐⭐⭐⭐ |
| 6 | ↓ Turun | ↓ Turun | Bid > Ask | Selling tapi ada bid support di bawah — mungkin bounce dulu | **SHORT** (tunggu bid break) | ⭐⭐⭐⭐ |
| 7 | ↓ Turun | ↑ Naik | Ask > Bid | **Spot jual, futures beli** — futures overleveraged, akan di-flush | **SHORT** (squeeze incoming) | ⭐⭐⭐⭐ |
| 8 | ↓ Turun | ↑ Naik | Bid > Ask | Mixed signal — konflik semua arah | **SKIP** | ⭐⭐ |

### Aturan Emas:
```
✅ SpotCVD = RAJA. Selalu ikuti arah Spot CVD sebagai primary signal.
✅ FutCVD yang BERLAWANAN dengan SpotCVD = potensi squeeze besar.
❌ Jangan trade kalau 3 sinyal ini saling bertentangan.
```

---

## 3. OI vs Price Matrix

### Open Interest x Harga — 9 Skenario

| OI | Harga | Arti | Signal | Action |
|----|-------|------|--------|--------|
| ↑ Naik | ↑ Naik | **New longs masuk** — trend bullish sehat, money flowing in | BULLISH | Hold/Add long |
| ↑ Naik | ↓ Turun | **New shorts masuk** — aggressive shorting, SHORT SQUEEZE incoming! | DANGER | Tunggu squeeze, lalu long |
| ↑ Naik | → Flat | **Accumulation** — posisi dibuka tapi belum ada arah, siap breakout | NEUTRAL | Siap-siap, pasang alert |
| ↓ Turun | ↑ Naik | **Short covering** — shorts tutup posisi, bukan real buying | WEAK BULL | Jangan chase, tunggu pullback |
| ↓ Turun | ↓ Turun | **Long liquidation** — longs menyerah, capitulation | BEARISH | Tunggu OI stabilize baru entry |
| ↓ Turun | → Flat | **Deleveraging** — posisi dikurangi tanpa impact harga | NEUTRAL | Tunggu OI naik lagi |
| → Flat | ↑ Naik | **Organic move** — harga naik tanpa leverage baru, sehat | HEALTHY BULL | Entry long aman |
| → Flat | ↓ Turun | **Organic selling** — turun tanpa panic, normal correction | HEALTHY BEAR | Bisa short ringan |
| → Flat | → Flat | **Dead market** — tidak ada aktivitas, jangan trade | NO TRADE | Tunggu volatility |

### Highlight Penting:
```
🟢 PALING BULLISH : OI naik + Harga naik (fresh money masuk)
🔴 PALING BEARISH : OI turun + Harga turun (capitulation)
⚠️ PALING BAHAYA  : OI naik + Harga turun (squeeze siap meledak)
💎 PALING SEHAT   : OI flat + Harga naik (organic, no leverage)
```

---

## 4. Heatmap Reading Guide

### Cara Baca Heatmap Liquidation

#### Warna
```
🟣 UNGU/BIRU     = Cluster liquidation kecil
🟡 KUNING        = Cluster liquidation medium
🟠 ORANGE/MERAH  = Cluster liquidation BESAR → magnet harga!
```

#### Ketebalan & Ukuran
```
Tipis   = Sedikit liquidation, bisa diabaikan
Sedang  = Perlu diperhatikan sebagai support/resistance
TEBAL   = Banyak posisi terjebak → HARGA AKAN DITARIK KESINI
```

#### Cara Pakai di Trading:

**Skenario 1: Cluster BESAR di ATAS harga**
```
→ Ada banyak SHORT yang SL-nya di atas
→ Market maker akan dorong harga NAIK untuk liquidate mereka
→ BIAS: Bullish short-term
→ Action: Cari long entry, target = level cluster
```

**Skenario 2: Cluster BESAR di BAWAH harga**
```
→ Ada banyak LONG yang SL-nya di bawah
→ Market maker akan dorong harga TURUN untuk liquidate mereka
→ BIAS: Bearish short-term
→ Action: Cari short entry, target = level cluster
```

**Skenario 3: Cluster BESAR di ATAS dan BAWAH (Sandwich)**
```
→ Liquidity di kedua sisi → VOLATILITAS TINGGI
→ Harga akan sweep satu sisi dulu, lalu reversal ke sisi lain
→ Action: TUNGGU sweep pertama, entry di reversal
→ Ini setup paling profitable tapi perlu sabar
```

### Liquidity Sweep Pattern:
```
1. Harga mendekati cluster → slow, grinding move
2. Harga menyentuh cluster → SPIKE cepat (liquidation cascade)
3. Setelah sweep → REVERSAL tajam (karena fuel habis)
4. Entry terbaik = SETELAH sweep selesai, bukan sebelum
```

---

## 5. Proven Patterns

### Pattern 1: Failed Sweep (Reversal Signal)
```
Setup:
  Harga turun ke liquidity cluster di bawah
  → Sweep terjadi (wick panjang ke bawah)
  → TAPI harga GAGAL close di bawah
  → Langsung bounce kembali

Entry: Long setelah candle close di atas level sweep
SL:    Di bawah wick low
TP:    Cluster liquidation berikutnya di atas

Confidence: ⭐⭐⭐⭐⭐ (highest win rate)
```

### Pattern 2: Bottom Signal (Capitulation Buy)
```
Setup:
  OI turun drastis + Harga turun drastis
  → Long liquidation cascade
  → SpotCVD mulai NAIK (smart money beli)
  → FuturesCVD masih turun (retail masih panic)

Entry: Long ketika SpotCVD confirm naik + OI mulai stabilize
SL:    Di bawah low capitulation
TP:    Previous support yang jadi resistance

Confidence: ⭐⭐⭐⭐ (perlu timing tepat)
```

### Pattern 3: Squeeze Setup
```
Setup:
  OI naik TAJAM + Harga TURUN
  → Artinya shorts aggressif masuk
  → Funding rate sangat negatif
  → Heatmap cluster BESAR di atas (short SLs)

Entry: Long ketika harga mulai push ke atas cluster pertama
SL:    Di bawah recent low
TP:    Level cluster teratas di heatmap

Trigger: Biasanya terjadi setelah news/event yang bikin shorts confidence
Confidence: ⭐⭐⭐⭐⭐ (explosive move)
```

### Pattern 4: Distribution (Top Signal)
```
Setup:
  Harga naik tapi SpotCVD mulai FLAT atau TURUN
  → FuturesCVD masih naik (retail FOMO)
  → OI naik tapi buying pressure lemah
  → OBDelta: Ask wall muncul di atas (smart money jual)

Entry: Short ketika harga break di bawah recent support
SL:    Di atas high
TP:    Level OI support di bawah

Warning: Jangan short terlalu cepat, tunggu KONFIRMASI break
Confidence: ⭐⭐⭐⭐
```

### Pattern 5: Funding Rate Extreme
```
Setup:
  Funding rate > +0.05% → Terlalu banyak LONG → expect dump
  Funding rate < -0.05% → Terlalu banyak SHORT → expect pump

Entry: Counter-trend ketika funding extreme + harga stall
SL:    Tight, di balik recent swing
TP:    Mean reversion ke funding normal

Note: Funding extreme BUKAN instant signal — bisa extreme lebih lama
      Combine dengan CVD divergence untuk timing
Confidence: ⭐⭐⭐
```

---

## 6. Top-Down Flow (5 Menit Pre-Trade)

### Step-by-Step Sebelum Setiap Trade:

```
MENIT 1: CEK MACRO
├── BTC dominance naik/turun?
├── Total market cap trend?
├── Ada news besar hari ini? (FOMC, CPI, dll)
└── Session apa sekarang? (Asia/London/NY)

MENIT 2: CEK HEATMAP
├── Di mana cluster liquidation terdekat? (atas & bawah)
├── Ada sandwich setup?
├── Sudah ada sweep baru-baru ini?
└── Magnet harga kemana?

MENIT 3: CEK FLOW DATA
├── SpotCVD: naik/turun/flat?
├── FuturesCVD: searah atau diverge?
├── OBDelta: siapa yang dominan?
└── Apakah 3 sinyal ini ALIGN?

MENIT 4: CEK OI & FUNDING
├── OI naik/turun/flat?
├── Combine dengan harga → skenario apa?
├── Funding rate normal atau extreme?
└── Ada tanda squeeze?

MENIT 5: KEPUTUSAN
├── LONG / SHORT / SKIP?
├── Entry level? (exact price)
├── SL level? (exact price, max 1-2% risk)
├── TP level? (min 1:2 RR)
└── Size? (max 5% portfolio per trade)
```

### Session Timing (WIB):

```
┌──────────────────────────────────────────────┐
│  ASIA SESSION     : 07:00 - 15:00 WIB       │
│  → Biasanya low volatility                   │
│  → Range-bound, good for scalp               │
│                                              │
│  LONDON SESSION   : 14:00 - 22:00 WIB       │
│  → Volatility mulai naik                     │
│  → Sering terjadi FAKE MOVE di awal          │
│  → Overlap Asia-London: 14:00-15:00          │
│                                              │
│  NEW YORK SESSION : 20:00 - 04:00 WIB       │
│  → HIGHEST volatility                        │
│  → Trend sesungguhnya terbentuk di sini      │
│  → Overlap London-NY: 20:00-22:00 = PRIME   │
│                                              │
│  ⭐ BEST TIME TO TRADE:                      │
│  → 20:00 - 23:00 WIB (London-NY overlap)    │
│  → 14:00 - 16:00 WIB (Asia-London overlap)  │
│                                              │
│  ❌ AVOID:                                   │
│  → 04:00 - 07:00 WIB (dead zone)            │
│  → Tepat saat news release (15 menit)        │
└──────────────────────────────────────────────┘
```

---

## 7. Common Traps

### Trap 1: Stale Data
```
❌ Masalah : Pakai data lama yang sudah tidak relevan
✅ Solusi  : Selalu refresh data sebelum entry
             Data > 5 menit = stale untuk scalping
             Data > 30 menit = stale untuk swing
```

### Trap 2: CVD Trap (False Divergence)
```
❌ Masalah : SpotCVD naik sedikit, langsung long
             Padahal itu cuma 1 big order, bukan trend
✅ Solusi  : Tunggu CVD SUSTAINED movement (min 3-5 candle)
             1 candle spike ≠ trend change
```

### Trap 3: SL Terlalu Ketat
```
❌ Masalah : SL 0.1% → kena terus padahal arah benar
✅ Solusi  : SL minimal di balik structure (support/resistance)
             Lebih baik size kecil + SL lebar
             Daripada size besar + SL ketat
```

### Trap 4: Trading Melawan Trend Besar
```
❌ Masalah : BTC bearish, tapi coba long altcoin
✅ Solusi  : Kalau BTC turun, 90% altcoin ikut turun
             SELALU cek BTC dulu sebelum trade apapun
             "BTC is the tide, alts are the boats"
```

### Trap 5: Overtrading Setelah Loss
```
❌ Masalah : Loss → emosi → revenge trade → loss lagi
✅ Solusi  : Max 3 consecutive losses → STOP trading hari itu
             Setiap loss, SIZE turun 50%
             Journaling: tulis KENAPA loss sebelum trade lagi
```

### Trap 6: Ignoring Funding Rate
```
❌ Masalah : Long di funding +0.1% → bayar mahal + crowded trade
✅ Solusi  : Funding extreme = RED FLAG
             Bukan berarti instant reversal
             Tapi berarti JANGAN tambah posisi searah crowd
```

---

## 8. Entry Checklist

### LONG Checklist (minimal 8/12 untuk entry)

```
FLOW CONFIRMATION:
[ ] 1. SpotCVD trending NAIK (min 3 candle)
[ ] 2. FuturesCVD naik ATAU diverge bearish (squeeze setup)
[ ] 3. OBDelta: Bid > Ask (buyer dominan)

STRUCTURE:
[ ] 4. Harga di atas support terdekat
[ ] 5. OI naik atau flat (bukan turun drastis)
[ ] 6. Funding rate BUKAN extreme positive (< +0.03%)

HEATMAP:
[ ] 7. Cluster liquidation besar di ATAS (magnet naik)
[ ] 8. Tidak ada cluster besar tepat di bawah (support aman)

RISK MANAGEMENT:
[ ] 9.  SL di bawah structure yang jelas
[ ] 10. Risk:Reward minimal 1:2
[ ] 11. Size max 5% portfolio
[ ] 12. Tidak dalam session dead zone (04:00-07:00 WIB)

SCORE: ___/12
→ 8-12: ✅ ENTRY
→ 6-7:  ⚠️ Reduced size
→ 0-5:  ❌ SKIP
```

### SHORT Checklist (minimal 8/12 untuk entry)

```
FLOW CONFIRMATION:
[ ] 1. SpotCVD trending TURUN (min 3 candle)
[ ] 2. FuturesCVD turun ATAU diverge bullish (squeeze setup)
[ ] 3. OBDelta: Ask > Bid (seller dominan)

STRUCTURE:
[ ] 4. Harga di bawah resistance terdekat
[ ] 5. OI naik atau flat (bukan turun drastis)
[ ] 6. Funding rate BUKAN extreme negative (> -0.03%)

HEATMAP:
[ ] 7. Cluster liquidation besar di BAWAH (magnet turun)
[ ] 8. Tidak ada cluster besar tepat di atas (resistance aman)

RISK MANAGEMENT:
[ ] 9.  SL di atas structure yang jelas
[ ] 10. Risk:Reward minimal 1:2
[ ] 11. Size max 5% portfolio
[ ] 12. Tidak dalam session dead zone (04:00-07:00 WIB)

SCORE: ___/12
→ 8-12: ✅ ENTRY
→ 6-7:  ⚠️ Reduced size
→ 0-5:  ❌ SKIP
```

### SKIP Conditions (Auto-skip kalau salah satu terpenuhi)

```
🚫 WAJIB SKIP kalau:
   [ ] SpotCVD dan FutCVD berlawanan DAN OBDelta netral
   [ ] OI turun drastis (deleveraging aktif)
   [ ] Funding rate > +0.08% atau < -0.08%
   [ ] 15 menit sebelum/sesudah major news
   [ ] Sudah 3x loss hari ini
   [ ] Heatmap sandwich dan belum ada sweep
   [ ] Tidak bisa tentukan SL yang jelas
   [ ] Emosi tidak stabil (marah/euforia/takut)
```

---

## QUICK REFERENCE CARD

```
╔══════════════════════════════════════════════════╗
║           RICOZ TRADING RULES                    ║
╠══════════════════════════════════════════════════╣
║                                                  ║
║  1. SpotCVD = KING. Always follow spot.          ║
║  2. Divergence = Opportunity, not confusion.      ║
║  3. Heatmap magnet > your opinion.               ║
║  4. OI + Price = Context. Never trade blind.      ║
║  5. Sweep first, entry second.                   ║
║  6. No alignment = No trade. Period.             ║
║  7. Max 3 losses/day. Walk away.                 ║
║  8. Size down after loss, not up.                ║
║  9. Best trades feel boring, not exciting.       ║
║  10. The market will be here tomorrow.           ║
║                                                  ║
╚══════════════════════════════════════════════════╝
```

---

*Cheatsheet dibuat: 2026-03-23*
*Berdasarkan: Ricoz Trading Framework + Live Session Analysis*
