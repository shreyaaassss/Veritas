"""
Password policy and temporary-password generation.

The policy follows current guidance (length and a blocklist matter more than composition
rules): at least 10 characters, not a well-known password, not made of one repeated
character, and not built from the user's own name or email. Applies to the first
administrator, newly created users, password changes and administrator resets.
"""
from __future__ import annotations

import re
import secrets
from typing import Optional

MIN_LENGTH = 10
MAX_LENGTH = 256

# Passwords that appear at the top of every breach list, plus product and keyboard patterns.
_COMMON = {
    "password", "password1", "password12", "password123", "password1234", "passw0rd", "p@ssw0rd",
    "p@ssword", "p@ssword1", "pa$$word", "qwerty", "qwerty123", "qwertyuiop", "qwerty12345",
    "1q2w3e4r", "1q2w3e4r5t", "1qaz2wsx", "zaq12wsx", "asdfghjkl", "zxcvbnm", "letmein",
    "letmein123", "welcome", "welcome1", "welcome123", "admin", "admin123", "admin1234",
    "administrator", "adminadmin", "root", "rootroot", "toor", "iloveyou", "monkey", "dragon",
    "football", "baseball", "superman", "batman", "trustno1", "sunshine", "princess", "master",
    "changeme", "changeme123", "default", "secret", "test", "test1234", "testing", "testtest",
    "abc123", "abcd1234", "abcdef", "abcdefgh", "abcdefghij", "123456", "1234567", "12345678",
    "123456789", "1234567890", "12345678910", "0123456789", "987654321", "111111", "000000",
    "123123", "123123123", "112233", "654321", "veritas", "veritas123", "veritas1234",
    "compliance", "compliance1", "dpdpa", "dpdpa123", "company", "company123", "india123",
    "india@123", "pass@123", "pass@1234", "admin@123", "admin@1234", "welcome@123", "qwerty@123",
}


def _strip_digits_and_symbols(value: str) -> str:
    return re.sub(r"[^a-z]", "", value.lower())


def check(password: str, username: Optional[str] = None, email: Optional[str] = None) -> Optional[str]:
    """Return an explanation if the password is not acceptable, else None."""
    if password is None or len(password) < MIN_LENGTH:
        return f"Password must be at least {MIN_LENGTH} characters."
    if len(password) > MAX_LENGTH:
        return f"Password must be at most {MAX_LENGTH} characters."
    if password != password.strip():
        return "Password must not start or end with a space."
    if len(set(password)) < 4:
        return "Password is too repetitive. Use a longer phrase or mix of characters."

    lowered = password.lower()
    if lowered in _COMMON or _strip_digits_and_symbols(password) in _COMMON:
        return "That password is too common. Choose something harder to guess."

    for label, value in (("username", username), ("email address", (email or "").split("@")[0])):
        value = (value or "").strip().lower()
        if len(value) >= 4 and value in lowered:
            return f"Password must not contain your {label}."
    return None


_ALPHABET_LOWER = "abcdefghjkmnpqrstuvwxyz"       # no i, l, o
_ALPHABET_UPPER = "ABCDEFGHJKLMNPQRSTUVWXYZ"       # no I, O
_ALPHABET_DIGIT = "23456789"                       # no 0, 1
_ALPHABET_SYMBOL = "#$%&*+-=?@"


def generate_temporary_password(length: int = 16) -> str:
    """A random password that satisfies the policy, readable aloud (no look-alike characters)."""
    pools = [_ALPHABET_LOWER, _ALPHABET_UPPER, _ALPHABET_DIGIT, _ALPHABET_SYMBOL]
    chars = [secrets.choice(p) for p in pools]
    everything = "".join(pools)
    chars += [secrets.choice(everything) for _ in range(length - len(chars))]
    secrets.SystemRandom().shuffle(chars)
    return "".join(chars)
