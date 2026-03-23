# BLUEPRINT: CoinGlass MCP Server untuk Claude

**Project**: coinglass-mcp
**Tujuan**: Query CoinGlass langsung dari Claude (seperti LunarCrush MCP)
**Stack**: Python + FastMCP + Streamable HTTP
**Deploy**: VPS Ubuntu 24.04

---

## PROGRESS TRACKER

| Phase | Nama | Status | Estimasi |
|-------|------|--------|----------|
| Phase 1 | Research & Keputusan | ✅ | 15 min |
| Phase 2 | Setup Project | ✅ | 30 min |
| Phase 3 | Implement Core | ✅ | 60-90 min |
| Phase 4 | Implement Tools | ✅ | 60-90 min |
| Phase 5 | HTTP Transport | ✅ | 30 min |
| Phase 6 | Deploy ke VPS | ⬜ | 30 min |
| Phase 7 | Connect ke Claude | ⬜ | 15 min |
| Phase 8 | Test & Validate | ⬜ | 30 min |

---

## PHASE 1: RESEARCH & KEPUTUSAN (15 min)

### 1.1 Pilih Approach

⬜ **OPSI A (Recommended): Fork forgequant/coinglass-mcp**
- Sudah ada 22 tools → 80+ endpoints
- Python + FastMCP, tested (45 tests pass)
- Caching 60s, retry logic built-in
- Tinggal tambah HTTP transport + deploy

⬜ **OPSI B: Custom Build dari Nol**
- Full kontrol, optimized untuk Ricoz Scalping Architecture
- Bisa bikin `full_analysis(coin)` = 1 call semua data
- Lebih banyak kerja tapi lebih fit

⬜ **OPSI C: CoinGlass Official MCP (Beta)**
- Paling simple tapi mungkin limited/belum stabil

### 1.2 Keputusan yang Perlu Diambil

⬜ CoinGlass API plan kamu apa? (hobbyist/startup/standard/professional)
⬜ API key sudah ada?
⬜ VPS mana yang mau dipake? (yang sama dengan TELEGLAS Pro?)
⬜ Python version di VPS? (minimal 3.10+)

---

## PHASE 2: SETUP PROJECT (30 min)

### 2.1 Clone & Setup (Opsi A - Fork)

```bash
git clone https://github.com/forgequant/coinglass-mcp.git
cd coinglass-mcp
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

⬜ Clone berhasil
⬜ Virtual env aktif
⬜ Dependencies terinstall

### 2.2 Setup (Opsi B - Custom Build)

```bash
mkdir coinglass-mcp && cd coinglass-mcp
python3 -m venv .venv
source .venv/bin/activate
pip install fastmcp httpx pydantic uvicorn
```

Struktur project:
```
coinglass-mcp/
├── src/coinglass_mcp/
│   ├── __init__.py
│   ├── server.py       # FastMCP server + tools
│   ├── client.py       # CoinGlass API client (httpx)
│   ├── config.py       # API key, plan tier, rate limits
│   └── models.py       # Pydantic response models
├── tests/
│   └── test_tools.py
├── .env                # COINGLASS_API_KEY=xxx
├── pyproject.toml
└── README.md
```

⬜ Project structure created
⬜ Dependencies installed

### 2.3 Environment Variables

```bash
# .env file
COINGLASS_API_KEY="your-api-key-here"
COINGLASS_PLAN="standard"
MCP_HOST="0.0.0.0"
MCP_PORT=8787
```

⬜ .env file configured

---

## PHASE 3: IMPLEMENT CORE (60-90 min)

### 3.1 API Client (`client.py`)

⬜ HTTP client dengan httpx.AsyncClient
⬜ Auth header: `CG-API-KEY: {api_key}`
⬜ Base URL: `https://open-api-v4.coinglass.com`
⬜ Retry logic (3x retry pada 5xx/timeout)
⬜ Response caching (60s TTL)
⬜ Rate limit handling (sesuai plan)
⬜ Error handling dengan pesan actionable

### 3.2 Config (`config.py`)

⬜ Plan tier definitions (rate limits per plan)
⬜ Endpoint access per plan
⬜ Interval mappings (1m, 5m, 15m, 1h, 4h, 1d)
⬜ Default parameters

### 3.3 Base Server (`server.py`)

```python
from fastmcp import FastMCP

mcp = FastMCP(
    name="coinglass-mcp",
    description="CoinGlass crypto derivatives analytics for order flow trading"
)
```

⬜ FastMCP server initialized
⬜ Lifespan pattern (shared httpx client)

---

## PHASE 4: IMPLEMENT TOOLS (60-90 min)

### 4.1 Priority Tools (Sesuai Ricoz Scalping Framework)

**CRITICAL — Harus ada:**

| # | Tool Name | CoinGlass Endpoint | Fungsi |
|---|-----------|-------------------|--------|
| 1 | `coinglass_spot_cvd` | `/api/spot/cvd-history` | **SpotCVD** (VETO SIGNAL UTAMA) |
| 2 | `coinglass_futures_cvd` | `/api/futures/aggregated-cvd-history` | **FutCVD** (entry filter) |
| 3 | `coinglass_funding_rate` | `/api/futures/funding-rate/exchange-list` | **FR** current semua exchange |
| 4 | `coinglass_open_interest` | `/api/futures/oi-ohlc-aggregated-history` | **OI** aggregated history |
| 5 | `coinglass_liquidation_map` | `/api/futures/liquidation/aggregated-map` | **Liquidation heatmap** data |
| 6 | `coinglass_orderbook` | `/api/futures/aggregated-orderbook-history` | **OBDelta** (orderbook bid/ask) |

⬜ Tool 1: coinglass_spot_cvd
⬜ Tool 2: coinglass_futures_cvd
⬜ Tool 3: coinglass_funding_rate
⬜ Tool 4: coinglass_open_interest
⬜ Tool 5: coinglass_liquidation_map
⬜ Tool 6: coinglass_orderbook

**IMPORTANT — Sangat berguna:**

| # | Tool Name | Endpoint | Fungsi |
|---|-----------|----------|--------|
| 7 | `coinglass_price_ohlc` | `/api/futures/price/ohlc-history` | Price + bisa hitung EMA |
| 8 | `coinglass_liquidation_history` | `/api/futures/liquidation/aggregated-history` | Liq history (volume) |
| 9 | `coinglass_long_short_ratio` | `/api/futures/global-longshort-account-ratio` | L/S ratio |
| 10 | `coinglass_taker_buysell` | `/api/futures/taker-buysell-volume` | Taker aggressor |
| 11 | `coinglass_fr_arbitrage` | `/api/futures/funding-rate/arbitrage` | FR paling extreme |
| 12 | `coinglass_coins_markets` | `/api/futures/coins-markets` | Market overview |

⬜ Tool 7-12 implemented

**NICE TO HAVE:**

| # | Tool Name | Endpoint | Fungsi |
|---|-----------|----------|--------|
| 13 | `coinglass_whale_alert` | `/api/hyperliquid/whale-alert` | Whale positions |
| 14 | `coinglass_indicators` | `/api/index/fear-greed-history` | Fear & Greed |
| 15 | `coinglass_footprint` | `/api/futures/footprint` | Footprint data (90d) |
| 16 | `coinglass_spot_netflow` | spot netflow endpoints | Spot exchange flow |
| 17 | `coinglass_orderbook_heatmap` | `/api/futures/orderbook-heatmap` | OB heatmap visual |

⬜ Tool 13-17 (optional, bisa ditambah nanti)

**CUSTOM WORKFLOW TOOL (Ricoz Special):**

| # | Tool Name | Fungsi |
|---|-----------|--------|
| 18 | `coinglass_full_scan` | ALL-IN-ONE: SpotCVD + FutCVD + FR + OI + OBDelta + Liq + Price dalam 1 call |

⬜ Tool 18: coinglass_full_scan (composite tool)

### 4.2 Tool Implementation Pattern

```python
@mcp.tool()
async def coinglass_spot_cvd(
    symbol: str = "BTC",
    interval: str = "5m",
    limit: int = 100
) -> str:
    """Get Spot CVD (Cumulative Volume Delta) - PRIMARY VETO SIGNAL.

    SpotCVD positive = spot buyers dominant (bullish).
    SpotCVD negative = DO NOT LONG regardless of other signals.
    Look at DIRECTION of line, not just absolute number.

    Args:
        symbol: Coin symbol (BTC, ETH, SOL, etc.)
        interval: Candle interval
        limit: Number of data points
    """
    data = await client.get("/api/spot/cvd-history", {
        "symbol": symbol,
        "interval": interval,
        "limit": limit
    })
    return format_cvd_response(data, "spot")
```

⬜ Tool pattern established
⬜ Description includes trading context

---

## PHASE 5: HTTP TRANSPORT (30 min)

### 5.1 Kenapa Perlu HTTP Transport?

- claude.ai hanya support **remote MCP** via URL
- Default FastMCP = stdio (lokal only, untuk Claude Desktop)
- Perlu expose via **Streamable HTTP** supaya claude.ai bisa connect

### 5.2 Add HTTP Server

```python
# server.py - tambahkan di akhir
if __name__ == "__main__":
    # Untuk HTTP (claude.ai remote)
    mcp.run(transport="streamable-http", host="0.0.0.0", port=8787)
```

### 5.3 Alternative: FastAPI Wrapper

```python
# http_server.py
from fastapi import FastAPI
from coinglass_mcp.server import mcp

app = FastAPI()
app.mount("/mcp", mcp.get_asgi_app())

@app.get("/health")
async def health():
    return {"status": "ok", "service": "coinglass-mcp"}
```

⬜ HTTP transport configured
⬜ Health check endpoint works

---

## PHASE 6: DEPLOY KE VPS (30 min)

### 6.1 Upload ke VPS

```bash
scp -r coinglass-mcp/ user@vps-ip:/home/user/
```

⬜ Code uploaded ke VPS

### 6.2 Setup di VPS

```bash
cd /home/user/coinglass-mcp
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
nano .env  # isi API key
```

⬜ Dependencies installed di VPS
⬜ .env configured

### 6.3 Run dengan PM2

```bash
npm install -g pm2

cat > ecosystem.config.js << 'EOF'
module.exports = {
  apps: [{
    name: "coinglass-mcp",
    script: ".venv/bin/python",
    args: "-m coinglass_mcp.server",
    cwd: "/home/user/coinglass-mcp",
    env: {
      COINGLASS_API_KEY: "your-key",
      COINGLASS_PLAN: "standard",
      MCP_PORT: "8787"
    },
    max_memory_restart: "200M",
    autorestart: true,
    watch: false
  }]
};
EOF

pm2 start ecosystem.config.js
pm2 save
pm2 startup
```

⬜ PM2 ecosystem created
⬜ Server running via PM2
⬜ Auto-restart configured

### 6.4 Reverse Proxy (Nginx) — OPTIONAL tapi Recommended

```nginx
server {
    listen 443 ssl;
    server_name mcp.yourdomain.com;

    ssl_certificate /etc/letsencrypt/live/yourdomain.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/yourdomain.com/privkey.pem;

    location / {
        proxy_pass http://127.0.0.1:8787;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

⬜ Nginx configured (optional)
⬜ SSL certificate (optional)

### 6.5 Verify Server Running

```bash
curl http://localhost:8787/health
curl -X POST http://localhost:8787/mcp \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","method":"tools/list","id":1}'
```

⬜ Health check returns OK
⬜ MCP tools/list returns tool list

---

## PHASE 7: CONNECT KE CLAUDE.AI (15 min)

### 7.1 Add MCP Connector

1. Buka **claude.ai** → **Settings** (gear icon)
2. Scroll ke **Integrations** / **Connectors**
3. Klik **"Add MCP Server"**
4. Masukkan URL: `https://mcp.yourdomain.com/mcp` (atau `http://VPS_IP:8787/mcp`)
5. Save & authorize

⬜ MCP connector added di claude.ai

### 7.2 Verify di Chat Baru

```
Cek apakah CoinGlass MCP sudah terhubung. List semua tools yang available.
```

⬜ Tools muncul di claude.ai
⬜ Test query: "Cek SpotCVD SOL 5m"

---

## PHASE 8: TEST & VALIDATE (30 min)

### 8.1 Test Setiap Tool

```
1. "Cek SpotCVD BTC interval 5m"
2. "Berapa funding rate SOL sekarang?"
3. "Cek OI history HYPE 1h terakhir 24 jam"
4. "Ada liquidation cluster di mana untuk SOL?"
5. "Full scan AVAX" (composite tool)
```

⬜ SpotCVD query works
⬜ FutCVD query works
⬜ FR query works
⬜ OI query works
⬜ Liquidation query works
⬜ Orderbook query works
⬜ Full scan works

### 8.2 Test Trading Workflow

```
"Analisa SOL untuk scalp sekarang,
gunakan CoinGlass data untuk SpotCVD, FutCVD, FR, OI, dan liquidation map"
```

⬜ Claude bisa pull semua data dan kasih analysis sesuai framework

### 8.3 Performance Check

⬜ Response time < 5 detik per tool call
⬜ No rate limit errors
⬜ Cache working (2nd call faster)
⬜ PM2 stable, no restart loops

---

## ENDPOINT REFERENCE (CoinGlass API V4)

Base URL: `https://open-api-v4.coinglass.com`
Auth Header: `CG-API-KEY: {your_key}`

### Futures Endpoints

| Category | Endpoint | Method |
|----------|----------|--------|
| **Price** | `/api/futures/price/ohlc-history` | GET |
| **OI** | `/api/futures/oi-ohlc-aggregated-history` | GET |
| **OI Exchange** | `/api/futures/oi-exchange-list` | GET |
| **FR** | `/api/futures/funding-rate/exchange-list` | GET |
| **FR Arbitrage** | `/api/futures/funding-rate/arbitrage` | GET |
| **L/S Ratio** | `/api/futures/global-longshort-account-ratio` | GET |
| **Liquidation** | `/api/futures/liquidation/aggregated-history` | GET |
| **Liq Heatmap** | `/api/futures/liquidation/aggregated-heatmap` | GET |
| **Liq Map** | `/api/futures/liquidation/aggregated-map` | GET |
| **Orderbook** | `/api/futures/aggregated-orderbook-history` | GET |
| **OB Heatmap** | `/api/futures/orderbook-heatmap` | GET |
| **Taker B/S** | `/api/futures/taker-buysell-volume` | GET |
| **Futures CVD** | `/api/futures/aggregated-cvd-history` | GET |
| **Footprint** | `/api/futures/footprint` | GET |
| **Whale** | `/api/hyperliquid/whale-alert` | GET |

### Spot Endpoints

| Category | Endpoint | Method |
|----------|----------|--------|
| **Spot CVD** | `/api/spot/cvd-history` | GET |
| **Spot Markets** | `/api/spot/coins-markets` | GET |
| **Spot Orderbook** | `/api/spot/aggregated-orderbook-history` | GET |

### Indicators

| Category | Endpoint | Method |
|----------|----------|--------|
| **Fear & Greed** | `/api/index/fear-greed-history` | GET |

---

## DECISION LOG

| Tanggal | Keputusan | Alasan |
|---------|-----------|--------|
| 2026-03-23 | Blueprint dibuat | Roadmap lengkap 8 phase |
| 2026-03-23 | Approach: **A Modified** (custom build inspired by forgequant) | 80% code reuse, full kontrol, FastMCP 3.1.1 |
| 2026-03-23 | CoinGlass Official MCP: **TIDAK ADA** | Riset konfirmasi tidak ada official MCP |
| 2026-03-23 | Language: Python 3.11 | FastMCP 3.1.1 native support |
| 2026-03-23 | Transport: Streamable HTTP | claude.ai remote access |
| 2026-03-23 | Deploy: PM2 on Hostinger VPS | Consistent with TELEGLAS setup |
| 2026-03-23 | Plan: Standard ($299/mo) | Langganan setelah system siap |
| 2026-03-23 | Phase 1-5 COMPLETE | 18 tools, 12 tests pass, HTTP ready |

---

## KNOWN ISSUES & TIPS

1. **Spot CVD endpoint** — Verify exact path di CoinGlass docs, bisa beda dari Futures CVD
2. **Rate limits** — Varies per plan. Hobbyist = 10 req/min, Standard = 30 req/min
3. **Caching penting** — 60s cache prevents hitting rate limits saat Claude multi-tool call
4. **claude.ai MCP** — Mungkin perlu HTTPS (bukan HTTP). Kalau gak punya domain, bisa pakai Cloudflare Tunnel
5. **Footprint data** — Hanya 90 hari history
6. **Liquidation heatmap** — Ada 3 model, Model1 paling umum
7. **FastMCP vs manual** — FastMCP handle JSON-RPC, tool registration, error handling otomatis

---

*Blueprint dibuat: 2026-03-23*
*Terakhir diupdate: 2026-03-23*
