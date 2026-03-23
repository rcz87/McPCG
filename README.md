# CoinGlass MCP Server

MCP Server yang menghubungkan **Claude AI** dengan **CoinGlass API** untuk analisis crypto derivatives real-time — dioptimasi untuk **Ricoz Scalping Framework**.

## Arsitektur

```
Claude AI (claude.ai) ←── Streamable HTTP ──→ CoinGlass MCP Server (VPS)
                                                      │
                                                      ↓
                                              CoinGlass API V4
```

## 18 Tools

### Critical (Ricoz Scalping Core)
| Tool | Endpoint | Fungsi |
|------|----------|--------|
| `coinglass_spot_cvd` | Spot Aggregated CVD | **VETO SIGNAL** — SpotCVD negatif = jangan long |
| `coinglass_futures_cvd` | Futures Aggregated CVD | Entry filter — konfirmasi arah |
| `coinglass_funding_rate` | FR semua exchange | Sentiment — extreme = contrarian |
| `coinglass_open_interest` | OI aggregated history | Positioning — OI + price direction |
| `coinglass_liquidation_map` | Liquidation clusters | Target — magnetic zones |
| `coinglass_orderbook` | Orderbook delta history | Pressure — bid/ask imbalance |

### Important (Supporting Analytics)
| Tool | Fungsi |
|------|--------|
| `coinglass_price_ohlc` | Price OHLC history |
| `coinglass_liquidation_history` | Liquidation volume over time |
| `coinglass_long_short_ratio` | L/S ratio (contrarian indicator) |
| `coinglass_taker_buysell` | Taker aggressor side |
| `coinglass_fr_arbitrage` | Extreme FR across all coins |
| `coinglass_coins_markets` | Market overview semua coin |

### Nice-to-Have
| Tool | Fungsi |
|------|--------|
| `coinglass_whale_alert` | Hyperliquid whale positions |
| `coinglass_fear_greed` | Fear & Greed index |
| `coinglass_footprint` | Footprint chart data (90d) |
| `coinglass_spot_netflow` | Exchange net flow |
| `coinglass_orderbook_heatmap` | Orderbook depth visual |

### Composite
| Tool | Fungsi |
|------|--------|
| `coinglass_full_scan` | **ALL-IN-ONE** — 9 metrics dalam 1 call |

## Quick Start

### 1. Clone & Install
```bash
git clone https://github.com/rcz87/McPCG.git
cd McPCG
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

### 2. Setup API Key
```bash
cp .env.example .env
nano .env  # masukkan COINGLASS_API_KEY
```

### 3. Run Server
```bash
# HTTP mode (untuk claude.ai remote)
python -m coinglass_mcp.server

# Stdio mode (untuk Claude Desktop lokal)
MCP_TRANSPORT=stdio python -m coinglass_mcp.server
```

Server berjalan di `http://0.0.0.0:8787/mcp`

### 4. Connect ke Claude

**claude.ai (remote):**
Settings → Integrations → Add MCP Server → URL: `http://YOUR_VPS_IP:8787/mcp`

**Claude Desktop (lokal):**
```json
{
  "mcpServers": {
    "coinglass": {
      "command": "python",
      "args": ["-m", "coinglass_mcp.server"],
      "cwd": "/path/to/McPCG",
      "env": {
        "COINGLASS_API_KEY": "your-key",
        "COINGLASS_PLAN": "standard",
        "MCP_TRANSPORT": "stdio"
      }
    }
  }
}
```

## Deploy ke VPS

### One-Command Setup
```bash
# Di VPS:
git clone https://github.com/rcz87/McPCG.git ~/coinglass-mcp
cd ~/coinglass-mcp
bash deploy/setup-vps.sh
```

### Management Commands
```bash
bash deploy/manage.sh start     # Start server
bash deploy/manage.sh stop      # Stop server
bash deploy/manage.sh restart   # Restart
bash deploy/manage.sh status    # PM2 status
bash deploy/manage.sh logs      # View logs
bash deploy/manage.sh update    # Pull + restart
bash deploy/manage.sh health    # Health check
bash deploy/manage.sh tools     # List tools
```

### HTTPS (Optional tapi Recommended)
```bash
bash deploy/setup-nginx.sh yourdomain.com
```

## Tech Stack

| Component | Version |
|-----------|---------|
| Python | >= 3.10 |
| FastMCP | 3.1.1 |
| httpx | >= 0.25.0 |
| tenacity | >= 8.2.0 |
| CoinGlass API | V4 |

## Project Structure

```
McPCG/
├── src/coinglass_mcp/
│   ├── __init__.py     # Package
│   ├── config.py       # Plan tiers, intervals, rate limits
│   ├── client.py       # Async HTTP client + caching + retry
│   └── server.py       # FastMCP server + 18 tools
├── tests/
│   ├── test_config.py  # Config tests
│   └── test_client.py  # Client tests
├── deploy/
│   ├── setup-vps.sh    # VPS setup script
│   ├── setup-nginx.sh  # Nginx + SSL setup
│   └── manage.sh       # Management commands
├── docs/
│   └── RICOZ-TRADING-CHEATSHEET.md
├── BLUEPRINT-MCP-SERVER.md
├── pyproject.toml
├── .env.example
└── .gitignore
```

## Lisensi

MIT
