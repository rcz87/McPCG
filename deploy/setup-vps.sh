#!/bin/bash
# ═══════════════════════════════════════════════════════════════════════════════
# CoinGlass MCP Server — VPS Setup Script (Hostinger Ubuntu 24.04)
# Run this once on your VPS to set up everything
# Usage: bash deploy/setup-vps.sh
# ═══════════════════════════════════════════════════════════════════════════════

set -euo pipefail

# ─── Config ───────────────────────────────────────────────────────────────────
APP_DIR="/home/$USER/coinglass-mcp"
REPO_URL="https://github.com/rcz87/McPCG.git"
BRANCH="claude/divisional-map-cloud-sync-ZYH5I"
PORT=8787

echo "═══════════════════════════════════════════════════════"
echo "  CoinGlass MCP Server — VPS Setup"
echo "═══════════════════════════════════════════════════════"

# ─── Step 1: System Dependencies ─────────────────────────────────────────────
echo ""
echo "[1/6] Checking system dependencies..."

# Python 3.10+
if ! command -v python3 &> /dev/null; then
    echo "  Installing Python3..."
    sudo apt-get update -qq
    sudo apt-get install -y python3 python3-venv python3-pip
fi

PYTHON_VERSION=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
echo "  Python version: $PYTHON_VERSION"

# Node.js + PM2
if ! command -v pm2 &> /dev/null; then
    echo "  Installing PM2..."
    if ! command -v node &> /dev/null; then
        curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
        sudo apt-get install -y nodejs
    fi
    sudo npm install -g pm2
fi
echo "  PM2: $(pm2 --version 2>/dev/null || echo 'installed')"

echo "  ✓ System dependencies OK"

# ─── Step 2: Clone/Update Repository ─────────────────────────────────────────
echo ""
echo "[2/6] Setting up project..."

if [ -d "$APP_DIR" ]; then
    echo "  Updating existing installation..."
    cd "$APP_DIR"
    git fetch origin "$BRANCH"
    git checkout "$BRANCH"
    git pull origin "$BRANCH"
else
    echo "  Cloning repository..."
    git clone -b "$BRANCH" "$REPO_URL" "$APP_DIR"
    cd "$APP_DIR"
fi

echo "  ✓ Repository ready at $APP_DIR"

# ─── Step 3: Python Virtual Environment ──────────────────────────────────────
echo ""
echo "[3/6] Setting up Python environment..."

if [ ! -d ".venv" ]; then
    python3 -m venv .venv
fi
source .venv/bin/activate
pip install -q -e ".[dev]"

echo "  ✓ Python environment ready"

# ─── Step 4: Environment File ────────────────────────────────────────────────
echo ""
echo "[4/6] Configuring environment..."

if [ ! -f ".env" ]; then
    cp .env.example .env
    echo ""
    echo "  ⚠️  IMPORTANT: Edit .env file with your API key!"
    echo "  Run: nano $APP_DIR/.env"
    echo ""
    echo "  Set these values:"
    echo "    COINGLASS_API_KEY=\"your-actual-api-key\""
    echo "    COINGLASS_PLAN=\"standard\""
    echo ""
else
    echo "  .env file already exists (keeping current config)"
fi

echo "  ✓ Environment configured"

# ─── Step 5: PM2 Setup ──────────────────────────────────────────────────────
echo ""
echo "[5/6] Setting up PM2 process manager..."

# Stop existing process if running
pm2 delete coinglass-mcp 2>/dev/null || true

# Create PM2 ecosystem config
cat > ecosystem.config.js << 'PMEOF'
module.exports = {
  apps: [{
    name: "coinglass-mcp",
    script: ".venv/bin/python",
    args: "-m coinglass_mcp.server",
    cwd: process.env.HOME + "/coinglass-mcp",
    env: {
      MCP_TRANSPORT: "streamable-http",
      MCP_HOST: "0.0.0.0",
      MCP_PORT: "8787"
    },
    max_memory_restart: "200M",
    autorestart: true,
    watch: false,
    max_restarts: 10,
    restart_delay: 5000,
    log_date_format: "YYYY-MM-DD HH:mm:ss",
    error_file: "logs/error.log",
    out_file: "logs/output.log",
    merge_logs: true
  }]
};
PMEOF

mkdir -p logs

echo "  ✓ PM2 config created"

# ─── Step 6: Run Tests ──────────────────────────────────────────────────────
echo ""
echo "[6/6] Running tests..."

python -m pytest tests/ -v

echo ""
echo "═══════════════════════════════════════════════════════"
echo "  ✓ Setup Complete!"
echo "═══════════════════════════════════════════════════════"
echo ""
echo "  Next steps:"
echo ""
echo "  1. Edit API key:"
echo "     nano $APP_DIR/.env"
echo ""
echo "  2. Start the server:"
echo "     cd $APP_DIR && pm2 start ecosystem.config.js"
echo ""
echo "  3. Save PM2 config (auto-start on reboot):"
echo "     pm2 save && pm2 startup"
echo ""
echo "  4. Verify it's running:"
echo "     curl http://localhost:$PORT/mcp -X POST \\"
echo "       -H 'Content-Type: application/json' \\"
echo "       -d '{\"jsonrpc\":\"2.0\",\"method\":\"initialize\",\"id\":1,\"params\":{\"protocolVersion\":\"2025-03-26\",\"capabilities\":{},\"clientInfo\":{\"name\":\"test\",\"version\":\"1.0\"}}}'"
echo ""
echo "  5. Connect to claude.ai:"
echo "     Settings → Integrations → Add MCP Server"
echo "     URL: http://YOUR_VPS_IP:$PORT/mcp"
echo ""
echo "  For HTTPS (recommended), set up Nginx reverse proxy:"
echo "     bash deploy/setup-nginx.sh yourdomain.com"
echo ""
