#!/bin/bash
# ═══════════════════════════════════════════════════════════════════════════════
# Auto-Deploy via Cron — check GitHub for updates, pull & restart if changed
#
# Setup (check every 2 minutes):
#   crontab -e
#   */2 * * * * bash /home/$USER/coinglass-mcp/deploy/autodeploy.sh >> /home/$USER/coinglass-mcp/logs/autodeploy.log 2>&1
#
# Or every 5 minutes:
#   */5 * * * * bash /home/$USER/coinglass-mcp/deploy/autodeploy.sh >> /home/$USER/coinglass-mcp/logs/autodeploy.log 2>&1
# ═══════════════════════════════════════════════════════════════════════════════

APP_DIR="/home/${USER:-$(whoami)}/coinglass-mcp"
BRANCH="claude/divisional-map-cloud-sync-ZYH5I"
LOCKFILE="/tmp/autodeploy.lock"

# Prevent concurrent runs
if [ -f "$LOCKFILE" ]; then
    LOCK_AGE=$(( $(date +%s) - $(stat -c %Y "$LOCKFILE" 2>/dev/null || echo 0) ))
    if [ "$LOCK_AGE" -lt 300 ]; then
        exit 0  # Another deploy is running
    fi
    rm -f "$LOCKFILE"  # Stale lock (>5min), remove it
fi

cd "$APP_DIR" || exit 1

# Fetch latest from GitHub
git fetch origin "$BRANCH" --quiet 2>/dev/null
if [ $? -ne 0 ]; then
    echo "$(date '+%Y-%m-%d %H:%M:%S') [autodeploy] WARN: git fetch failed (network?)"
    exit 1
fi

# Compare local vs remote
LOCAL=$(git rev-parse HEAD)
REMOTE=$(git rev-parse "origin/$BRANCH")

if [ "$LOCAL" = "$REMOTE" ]; then
    exit 0  # No changes, silent exit
fi

# ── New commits detected — deploy! ──
touch "$LOCKFILE"
echo "$(date '+%Y-%m-%d %H:%M:%S') [autodeploy] New commits detected!"
echo "  Local:  $LOCAL"
echo "  Remote: $REMOTE"
echo "  Diff:   $(git log --oneline $LOCAL..$REMOTE | wc -l) commit(s)"
git log --oneline "$LOCAL..$REMOTE" | head -5 | sed 's/^/    /'

# Pull
echo "$(date '+%Y-%m-%d %H:%M:%S') [autodeploy] Pulling..."
git pull origin "$BRANCH" --quiet
if [ $? -ne 0 ]; then
    echo "$(date '+%Y-%m-%d %H:%M:%S') [autodeploy] ERROR: git pull failed!"
    rm -f "$LOCKFILE"
    exit 1
fi

# Install deps (only if requirements changed)
if git diff "$LOCAL..$REMOTE" --name-only | grep -qE "(pyproject.toml|setup.py|requirements)"; then
    echo "$(date '+%Y-%m-%d %H:%M:%S') [autodeploy] Dependencies changed, installing..."
    source "$APP_DIR/.venv/bin/activate"
    pip install -q -e ".[dev]"
fi

# Restart MCP server
echo "$(date '+%Y-%m-%d %H:%M:%S') [autodeploy] Restarting coinglass-mcp..."
pm2 restart coinglass-mcp --silent

echo "$(date '+%Y-%m-%d %H:%M:%S') [autodeploy] ✓ Deploy complete! Now at $(git rev-parse --short HEAD)"
rm -f "$LOCKFILE"
