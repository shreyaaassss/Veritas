"""
The release version of this Veritas server, and which agent versions it supports.

The version is written into a VERSION file by the release build (and read from
$VERITAS_VERSION when set); a build from source reports "dev".
"""
from __future__ import annotations

import os
import re
import sys
from functools import lru_cache
from pathlib import Path
from typing import Optional, Tuple

# The oldest agent release this server fully supports. Agents older than this still work
# where possible but are flagged "outdated" in the dashboard and told to upgrade (older
# agents do not send the health report the dashboard relies on).
MIN_AGENT_VERSION = "1.0.18"


@lru_cache(maxsize=1)
def get_version() -> str:
    env = os.environ.get("VERITAS_VERSION", "").strip()
    if env:
        return env[:64]
    roots = []
    if getattr(sys, "_MEIPASS", None):
        roots.append(Path(sys._MEIPASS))
    roots.append(Path(__file__).resolve().parent)
    for root in roots:
        try:
            text = (root / "VERSION").read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if text:
            return text[:64]
    return "dev"


def parse(version: Optional[str]) -> Optional[Tuple[int, int, int]]:
    """'1.0.18', 'v1.0.18' or '1.0.18-rc1' -> (1, 0, 18). None when it is not a release number."""
    m = re.match(r"^v?(\d+)\.(\d+)\.(\d+)", (version or "").strip())
    return tuple(int(x) for x in m.groups()) if m else None


def is_agent_outdated(agent_version: Optional[str]) -> bool:
    """
    True when the agent is older than MIN_AGENT_VERSION. An agent that reports no version at
    all predates version reporting, so it is outdated. A non-numeric version such as "dev"
    (a build from source) is unknown, not outdated.
    """
    if not (agent_version or "").strip():
        return True
    parsed = parse(agent_version)
    if parsed is None:
        return False
    return parsed < parse(MIN_AGENT_VERSION)
