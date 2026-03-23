# McPCG - MCP Coinglass Server Blueprint
## (Model Context Protocol for Coinglass API → Claude AI)

---

## DAFTAR ISI

- [FASE 0: Persiapan](#fase-0-persiapan)
- [FASE 1: Fondasi MCP Server](#fase-1-fondasi-mcp-server)
- [FASE 2: Core Tools - Data Pasar](#fase-2-core-tools---data-pasar)
- [FASE 3: Advanced Tools - Analisis](#fase-3-advanced-tools---analisis)
- [FASE 4: Integrasi & Testing](#fase-4-integrasi--testing)
- [FASE 5: Deploy & Koneksi ke Claude](#fase-5-deploy--koneksi-ke-claude)
- [FASE 6: Divisional Map System](#fase-6-divisional-map-system)
- [PROGRESS TRACKER](#progress-tracker)

---

## ARSITEKTUR SISTEM

```
┌─────────────────────────────────────────────────────┐
│                    CLAUDE AI                        │
│              (Chat / Claude Desktop)                │
└──────────────────────┬──────────────────────────────┘
                       │ MCP Protocol (stdio/SSE)
                       ▼
┌─────────────────────────────────────────────────────┐
│              McPCG - MCP SERVER                     │
│  ┌───────────┐ ┌───────────┐ ┌───────────────────┐ │
│  │ Transport │ │  Router   │ │  Tool Registry    │ │
│  │ (stdio)   │ │           │ │                   │ │
│  └───────────┘ └───────────┘ └───────────────────┘ │
│  ┌─────────────────────────────────────────────┐   │
│  │              TOOLS (Divisi)                 │   │
│  │  ┌─────────┐ ┌──────────┐ ┌─────────────┐  │   │
│  │  │ Market  │ │ Futures  │ │  Exchange   │  │   │
│  │  │ Data    │ │ Data     │ │  Flow       │  │   │
│  │  ├─────────┤ ├──────────┤ ├─────────────┤  │   │
│  │  │Funding  │ │Liquidasi │ │ Long/Short  │  │   │
│  │  │Rate     │ │          │ │ Ratio       │  │   │
│  │  └─────────┘ └──────────┘ └─────────────┘  │   │
│  └─────────────────────────────────────────────┘   │
│  ┌─────────────────────────────────────────────┐   │
│  │           CACHE & CONFIG                    │   │
│  └─────────────────────────────────────────────┘   │
└──────────────────────┬──────────────────────────────┘
                       │ HTTPS REST API
                       ▼
┌─────────────────────────────────────────────────────┐
│              COINGLASS API                          │
│         https://open-api-v3.coinglass.com           │
└─────────────────────────────────────────────────────┘
```

---

## STRUKTUR FOLDER (Target Akhir)

```
McPCG/
├── BLUEPRINT.md              ← Dokumen ini (roadmap)
├── PROGRESS.md               ← Tracking progress harian
├── package.json
├── tsconfig.json
├── .env.example              ← Template environment variables
├── .gitignore
│
├── src/
│   ├── index.ts              ← Entry point MCP server
│   ├── config.ts             ← Konfigurasi & environment
│   │
│   ├── transport/
│   │   └── stdio.ts          ← MCP transport layer
│   │
│   ├── api/
│   │   ├── client.ts         ← HTTP client ke Coinglass
│   │   ├── endpoints.ts      ← Daftar endpoint Coinglass
│   │   └── cache.ts          ← Response caching
│   │
│   ├── tools/
│   │   ├── registry.ts       ← Tool registration system
│   │   │
│   │   ├── market/           ← DIVISI: Market Data
│   │   │   ├── index.ts
│   │   │   ├── price.ts
│   │   │   └── pairs.ts
│   │   │
│   │   ├── futures/          ← DIVISI: Futures
│   │   │   ├── index.ts
│   │   │   ├── funding-rate.ts
│   │   │   ├── open-interest.ts
│   │   │   └── liquidation.ts
│   │   │
│   │   ├── indicator/        ← DIVISI: Indikator
│   │   │   ├── index.ts
│   │   │   ├── long-short-ratio.ts
│   │   │   ├── buy-sell.ts
│   │   │   └── fear-greed.ts
│   │   │
│   │   └── exchange/         ← DIVISI: Exchange
│   │       ├── index.ts
│   │       ├── flow.ts
│   │       └── holdings.ts
│   │
│   ├── utils/
│   │   ├── formatter.ts      ← Format response untuk Claude
│   │   ├── validator.ts      ← Validasi input
│   │   └── error-handler.ts  ← Error handling
│   │
│   └── types/
│       ├── coinglass.ts      ← Type dari Coinglass API
│       ├── mcp.ts            ← Type MCP protocol
│       └── tools.ts          ← Type untuk tools
│
├── tests/
│   ├── api/
│   ├── tools/
│   └── integration/
│
└── docs/
    └── divisional-map.md     ← Peta divisi & penjelasan
```

---

## FASE 0: Persiapan
**Status: [ ] Belum Mulai**

### Tujuan:
Setup project dasar, pastikan semua kebutuhan tersedia.

### Langkah:
| # | Task | File | Status |
|---|------|------|--------|
| 0.1 | Inisialisasi project Node.js + TypeScript | `package.json`, `tsconfig.json` | [ ] |
| 0.2 | Setup .gitignore | `.gitignore` | [ ] |
| 0.3 | Buat .env.example dengan template API key | `.env.example` | [ ] |
| 0.4 | Install dependencies (MCP SDK, axios, zod) | `package.json` | [ ] |
| 0.5 | Setup build & dev scripts | `package.json` | [ ] |

### Dependencies:
```
@modelcontextprotocol/sdk   → MCP SDK resmi
axios                       → HTTP client
zod                         → Validasi schema
dotenv                      → Environment variables
typescript                  → TypeScript compiler
```

### Selesai ketika:
- `npm run build` berhasil tanpa error
- File kosong `src/index.ts` bisa di-compile

---

## FASE 1: Fondasi MCP Server
**Status: [ ] Belum Mulai**

### Tujuan:
MCP server bisa berjalan dan dikenali Claude, meski belum ada tools.

### Langkah:
| # | Task | File | Status |
|---|------|------|--------|
| 1.1 | Buat MCP server dasar dengan stdio transport | `src/index.ts` | [ ] |
| 1.2 | Setup konfigurasi & env loader | `src/config.ts` | [ ] |
| 1.3 | Buat HTTP client untuk Coinglass API | `src/api/client.ts` | [ ] |
| 1.4 | Definisikan daftar endpoint Coinglass | `src/api/endpoints.ts` | [ ] |
| 1.5 | Buat tool registration system | `src/tools/registry.ts` | [ ] |
| 1.6 | Buat error handler | `src/utils/error-handler.ts` | [ ] |
| 1.7 | Buat type definitions | `src/types/*.ts` | [ ] |

### Selesai ketika:
- MCP server bisa start via `npm start`
- Server merespon `initialize` request dari MCP protocol
- API client bisa ping Coinglass (dengan API key)

---

## FASE 2: Core Tools - Data Pasar
**Status: [ ] Belum Mulai**

### Tujuan:
Tools utama untuk data pasar tersedia dan bisa dipanggil Claude.

### DIVISI A: Market Data
| # | Task | Tool Name | File | Status |
|---|------|-----------|------|--------|
| 2.1 | Daftar koin & pair yang tersedia | `CG_Pairs` | `src/tools/market/pairs.ts` | [ ] |
| 2.2 | Harga & market overview | `CG_Price` | `src/tools/market/price.ts` | [ ] |

### DIVISI B: Futures Data
| # | Task | Tool Name | File | Status |
|---|------|-----------|------|--------|
| 2.3 | Funding rate semua exchange | `CG_FundingRate` | `src/tools/futures/funding-rate.ts` | [ ] |
| 2.4 | Open interest aggregated | `CG_OpenInterest` | `src/tools/futures/open-interest.ts` | [ ] |
| 2.5 | Data liquidation | `CG_Liquidation` | `src/tools/futures/liquidation.ts` | [ ] |

### Selesai ketika:
- Semua 5 tools terdaftar di MCP server
- Claude bisa panggil setiap tool dan dapat data yang benar
- Response terformat rapi untuk dibaca Claude

---

## FASE 3: Advanced Tools - Analisis
**Status: [ ] Belum Mulai**

### Tujuan:
Tools analisis lanjutan untuk insight yang lebih dalam.

### DIVISI C: Indikator
| # | Task | Tool Name | File | Status |
|---|------|-----------|------|--------|
| 3.1 | Long/Short ratio global | `CG_LongShortRatio` | `src/tools/indicator/long-short-ratio.ts` | [ ] |
| 3.2 | Buy/Sell pressure (taker) | `CG_BuySell` | `src/tools/indicator/buy-sell.ts` | [ ] |
| 3.3 | Fear & Greed index (jika ada) | `CG_FearGreed` | `src/tools/indicator/fear-greed.ts` | [ ] |

### DIVISI D: Exchange
| # | Task | Tool Name | File | Status |
|---|------|-----------|------|--------|
| 3.4 | Exchange inflow/outflow | `CG_ExchangeFlow` | `src/tools/exchange/flow.ts` | [ ] |
| 3.5 | Grayscale/ETF holdings | `CG_Holdings` | `src/tools/exchange/holdings.ts` | [ ] |

### Selesai ketika:
- Semua 5 tools tambahan berfungsi
- Total 10 tools tersedia di MCP server

---

## FASE 4: Integrasi & Testing
**Status: [ ] Belum Mulai**

### Tujuan:
Pastikan semua tools stabil, ada caching, dan error handling solid.

### Langkah:
| # | Task | File | Status |
|---|------|------|--------|
| 4.1 | Implementasi response cache (TTL-based) | `src/api/cache.ts` | [ ] |
| 4.2 | Rate limiting untuk API Coinglass | `src/api/client.ts` | [ ] |
| 4.3 | Unit test untuk setiap tool | `tests/tools/*.test.ts` | [ ] |
| 4.4 | Integration test end-to-end | `tests/integration/*.test.ts` | [ ] |
| 4.5 | Response formatter (clean output) | `src/utils/formatter.ts` | [ ] |

### Selesai ketika:
- Semua test pass
- Cache berfungsi (tidak spam API)
- Error handling graceful (tidak crash)

---

## FASE 5: Deploy & Koneksi ke Claude
**Status: [ ] Belum Mulai**

### Tujuan:
MCP server bisa dipakai di Claude Desktop / Claude AI.

### Langkah:
| # | Task | File | Status |
|---|------|------|--------|
| 5.1 | Buat config untuk Claude Desktop | `claude_desktop_config.json` | [ ] |
| 5.2 | Dokumentasi cara install & setup | `README.md` | [ ] |
| 5.3 | Build final & test koneksi | - | [ ] |
| 5.4 | Test semua tools via Claude chat | - | [ ] |

### Config Claude Desktop (preview):
```json
{
  "mcpServers": {
    "coinglass": {
      "command": "node",
      "args": ["path/to/McPCG/dist/index.js"],
      "env": {
        "COINGLASS_API_KEY": "your-api-key-here"
      }
    }
  }
}
```

### Selesai ketika:
- Claude Desktop bisa detect MCP server
- Semua tools muncul di Claude dan bisa digunakan
- Data real-time dari Coinglass tampil di chat

---

## FASE 6: Divisional Map System
**Status: [ ] Belum Mulai**

### Tujuan:
Sistem "MAP" yang menghubungkan data dari berbagai divisi + sinkronisasi dengan cloud (LunarCrush MCP).

### Konsep Divisional Map:
```
┌──────────────────────────────────────────────┐
│              DIVISIONAL MAP                  │
│                                              │
│   ┌──────────┐    ┌──────────┐              │
│   │COINGLASS │    │LUNAR     │              │
│   │MCP       │◄──►│CRUSH MCP │              │
│   │(Market   │    │(Sentiment│              │
│   │Structure)│    │ & Social)│              │
│   └────┬─────┘    └────┬─────┘              │
│        │               │                    │
│        ▼               ▼                    │
│   ┌─────────────────────────┐               │
│   │    COMBINED ANALYSIS    │               │
│   │  • Market + Sentiment   │               │
│   │  • Technical + Social   │               │
│   │  • Risk Assessment      │               │
│   └─────────────────────────┘               │
│        │                                    │
│        ▼                                    │
│   ┌─────────────────────────┐               │
│   │    CLOUD SYNC           │               │
│   │  • Save analysis state  │               │
│   │  • Historical tracking  │               │
│   │  • Alert system         │               │
│   └─────────────────────────┘               │
└──────────────────────────────────────────────┘
```

### Langkah:
| # | Task | File | Status |
|---|------|------|--------|
| 6.1 | Design map protocol antar divisi | `docs/divisional-map.md` | [ ] |
| 6.2 | Buat combined analysis tool | `src/tools/map/analysis.ts` | [ ] |
| 6.3 | Integrasi query LunarCrush + Coinglass | `src/tools/map/combined.ts` | [ ] |
| 6.4 | Cloud sync state management | `src/tools/map/sync.ts` | [ ] |
| 6.5 | Alert & notification system | `src/tools/map/alerts.ts` | [ ] |

### Selesai ketika:
- Claude bisa query kedua MCP sekaligus
- Analisis gabungan (market structure + sentiment) berfungsi
- State tersimpan dan bisa di-recall

---

## PROGRESS TRACKER

### Overview
| Fase | Nama | Tasks | Selesai | Persen |
|------|------|-------|---------|--------|
| 0 | Persiapan | 5 | 0 | 0% |
| 1 | Fondasi MCP | 7 | 0 | 0% |
| 2 | Core Tools | 5 | 0 | 0% |
| 3 | Advanced Tools | 5 | 0 | 0% |
| 4 | Testing | 5 | 0 | 0% |
| 5 | Deploy | 4 | 0 | 0% |
| 6 | Map System | 5 | 0 | 0% |
| **TOTAL** | | **36** | **0** | **0%** |

### Log Perubahan
| Tanggal | Fase | Task | Keterangan |
|---------|------|------|------------|
| 2026-03-23 | - | - | Blueprint dibuat |

---

## CATATAN PENTING

1. **API Key Coinglass** diperlukan sebelum memulai Fase 1
2. Setiap fase harus **selesai 100%** sebelum lanjut ke fase berikutnya
3. Blueprint ini akan di-update setiap kali ada progress
4. Jika ada perubahan rencana, catat di Log Perubahan
5. Setiap tool yang dibuat harus langsung di-test sebelum lanjut

---

*Blueprint dibuat: 2026-03-23*
*Terakhir diupdate: 2026-03-23*
