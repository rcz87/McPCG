#!/bin/bash
# ═══════════════════════════════════════════════════════════════════════════════
# Health Monitor — ping /health every 5 min, Telegram alert on 2 consecutive fails
#
# Setup:
#   crontab -e
#   */5 * * * * bash /root/McPCG/deploy/health-monitor.sh
# ═══════════════════════════════════════════════════════════════════════════════

HEALTH_URL="http://localhost:8787/health"
FAIL_FILE="/tmp/mcpcg-health-fail"
ENV_FILE="/root/McPCG/.env"

# Load Telegram credentials from .env
TELEGRAM_BOT_TOKEN=$(grep TELEGRAM_BOT_TOKEN "$ENV_FILE" | cut -d= -f2-)
TELEGRAM_CHAT_ID=$(grep TELEGRAM_CHAT_ID "$ENV_FILE" | cut -d= -f2-)

send_telegram() {
    local msg="$1"
    curl -s -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
        -d chat_id="${TELEGRAM_CHAT_ID}" \
        -d parse_mode="HTML" \
        -d text="$msg" > /dev/null 2>&1
}

# Ping /health (3s timeout)
HTTP_CODE=$(curl -s -o /tmp/mcpcg-health-resp -w "%{http_code}" --max-time 3 "$HEALTH_URL" 2>/dev/null)

if [ "$HTTP_CODE" = "200" ]; then
    # ─── OK ───
    if [ -f "$FAIL_FILE" ]; then
        PREV_FAILS=$(cat "$FAIL_FILE")
        rm -f "$FAIL_FILE"
        # Recovery alert if was down
        if [ "$PREV_FAILS" -ge 2 ] 2>/dev/null; then
            UPTIME=$(cat /tmp/mcpcg-health-resp | python3 -c "import sys,json; print(json.load(sys.stdin).get('uptime_human','?'))" 2>/dev/null)
            send_telegram "$(cat <<EOF
<b>MCP Server RECOVERED</b>
Status: online
Uptime: ${UPTIME:-just started}
Time: $(TZ=Asia/Jakarta date '+%H:%M WIB')
EOF
)"
        fi
    fi
    exit 0
fi

# ─── FAIL ───
PREV_FAILS=0
if [ -f "$FAIL_FILE" ]; then
    PREV_FAILS=$(cat "$FAIL_FILE")
fi
FAILS=$((PREV_FAILS + 1))
echo "$FAILS" > "$FAIL_FILE"

# Alert on 2nd consecutive fail
if [ "$FAILS" -eq 2 ]; then
    PM2_STATUS=$(pm2 jlist 2>/dev/null | python3 -c "
import sys,json
procs = json.load(sys.stdin)
for p in procs:
    if p['name'] == 'coinglass-mcp':
        print(f\"status={p['pm2_env']['status']}, restarts={p['pm2_env']['restart_time']}\")
        break
else:
    print('not found in PM2')
" 2>/dev/null || echo "pm2 error")

    send_telegram "$(cat <<EOF
<b>MCP Server DOWN</b>
Health: ${HEALTH_URL}
HTTP: ${HTTP_CODE:-timeout}
PM2: ${PM2_STATUS}
Fails: ${FAILS} consecutive
Time: $(TZ=Asia/Jakarta date '+%H:%M WIB')

Check: <code>pm2 logs coinglass-mcp --lines 20</code>
EOF
)"
fi
