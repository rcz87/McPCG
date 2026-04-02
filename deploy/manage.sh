#!/bin/bash
# ═══════════════════════════════════════════════════════════════════════════════
# CoinGlass MCP Server — Management Commands
# Usage: bash deploy/manage.sh [command]
# ═══════════════════════════════════════════════════════════════════════════════

APP_DIR="/home/$USER/coinglass-mcp"
cd "$APP_DIR" 2>/dev/null || { echo "Error: $APP_DIR not found. Run setup-vps.sh first."; exit 1; }

case "${1:-help}" in
    start)
        echo "Starting CoinGlass MCP Server..."
        pm2 start ecosystem.config.js
        pm2 save
        echo "✓ Server started"
        ;;
    stop)
        echo "Stopping CoinGlass MCP Server..."
        pm2 stop coinglass-mcp
        echo "✓ Server stopped"
        ;;
    restart)
        echo "Restarting CoinGlass MCP Server..."
        pm2 restart coinglass-mcp
        echo "✓ Server restarted"
        ;;
    status)
        pm2 status coinglass-mcp
        ;;
    logs)
        pm2 logs coinglass-mcp --lines "${2:-50}"
        ;;
    update)
        echo "Updating CoinGlass MCP Server..."
        git pull origin claude/divisional-map-cloud-sync-ZYH5I
        source .venv/bin/activate
        pip install -q -e ".[dev]"
        python -m pytest tests/ -v
        pm2 restart coinglass-mcp
        echo "✓ Updated and restarted"
        ;;
    test)
        source .venv/bin/activate
        python -m pytest tests/ -v
        ;;
    health)
        echo "Checking MCP server health..."
        RESPONSE=$(curl -s -o /dev/null -w "%{http_code}" -X POST \
            http://localhost:8787/mcp \
            -H "Content-Type: application/json" \
            -d '{"jsonrpc":"2.0","method":"initialize","id":1,"params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"healthcheck","version":"1.0"}}}')
        if [ "$RESPONSE" = "200" ]; then
            echo "✓ MCP Server is healthy (HTTP $RESPONSE)"
        else
            echo "✗ MCP Server returned HTTP $RESPONSE"
            echo "  Check logs: bash deploy/manage.sh logs"
        fi
        ;;
    tools)
        echo "Listing registered MCP tools..."
        source .venv/bin/activate
        python -c "
from coinglass_mcp.server import mcp
import asyncio
async def check():
    tools = await mcp.list_tools()
    print(f'Total tools: {len(tools)}')
    for t in sorted(tools, key=lambda x: x.name):
        print(f'  - {t.name}: {t.description[:60]}...')
asyncio.run(check())
"
        ;;
    autodeploy)
        echo "Setting up auto-deploy cron (every 2 minutes)..."
        CRON_CMD="*/2 * * * * bash $APP_DIR/deploy/autodeploy.sh >> $APP_DIR/logs/autodeploy.log 2>&1"
        # Remove existing autodeploy cron if any, then add new one
        (crontab -l 2>/dev/null | grep -v "autodeploy.sh"; echo "$CRON_CMD") | crontab -
        echo "✓ Cron installed! Checking GitHub every 2 minutes."
        echo "  Log: tail -f $APP_DIR/logs/autodeploy.log"
        echo "  Remove: bash deploy/manage.sh autodeploy-off"
        ;;
    autodeploy-off)
        echo "Removing auto-deploy cron..."
        crontab -l 2>/dev/null | grep -v "autodeploy.sh" | crontab -
        echo "✓ Auto-deploy cron removed"
        ;;
    autodeploy-logs)
        tail -f "$APP_DIR/logs/autodeploy.log" 2>/dev/null || echo "No autodeploy logs yet"
        ;;
    help|*)
        echo "CoinGlass MCP Server — Management"
        echo ""
        echo "Usage: bash deploy/manage.sh [command]"
        echo ""
        echo "Commands:"
        echo "  start          — Start the server via PM2"
        echo "  stop           — Stop the server"
        echo "  restart        — Restart the server"
        echo "  status         — Show PM2 process status"
        echo "  logs           — View server logs (optional: number of lines)"
        echo "  update         — Pull latest code, install deps, restart"
        echo "  test           — Run test suite"
        echo "  health         — Check if MCP server is responding"
        echo "  tools          — List all registered MCP tools"
        echo "  autodeploy     — Setup cron auto-deploy (check GitHub every 2 min)"
        echo "  autodeploy-off — Remove auto-deploy cron"
        echo "  autodeploy-logs— View auto-deploy logs"
        echo "  help           — Show this help message"
        ;;
esac
