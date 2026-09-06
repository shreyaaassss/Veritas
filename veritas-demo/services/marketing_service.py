"""
Simulated marketing-analytics service — writes to /logs/marketing-analytics.log

Violation type: PURPOSE_001
Trigger: raw 'phone' field appears in a marketing payload. The marketing-analytics
registry entry has consent_scope="deidentified_or_hashed_only" — any raw PII
is a purpose-limitation violation (raw identifiers are forbidden in this scope).
source_system must be exactly "marketing-analytics" to match the blinkit registry.
"""

import json
import os
import random
import time

from common import CAMPAIGN_SEGMENTS, EVENT_TYPES, fake_hashed_id, fake_phone, ts

LOG_FILE = "/logs/marketing-analytics.log"
RATE     = float(os.getenv("VIOLATION_RATE", "0.10"))
INTERVAL = float(os.getenv("EMIT_INTERVAL", "2.5"))


def clean_line() -> str:
    """Clean marketing event — only hashed identifiers, no raw PII."""
    payload = {
        "event":               "marketing_engagement",
        "hashed_customer_id":  fake_hashed_id(),
        "campaign_segment":    random.choice(CAMPAIGN_SEGMENTS),
        "event_type":          random.choice(EVENT_TYPES),
    }
    return f"[{ts()}] INFO marketing-analytics: {json.dumps(payload, separators=(',', ':'))}"


def violation_line() -> str:
    """PURPOSE_001 — raw phone leaked into a payload that must only contain hashed IDs."""
    payload = {
        "event":               "marketing_engagement",
        "hashed_customer_id":  fake_hashed_id(),
        "campaign_segment":    random.choice(CAMPAIGN_SEGMENTS),
        "event_type":          random.choice(EVENT_TYPES),
        "phone":               fake_phone(),   # ← raw PII in deidentified-only scope
    }
    return f"[{ts()}] INFO marketing-analytics: {json.dumps(payload, separators=(',', ':'))}"


if __name__ == "__main__":
    print(f"[marketing-analytics] starting — violation_rate={RATE}, interval={INTERVAL}s", flush=True)
    with open(LOG_FILE, "a", buffering=1) as f:
        while True:
            line = violation_line() if random.random() < RATE else clean_line()
            f.write(line + "\n")
            time.sleep(INTERVAL)
