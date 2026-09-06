"""
Veritas Runtime Path Resolver
==============================
Handles the difference between running from Python source (development)
and running from a PyInstaller bundle (production .exe).

When PyInstaller builds veritas-runtime.exe:
  - All .py files are compiled to bytecode and bundled inside the .exe
  - At runtime, they are extracted to a temp directory (sys._MEIPASS)
  - Path(__file__).parent points INSIDE the temp dir — not writable/persistent
  - The .exe itself lives in C:\Program Files\Veritas\

Two categories of paths:

  bundle_root()  — read-only bundled assets (HTML, JSON templates)
                   Dev:    the source directory (e.g. dpdpa-agent/)
                   Bundle: sys._MEIPASS (temp dir where assets are extracted)

  data_root()    — writable runtime files (SQLite DBs, org configs, license)
                   Dev:    dpdpa-agent/ (same as before)
                   Bundle: directory containing veritas-runtime.exe
                           OR override via VERITAS_DATA_DIR environment variable
                           (set by veritas-launcher.exe to C:\Program Files\Veritas\)

Usage:
    from runtime_paths import bundle_root, data_root

    # Read-only asset:
    html = bundle_root() / "dashboard" / "index.html"

    # Writable runtime file:
    db = data_root() / "evidence.db"
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def is_bundled() -> bool:
    """True when running inside a PyInstaller-built executable."""
    return getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS")


def bundle_root() -> Path:
    """
    Directory containing read-only bundled assets.

    Development: the dpdpa-agent/ source directory (Path(__file__).parent).
    Bundle:      sys._MEIPASS — the temp dir where PyInstaller extracts assets.
    """
    if is_bundled():
        return Path(sys._MEIPASS)  # type: ignore[attr-defined]
    return Path(__file__).parent


def data_root() -> Path:
    """
    Directory for writable runtime files: SQLite databases, org configs,
    license file, and logs.

    Priority:
      1. VERITAS_DATA_DIR environment variable (set by veritas-launcher.exe)
      2. Directory containing veritas-runtime.exe (when bundled)
      3. dpdpa-agent/ source directory (development)

    The Go launcher sets VERITAS_DATA_DIR to C:\\Program Files\\Veritas\\ so
    all writable files land in the installation directory regardless of
    where Windows extracts the temp bundle.
    """
    env = os.environ.get("VERITAS_DATA_DIR")
    if env:
        p = Path(env)
        p.mkdir(parents=True, exist_ok=True)
        return p

    if is_bundled():
        return Path(sys.executable).parent  # type: ignore[attr-defined]

    return Path(__file__).parent
