"""
Veritas — Database Field Encryption (Phase 17)
===============================================
Fernet symmetric encryption for sensitive database columns.

The evidence store's payload_json column contains the full breach record
including matched_text (the actual raw PII string that triggered detection).
This module encrypts it at rest so a raw SQLite file cannot be read to
extract PII without the application's key.

Key management:
  - Key file: data_root()/secrets/db.key (chmod 600)
  - Auto-generated on first use; never hardcoded
  - One key per installation (not per-tenant)

Encrypted value format:
  "enc:<url-safe-base64 Fernet token>"

  Values NOT starting with "enc:" are treated as unencrypted plaintext —
  this provides seamless backward compatibility with databases written
  before this phase was activated. New writes are always encrypted.

Dependency:
  cryptography >= 2.6  (already installed for tls.py)
"""

from __future__ import annotations

import logging
import stat
from pathlib import Path

logger = logging.getLogger("veritas.db_encryption")

_KEY_FILENAME = "db.key"
_ENC_PREFIX = "enc:"

_fernet_instance = None


def _secrets_dir() -> Path:
    """Return the secrets directory path (delegates to secret_manager)."""
    from secret_manager import _secrets_dir as _sd
    return _sd()


def _key_path() -> Path:
    return _secrets_dir() / _KEY_FILENAME


def _load_or_generate_key() -> bytes:
    """
    Load the Fernet key from db.key, generating it if absent.
    The generated key is written with chmod 600 immediately.
    Returns raw key bytes (URL-safe base64, 44 bytes).
    """
    path = _key_path()
    if path.exists():
        return path.read_bytes().strip()

    from cryptography.fernet import Fernet
    key = Fernet.generate_key()
    path.write_bytes(key)
    try:
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    except Exception:
        pass
    logger.info("Generated new database encryption key at %s", path)
    return key


def _fernet():
    """Lazy singleton Fernet instance — initialised on first use."""
    global _fernet_instance
    if _fernet_instance is None:
        from cryptography.fernet import Fernet
        _fernet_instance = Fernet(_load_or_generate_key())
    return _fernet_instance


def encrypt_field(plaintext: str) -> str:
    """
    Encrypt a plaintext string.

    Returns: "enc:<base64 ciphertext>"
    On any failure: returns plaintext unchanged (never crashes the pipeline;
    a failed encrypt is a security degradation, not a data loss).
    """
    try:
        token = _fernet().encrypt(plaintext.encode("utf-8"))
        return _ENC_PREFIX + token.decode("ascii")
    except Exception as exc:
        logger.warning("encrypt_field failed — storing plaintext: %s", exc)
        return plaintext


def decrypt_field(stored: str) -> str:
    """
    Decrypt a stored string.

    - "enc:<base64>" → Fernet-decrypted plaintext
    - Anything else  → returned as-is (unencrypted legacy row)

    On decryption failure: returns stored value as-is (audit trail preserved).
    """
    if not stored.startswith(_ENC_PREFIX):
        return stored
    try:
        ciphertext = stored[len(_ENC_PREFIX):].encode("ascii")
        return _fernet().decrypt(ciphertext).decode("utf-8")
    except Exception as exc:
        logger.warning("decrypt_field failed: %s", exc)
        return stored


def reset_fernet_for_tests() -> None:
    """Reset the Fernet singleton. Call in test teardown when tmp_path changes."""
    global _fernet_instance
    _fernet_instance = None
