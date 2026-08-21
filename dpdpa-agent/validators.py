"""
Veritas DPDPA Agent — Identifier Validators (Phase 2)
========================================================
Pluggable, structural/checksum validation for identifiers an org declares
in its Org Config `identifiers` list (see org_config/schema.py).

This module is the ONE deliberate, explicit exception to Veritas's
"no domain hardcoding in pipeline logic" rule: it is allowed to *implement*
named validators like `is_valid_pan` and `is_valid_aadhaar` because that is
building entries in a generic plugin registry (`validator_registry`), not
special-casing pipeline behavior on an org's identity. No file outside this
one may branch on "if org == X" — this file just happens to know what a
PAN or an Aadhaar number looks like, the same way a phone-number library
knows what a phone number looks like.

Public interface:
  is_valid_pan(value: str) -> bool
  is_valid_aadhaar(value: str) -> bool
  validator_registry: dict[str, Callable[[str], bool]]
  register_validator(name: str, fn: Callable[[str], bool], overwrite: bool = False) -> None
  get_validator(name: str) -> Callable[[str], bool] | None
"""

from __future__ import annotations

import re
from typing import Callable, Dict, Optional

# ---------------------------------------------------------------------------
# PAN — structural/positional validation only (no public checksum digit)
# ---------------------------------------------------------------------------

# 4th character of a PAN denotes the holder's category. This is public,
# documented structure (Income Tax Department PAN format spec) — checking
# it is still "structural correctness," not a checksum, since there is no
# digit anywhere in a PAN whose value is mathematically derived from the
# others.
_PAN_HOLDER_TYPE_CODES = frozenset("ABCFGHLJPT")

_PAN_PATTERN = re.compile(r"^[A-Z]{5}[0-9]{4}[A-Z]$")


def is_valid_pan(value: str) -> bool:
    """
    PAN format: 5 letters, 4 digits, 1 letter (e.g. ABCDE1234F).

    IMPORTANT — this is NOT checksum validation. PAN has no public checksum
    digit: the 4th character encodes holder type (P=Individual, C=Company,
    H=HUF, A=AOP, B=BOI, G=Government, J=Artificial Judicial Person,
    L=Local Authority, F=Firm, T=Trust) and the 5th character is typically
    derived from the surname/entity name, neither of which is independently
    re-derivable from the rest of the string the way a checksum digit is.
    "Validation" here means structural/positional correctness: exact
    length, exact letter/digit layout, and a recognized holder-type code
    in position 4. A string can pass this check and still not be a real,
    issued PAN — this function narrows false positives, it does not prove
    authenticity.
    """
    if not isinstance(value, str):
        return False
    value = value.strip()
    if not _PAN_PATTERN.match(value):
        return False
    return value[3] in _PAN_HOLDER_TYPE_CODES


# ---------------------------------------------------------------------------
# Aadhaar — Verhoeff checksum validation
# ---------------------------------------------------------------------------

# Verhoeff algorithm tables, reproduced directly (no third-party dependency).
# Multiplication table (d): d[i][j] gives the group operation result.
_VERHOEFF_D = (
    (0, 1, 2, 3, 4, 5, 6, 7, 8, 9),
    (1, 2, 3, 4, 0, 6, 7, 8, 9, 5),
    (2, 3, 4, 0, 1, 7, 8, 9, 5, 6),
    (3, 4, 0, 1, 2, 8, 9, 5, 6, 7),
    (4, 0, 1, 2, 3, 9, 5, 6, 7, 8),
    (5, 9, 8, 7, 6, 0, 4, 3, 2, 1),
    (6, 5, 9, 8, 7, 1, 0, 4, 3, 2),
    (7, 6, 5, 9, 8, 2, 1, 0, 4, 3),
    (8, 7, 6, 5, 9, 3, 2, 1, 0, 4),
    (9, 8, 7, 6, 5, 4, 3, 2, 1, 0),
)

# Permutation table (p): p[i % 8][digit] permutes each digit by its position.
_VERHOEFF_P = (
    (0, 1, 2, 3, 4, 5, 6, 7, 8, 9),
    (1, 5, 7, 6, 2, 8, 3, 0, 9, 4),
    (5, 8, 0, 3, 7, 9, 6, 1, 4, 2),
    (8, 9, 1, 6, 0, 4, 3, 5, 2, 7),
    (9, 4, 5, 3, 1, 2, 6, 8, 7, 0),
    (4, 2, 8, 6, 5, 7, 3, 9, 0, 1),
    (2, 7, 9, 3, 8, 0, 6, 4, 1, 5),
    (7, 0, 4, 6, 9, 1, 3, 2, 5, 8),
)

# Inverse table — unused by pure validation (only needed to *generate* a
# check digit) but included for completeness/reuse, e.g. a future
# "generate a fixture Aadhaar" helper.
_VERHOEFF_INV = (0, 4, 3, 2, 1, 5, 6, 7, 8, 9)


def _verhoeff_is_valid(digits: str) -> bool:
    """
    Runs the Verhoeff checksum algorithm over a full digit string
    (including its trailing check digit). Returns True iff the running
    checksum reduces to 0, per the Verhoeff validation contract.
    """
    c = 0
    for i, ch in enumerate(reversed(digits)):
        c = _VERHOEFF_D[c][_VERHOEFF_P[i % 8][int(ch)]]
    return c == 0


def is_valid_aadhaar(value: str) -> bool:
    """
    Aadhaar format: 12 digits, first digit non-zero, with a Verhoeff
    checksum embedded across all 12 digits (the 12th digit is the check
    digit, but Verhoeff validates the whole string in one pass rather
    than isolating "the last digit" arithmetically).

    - Whitespace is stripped throughout (Aadhaar is conventionally shown
      as three space-separated 4-digit groups, e.g. "XXXX XXXX XXXX").
    - Rejects anything that isn't exactly 12 digits after stripping.
    - Rejects a leading zero (Aadhaar numbers never start with 0).
    - Returns True only if the Verhoeff checksum validates.
    """
    if not isinstance(value, str):
        return False
    stripped = re.sub(r"\s+", "", value)
    if len(stripped) != 12 or not stripped.isdigit():
        return False
    if stripped[0] == "0":
        return False
    return _verhoeff_is_valid(stripped)


# ---------------------------------------------------------------------------
# Pluggable validator registry
# ---------------------------------------------------------------------------

validator_registry: Dict[str, Callable[[str], bool]] = {
    "pan": is_valid_pan,
    "aadhaar": is_valid_aadhaar,
}


def register_validator(name: str, fn: Callable[[str], bool], overwrite: bool = False) -> None:
    """
    Registers a new validator at runtime, e.g. for an org-specific
    identifier once it gets a public checksum spec (APAAR is the
    canonical example this codebase is waiting on).

    Raises ValueError if `name` is already registered and overwrite is
    not explicitly True — this stops a typo'd re-registration from
    silently clobbering a built-in validator like "pan" or "aadhaar".
    """
    if name in validator_registry and not overwrite:
        raise ValueError(
            f"validator {name!r} is already registered. Pass overwrite=True "
            f"if you intend to replace it."
        )
    validator_registry[name] = fn


def get_validator(name: str) -> Optional[Callable[[str], bool]]:
    """
    Looks up a validator by name. Returns None (never raises) if `name`
    isn't registered — an org referencing an unregistered validator name
    (typo, or a not-yet-built validator like "apaar") is an expected,
    non-error state; callers fall back to pattern-match confidence.
    """
    return validator_registry.get(name)
