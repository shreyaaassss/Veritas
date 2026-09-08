#!/bin/bash
set -e
BINARY=${1:-dist/veritas-runtime}
VERSION=${2:-1.0.0}
ARCH=${3:-arm64}
PKG_NAME="veritas_${VERSION}_${ARCH}"

echo "Building $PKG_NAME.pkg ..."
mkdir -p /tmp/veritas-pkg-root/opt/veritas
mkdir -p /tmp/veritas-pkg-root/var/lib/veritas
mkdir -p /tmp/veritas-pkg-root/var/log/veritas
mkdir -p /tmp/veritas-pkg-scripts
mkdir -p /tmp/veritas-pkg-resources
mkdir -p dist

cp "$BINARY" /tmp/veritas-pkg-root/opt/veritas/veritas-runtime
chmod 755 /tmp/veritas-pkg-root/opt/veritas/veritas-runtime
cp package/macos/com.veritas.daemon.plist /tmp/veritas-pkg-root/opt/veritas/
cp package/macos/scripts/postinstall /tmp/veritas-pkg-scripts/
cp package/macos/scripts/preinstall /tmp/veritas-pkg-scripts/
chmod 755 /tmp/veritas-pkg-scripts/*

# Install veritas CLI
mkdir -p /tmp/veritas-pkg-root/usr/local/bin
cat > /tmp/veritas-pkg-root/usr/local/bin/veritas <<'CLISCRIPT'
#!/bin/bash
PLIST=/Library/LaunchDaemons/com.veritas.daemon.plist
case "$1" in
  start)    launchctl load -w $PLIST && echo "Veritas started — http://localhost:8000" ;;
  stop)     launchctl unload $PLIST ;;
  restart)  launchctl unload $PLIST; launchctl load -w $PLIST ;;
  status)   launchctl list | grep veritas || echo "Veritas not running" ;;
  logs)     tail -f /var/log/veritas/veritas.log ;;
  version)  echo "Veritas 1.0.0" ;;
  *)
    echo "Usage: veritas {start|stop|restart|status|logs|version}"
    echo "Dashboard: http://localhost:8000"
    ;;
esac
CLISCRIPT
chmod 755 /tmp/veritas-pkg-root/usr/local/bin/veritas

pkgbuild \
  --root /tmp/veritas-pkg-root \
  --scripts /tmp/veritas-pkg-scripts \
  --identifier com.veritas.pkg \
  --version "$VERSION" \
  --install-location / \
  "dist/${PKG_NAME}.pkg"

echo "Built: dist/${PKG_NAME}.pkg"
