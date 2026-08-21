"""
DPDPA Compliance Agent — Detection Test Fixtures
====================================================
Real/realistic sample events used across detection tests. Where possible
these are pulled from actual Phase 2 generator output (captured and
pinned below) rather than only hand-invented textbook examples, per the
plan's explicit instruction.

NOTE on support_tickets: Phase 2's log_generator only emits
source_system in {support-ticketing, order-service} with a fixed
"fetched customer record {...}" log shape — it does NOT yet produce a
free-text "support ticket notes" style event (that table exists in
Phase 1's registry, but Phase 2 didn't build a dedicated generator
variant for it, since the plan's Phase 2 spec only required the
exposure vector via the existing log line shape). To satisfy this
phase's explicit requirement to test free-text detection against a
support_tickets-style event, SUPPORT_TICKET_FREE_TEXT_EVENT below
constructs a realistic support-ticket note by hand, modeled directly on
Phase 1's registry.seed_registry.py comment: "an agent pastes a
customer's phone number into a resolution note" — i.e. PII riding along
in unstructured text with NO corresponding structured field, which is
exactly the case field-scanning alone cannot catch.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from schemas.models import Event, SourceSystem, SourceType

# ---------------------------------------------------------------------------
# Pulled from real Phase 2 log_generator output (log_generator.py,
# _exposure_violation_log_line), field values substituted with fixed
# values here so tests are deterministic and don't depend on random seeds
# matching across a Phase 2 version bump.
# ---------------------------------------------------------------------------

EXPOSURE_LOG_EVENT = Event(
    event_id=str(uuid4()),
    source_type=SourceType.LOG,
    source_system=SourceSystem.SUPPORT_TICKETING,
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

# A completely clean log line (Phase 2's _clean_log_line shape) — no PII anywhere.
CLEAN_LOG_EVENT = Event(
    event_id=str(uuid4()),
    source_type=SourceType.LOG,
    source_system=SourceSystem.ORDER_SERVICE,
    timestamp=datetime.now(timezone.utc).isoformat(),
    raw_snippet='[2026-08-21T10:33:01Z] INFO order-service: order BLK-431682 status updated to \'confirmed\'',
    fields={"order_id": "BLK-431682"},
)

# Pulled from real Phase 2 api_generator output shape
# (_purpose_violation_marketing_payload) — raw phone leaking into a
# marketing-analytics event that should be hashed-only.
MARKETING_PURPOSE_VIOLATION_EVENT = Event(
    event_id=str(uuid4()),
    source_type=SourceType.API,
    source_system=SourceSystem.MARKETING_ANALYTICS,
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
    event_id=str(uuid4()),
    source_type=SourceType.API,
    source_system=SourceSystem.MARKETING_ANALYTICS,
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

# Pulled from real Phase 2 api_generator output shape
# (_retention_violation_delivery_partner_payload) — the exact seeded
# stale delivery partner (DP-4471 / Suresh K.) shared with Phase 1's
# registry.
RETENTION_VIOLATION_EVENT = Event(
    event_id=str(uuid4()),
    source_type=SourceType.API,
    source_system=SourceSystem.DELIVERY_PARTNER,
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

# PAN appears in a fresh (non-violation) delivery_partners-shaped payload
# — Phase 2's _fresh_delivery_partner_payload includes a pan field that
# the retention-violation variant does not. Constructed here to give
# Phase 3's PAN recognizer a realistic field-sourced test case.
DELIVERY_PARTNER_WITH_PAN_EVENT = Event(
    event_id=str(uuid4()),
    source_type=SourceType.API,
    source_system=SourceSystem.DELIVERY_PARTNER,
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

# ---------------------------------------------------------------------------
# Hand-constructed support_tickets free-text event (see module docstring
# for why this doesn't come from an actual Phase 2 generator run).
# Models Phase 1's registry.seed_registry.py comment: a support agent
# pastes PII into a free-text resolution note. There is NO structured
# 'phone' field here — the phone number exists ONLY inside raw_snippet,
# which is exactly the case field-scanning alone cannot catch and
# raw_snippet-scanning exists to cover.
# ---------------------------------------------------------------------------

SUPPORT_TICKET_FREE_TEXT_EVENT = Event(
    event_id=str(uuid4()),
    source_type=SourceType.LOG,
    source_system=SourceSystem.SUPPORT_TICKETING,
    timestamp=datetime.now(timezone.utc).isoformat(),
    raw_snippet=(
        "[2026-08-21T11:02:47Z] INFO support-ticketing: ticket #48213 resolved. "
        "Agent note: Called customer Ananya Bhat back on 9812345670 to confirm "
        "refund, she confirmed UPI id is fine, closing ticket."
    ),
    fields={
        "ticket_id": "48213",
        "resolution_status": "resolved",
        # Deliberately NO 'phone' or 'name' field here — those values
        # exist only inside the free-text agent note in raw_snippet.
    },
)
