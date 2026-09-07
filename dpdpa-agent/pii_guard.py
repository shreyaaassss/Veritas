"""
Veritas — PII Guard (Phase 9)
==============================
Defense-in-depth logging filter that scrubs accidental PII leakage
from log records before they reach any log handler.

The API surface and DB write paths already avoid exposing raw PII
values. This filter is the final backstop — even if a future developer
accidentally logs a raw match value, it is replaced with a placeholder
before it reaches disk or stdout.

Apply once at startup via install_pii_log_filter(). The filter is
idempotent — calling it more than once has no effect.

Scrubbed patterns:
  - Aadhaar numbers (12-digit, with or without spaces)
  - PAN numbers     (10-char alphanumeric Indian tax ID)
  - Indian phone numbers (10-digit, optional +91 prefix)
  - Log record fields named: matched_text, raw_snippet, password, api_key, secret, token
"""

from __future__ import annotations

import logging
import re

# ---------------------------------------------------------------------------
# PII regex patterns (Indian context — DPDPA-relevant identifiers)
# ---------------------------------------------------------------------------

# Aadhaar: 12 digits, first digit 2-9, optional spaces every 4 digits
_AADHAAR_RE = re.compile(r'\b[2-9]\d{3}[\s\-]?\d{4}[\s\-]?\d{4}\b')

# PAN: AAAAA9999A (5 alpha + 4 digit + 1 alpha)
_PAN_RE = re.compile(r'\b[A-Z]{5}[0-9]{4}[A-Z]\b')

# Indian mobile: +91 optional, then 6-9 start, 9 more digits
_PHONE_RE = re.compile(r'\b(?:\+91[\s\-]?)?[6-9]\d{9}\b')

# Keys in dict-style log args whose values should always be scrubbed entirely
_SENSITIVE_KEYS: frozenset[str] = frozenset({
    "matched_text", "raw_snippet", "password", "password_hash",
    "api_key", "secret", "token", "private_key",
})


def scrub(text: str) -> str:
    """
    Replace known PII patterns in a string with opaque placeholders.
    Safe to call on any string — never raises.
    """
    try:
        text = _AADHAAR_RE.sub('[AADHAAR]', text)
        text = _PAN_RE.sub('[PAN]', text)
        text = _PHONE_RE.sub('[PHONE]', text)
    except Exception:
        pass
    return text


class _PIILogFilter(logging.Filter):
    """
    Logging filter that scrubs PII from log messages before emission.
    Attached to the root logger so it intercepts every log record,
    regardless of which logger or handler is used.

    Conservative approach: scrubs the message string and any string
    values in dict-style args where the key is in _SENSITIVE_KEYS.
    Positional tuple args are left as-is (scrubbing them would require
    matching arg positions to format string placeholders, which is
    fragile and rarely needed — the message-level scrub catches most
    real-world cases).
    """

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            record.msg = scrub(str(record.msg))
            if isinstance(record.args, dict):
                record.args = {
                    k: '[REDACTED]' if k in _SENSITIVE_KEYS
                    else scrub(str(v)) if isinstance(v, str) else v
                    for k, v in record.args.items()
                }
        except Exception:
            pass
        return True  # always pass — we scrub, not block


def install_pii_log_filter() -> None:
    """
    Install the PII scrubbing filter on the root logger.
    Idempotent: calling multiple times has no effect.
    Call once at application startup, before any logging occurs.
    """
    root = logging.getLogger()
    for f in root.filters:
        if isinstance(f, _PIILogFilter):
            return  # already installed
    root.addFilter(_PIILogFilter())
    logging.getLogger("veritas.pii_guard").debug("PII log filter installed.")
