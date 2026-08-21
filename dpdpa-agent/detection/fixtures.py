"""
DPDPA Compliance Agent — Detection Test Fixtures (Phase 0 Generalised)
========================================================================
Real/realistic sample events used across detection tests.

Phase 0:
  - tenant_id added to every Event fixture (default: "blinkit")
  - source_system values are plain strings ("support-ticketing", "order-service", etc.)
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from schemas.models import Event, SourceType

TENANT_ID = "blinkit"

EXPOSURE_LOG_EVENT = Event(
    tenant_id=TENANT_ID,
    event_id=str(uuid4()),
    source_type=SourceType.LOG,
    source_system="support-ticketing",
    timestamp=datetime.now(timezone.utc).isoformat(),
    raw_snippet=(
        '[2026-08-21T10:32:14Z] DEBUG support-ticketing: fetched customer record '
        '{name: "Priya Nair", phone: "9876543210", address: "Flat 4B, Palm Residency, Koramangala, Bengaluru"}'
    ),
    fields={
        "name": "Priya Nair",
        "phone": "9876543210",
        "address": "Flat 4B, Palm Residency, Koramangala, Bengaluru",
    },
)

# A completely clean log line — no PII anywhere.
CLEAN_LOG_EVENT = Event(
    tenant_id=TENANT_ID,
    event_id=str(uuid4()),
    source_type=SourceType.LOG,
    source_system="order-service",
    timestamp=datetime.now(timezone.utc).isoformat(),
    raw_snippet='[2026-08-21T10:33:01Z] INFO order-service: order BLK-431682 status updated to \'confirmed\'',
    fields={"order_id": "BLK-431682"},
)

# Raw phone leaking into a marketing-analytics event that should be hashed-only.
MARKETING_PURPOSE_VIOLATION_EVENT = Event(
    tenant_id=TENANT_ID,
    event_id=str(uuid4()),
    source_type=SourceType.API,
    source_system="marketing-analytics",
    timestamp=datetime.now(timezone.utc).isoformat(),
    raw_snippet=(
        '{"event": "marketing_engagement", "hashed_customer_id": "hcid_d8ba0b4b", '
        '"campaign_segment": "dormant_30d", "event_type": "coupon_redeemed", "phone": "8541549317"}'
    ),
    fields={
        "hashed_customer_id": "hcid_d8ba0b4b",
        "campaign_segment": "dormant_30d",
        "event_type": "coupon_redeemed",
        "phone": "8541549317",
    },
)

# Clean marketing event — hashed fields only, no raw PII.
MARKETING_CLEAN_EVENT = Event(
    tenant_id=TENANT_ID,
    event_id=str(uuid4()),
    source_type=SourceType.API,
    source_system="marketing-analytics",
    timestamp=datetime.now(timezone.utc).isoformat(),
    raw_snippet=(
        '{"event": "marketing_engagement", "hashed_customer_id": "hcid_f3e46bdd", '
        '"campaign_segment": "festive_push", "event_type": "email_open"}'
    ),
    fields={
        "hashed_customer_id": "hcid_f3e46bdd",
        "campaign_segment": "festive_push",
        "event_type": "email_open",
    },
)

# Retention violation delivery partner event.
RETENTION_VIOLATION_EVENT = Event(
    tenant_id=TENANT_ID,
    event_id=str(uuid4()),
    source_type=SourceType.API,
    source_system="delivery-partner-service",
    timestamp=datetime.now(timezone.utc).isoformat(),
    raw_snippet=(
        '{"event": "delivery_partner_profile_fetch", "partner_id": "DP-4471", '
        '"name": "Suresh K.", "aadhaar": "5521 8890 3347", '
        '"onboarded_at": "2025-12-24T09:05:40+00:00", "status": "inactive"}'
    ),
    fields={
        "name": "Suresh K.",
        "aadhaar": "5521 8890 3347",
        "onboarded_at": "2025-12-24T09:05:40+00:00",
        "partner_id": "DP-4471",
    },
)

# Fresh delivery partner with PAN.
DELIVERY_PARTNER_WITH_PAN_EVENT = Event(
    tenant_id=TENANT_ID,
    event_id=str(uuid4()),
    source_type=SourceType.API,
    source_system="delivery-partner-service",
    timestamp=datetime.now(timezone.utc).isoformat(),
    raw_snippet=(
        '{"event": "delivery_partner_profile_fetch", "partner_id": "DP-7712", '
        '"name": "Vikram Rao", "aadhaar": "4412 7789 0021", "pan": "BQPRV4521K", '
        '"bank_details": "XXXXXXXX4521", "onboarded_at": "2026-06-01T00:00:00+00:00", "status": "active"}'
    ),
    fields={
        "name": "Vikram Rao",
        "aadhaar": "4412 7789 0021",
        "pan": "BQPRV4521K",
        "bank_details": "XXXXXXXX4521",
        "onboarded_at": "2026-06-01T00:00:00+00:00",
    },
)

# Support ticket free-text event.
SUPPORT_TICKET_FREE_TEXT_EVENT = Event(
    tenant_id=TENANT_ID,
    event_id=str(uuid4()),
    source_type=SourceType.LOG,
    source_system="support-ticketing",
    timestamp=datetime.now(timezone.utc).isoformat(),
    raw_snippet=(
        "[2026-08-21T11:02:47Z] INFO support-ticketing: ticket #48213 resolved. "
        "Agent note: Called customer Ananya Bhat back on 9812345670 to confirm "
        "refund, she confirmed UPI id is fine, closing ticket."
    ),
    fields={
        "ticket_id": "48213",
        "resolution_status": "resolved",
    },
)
