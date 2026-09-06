"""
Veritas Machine Fingerprint Tool
==================================
Run on the CLIENT'S machine to generate a hardware fingerprint.
The client sends this hash to Veritas, who uses it to generate a machine-bound .vlic.

Usage:
    python tools/fingerprint.py
    # → prints a 64-character hex fingerprint

Collects: disk serial number + MAC address + hostname (platform-specific).
This is stable across reboots but changes if the machine is replaced.

Platform support:
  Windows  — wmic diskdrive get serialnumber
  Linux    — lsblk -dno SERIAL /dev/sda
  macOS    — system_profiler SPHardwareDataType (Serial Number field)

Compiled forms:
  Windows → veritas-fingerprint.exe  (PyInstaller)
  macOS   → veritas-fingerprint      (PyInstaller)
  Linux   → veritas-fingerprint      (PyInstaller)
"""

import hashlib
import platform
import socket
import subprocess
import sys
import uuid


def get_disk_serial_windows() -> str:
    """Get primary disk serial number via WMIC on Windows."""
    try:
        output = subprocess.check_output(
            "wmic diskdrive get serialnumber",
            shell=True,
            stderr=subprocess.DEVNULL,
            timeout=10,
        ).decode("utf-8", errors="replace")
        lines = [l.strip() for l in output.strip().splitlines() if l.strip()]
        # First line is the header "SerialNumber", rest are values
        serials = [l for l in lines if l.lower() != "serialnumber" and l]
        return serials[0] if serials else "NO_SERIAL"
    except Exception:
        return "NO_SERIAL"


def get_disk_serial_linux() -> str:
    """Get disk serial on Linux via lsblk."""
    try:
        output = subprocess.check_output(
            "lsblk -dno SERIAL /dev/sda",
            shell=True,
            stderr=subprocess.DEVNULL,
            timeout=10,
        ).decode("utf-8", errors="replace").strip()
        return output if output else "NO_SERIAL"
    except Exception:
        return "NO_SERIAL"


def get_disk_serial_macos() -> str:
    """Get hardware serial number on macOS via system_profiler."""
    try:
        output = subprocess.check_output(
            ["system_profiler", "SPHardwareDataType"],
            stderr=subprocess.DEVNULL,
            timeout=10,
        ).decode("utf-8", errors="replace")
        for line in output.splitlines():
            if "Serial Number" in line:
                return line.split(":")[-1].strip()
        return "NO_SERIAL"
    except Exception:
        return "NO_SERIAL"


def machine_fingerprint() -> str:
    """
    Compute a stable machine fingerprint from hardware identifiers.
    Returns a 64-character hex string (SHA-256).
    """
    mac      = hex(uuid.getnode())
    hostname = socket.gethostname()
    system   = platform.system()

    if system == "Windows":
        disk_serial = get_disk_serial_windows()
    elif system == "Linux":
        disk_serial = get_disk_serial_linux()
    elif system == "Darwin":
        disk_serial = get_disk_serial_macos()
    else:
        disk_serial = "NO_SERIAL"

    raw = f"{disk_serial}:{mac}:{hostname}:{system}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def main() -> None:
    fp = machine_fingerprint()
    print(f"Veritas Machine Fingerprint")
    print(f"===========================")
    print(f"Send this to support@veritas.io with your order:")
    print()
    print(fp)
    print()
    print(f"System:   {platform.system()} {platform.release()}")
    print(f"Hostname: {socket.gethostname()}")


if __name__ == "__main__":
    main()
