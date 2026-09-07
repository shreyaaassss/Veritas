"""
Veritas Agent — Windows Service
=================================
Registers the Veritas Agent as a Windows Service so it starts automatically
with Windows and runs without a logged-in user.

Usage (run as Administrator):
    python windows_service.py install     -- install the service
    python windows_service.py start       -- start the service
    python windows_service.py stop        -- stop the service
    python windows_service.py remove      -- uninstall the service
    python windows_service.py status      -- check service status

Or use the batch installer: deploy\\install-agent-windows.bat

Requirements:
    pip install pywin32

The service expects agent-config.yaml in the same directory as this script
(or set VERITAS_AGENT_CONFIG env var to a custom path).
"""

import os
import subprocess
import sys
import time
from pathlib import Path

# pywin32 is Windows-only
try:
    import win32event
    import win32service
    import win32serviceutil
    import servicemanager
    _WIN32 = True
except ImportError:
    _WIN32 = False

_SERVICE_NAME    = "VeritasAgent"
_SERVICE_DISPLAY = "Veritas Agent"
_SERVICE_DESC    = (
    "Veritas DPDPA compliance telemetry forwarder. "
    "Monitors application logs and forwards events to the Veritas Server."
)
_AGENT_DIR       = Path(__file__).parent
_CONFIG_DEFAULT  = _AGENT_DIR / "agent-config.yaml"
_RESTART_DELAY   = 10  # seconds before restarting after a crash


def _config_path() -> Path:
    env = os.environ.get("VERITAS_AGENT_CONFIG", "").strip()
    return Path(env) if env else _CONFIG_DEFAULT


def _python_exe() -> str:
    """Return the Python executable to use for running agent.py."""
    # Prefer venv Python if present (same directory)
    venv_python = _AGENT_DIR / "venv" / "Scripts" / "python.exe"
    if venv_python.exists():
        return str(venv_python)
    return sys.executable


if _WIN32:
    class VeritasAgentService(win32serviceutil.ServiceFramework):
        _svc_name_         = _SERVICE_NAME
        _svc_display_name_ = _SERVICE_DISPLAY
        _svc_description_  = _SERVICE_DESC

        def __init__(self, args):
            win32serviceutil.ServiceFramework.__init__(self, args)
            self._stop_event = win32event.CreateEvent(None, 0, 0, None)
            self._process    = None

        def SvcStop(self):
            self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
            win32event.SetEvent(self._stop_event)
            if self._process:
                try:
                    self._process.terminate()
                except Exception:
                    pass

        def SvcDoRun(self):
            servicemanager.LogMsg(
                servicemanager.EVENTLOG_INFORMATION_TYPE,
                servicemanager.PYS_SERVICE_STARTED,
                (self._svc_name_, ""),
            )

            agent_py = str(_AGENT_DIR / "agent.py")
            config   = str(_config_path())
            python   = _python_exe()
            cmd      = [python, agent_py, "--config", config]

            while True:
                # Check if stop was requested before starting
                if win32event.WaitForSingleObject(self._stop_event, 0) == win32event.WAIT_OBJECT_0:
                    break

                try:
                    self._process = subprocess.Popen(
                        cmd,
                        cwd=str(_AGENT_DIR),
                        env={**os.environ, "PYTHONUNBUFFERED": "1"},
                    )
                    self._process.wait()
                except Exception as e:
                    servicemanager.LogErrorMsg(f"VeritasAgent process error: {e}")

                # Wait _RESTART_DELAY seconds before restarting (unless stop requested)
                if win32event.WaitForSingleObject(self._stop_event, _RESTART_DELAY * 1000) == win32event.WAIT_OBJECT_0:
                    break

            servicemanager.LogMsg(
                servicemanager.EVENTLOG_INFORMATION_TYPE,
                servicemanager.PYS_SERVICE_STOPPED,
                (self._svc_name_, ""),
            )


def _status():
    """Print the current service status (Windows only)."""
    if not _WIN32:
        print("Windows-only command.")
        return
    try:
        status = win32serviceutil.QueryServiceStatus(_SERVICE_NAME)
        states = {
            win32service.SERVICE_STOPPED:         "STOPPED",
            win32service.SERVICE_START_PENDING:   "START PENDING",
            win32service.SERVICE_STOP_PENDING:    "STOP PENDING",
            win32service.SERVICE_RUNNING:         "RUNNING",
            win32service.SERVICE_CONTINUE_PENDING: "CONTINUE PENDING",
            win32service.SERVICE_PAUSE_PENDING:   "PAUSE PENDING",
            win32service.SERVICE_PAUSED:          "PAUSED",
        }
        state = states.get(status[1], "UNKNOWN")
        print(f"Service '{_SERVICE_NAME}': {state}")
    except Exception:
        print(f"Service '{_SERVICE_NAME}': not installed")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "status":
        _status()
        sys.exit(0)

    if not _WIN32:
        print("ERROR: pywin32 is required for Windows service management.")
        print("       Run: pip install pywin32")
        sys.exit(1)

    win32serviceutil.HandleCommandLine(VeritasAgentService)
