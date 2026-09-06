"""
Shared fake data helpers for Veritas demo simulation services.
All PII patterns match what the Veritas detection engine recognises.
"""

import random
from datetime import datetime, timezone

FIRST_NAMES = [
    "Priya", "Rahul", "Ananya", "Vikram", "Sneha", "Arjun", "Kavya", "Rohan",
    "Meera", "Aditya", "Isha", "Karan", "Divya", "Siddharth", "Pooja", "Aman",
]
LAST_NAMES = [
    "Sharma", "Nair", "Patel", "Reddy", "Iyer", "Singh", "Gupta", "Menon",
    "Rao", "Kulkarni", "Verma", "Joshi", "Chatterjee", "Desai", "Bhat",
]

BUILDINGS = ["Green Meadows", "Sunrise Apartments", "Palm Residency", "Silver Oaks"]
AREAS     = ["Koramangala", "Baner", "Andheri West", "Indiranagar", "Whitefield"]
CITIES    = ["Bengaluru", "Pune", "Mumbai"]

CAMPAIGN_SEGMENTS = ["high_value_reorder", "dormant_30d", "first_order_incentive", "festive_push"]
EVENT_TYPES       = ["email_open", "push_click", "cart_abandon_nudge_sent", "coupon_redeemed"]
TICKET_CATEGORIES = ["delivery_issue", "payment_failed", "wrong_item", "missing_item", "refund_request"]
ORDER_STATUSES    = ["confirmed", "packed", "out_for_delivery", "delivered", "cancelled"]


def fake_name() -> str:
    return f"{random.choice(FIRST_NAMES)} {random.choice(LAST_NAMES)}"


def fake_phone() -> str:
    """Indian mobile: starts 6-9, 10 digits total — matches IndianPhoneRecognizer."""
    return f"{random.choice('6789')}{random.randint(10**8, 10**9 - 1)}"


def fake_aadhaar() -> str:
    """Spaced 4-4-4 format — matches AadhaarRecognizer (high-confidence pattern)."""
    return f"{random.randint(1000, 9999)} {random.randint(1000, 9999)} {random.randint(1000, 9999)}"


def fake_pan() -> str:
    """5 letters + 4 digits + holder-type letter — matches PANRecognizer."""
    letters = "".join(random.choices("ABCDEFGHIJKLMNOPQRSTUVWXYZ", k=5))
    digits  = random.randint(1000, 9999)
    holder  = random.choice("ABCFGHJLPT")
    return f"{letters}{digits}{holder}"


def fake_address() -> str:
    flat  = f"Flat {random.randint(1, 12)}{random.choice('ABCD')}"
    bldg  = random.choice(BUILDINGS)
    area  = random.choice(AREAS)
    city  = random.choice(CITIES)
    return f"{flat}, {bldg}, {area}, {city}"


def fake_order_id() -> str:
    return f"BLK-{random.randint(100000, 999999)}"


def fake_ticket_id() -> str:
    return f"TKT-{random.randint(10000, 99999)}"


def fake_partner_id() -> str:
    return f"DP-{random.randint(1000, 9999)}"


def fake_hashed_id() -> str:
    return f"hcid_{random.randint(10**9, 10**10 - 1):x}"


def ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
