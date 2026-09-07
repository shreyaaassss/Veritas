"""
Veritas Agent — Cross-Platform Service Manager
================================================
Unified CLI for managing the Veritas Agent service on any OS.

Usage:
    python manage.py install    -- install as OS service
    python manage.py start      -- start service
    python manage.py stop       -- stop service
    python manage.py restart    -- restart service
    python manage.py status     -- show status
    python manage.py uninstall  -- remove service
    python manage.py run        -- run directly (not as service, for testing)

The appropriate backend is selected automatically:
    Linux   → systemctl
    macOS   → launchctl
    Windows → Windows Service Control Manager (via pywin32)
"""

import platform
import subprocess
import sys
from pathlib import Path

_PLATFORM  = platform.system()
_AGENT_DIR = Path(__file__).parent

# ---- Linux (systemd) ----
_LINUX_SERVICE  = "veritas-agent"
_LINUX_PLIST    = _AGENT_DIR / "deploy" / "veritas-agent.service"
_LINUX_SVC_DEST = Path("/etc/systemd/system/veritas-agent.service")

# ---- macOS (launchd) ----
_MACOS_LABEL    = "com.veritas.technologies.agent"
_MACOS_PLIST    = _AGENT_DIR / "deploy" / "com.veritas.technologies.agent.plist"
_MACOS_SVC_DEST = Path("/Library/LaunchDaemons/com.veritas.technologies.agent.plist")

# ---- Windows ----
_WIN_SERVICE    = "VeritasAgent"


def _run(cmd: list, check=True) -> int:
    r = subprocess.run(cmd)
    if check and r.returncode != 0:
        print(f"Command failed: {' '.join(cmd)}", file=sys.stderr)
        sys.exit(r.returncode)
    return r.returncode


def install():
    if _PLATFORM == "Linux":
        if not _LINUX_SVC_DEST.exists():
            import shutil
            shutil.copy(_LINUX_PLIST, _LINUX_SVC_DEST)
            print(f"Copied service file to {_LINUX_SVC_DEST}")
        _run(["systemctl", "daemon-reload"])
        _run(["systemctl", "enable", _LINUX_SERVICE])
        print(f"[OK] Service '{_LINUX_SERVICE}' installed and enabled.")
        print(f"     Run: python manage.py start")

    elif _PLATFORM == "Darwin":
        import shutil
        shutil.copy(_MACOS_PLIST, _MACOS_SVC_DEST)
        subprocess.run(["chown", "root:wheel", str(_MACOS_SVC_DEST)])
        _run(["launchctl", "load", "-w", str(_MACOS_SVC_DEST)])
        print(f"[OK] Daemon '{_MACOS_LABEL}' installed and loaded.")

    elif _PLATFORM == "Windows":
        import windows_service
        sys.argv = [sys.argv[0], "install"]
        import win32serviceutil
        # Handled by windows_service.HandleCommandLine
        from windows_service import VeritasAgentService
        win32serviceutil.HandleCommandLine(VeritasAgentService)

    else:
        print(f"Unsupported platform: {_PLATFORM}")
        sys.exit(1)


def start():
    if _PLATFORM == "Linux":
        _run(["systemctl", "start", _LINUX_SERVICE])
        print(f"[OK] {_LINUX_SERVICE} started. Dashboard: check logs with journalctl -u veritas-agent -f")
    elif _PLATFORM == "Darwin":
        _run(["launchctl", "start", _MACOS_LABEL])
        print(f"[OK] {_MACOS_LABEL} started.")
    elif _PLATFORM == "Windows":
        _run(["python", str(_AGENT_DIR / "windows_service.py"), "start"])


def stop():
    if _PLATFORM == "Linux":
        _run(["systemctl", "stop", _LINUX_SERVICE])
    elif _PLATFORM == "Darwin":
        _run(["launchctl", "stop", _MACOS_LABEL])
    elif _PLATFORM == "Windows":
        _run(["python", str(_AGENT_DIR / "windows_service.py"), "stop"])
    print(f"[OK] Agent stopped.")


def restart():
    stop()
    import time; time.sleep(1)
    start()


def status():
    if _PLATFORM == "Linux":
        _run(["systemctl", "status", _LINUX_SERVICE, "--no-pager"], check=False)
    elif _PLATFORM == "Darwin":
        r = _run(["launchctl", "list", _MACOS_LABEL], check=False)
        if r == 0:
            print(f"Daemon '{_MACOS_LABEL}': RUNNING")
        else:
            print(f"Daemon '{_MACOS_LABEL}': not running")
    elif _PLATFORM == "Windows":
        _run(["python", str(_AGENT_DIR / "windows_service.py"), "status"], check=False)


def uninstall():
    stop_result = None
    if _PLATFORM == "Linux":
        subprocess.run(["systemctl", "stop", _LINUX_SERVICE])
        _run(["systemctl", "disable", _LINUX_SERVICE])
        if _LINUX_SVC_DEST.exists():
            _LINUX_SVC_DEST.unlink()
        _run(["systemctl", "daemon-reload"])
        print(f"[OK] Service '{_LINUX_SERVICE}' uninstalled.")
    elif _PLATFORM == "Darwin":
        subprocess.run(["launchctl", "unload", "-w", str(_MACOS_SVC_DEST)])
        if _MACOS_SVC_DEST.exists():
            _MACOS_SVC_DEST.unlink()
        print(f"[OK] Daemon '{_MACOS_LABEL}' uninstalled.")
    elif _PLATFORM == "Windows":
        subprocess.run(["python", str(_AGENT_DIR / "windows_service.py"), "stop"])
        _run(["python", str(_AGENT_DIR / "windows_service.py"), "remove"])


def run_direct():
    """Run the agent directly (not as a service) for testing."""
    import subprocess
    config = _AGENT_DIR / "agent-config.yaml"
    if not config.exists():
        config = _AGENT_DIR / "agent-config.yaml.example"
    subprocess.run([sys.executable, str(_AGENT_DIR / "agent.py"), "--config", str(config)])


_COMMANDS = {
    "install":   install,
    "start":     start,
    "stop":      stop,
    "restart":   restart,
    "status":    status,
    "uninstall": uninstall,
    "remove":    uninstall,
    "run":       run_direct,
}

if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in _COMMANDS:
        print(f"Usage: python manage.py <{'|'.join(_COMMANDS)}>\n")
        print("  install    Install as OS service (auto-start on boot)")
        print("  start      Start the service")
        print("  stop       Stop the service")
        print("  restart    Restart the service")
        print("  status     Show service status")
        print("  uninstall  Remove the service")
        print("  run        Run directly (for testing)")
        sys.exit(1)

    _COMMANDS[sys.argv[1]]()
