"""
Veritas License Validator (RSA)
================================
Reads and validates the veritas.vlic license file.
Uses RSA public key verification — the private key never ships with this product.

Called by:
  - run_pipeline.py on every startup (hard gate — exits if invalid)
  - dashboard/server.py GET /api/license (returns metadata for dashboard badge)

License file format (veritas.vlic):
    -----BEGIN VERITAS LICENSE-----
    <base64(payload_json)>:<base64(RSA_PSS_SHA256_signature)>
    -----END VERITAS LICENSE-----

Security model:
  - Only the holder of tools/private_key.pem (Veritas company) can generate valid licenses.
  - This file ships with the product and contains only the PUBLIC key.
  - Even if a client reads every byte of this file, they cannot forge a license.
  - Machine fingerprint in the payload prevents copying a license to another machine.
"""

from __future__ import annotations

import base64
import hashlib
import json
import platform
import socket
import subprocess
import uuid
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

# ---------------------------------------------------------------------------
# RSA Public Key — embedded at build time from tools/public_key.pem
# The corresponding private key lives only on Veritas's machines.
# ---------------------------------------------------------------------------
_PUBLIC_KEY_PEM = b"""-----BEGIN PUBLIC KEY-----
MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAuCG3FJivv9ISob+SIPhg
fDR4R3Q69dL2Rsn+jY18FQ45/RSXuWeFdAJeIkqelDXjg0Z6QEeZ8ZaPrZ5iyxCT
RovDk8jsEXQXXtVjZ+HaY9Oe/1OpDH7CCX0Dh0igpnRou9z3+0FGE2QPGf7+D5CS
LPFVXACuYA8y7auxEjNUtad8BjHW5ZTcqNLBh9hqjqRFp4Ns903xTpEoYQEKHEhD
aUnjyApwpQdyxLQflHBqXMO/5lTX1+BbSy7p9ZriHxDSM2JndrxNb2QX4pngJFQI
bYJwV+U4aL5Rk3dF5qKk9ESg5f0ypSnQuGE/YBjJb0Acu5oTEInwaYw19ZKShu8K
6QIDAQAB
-----END PUBLIC KEY-----"""

from runtime_paths import data_root as _data_root
_LICENSE_PATH = _data_root() / "veritas.vlic"

_VLIC_HEADER = "-----BEGIN VERITAS LICENSE-----"
_VLIC_FOOTER = "-----END VERITAS LICENSE-----"


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------

class LicenseError(Exception):
    """Raised when the license file is missing, invalid, expired, or not for this machine."""


@dataclass
class LicenseInfo:
    org:            str
    tier:           str
    expiry:         str
    issued:         str
    days_remaining: int
    machine_bound:  bool   # True if the license carries a fingerprint


# ---------------------------------------------------------------------------
# Machine fingerprint (matches tools/fingerprint.py)
# ---------------------------------------------------------------------------

def _disk_serial() -> str:
    """
    Return the primary disk serial number. Must produce the same value as
    tools/fingerprint.py on every platform (they are kept in sync).

    Linux: tries sda → nvme0n1 → vda → xvda in order; returns first non-empty
           serial found. This covers bare-metal (sda/sdb), cloud NVMe (nvme0n1),
           KVM/QEMU (vda) and older Xen EC2 (xvda).
    macOS: reads hardware serial from system_profiler.
    Windows: reads disk serial from WMIC.
    """
    system = platform.system()
    try:
        if system == "Windows":
            out = subprocess.check_output(
                "wmic diskdrive get serialnumber",
                shell=True, stderr=subprocess.DEVNULL, timeout=10,
            ).decode("utf-8", errors="replace")
            lines = [l.strip() for l in out.strip().splitlines() if l.strip()]
            serials = [l for l in lines if l.lower() != "serialnumber" and l]
            return serials[0] if serials else "NO_SERIAL"
        elif system == "Linux":
            for dev in ("/dev/sda", "/dev/nvme0n1", "/dev/vda", "/dev/xvda"):
                try:
                    out = subprocess.check_output(
                        ["lsblk", "-dno", "SERIAL", dev],
                        stderr=subprocess.DEVNULL, timeout=5,
                    ).decode("utf-8", errors="replace").strip()
                    if out:
                        return out
                except Exception:
                    pass
            return "NO_SERIAL"
        elif system == "Darwin":
            out = subprocess.check_output(
                ["system_profiler", "SPHardwareDataType"],
                stderr=subprocess.DEVNULL, timeout=10,
            ).decode("utf-8", errors="replace")
            for line in out.splitlines():
                if "Serial Number" in line:
                    return line.split(":")[-1].strip()
    except Exception:
        pass
    return "NO_SERIAL"


def current_fingerprint() -> str:
    """Compute this machine's fingerprint. Must match tools/fingerprint.py."""
    mac      = hex(uuid.getnode())
    hostname = socket.gethostname()
    system   = platform.system()
    serial   = _disk_serial()
    raw      = f"{serial}:{mac}:{hostname}:{system}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# License validation
# ---------------------------------------------------------------------------

def validate_license(path: Path = _LICENSE_PATH) -> LicenseInfo:
    """
    Read and validate a Veritas .vlic license file.

    Checks (in order):
      1. File exists
      2. File has correct PEM-like header/footer
      3. RSA-PSS signature is valid (using embedded public key)
      4. Payload decodes to valid JSON with required fields
      5. Expiry date has not passed
      6. Machine fingerprint matches (if license is machine-bound)

    Returns LicenseInfo on success.
    Raises LicenseError with a user-friendly message on any failure.
    """

    # 1. File must exist
    if not path.exists():
        raise LicenseError(
            "Veritas license file (veritas.vlic) not found.\n"
            "Place your veritas.vlic file in the dpdpa-agent/ directory.\n"
            "Contact support@veritas.io to obtain a license."
        )

    raw = path.read_text(encoding="utf-8").strip()

    # 2. Parse PEM-like structure
    if not (raw.startswith(_VLIC_HEADER) and raw.endswith(_VLIC_FOOTER)):
        raise LicenseError(
            "Veritas license file is malformed or has been tampered with.\n"
            "Contact support@veritas.io for a replacement license."
        )

    body = raw[len(_VLIC_HEADER):raw.rfind(_VLIC_FOOTER)].strip()

    if ":" not in body:
        raise LicenseError(
            "Veritas license file format is invalid.\n"
            "Contact support@veritas.io for a replacement license."
        )

    payload_b64, sig_b64 = body.split(":", 1)

    # 3. Verify RSA-PSS signature
    try:
        signature = base64.b64decode(sig_b64)
    except Exception:
        raise LicenseError("Veritas license signature encoding is corrupt.")

    public_key = serialization.load_pem_public_key(_PUBLIC_KEY_PEM)

    try:
        public_key.verify(
            signature,
            payload_b64.encode("ascii"),
            padding.PSS(
                mgf=padding.MGF1(hashes.SHA256()),
                salt_length=padding.PSS.MAX_LENGTH,
            ),
            hashes.SHA256(),
        )
    except InvalidSignature:
        raise LicenseError(
            "Veritas license signature is invalid or has been tampered with.\n"
            "Contact support@veritas.io for a replacement license."
        )
    except Exception:
        raise LicenseError(
            "Veritas license signature verification failed.\n"
            "Contact support@veritas.io for a replacement license."
        )

    # 4. Decode and parse payload
    try:
        payload_json = base64.b64decode(payload_b64).decode("utf-8")
        payload = json.loads(payload_json)
    except Exception:
        raise LicenseError(
            "Veritas license payload is corrupt.\n"
            "Contact support@veritas.io for a replacement license."
        )

    # 5. Check expiry
    expiry_str = payload.get("expiry")
    if not expiry_str:
        raise LicenseError("Veritas license is missing an expiry date.")

    try:
        expiry_date = date.fromisoformat(expiry_str)
    except ValueError:
        raise LicenseError(f"Veritas license expiry date '{expiry_str}' is malformed.")

    today = date.today()
    if today > expiry_date:
        raise LicenseError(
            f"Veritas license expired on {expiry_str}.\n"
            "Contact support@veritas.io to renew your license."
        )

    # 6. Check machine fingerprint (if license is machine-bound)
    license_fingerprint = payload.get("fingerprint")
    machine_bound = bool(license_fingerprint)

    if license_fingerprint:
        this_machine = current_fingerprint()
        if this_machine != license_fingerprint:
            raise LicenseError(
                "Veritas license is not valid for this machine.\n"
                "This license was issued for a different server.\n"
                "Contact support@veritas.io to transfer your license."
            )

    return LicenseInfo(
        org=payload.get("org", "unknown"),
        tier=payload.get("tier", "unknown"),
        expiry=expiry_str,
        issued=payload.get("issued", "unknown"),
        days_remaining=(expiry_date - today).days,
        machine_bound=machine_bound,
    )
