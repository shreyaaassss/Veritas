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
DATA_DIR=/var/lib/veritas

case "$1" in
  start)
    launchctl bootout system "$PLIST" 2>/dev/null || true
    sleep 1
    launchctl bootstrap system "$PLIST" && echo "Veritas started — http://localhost:8000" || echo "Failed to start. Try: sudo launchctl bootstrap system $PLIST"
    ;;
  stop)
    launchctl bootout system "$PLIST" 2>/dev/null || launchctl unload "$PLIST" 2>/dev/null || true
    echo "Veritas stopped."
    ;;
  restart)
    launchctl bootout system "$PLIST" 2>/dev/null || launchctl unload "$PLIST" 2>/dev/null || true
    sleep 2
    launchctl bootstrap system "$PLIST" && echo "Veritas restarted — http://localhost:8000"
    ;;
  status)   launchctl list | grep veritas || echo "Veritas not running" ;;
  logs)     tail -f /var/log/veritas/veritas.log ;;

  fingerprint)
    python3 - <<'PYEOF'
import hashlib, platform, socket, subprocess, uuid
def _disk_serial():
    try:
        out = subprocess.check_output(
            ["system_profiler", "SPHardwareDataType"], text=True, timeout=10,
        )
        for line in out.splitlines():
            if "Serial Number" in line:
                return line.split(":")[-1].strip()
    except Exception:
        pass
    return "NO_SERIAL"
mac    = hex(uuid.getnode())
host   = socket.gethostname()
system = platform.system()
serial = _disk_serial()
raw    = f"{serial}:{mac}:{host}:{system}"
fp     = hashlib.sha256(raw.encode()).hexdigest()
print("Veritas Machine Fingerprint")
print("=" * 44)
print("Send this fingerprint to your Veritas contact")
print("to receive your license file (veritas.vlic):")
print()
print(fp)
print()
print(f"System:   {system} {platform.release()}")
print(f"Hostname: {host}")
PYEOF
    ;;

  license)
    if [ -z "$2" ]; then
      echo "Usage: sudo veritas license <path-to-veritas.vlic>"
      exit 1
    fi
    cp "$2" "$DATA_DIR/veritas.vlic"
    chown _veritas:_veritas "$DATA_DIR/veritas.vlic" 2>/dev/null || true
    chmod 640 "$DATA_DIR/veritas.vlic"
    launchctl bootout system "$PLIST" 2>/dev/null || launchctl unload "$PLIST" 2>/dev/null || true
    sleep 2
    launchctl bootstrap system "$PLIST"
    echo "License installed. Veritas is restarting..."
    sleep 3
    launchctl list | grep -q veritas && echo "  Status: running" && echo "  Open: http://localhost:8000/setup"
    ;;

  version)  echo "Veritas 1.0.0" ;;
  *)
    echo "Veritas DPDPA Compliance Platform v1.0.0"
    echo ""
    echo "Usage: veritas <command>"
    echo ""
    echo "Commands:"
    echo "  start         Start the Veritas service"
    echo "  stop          Stop the Veritas service"
    echo "  restart       Restart the Veritas service"
    echo "  status        Show service status"
    echo "  logs          Stream service logs (Ctrl+C to stop)"
    echo "  fingerprint   Print machine fingerprint (send to Veritas for license)"
    echo "  license <f>   Install license file and restart"
    echo "  version       Print version"
    echo ""
    echo "Getting started:"
    echo "  1. sudo veritas fingerprint   → send output to your Veritas contact"
    echo "  2. sudo veritas license /path/to/veritas.vlic"
    echo "  3. sudo veritas start"
    echo "  4. Open http://localhost:8000/setup"
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
