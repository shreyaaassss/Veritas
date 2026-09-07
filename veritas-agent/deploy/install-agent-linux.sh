#!/usr/bin/env bash
# Veritas Agent — Linux Install Script
# ======================================
# Installs and starts the Veritas Agent as a systemd service.
# Run as root: sudo bash install-agent-linux.sh
#
# Prerequisites:
#   - A veritas.vlic license on the Veritas Server
#   - A registration key from: Dashboard → Agents → Issue Registration Key
#   - The Veritas Server TLS certificate (download from server /api/tls/cert)
#
# What this does:
#   1. Creates system user veritas-agent
#   2. Installs agent files to /opt/veritas-agent/
#   3. Installs config to /etc/veritas-agent/agent-config.yaml
#   4. Installs TLS cert to /etc/veritas-agent/server.crt
#   5. Creates Python venv + installs dependencies
#   6. Installs and starts veritas-agent.service

set -euo pipefail

INSTALL_DIR="/opt/veritas-agent"
CONFIG_DIR="/etc/veritas-agent"
VENV_DIR="$INSTALL_DIR/venv"
SERVICE_DST="/etc/systemd/system/veritas-agent.service"
AGENT_SRC="$(cd "$(dirname "$0")/.." && pwd)"

# ---- Check root ----
if [[ $EUID -ne 0 ]]; then
    echo "ERROR: Run as root: sudo bash deploy/install-agent-linux.sh" >&2
    exit 1
fi

echo ""
echo "============================================"
echo "  Veritas Agent — Linux Install"
echo "============================================"
echo ""

# ---- Create system user ----
echo "==> Creating system user 'veritas-agent'..."
if id -u veritas-agent &>/dev/null; then
    echo "    (user already exists)"
else
    useradd --system --no-create-home --shell /bin/false veritas-agent
fi

# ---- Install agent files ----
echo "==> Installing agent to $INSTALL_DIR..."
mkdir -p "$INSTALL_DIR" "$CONFIG_DIR"
rsync -a --delete "$AGENT_SRC/" "$INSTALL_DIR/" \
    --exclude '__pycache__' --exclude '*.pyc' --exclude '.git' \
    --exclude 'deploy/' --exclude '*.vlic' 2>/dev/null || \
    cp -r "$AGENT_SRC/." "$INSTALL_DIR/"

# ---- Config setup ----
if [[ ! -f "$CONFIG_DIR/agent-config.yaml" ]]; then
    echo "==> Creating default config at $CONFIG_DIR/agent-config.yaml..."
    cp "$INSTALL_DIR/agent-config.yaml.example" "$CONFIG_DIR/agent-config.yaml"
    echo ""
    echo "  ACTION REQUIRED: Edit $CONFIG_DIR/agent-config.yaml"
    echo "  Set veritas_address and registration_key before starting the service."
    echo ""
else
    echo "==> Config already exists at $CONFIG_DIR/agent-config.yaml — skipping."
fi

# ---- TLS certificate ----
if [[ -f "$CONFIG_DIR/server.crt" ]]; then
    echo "==> TLS certificate already installed."
else
    # Try to download from server if veritas_address is set
    SERVER=$(grep 'veritas_address:' "$CONFIG_DIR/agent-config.yaml" 2>/dev/null | awk '{print $2}' | tr -d '"')
    if [[ -n "$SERVER" && "$SERVER" != "https://192.168.1.50:8000" ]]; then
        echo "==> Downloading TLS certificate from $SERVER..."
        CERT_URL="${SERVER%/}/api/tls/cert"
        if curl -sk "$CERT_URL" -o "$CONFIG_DIR/server.crt" 2>/dev/null && \
           grep -q "BEGIN CERTIFICATE" "$CONFIG_DIR/server.crt" 2>/dev/null; then
            echo "    [OK] TLS certificate saved to $CONFIG_DIR/server.crt"
            # Add ca_cert to config if not already set
            if ! grep -q "ca_cert:" "$CONFIG_DIR/agent-config.yaml"; then
                sed -i '/^tls:/a\  ca_cert: /etc/veritas-agent/server.crt' \
                    "$CONFIG_DIR/agent-config.yaml" 2>/dev/null || true
            fi
        else
            rm -f "$CONFIG_DIR/server.crt"
            echo "    WARNING: Could not download TLS certificate."
            echo "    Download manually: curl -sk $CERT_URL -o $CONFIG_DIR/server.crt"
        fi
    else
        echo "  INFO: Set veritas_address in config, then run:"
        echo "    curl -sk <veritas_address>/api/tls/cert -o $CONFIG_DIR/server.crt"
    fi
fi

# ---- Python venv + deps ----
if [[ ! -d "$VENV_DIR" ]]; then
    echo "==> Creating Python virtual environment..."
    python3 -m venv "$VENV_DIR"
fi

echo "==> Installing Python dependencies..."
"$VENV_DIR/bin/pip" install --upgrade pip --quiet
"$VENV_DIR/bin/pip" install -r "$INSTALL_DIR/requirements.txt" --quiet

# ---- Permissions ----
chown -R veritas-agent:veritas-agent "$INSTALL_DIR"
chown -R root:veritas-agent "$CONFIG_DIR"
chmod 750 "$CONFIG_DIR"
chmod 640 "$CONFIG_DIR/agent-config.yaml" 2>/dev/null || true
chmod 644 "$CONFIG_DIR/server.crt" 2>/dev/null || true

# ---- Systemd service ----
echo "==> Installing systemd service..."
cp "$AGENT_SRC/deploy/veritas-agent.service" "$SERVICE_DST"
systemctl daemon-reload
systemctl enable veritas-agent

echo ""
echo "============================================"
echo "  Installation complete"
echo "============================================"
echo ""
echo "  NEXT STEPS:"
echo "  1. Edit config:   nano $CONFIG_DIR/agent-config.yaml"
echo "     Set:           veritas_address, registration_key"
echo ""
echo "  2. Start agent:   systemctl start veritas-agent"
echo "  3. Check status:  systemctl status veritas-agent"
echo "  4. View logs:     journalctl -u veritas-agent -f"
echo ""
echo "  The agent will start automatically on next boot."
echo "============================================"
