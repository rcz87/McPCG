#!/bin/bash
# ═══════════════════════════════════════════════════════════════════════════════
# CoinGlass MCP Server — Nginx + SSL Setup (Optional)
# Usage: bash deploy/setup-nginx.sh yourdomain.com
# ═══════════════════════════════════════════════════════════════════════════════

set -euo pipefail

DOMAIN="${1:-}"
PORT=8787

if [ -z "$DOMAIN" ]; then
    echo "Usage: bash deploy/setup-nginx.sh yourdomain.com"
    echo ""
    echo "If you don't have a domain, you can skip this and use"
    echo "http://YOUR_VPS_IP:$PORT/mcp directly."
    exit 1
fi

echo "Setting up Nginx + SSL for $DOMAIN..."

# Install Nginx + Certbot
sudo apt-get update -qq
sudo apt-get install -y nginx certbot python3-certbot-nginx

# Create Nginx config
sudo tee /etc/nginx/sites-available/coinglass-mcp > /dev/null << NGEOF
server {
    listen 80;
    server_name $DOMAIN;

    location / {
        proxy_pass http://127.0.0.1:$PORT;
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;

        # Timeout for long MCP calls (full_scan can take a while)
        proxy_read_timeout 120s;
        proxy_send_timeout 120s;
    }
}
NGEOF

# Enable site
sudo ln -sf /etc/nginx/sites-available/coinglass-mcp /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl reload nginx

echo "  ✓ Nginx configured for $DOMAIN"

# SSL with Let's Encrypt
echo ""
echo "Setting up SSL certificate..."
sudo certbot --nginx -d "$DOMAIN" --non-interactive --agree-tos --email admin@$DOMAIN

echo ""
echo "═══════════════════════════════════════════════════════"
echo "  ✓ Nginx + SSL Setup Complete!"
echo "═══════════════════════════════════════════════════════"
echo ""
echo "  MCP Server URL: https://$DOMAIN/mcp"
echo ""
echo "  Use this URL in claude.ai:"
echo "  Settings → Integrations → Add MCP Server"
echo "  URL: https://$DOMAIN/mcp"
echo ""
