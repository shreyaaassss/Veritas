"""
One-time setup code for creating the first administrator.

A fresh Veritas server has no users, so POST /api/auth/setup has to be open. Without more,
whoever reaches /setup first on a network would become SUPER_ADMIN. The setup code closes
that: it is created on the server, shown only to whoever can read the server's log or its
data directory (the operator), and the setup call is refused without it.

  * Created the first time it is needed (server start, or the first setup attempt) and kept
    in <data dir>/secrets/setup_code (owner-only) so it survives restarts until it is used.
  * Printed in the server's start-up banner (the service log) and shown by
    `veritas setup-code`.
  * Deleted as soon as the first administrator has been created.
  * For automated installs it can be preset with the VERITAS_SETUP_CODE environment variable;
    then no file is written.

Format: XXXX-XXXX-XXXX from an alphabet without look-alike characters (60 bits). Comparison
ignores case, spaces and dashes, and runs in constant time. The endpoint is also rate limited.
"""
from __future__ import annotations

import hmac
import logging
import os
import re
import secrets
from pathlib import Path
from typing import Optional

from runtime_paths import data_root

logger = logging.getLogger("veritas.setup_code")

ENV_VAR = "VERITAS_SETUP_CODE"
_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"   # no 0, O, 1 or I
_GROUPS, _GROUP_LEN = 3, 4


def code_path() -> Path:
    return data_root() / "secrets" / "setup_code"


def normalize(code: Optional[str]) -> str:
    """Case, spaces and dashes do not matter when a person types the code."""
    return re.sub(r"[\s-]", "", code or "").upper()


def generate() -> str:
    return "-".join(
        "".join(secrets.choice(_ALPHABET) for _ in range(_GROUP_LEN)) for _ in range(_GROUPS)
    )


def from_environment() -> Optional[str]:
    value = os.environ.get(ENV_VAR, "").strip()
    return value or None


def read_existing() -> Optional[str]:
    """The current code if there is one (preset or on disk); never creates one."""
    preset = from_environment()
    if preset:
        return preset
    path = code_path()
    try:
        text = path.read_text(encoding="ascii").strip()
    except FileNotFoundError:
        return None
    return text or None


def _read_when_complete(path: Path, attempts: int = 50) -> str:
    """Read the code file, waiting briefly if its creator has not finished writing it."""
    import time
    for _ in range(attempts):
        text = path.read_text(encoding="ascii").strip()
        if text:
            return text
        time.sleep(0.02)
    raise RuntimeError(f"The setup code file {path} exists but is empty")


def get_or_create() -> str:
    """The current code, created and stored (owner-only) if none exists yet."""
    existing = read_existing()
    if existing:
        return existing
    path = code_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path.parent, 0o700)
    except OSError:
        pass
    code = generate()

    # Write a private temporary file completely, then publish it with a hard link: that is
    # atomic and fails if the file already exists, so a request racing with this one either
    # wins with a complete file or reads the winner's complete file; never a half-written one.
    tmp = path.with_name(f".setup_code.{os.getpid()}.{secrets.token_hex(4)}.tmp")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="ascii") as f:
            f.write(code + "\n")
            f.flush()
            os.fsync(f.fileno())
        try:
            os.link(str(tmp), str(path))
        except FileExistsError:
            return _read_when_complete(path)
        except OSError:
            # No hard links on this filesystem: fall back to exclusive creation.
            try:
                fd2 = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            except FileExistsError:
                return _read_when_complete(path)
            with os.fdopen(fd2, "w", encoding="ascii") as f2:
                f2.write(code + "\n")
    finally:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass
    logger.info("Created a setup code for the first administrator (%s)", path)
    return code


def verify(candidate: Optional[str]) -> bool:
    """True if candidate is the current setup code. Creates one if none exists yet."""
    expected = normalize(get_or_create())
    given = normalize(candidate)
    return bool(given) and hmac.compare_digest(given.encode("utf-8"), expected.encode("utf-8"))


def clear() -> None:
    """Remove the stored code once setup is complete (a preset code needs no clean-up)."""
    try:
        code_path().unlink()
    except FileNotFoundError:
        pass
    except OSError as e:
        logger.warning("Could not remove the setup code file: %s", e)
