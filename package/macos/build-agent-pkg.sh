#!/bin/bash
# Build the Veritas Agent .pkg for macOS (Apple Silicon).
# Usage: bash package/macos/build-agent-pkg.sh <binary> <version> [arch]
set -e
export COPYFILE_DISABLE=1   # keep macOS ._* metadata files out of the package
BINARY=${1:-veritas-agent/dist/veritas-agent-runtime}
VERSION=${2:-1.0.0}
ARCH=${3:-arm64}
PKG_NAME="veritas-agent_${VERSION}_${ARCH}"
SRC=package/macos-agent
ROOT=/tmp/veritas-agent-pkg-root
SCRIPTS=/tmp/veritas-agent-pkg-scripts

echo "Building $PKG_NAME.pkg ..."
rm -rf "$ROOT" "$SCRIPTS"
mkdir -p "$ROOT/opt/veritas-agent" "$ROOT/usr/local/bin" "$SCRIPTS" dist

cp "$BINARY" "$ROOT/opt/veritas-agent/veritas-agent-runtime"
chmod 755 "$ROOT/opt/veritas-agent/veritas-agent-runtime"
cp "$SRC/com.veritas.agent.plist" "$SRC/config.yaml.example" "$ROOT/opt/veritas-agent/"
echo "$VERSION" > "$ROOT/opt/veritas-agent/VERSION"
cp "$SRC/veritas-agent-cli" "$ROOT/usr/local/bin/veritas-agent"
chmod 755 "$ROOT/usr/local/bin/veritas-agent"
cp "$SRC/scripts/preinstall" "$SRC/scripts/postinstall" "$SCRIPTS/"
chmod 755 "$SCRIPTS"/*
xattr -cr "$ROOT" "$SCRIPTS" 2>/dev/null || true   # no extended attributes, so no ._* files

pkgbuild \
  --root "$ROOT" \
  --scripts "$SCRIPTS" \
  --identifier com.veritas.agent.pkg \
  --version "$VERSION" \
  --install-location / \
  "dist/${PKG_NAME}.pkg"

echo "Built: dist/${PKG_NAME}.pkg"
