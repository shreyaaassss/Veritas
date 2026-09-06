"""
Simulated delivery-partner-service — writes to /logs/delivery-partner-service.log

Violation type: RETENTION_001
Trigger: references the seeded stale delivery partner DP-4471 (Suresh K.), whose
Aadhaar record is 240 days old in the blinkit registry — 60 days past the 180-day
KYC retention limit. The rule engine checks the registry entry's created_at, so
ANY event from this source_system that contains an aadhaar will trigger the check,
and the seeded violation fires deterministically.

source_system must be exactly "delivery-partner-service" to match the blinkit registry.
"""

import json
import os
import random
import time
from datetime import datetime, timedelta, timezone

from common import fake_aadhaar, fake_name, fake_pan, fake_partner_id, ts

LOG_FILE = "/logs/delivery-partner-service.log"
RATE     = float(os.getenv("VIOLATION_RATE", "0.20"))
INTERVAL = float(os.getenv("EMIT_INTERVAL", "5.0"))

# Seeded stale partner — registered in blinkit registry as 240 days old (> 180-day limit)
STALE_PARTNER = {
    "partner_id":   "DP-4471",
    "name":         "Suresh K.",
    "aadhaar":      "5521 8890 3347",
    "onboarded_at": (datetime.now(timezone.utc) - timedelta(days=240)).strftime("%Y-%m-%dT%H:%M:%SZ"),
    "status":       "inactive",
}


def clean_line() -> str:
    """Fresh partner profile — within retention window, no violation."""
    choice = random.randint(0, 1)
    if choice == 0:
        partner_id   = fake_partner_id()
        days_ago     = random.randint(10, 90)
        return (
            f"[{ts()}] INFO delivery-partner-service: "
            f"partner {partner_id} profile OK, status: active, onboarded {days_ago} days ago"
        )
    else:
        partner_id = fake_partner_id()
        return (
            f"[{ts()}] INFO delivery-partner-service: "
            f"partner {partner_id} KYC verified, documents current"
        )


def violation_line() -> str:
    """RETENTION_001 — references DP-4471 whose aadhaar is past the 180-day retention window."""
    payload = {
        "event":        "partner_profile_fetch",
        "partner_id":   STALE_PARTNER["partner_id"],
        "name":         STALE_PARTNER["name"],
        "aadhaar":      STALE_PARTNER["aadhaar"],
        "onboarded_at": STALE_PARTNER["onboarded_at"],
        "status":       STALE_PARTNER["status"],
    }
    return (
        f"[{ts()}] INFO delivery-partner-service: "
        f"{json.dumps(payload, separators=(',', ':'))}"
    )


if __name__ == "__main__":
    print(f"[delivery-partner-service] starting — violation_rate={RATE}, interval={INTERVAL}s", flush=True)
    with open(LOG_FILE, "a", buffering=1) as f:
        while True:
            line = violation_line() if random.random() < RATE else clean_line()
            f.write(line + "\n")
            time.sleep(INTERVAL)
