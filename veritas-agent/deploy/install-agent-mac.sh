#!/usr/bin/env bash
# Veritas Agent — macOS Install Script
# ======================================
# Installs and starts the Veritas Agent as a launchd daemon on macOS.
# Run as root: sudo bash deploy/install-agent-mac.sh
#
# Usage:
#   sudo bash deploy/install-agent-mac.sh

set -euo pipefail

INSTALL_DIR="/opt/veritas-agent"
CONFIG_DIR="/etc/veritas-agent"
LOG_DIR="/var/log/veritas-agent"
VENV_DIR="$INSTALL_DIR/venv"
PLIST_DST="/Library/LaunchDaemons/com.veritas.technologies.agent.plist"
AGENT_SRC="$(cd "$(dirname "$0")/.." && pwd)"

if [[ $EUID -ne 0 ]]; then
    echo "ERROR: Run as root: sudo bash deploy/install-agent-mac.sh" >&2
    exit 1
fi

echo ""
echo "============================================"
echo "  Veritas Agent — macOS Install"
echo "============================================"
echo ""

echo "==> Creating directories..."
mkdir -p "$INSTALL_DIR" "$CONFIG_DIR" "$LOG_DIR"

echo "==> Installing agent files..."
rsync -a --delete "$AGENT_SRC/" "$INSTALL_DIR/" \
    --exclude '__pycache__' --exclude '*.pyc' --exclude '.git' \
    --exclude 'deploy/' 2>/dev/null || cp -r "$AGENT_SRC/." "$INSTALL_DIR/"

if [[ ! -f "$CONFIG_DIR/agent-config.yaml" ]]; then
    cp "$INSTALL_DIR/agent-config.yaml.example" "$CONFIG_DIR/agent-config.yaml"
    echo ""
    echo "  ACTION REQUIRED: Edit $CONFIG_DIR/agent-config.yaml"
    echo "  Set veritas_address and registration_key"
    echo ""
fi

# Try to download TLS cert from server
SERVER=$(grep 'veritas_address:' "$CONFIG_DIR/agent-config.yaml" 2>/dev/null | awk '{print $2}' | tr -d '"' || true)
if [[ -n "$SERVER" && "$SERVER" != "https://192.168.1.50:8000" && ! -f "$CONFIG_DIR/server.crt" ]]; then
    echo "==> Downloading TLS certificate from $SERVER..."
    if curl -sk "${SERVER%/}/api/tls/cert" -o "$CONFIG_DIR/server.crt" 2>/dev/null && \
       grep -q "BEGIN CERTIFICATE" "$CONFIG_DIR/server.crt" 2>/dev/null; then
        echo "    [OK] TLS certificate saved"
    else
        rm -f "$CONFIG_DIR/server.crt"
        echo "    WARNING: Could not download TLS cert — set ca_cert manually"
    fi
fi

echo "==> Creating Python virtual environment..."
if [[ ! -d "$VENV_DIR" ]]; then
    python3 -m venv "$VENV_DIR"
fi
"$VENV_DIR/bin/pip" install --upgrade pip --quiet
"$VENV_DIR/bin/pip" install -r "$INSTALL_DIR/requirements.txt" --quiet

echo "==> Installing launchd daemon..."
cp "$AGENT_SRC/deploy/com.veritas.technologies.agent.plist" "$PLIST_DST"
chown root:wheel "$PLIST_DST"
chmod 644 "$PLIST_DST"

launchctl unload -w "$PLIST_DST" 2>/dev/null || true
launchctl load -w "$PLIST_DST"

echo ""
echo "============================================"
echo "  Installation complete"
echo "============================================"
echo ""
echo "  Config:  $CONFIG_DIR/agent-config.yaml"
echo "  Logs:    tail -f $LOG_DIR/agent.log"
echo "  Status:  sudo launchctl list com.veritas.technologies.agent"
echo "  Stop:    sudo launchctl stop com.veritas.technologies.agent"
echo ""
echo "  The agent starts automatically at boot."
echo "============================================"
