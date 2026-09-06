#!/usr/bin/env bash
# Veritas Launcher — Cross-Platform Build Script
# ================================================
# Builds veritas-launcher for Linux, macOS Intel, and macOS Apple Silicon.
# Run from any machine that has Go installed.
#
# Usage:
#   cd veritas-launcher
#   bash build.sh
#
# Output:
#   ../dist/linux/veritas-launcher          (Linux x64)
#   ../dist/mac-intel/veritas-launcher      (macOS Intel x64)
#   ../dist/mac-arm/veritas-launcher        (macOS Apple Silicon arm64)

set -euo pipefail

cd "$(dirname "$0")"

export PATH="/c/Program Files/Go/bin:$PATH"  # Windows/Git Bash
export PATH="/usr/local/go/bin:$PATH"         # Linux/Mac standard install

# Verify Go
if ! command -v go &>/dev/null; then
    echo "ERROR: Go is not installed or not in PATH." >&2
    echo "Download from: https://go.dev/dl/" >&2
    exit 1
fi

echo "Go version: $(go version)"
echo ""

# Download dependencies
echo "==> go mod tidy..."
go mod tidy

# Create output directories
mkdir -p ../dist/linux ../dist/mac-intel ../dist/mac-arm

# ---------------------------------------------------------------------------
# Linux x64
# ---------------------------------------------------------------------------
echo "==> Building: Linux x64..."
GOOS=linux GOARCH=amd64 go build \
    -ldflags="-s -w" \
    -o "../dist/linux/veritas-launcher" \
    .
echo "    [OK] dist/linux/veritas-launcher ($(du -sh ../dist/linux/veritas-launcher | cut -f1))"

# ---------------------------------------------------------------------------
# macOS Intel (x86_64)
# ---------------------------------------------------------------------------
echo "==> Building: macOS Intel..."
GOOS=darwin GOARCH=amd64 go build \
    -ldflags="-s -w" \
    -o "../dist/mac-intel/veritas-launcher" \
    .
echo "    [OK] dist/mac-intel/veritas-launcher ($(du -sh ../dist/mac-intel/veritas-launcher | cut -f1))"

# ---------------------------------------------------------------------------
# macOS Apple Silicon (arm64) — M1/M2/M3
# ---------------------------------------------------------------------------
echo "==> Building: macOS Apple Silicon (arm64)..."
GOOS=darwin GOARCH=arm64 go build \
    -ldflags="-s -w" \
    -o "../dist/mac-arm/veritas-launcher" \
    .
echo "    [OK] dist/mac-arm/veritas-launcher ($(du -sh ../dist/mac-arm/veritas-launcher | cut -f1))"

echo ""
echo "============================================"
echo "  Build complete"
echo "============================================"
echo "  dist/linux/veritas-launcher       — deploy on Linux"
echo "  dist/mac-intel/veritas-launcher   — deploy on Mac Intel"
echo "  dist/mac-arm/veritas-launcher     — deploy on Mac M1/M2/M3"
echo ""
echo "Next steps:"
echo "  Linux:  Copy dist/linux/veritas-launcher to server + sudo bash deploy/install-linux.sh"
echo "  macOS:  Copy dist/mac-arm/veritas-launcher to Mac + sudo bash deploy/install-mac.sh"
