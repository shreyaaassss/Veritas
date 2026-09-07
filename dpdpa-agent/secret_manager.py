"""
Veritas Secrets Management
===========================
Centralises all secret file handling with strict OS-level permissions.

Principles:
  - Secrets are NEVER logged
  - Secrets are NEVER returned in API responses
  - Secret files are chmod 600 (owner read/write only)
  - Secret directories are chmod 700 (owner only)
  - On Windows: ACL restriction is best-effort (no native chmod equivalent)

Secret locations (all under data_root()/secrets/):
  jwt_secret.key    — JWT signing secret (auto-generated, 64 bytes URL-safe)
  (TLS keys live in data_root()/certs/ — managed by tls.py)
"""

from __future__ import annotations

import logging
import os
import stat
from pathlib import Path
from typing import Optional

logger = logging.getLogger("veritas.secrets")


def _secrets_dir() -> Path:
    from runtime_paths import data_root
    d = data_root() / "secrets"
    d.mkdir(parents=True, exist_ok=True)
    _secure_dir(d)
    return d


def _secure_dir(path: Path) -> None:
    """Set directory permissions to 700 (owner only). No-op on Windows."""
    try:
        path.chmod(stat.S_IRWXU)
    except Exception:
        pass


def _secure_file(path: Path) -> None:
    """Set file permissions to 600 (owner read/write only). No-op on Windows."""
    try:
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    except Exception:
        pass


def audit_secret_permissions() -> dict:
    """
    Check that secret files have correct permissions.
    Returns a dict of {filename: status} for monitoring/health checks.
    """
    results = {}
    secrets_path = _secrets_dir()

    for path in secrets_path.iterdir():
        if not path.is_file():
            continue
        try:
            mode = path.stat().st_mode
            owner_only = bool(mode & stat.S_IRWXG == 0 and mode & stat.S_IRWXO == 0)
            results[path.name] = "ok" if owner_only else "permissions_too_open"
        except Exception as e:
            results[path.name] = f"error: {e}"

    # Check TLS key
    from runtime_paths import data_root
    tls_key = data_root() / "certs" / "server.key"
    if tls_key.exists():
        try:
            mode = tls_key.stat().st_mode
            owner_only = bool(mode & stat.S_IRWXG == 0 and mode & stat.S_IRWXO == 0)
            results["server.key"] = "ok" if owner_only else "permissions_too_open"
        except Exception:
            pass

    return results


def harden_all_secrets() -> None:
    """
    Apply secure permissions to all known secret files.
    Call on startup to ensure correct permissions after install.
    """
    try:
        secrets_dir = _secrets_dir()
        for f in secrets_dir.iterdir():
            if f.is_file():
                _secure_file(f)

        from runtime_paths import data_root
        certs_dir = data_root() / "certs"
        if certs_dir.exists():
            _secure_dir(certs_dir)
            key = certs_dir / "server.key"
            if key.exists():
                _secure_file(key)

        logger.debug("Secret file permissions hardened.")
    except Exception as e:
        logger.warning("Could not harden secret permissions (non-fatal): %s", e)
