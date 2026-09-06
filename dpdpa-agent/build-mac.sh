#!/usr/bin/env bash
# Veritas Runtime — macOS Build Script
# =======================================
# Run this ON your Mac to produce veritas-runtime (macOS binary).
#
# Prerequisites (run once):
#   brew install python@3.12    # or use system Python 3.10+
#   pip3 install pyinstaller
#
# Usage:
#   cd dpdpa-agent
#   bash build-mac.sh
#
# Output:
#   dist/mac-arm/veritas-runtime    (Apple Silicon)
#   dist/mac-intel/veritas-runtime  (Intel)
#   (only the one matching your Mac is built — see --target-arch below)

set -euo pipefail
cd "$(dirname "$0")"

echo ""
echo "============================================"
echo "  Veritas Runtime — macOS Build"
echo "============================================"
echo "  Python: $(python3 --version)"
echo "  Arch:   $(uname -m)"
echo ""

# ---- Install PyInstaller if missing ----
if ! python3 -c "import PyInstaller" &>/dev/null; then
    echo "==> Installing PyInstaller..."
    pip3 install pyinstaller --quiet
fi

# ---- Install project dependencies ----
echo "==> Installing dependencies..."
pip3 install -r requirements.txt --quiet

# ---- Download spaCy model if missing ----
if ! python3 -c "import spacy; spacy.load('en_core_web_lg')" &>/dev/null; then
    echo "==> Downloading spaCy language model (~750 MB, this takes a few minutes)..."
    python3 -m spacy download en_core_web_lg
else
    echo "==> spaCy model already installed."
fi

# ---- Detect architecture and set output directory ----
ARCH=$(uname -m)
if [[ "$ARCH" == "arm64" ]]; then
    OUT_DIR="../dist/mac-arm"
    echo "==> Building for Apple Silicon (arm64)..."
else
    OUT_DIR="../dist/mac-intel"
    echo "==> Building for Intel (x86_64)..."
fi

mkdir -p "$OUT_DIR"

# ---- Build ----
echo "==> Running PyInstaller (this takes 5-15 minutes)..."
python3 -m PyInstaller veritas-mac.spec --noconfirm --distpath "$OUT_DIR"

echo ""
if [[ -f "$OUT_DIR/veritas-runtime" ]]; then
    SIZE=$(du -sh "$OUT_DIR/veritas-runtime" | cut -f1)
    echo "============================================"
    echo "  BUILD SUCCESSFUL"
    echo "============================================"
    echo "  Output: $OUT_DIR/veritas-runtime  ($SIZE)"
    echo ""
    echo "  Quick test (should print license error then exit):"
    echo "    $OUT_DIR/veritas-runtime --max-events 1"
    echo ""
    echo "  To install on this Mac:"
    echo "    Copy veritas.vlic next to the binary"
    echo "    sudo bash deploy/install-mac.sh"
    echo "============================================"
else
    echo "BUILD FAILED — check output above"
    exit 1
fi
