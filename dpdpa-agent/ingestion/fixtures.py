"""
DPDPA Compliance Agent — Ingestion Fixtures
==============================================
Realistic-looking (but entirely synthetic) Blinkit content used by both
generators: names, phone numbers, Aadhaar/PAN-shaped strings, addresses,
order references, and template strings for log lines / API payloads.

No real PII. All values are deterministically fake — Aadhaar/PAN numbers
here do NOT pass real checksum validation and are not associated with any
real person.
"""

from __future__ import annotations

import random

# ---------------------------------------------------------------------------
# Name pools
# ---------------------------------------------------------------------------

FIRST_NAMES = [
    "Priya", "Rahul", "Ananya", "Vikram", "Sneha", "Arjun", "Kavya", "Rohan",
    "Meera", "Aditya", "Isha", "Karan", "Divya", "Siddharth", "Pooja", "Aman",
]

LAST_NAMES = [
    "Sharma", "Nair", "Patel", "Reddy", "Iyer", "Singh", "Gupta", "Menon",
    "Rao", "Kulkarni", "Verma", "Joshi", "Chatterjee", "Desai", "Bhat",
]


def fake_name() -> str:
    return f"{random.choice(FIRST_NAMES)} {random.choice(LAST_NAMES)}"


# ---------------------------------------------------------------------------
# Fake PII value generators — shape-valid, not checksum-valid
# ---------------------------------------------------------------------------

def fake_phone() -> str:
    """Indian mobile shape: starts 6-9, 10 digits total."""
    return f"{random.choice('6789')}{random.randint(10**8, 10**9 - 1)}"


def fake_aadhaar() -> str:
    """Aadhaar shape: 4-4-4 digit groups. Not a real/valid Aadhaar number."""
    return f"{random.randint(1000,9999)} {random.randint(1000,9999)} {random.randint(1000,9999)}"


def fake_pan() -> str:
    """PAN shape: 5 letters, 4 digits, 1 letter. Not a real/valid PAN."""
    letters1 = "".join(random.choices("ABCDEFGHIJKLMNOPQRSTUVWXYZ", k=5))
    digits = random.randint(1000, 9999)
    letter2 = random.choice("ABCDEFGHIJKLMNOPQRSTUVWXYZ")
    return f"{letters1}{digits}{letter2}"


def fake_email(name: str) -> str:
    handle = name.lower().replace(" ", ".")
    domain = random.choice(["gmail.com", "yahoo.com", "outlook.com"])
    return f"{handle}{random.randint(1,999)}@{domain}"


def fake_address() -> str:
    flat = f"Flat {random.randint(1,12)}{random.choice('ABCD')}"
    building = random.choice(["Green Meadows", "Sunrise Apartments", "Palm Residency", "Silver Oaks"])
    area = random.choice(["Koramangala", "Baner", "Andheri West", "Indiranagar", "Whitefield"])
    city = random.choice(["Bengaluru", "Pune", "Mumbai"])
    return f"{flat}, {building}, {area}, {city}"


def fake_order_id() -> str:
    return f"BLK-{random.randint(100000, 999999)}"


def fake_bank_account() -> str:
    return f"XXXXXXXX{random.randint(1000,9999)}"


# ---------------------------------------------------------------------------
# The seeded retention-violation delivery partner (aligns with Phase 1)
# ---------------------------------------------------------------------------
# Same identity as registry/seed_registry.py's DELIVERY_PARTNERS_ENTRIES
# seeded-violation row: an inactive partner whose Aadhaar record is 240
# days old against a 180-day retention window. Field name ("aadhaar") and
# source_system ("delivery-partner-service") match exactly so Phase 4 can
# resolve get_registry_entry("aadhaar", "delivery-partner-service") against
# whatever this generator emits and land on the same registry row.
STALE_DELIVERY_PARTNER = {
    "partner_id": "DP-4471",
    "name": "Suresh K.",
    "aadhaar": "5521 8890 3347",
    "onboarded_days_ago": 240,  # > 180-day retention_days -> RETENTION_001
    "status": "inactive",
}


# ---------------------------------------------------------------------------
# Marketing event "internal-only type" copy-paste victims
# ---------------------------------------------------------------------------
# Field names here intentionally match Phase 1's marketing_events registry
# entries exactly (hashed_customer_id, campaign_segment, event_type, and
# the seeded raw 'phone' carrier) so Phase 4's lookups resolve correctly.
CAMPAIGN_SEGMENTS = ["high_value_reorder", "dormant_30d", "first_order_incentive", "festive_push"]
EVENT_TYPES = ["email_open", "push_click", "cart_abandon_nudge_sent", "coupon_redeemed"]
