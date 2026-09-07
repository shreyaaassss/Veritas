#!/usr/bin/env bash
# Veritas — Linux Install Script
# ================================
# Installs Veritas Runtime + Launcher on Linux (systemd-based distros).
# Run as root: sudo bash deploy/install-linux.sh
#
# What this does:
#   1. Creates /opt/veritas/ and copies binaries
#   2. Creates Python venv + installs dependencies
#   3. Downloads spaCy language model
#   4. Installs systemd service (veritas.service)
#   5. Enables + starts the service
#
# After install:
#   Dashboard: http://<your-ip>:8000
#   Logs:      journalctl -u veritas -f
#   Status:    systemctl status veritas

set -euo pipefail

INSTALL_DIR="/opt/veritas"
VENV_DIR="$INSTALL_DIR/venv"
PROJECT_SRC="$(cd "$(dirname "$0")/.." && pwd)"
LAUNCHER_SRC="$(cd "$PROJECT_SRC/../veritas-launcher" && pwd)"

# ---- Check root ----
if [[ $EUID -ne 0 ]]; then
    echo "ERROR: Run as root: sudo bash deploy/install-linux.sh" >&2
    exit 1
fi

# ---- Detect package manager ----
if command -v apt-get &>/dev/null; then
    PKG_INSTALL="apt-get install -y --quiet"
    echo "==> Detected: apt (Debian/Ubuntu)"
elif command -v dnf &>/dev/null; then
    PKG_INSTALL="dnf install -y -q"
    echo "==> Detected: dnf (RHEL/Fedora)"
elif command -v yum &>/dev/null; then
    PKG_INSTALL="yum install -y -q"
    echo "==> Detected: yum (CentOS)"
else
    echo "WARNING: Unknown package manager — skipping system package install"
    PKG_INSTALL=""
fi

# ---- Install rsync if missing ----
if ! command -v rsync &>/dev/null && [[ -n "$PKG_INSTALL" ]]; then
    echo "==> Installing rsync..."
    $PKG_INSTALL rsync
fi

# ---- Create system user ----
echo "==> Creating system user 'veritas'..."
id -u veritas &>/dev/null || useradd --system --no-create-home --shell /bin/false veritas

# ---- Copy project files ----
echo "==> Copying Veritas Runtime to $INSTALL_DIR..."
mkdir -p "$INSTALL_DIR"
rsync -a --delete "$PROJECT_SRC/" "$INSTALL_DIR/" \
    --exclude '__pycache__' \
    --exclude '*.pyc' \
    --exclude '.git' \
    --exclude '.env' \
    --exclude 'evidence_store/evidence.db' \
    --exclude 'agent_store/agents.db' \
    --exclude '*.log'

# ---- Copy launcher binary (if pre-built) ----
LAUNCHER_BIN="$LAUNCHER_SRC/dist/linux/veritas-launcher"
if [[ -f "$LAUNCHER_BIN" ]]; then
    echo "==> Copying veritas-launcher binary..."
    cp "$LAUNCHER_BIN" "$INSTALL_DIR/veritas-launcher"
    chmod +x "$INSTALL_DIR/veritas-launcher"
else
    echo "WARNING: veritas-launcher binary not found at $LAUNCHER_BIN"
    echo "         Build it first: cd veritas-launcher && bash build.sh"
    echo "         Falling back to python run_pipeline.py mode."
fi

# ---- Copy veritas-runtime binary (if PyInstaller bundle exists) ----
RUNTIME_BIN="$PROJECT_SRC/dist/veritas-runtime"
if [[ -f "$RUNTIME_BIN" ]]; then
    echo "==> Copying veritas-runtime binary..."
    cp "$RUNTIME_BIN" "$INSTALL_DIR/veritas-runtime"
    chmod +x "$INSTALL_DIR/veritas-runtime"
fi

# ---- Python venv + deps ----
if [[ ! -d "$VENV_DIR" ]]; then
    echo "==> Creating Python virtual environment..."
    python3 -m venv "$VENV_DIR"
fi

echo "==> Installing Python dependencies..."
"$VENV_DIR/bin/pip" install --upgrade pip --quiet
"$VENV_DIR/bin/pip" install -r "$INSTALL_DIR/requirements.txt" --quiet

# ---- spaCy model ----
if ! "$VENV_DIR/bin/python" -c "import spacy; spacy.load('en_core_web_lg')" &>/dev/null; then
    echo "==> Downloading spaCy language model (~750 MB)..."
    "$VENV_DIR/bin/python" -m spacy download en_core_web_lg --quiet
else
    echo "==> spaCy model already installed."
fi

# ---- TLS certificate ----
CERT_DIR="$INSTALL_DIR/certs"
CERT_FILE="$CERT_DIR/server.crt"
KEY_FILE="$CERT_DIR/server.key"
if [[ ! -f "$CERT_FILE" ]]; then
    echo "==> Generating self-signed TLS certificate..."
    mkdir -p "$CERT_DIR"
    "$VENV_DIR/bin/python" -c "
from tls import generate_self_signed_cert
from pathlib import Path
generate_self_signed_cert(Path('$CERT_FILE'), Path('$KEY_FILE'))
    " && echo "    [OK] TLS certificate generated at $CERT_FILE" || echo "    WARNING: TLS cert generation failed — server will run over HTTP"
else
    echo "==> TLS certificate already exists — skipping generation."
fi

# ---- Permissions ----
echo "==> Setting permissions..."
chown -R veritas:veritas "$INSTALL_DIR"

# ---- systemd service ----
echo "==> Installing systemd service..."
cp "$PROJECT_SRC/deploy/veritas-linux.service" /etc/systemd/system/veritas.service
systemctl daemon-reload
systemctl enable veritas

if systemctl is-active --quiet veritas; then
    systemctl restart veritas
else
    systemctl start veritas
fi

sleep 3
echo ""
echo "============================================"
echo "  Veritas installed successfully (Linux)"
echo "============================================"
LOCAL_IP=$(hostname -I 2>/dev/null | awk '{print $1}' || echo "localhost")
echo "  Dashboard: http://${LOCAL_IP}:8000"
echo "  Logs:      journalctl -u veritas -f"
echo "  Status:    systemctl status veritas"
echo ""
