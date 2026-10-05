"""
Veritas Machine Fingerprint Tool
==================================
Run on the CLIENT'S machine to generate a hardware fingerprint.
The client sends this hash to Veritas, who uses it to generate a machine-bound .vlic.

Usage:
    python tools/fingerprint.py
    # → prints a 64-character hex fingerprint

Linux (fingerprint version 2, "v2:" + 64 hex chars):
  Anchored on /etc/machine-id, falling back to the primary disk serial. MAC
  address and hostname are NOT used (they change with docker, wifi, VPNs and
  renames). Must stay identical to dpdpa-agent/license.py, which the product
  uses to validate licenses; dpdpa-agent/test_license_fingerprint.py checks it.

Windows / macOS (legacy version 1, 64 hex chars):
  disk serial + MAC address + hostname + OS. To be migrated later.

Platform support for the disk serial:
  Windows  — wmic diskdrive get serialnumber
  Linux    — lsblk -dno SERIAL on sda / nvme0n1 / vda / xvda
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
    """
    Get disk serial on Linux via lsblk.
    Tries sda → nvme0n1 → vda → xvda to cover bare-metal, cloud NVMe,
    KVM and Xen instances. Must stay in sync with dpdpa-agent/license.py.
    """
    for dev in ("/dev/sda", "/dev/nvme0n1", "/dev/vda", "/dev/xvda"):
        try:
            output = subprocess.check_output(
                ["lsblk", "-dno", "SERIAL", dev],
                stderr=subprocess.DEVNULL,
                timeout=5,
            ).decode("utf-8", errors="replace").strip()
            if output:
                return output
        except Exception:
            pass
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


MACHINE_ID_PATHS = ("/etc/machine-id", "/var/lib/dbus/machine-id")


def linux_machine_id() -> str:
    """systemd/dbus machine-id (32 hex chars) or '' if unavailable."""
    for path in MACHINE_ID_PATHS:
        try:
            with open(path, "r", encoding="ascii") as f:
                value = f.read().strip().lower()
        except (OSError, UnicodeDecodeError):
            continue
        if value and all(c in "0123456789abcdef" for c in value):
            return value
    return ""


def machine_fingerprint() -> str:
    """
    Compute the machine fingerprint for this platform.
    Linux returns "v2:<64 hex>". Other platforms return the legacy 64-hex value.
    Raises RuntimeError if no stable machine identity can be determined (Linux).
    """
    system = platform.system()

    if system == "Linux":
        machine_id = linux_machine_id()
        if machine_id:
            kind, value = "machine-id", machine_id
        else:
            serial = get_disk_serial_linux()
            if serial == "NO_SERIAL":
                raise RuntimeError(
                    "Cannot determine a stable machine identity: /etc/machine-id "
                    "is missing and no disk serial is available."
                )
            kind, value = "disk-serial", serial
        raw = f"veritas-fp-v2|linux|{kind}|{value}"
        return "v2:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()

    mac  = hex(uuid.getnode())
    host = socket.gethostname()
    if system == "Windows":
        disk_serial = get_disk_serial_windows()
    elif system == "Darwin":
        disk_serial = get_disk_serial_macos()
    else:
        disk_serial = "NO_SERIAL"
    raw = f"{disk_serial}:{mac}:{host}:{system}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def main() -> None:
    try:
        fp = machine_fingerprint()
    except RuntimeError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
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
