# McPCG - MCP Coinglass Server

MCP (Model Context Protocol) server yang menghubungkan **Claude AI** dengan **Coinglass API** untuk analisis data crypto real-time.

## Apa Ini?

McPCG adalah jembatan antara Claude dan Coinglass. Dengan ini, Claude bisa langsung:
- Cek **funding rate** semua exchange
- Lihat **open interest** dan **liquidation** data
- Analisis **long/short ratio** dan **buy/sell pressure**
- Monitor **exchange inflow/outflow**
- Gabungkan data market structure + sentiment (via LunarCrush)

## Arsitektur

```
Claude AI ←→ McPCG (MCP Server) ←→ Coinglass API
                                 ←→ LunarCrush API (Fase 6)
```

## Tools yang Tersedia

### Divisi A: Market Data
| Tool | Fungsi |
|------|--------|
| `CG_Pairs` | Daftar koin & pair yang tersedia |
| `CG_Price` | Harga & market overview |

### Divisi B: Futures
| Tool | Fungsi |
|------|--------|
| `CG_FundingRate` | Funding rate semua exchange |
| `CG_OpenInterest` | Open interest aggregated |
| `CG_Liquidation` | Data liquidation |

### Divisi C: Indikator
| Tool | Fungsi |
|------|--------|
| `CG_LongShortRatio` | Long/Short ratio global |
| `CG_BuySell` | Buy/Sell pressure (taker) |
| `CG_FearGreed` | Fear & Greed index |

### Divisi D: Exchange
| Tool | Fungsi |
|------|--------|
| `CG_ExchangeFlow` | Exchange inflow/outflow |
| `CG_Holdings` | Grayscale/ETF holdings |

## Quick Start

### 1. Clone & Install
```bash
git clone https://github.com/rcz87/McPCG.git
cd McPCG
npm install
```

### 2. Setup API Key
```bash
cp .env.example .env
# Edit .env dan masukkan COINGLASS_API_KEY
```

### 3. Build & Run
```bash
npm run build
npm start
```

### 4. Koneksi ke Claude Desktop
Tambahkan ke `claude_desktop_config.json`:
```json
{
  "mcpServers": {
    "coinglass": {
      "command": "node",
      "args": ["/path/to/McPCG/dist/index.js"],
      "env": {
        "COINGLASS_API_KEY": "your-api-key"
      }
    }
  }
}
```

## Development Progress

Lihat [BLUEPRINT.md](./BLUEPRINT.md) untuk roadmap lengkap dan progress tracker.

| Fase | Status |
|------|--------|
| Fase 0: Persiapan | Belum mulai |
| Fase 1: Fondasi MCP Server | Belum mulai |
| Fase 2: Core Tools | Belum mulai |
| Fase 3: Advanced Tools | Belum mulai |
| Fase 4: Testing | Belum mulai |
| Fase 5: Deploy | Belum mulai |
| Fase 6: Divisional Map | Belum mulai |

## Tech Stack

- **Runtime**: Node.js + TypeScript
- **MCP SDK**: `@modelcontextprotocol/sdk`
- **HTTP Client**: `axios`
- **Validation**: `zod`
- **API**: Coinglass Open API v3

## Struktur Folder

```
McPCG/
├── BLUEPRINT.md          # Roadmap & progress tracker
├── README.md             # Dokumentasi ini
├── src/
│   ├── index.ts          # Entry point MCP server
│   ├── config.ts         # Konfigurasi
│   ├── api/              # HTTP client & caching
│   ├── tools/            # Semua MCP tools (per divisi)
│   │   ├── market/
│   │   ├── futures/
│   │   ├── indicator/
│   │   └── exchange/
│   ├── utils/            # Helper functions
│   └── types/            # TypeScript type definitions
└── tests/                # Unit & integration tests
```

## Lisensi

MIT
