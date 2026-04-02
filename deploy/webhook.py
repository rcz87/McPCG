#!/usr/bin/env python3
"""GitHub Webhook Auto-Deploy — listens for push events and updates the server.

Runs alongside the MCP server via PM2. When GitHub sends a push webhook,
this script verifies the signature, pulls latest code, installs deps, and
restarts the MCP server.

Setup:
  1. Set WEBHOOK_SECRET env var (same as GitHub webhook secret)
  2. Add to PM2: pm2 start deploy/webhook.py --name webhook --interpreter python3
  3. In GitHub repo → Settings → Webhooks → Add webhook:
     - URL: http://YOUR_VPS_IP:9000/webhook
     - Content type: application/json
     - Secret: (same as WEBHOOK_SECRET)
     - Events: Just the push event
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import subprocess
import sys
from http.server import HTTPServer, BaseHTTPRequestHandler
from threading import Thread

# ─── Config ──────────────────────────────────────────────────────────────────

WEBHOOK_PORT = int(os.getenv("WEBHOOK_PORT", "9000"))
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "")
APP_DIR = os.getenv("APP_DIR", f"/home/{os.getenv('USER', 'root')}/coinglass-mcp")
DEPLOY_BRANCH = os.getenv("DEPLOY_BRANCH", "claude/divisional-map-cloud-sync-ZYH5I")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [webhook] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("webhook")


# ─── Signature Verification ──────────────────────────────────────────────────

def verify_signature(payload: bytes, signature: str) -> bool:
    """Verify GitHub webhook HMAC-SHA256 signature."""
    if not WEBHOOK_SECRET:
        log.warning("WEBHOOK_SECRET not set — skipping signature verification!")
        return True
    if not signature or not signature.startswith("sha256="):
        return False
    expected = hmac.new(
        WEBHOOK_SECRET.encode(), payload, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(f"sha256={expected}", signature)


# ─── Deploy Logic ────────────────────────────────────────────────────────────

def run_deploy() -> tuple[bool, str]:
    """Pull latest code, install deps, run tests, restart MCP server."""
    steps = [
        ("git fetch", ["git", "fetch", "origin", DEPLOY_BRANCH]),
        ("git pull", ["git", "pull", "origin", DEPLOY_BRANCH]),
        ("pip install", [f"{APP_DIR}/.venv/bin/pip", "install", "-q", "-e", ".[dev]"]),
        ("restart", ["pm2", "restart", "coinglass-mcp"]),
    ]

    output_lines = []
    for name, cmd in steps:
        log.info("Running: %s", name)
        try:
            result = subprocess.run(
                cmd,
                cwd=APP_DIR,
                capture_output=True,
                text=True,
                timeout=120,
            )
            if result.returncode != 0:
                msg = f"FAILED at {name}: {result.stderr.strip()}"
                log.error(msg)
                output_lines.append(msg)
                return False, "\n".join(output_lines)
            output_lines.append(f"✓ {name}")
            log.info("✓ %s done", name)
        except subprocess.TimeoutExpired:
            msg = f"TIMEOUT at {name} (120s)"
            log.error(msg)
            output_lines.append(msg)
            return False, "\n".join(output_lines)

    return True, "\n".join(output_lines)


# ─── HTTP Handler ────────────────────────────────────────────────────────────

class WebhookHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        if self.path != "/webhook":
            self.send_response(404)
            self.end_headers()
            return

        content_length = int(self.headers.get("Content-Length", 0))
        payload = self.rfile.read(content_length)

        # Verify signature
        signature = self.headers.get("X-Hub-Signature-256", "")
        if not verify_signature(payload, signature):
            log.warning("Invalid signature from %s", self.client_address[0])
            self.send_response(403)
            self.end_headers()
            self.wfile.write(b"Invalid signature")
            return

        # Parse event
        event = self.headers.get("X-GitHub-Event", "")
        if event == "ping":
            log.info("Ping received — webhook is connected!")
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"pong")
            return

        if event != "push":
            log.info("Ignoring event: %s", event)
            self.send_response(200)
            self.end_headers()
            self.wfile.write(f"Ignored event: {event}".encode())
            return

        # Check branch
        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            self.send_response(400)
            self.end_headers()
            return

        ref = data.get("ref", "")
        pushed_branch = ref.replace("refs/heads/", "")
        if pushed_branch != DEPLOY_BRANCH:
            msg = f"Push to {pushed_branch}, not {DEPLOY_BRANCH} — skipping"
            log.info(msg)
            self.send_response(200)
            self.end_headers()
            self.wfile.write(msg.encode())
            return

        # Deploy!
        pusher = data.get("pusher", {}).get("name", "unknown")
        commits = len(data.get("commits", []))
        log.info("Push from %s (%d commits) to %s — deploying...", pusher, commits, pushed_branch)

        # Respond immediately, deploy in background thread
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Deploying...")

        Thread(target=self._deploy_background, daemon=True).start()

    def _deploy_background(self):
        success, output = run_deploy()
        if success:
            log.info("Deploy SUCCESS:\n%s", output)
        else:
            log.error("Deploy FAILED:\n%s", output)

    def log_message(self, format, *args):
        """Suppress default access logs — we have our own logging."""
        pass


# ─── Main ────────────────────────────────────────────────────────────────────

def main():
    if not WEBHOOK_SECRET:
        log.warning(
            "⚠️  WEBHOOK_SECRET not set! Set it in .env for security.\n"
            "  Generate one: python3 -c \"import secrets; print(secrets.token_hex(32))\""
        )

    server = HTTPServer(("0.0.0.0", WEBHOOK_PORT), WebhookHandler)
    log.info("Webhook listener started on port %d", WEBHOOK_PORT)
    log.info("Deploy branch: %s", DEPLOY_BRANCH)
    log.info("App directory: %s", APP_DIR)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("Shutting down webhook listener")
        server.server_close()


if __name__ == "__main__":
    main()
