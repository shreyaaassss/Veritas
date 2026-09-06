#!/usr/bin/env bash
# Veritas — macOS Install Script
# =================================
# Installs Veritas Runtime + Launcher on macOS using launchd.
# Run as root: sudo bash deploy/install-mac.sh
#
# What this does:
#   1. Creates /Library/Application Support/Veritas/ and copies binaries
#   2. Creates Python venv + installs dependencies
#   3. Downloads spaCy language model
#   4. Installs launchd daemon plist
#   5. Loads the daemon (starts now + auto-starts on boot)
#
# After install:
#   Dashboard: http://localhost:8000
#   Logs:      tail -f /Library/Logs/Veritas/veritas.log
#   Status:    sudo launchctl list com.veritas.technologies.veritas

set -euo pipefail

INSTALL_DIR="/Library/Application Support/Veritas"
VENV_DIR="$INSTALL_DIR/venv"
LOG_DIR="/Library/Logs/Veritas"
PLIST_DST="/Library/LaunchDaemons/com.veritas.technologies.veritas.plist"
PROJECT_SRC="$(cd "$(dirname "$0")/.." && pwd)"
LAUNCHER_SRC="$(cd "$PROJECT_SRC/../veritas-launcher" && pwd)"

# ---- Check root ----
if [[ $EUID -ne 0 ]]; then
    echo "ERROR: Run as root: sudo bash deploy/install-mac.sh" >&2
    exit 1
fi

# ---- Create directories ----
echo "==> Creating installation directories..."
mkdir -p "$INSTALL_DIR" "$LOG_DIR"

# ---- Copy project files ----
echo "==> Copying Veritas Runtime to '$INSTALL_DIR'..."
rsync -a --delete "$PROJECT_SRC/" "$INSTALL_DIR/" \
    --exclude '__pycache__' \
    --exclude '*.pyc' \
    --exclude '.git' \
    --exclude '.env' \
    --exclude 'evidence_store/evidence.db' \
    --exclude 'agent_store/agents.db' \
    --exclude '*.log' 2>/dev/null || \
cp -r "$PROJECT_SRC/." "$INSTALL_DIR/"

# ---- Copy launcher binary ----
LAUNCHER_ARM="$LAUNCHER_SRC/dist/mac-arm/veritas-launcher"
LAUNCHER_INTEL="$LAUNCHER_SRC/dist/mac-intel/veritas-launcher"

if [[ -f "$LAUNCHER_ARM" && "$(uname -m)" == "arm64" ]]; then
    echo "==> Copying veritas-launcher (Apple Silicon)..."
    cp "$LAUNCHER_ARM" "$INSTALL_DIR/veritas-launcher"
    chmod +x "$INSTALL_DIR/veritas-launcher"
elif [[ -f "$LAUNCHER_INTEL" ]]; then
    echo "==> Copying veritas-launcher (Intel)..."
    cp "$LAUNCHER_INTEL" "$INSTALL_DIR/veritas-launcher"
    chmod +x "$INSTALL_DIR/veritas-launcher"
else
    echo "WARNING: veritas-launcher binary not found."
    echo "         Build it: cd veritas-launcher && bash build.sh"
fi

# ---- Copy veritas-runtime binary (PyInstaller bundle) ----
RUNTIME_BIN="$PROJECT_SRC/dist/veritas-runtime"
if [[ -f "$RUNTIME_BIN" ]]; then
    echo "==> Copying veritas-runtime binary..."
    cp "$RUNTIME_BIN" "$INSTALL_DIR/veritas-runtime"
    chmod +x "$INSTALL_DIR/veritas-runtime"
fi

# ---- Python venv + deps ----
PYTHON3=$(command -v python3 || echo "")
if [[ -z "$PYTHON3" ]]; then
    echo "ERROR: python3 not found. Install via: brew install python3" >&2
    exit 1
fi

if [[ ! -d "$VENV_DIR" ]]; then
    echo "==> Creating Python virtual environment..."
    "$PYTHON3" -m venv "$VENV_DIR"
fi

echo "==> Installing Python dependencies (this may take a few minutes)..."
"$VENV_DIR/bin/pip" install --upgrade pip --quiet
"$VENV_DIR/bin/pip" install -r "$INSTALL_DIR/requirements.txt" --quiet

# ---- spaCy model ----
if ! "$VENV_DIR/bin/python" -c "import spacy; spacy.load('en_core_web_lg')" &>/dev/null; then
    echo "==> Downloading spaCy language model (~750 MB)..."
    "$VENV_DIR/bin/python" -m spacy download en_core_web_lg --quiet
else
    echo "==> spaCy model already installed."
fi

# ---- Install launchd plist ----
echo "==> Installing launchd daemon..."
cp "$PROJECT_SRC/deploy/com.veritas.technologies.veritas.plist" "$PLIST_DST"
chown root:wheel "$PLIST_DST"
chmod 644 "$PLIST_DST"

# Unload if already loaded (for reinstall)
launchctl unload -w "$PLIST_DST" 2>/dev/null || true
launchctl load -w "$PLIST_DST"

sleep 3
echo ""
echo "============================================"
echo "  Veritas installed successfully (macOS)"
echo "============================================"
echo "  Dashboard: http://localhost:8000"
echo "  Logs:      tail -f /Library/Logs/Veritas/veritas.log"
echo "  Status:    sudo launchctl list com.veritas.technologies.veritas"
echo "  Stop:      sudo launchctl stop com.veritas.technologies.veritas"
echo ""
