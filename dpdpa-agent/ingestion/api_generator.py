"""
DPDPA Compliance Agent — API Traffic Generator (Purpose + Retention Vectors)
===============================================================================
Emits realistic Blinkit-style API request/response JSON payloads at a
steady, configurable interval. Models TWO independent violation vectors:

1. PURPOSE-LIMITATION vector (marketing-analytics):
   Marketing events should carry only hashed/de-identified fields
   (hashed_customer_id, campaign_segment, event_type — matching Phase 1's
   registry exactly). At a configurable rate, this generator deliberately
   emits a raw phone or Aadhaar number instead. FRAMING for Phase 8's
   pitch: modeled as "the analytics event schema got copy-pasted from an
   internal-only type that still had the raw customer object nested in
   it" — i.e. vendor/team over-sharing via schema reuse, not malice.

2. RETENTION vector (delivery-partner-service):
   Emits payloads that reference an onboarding record older than its
   180-day retention window. Uses the EXACT SAME seeded delivery partner
   (DP-4471 / "Suresh K." / aadhaar "5521 8890 3347" / 240 days old) as
   Phase 1's registry.seed_registry.DELIVERY_PARTNERS_ENTRIES seeded
   violation row, so Phase 4's rule engine resolves both to the same
   ground-truth record — this is not a coincidence, see fixtures.py's
   STALE_DELIVERY_PARTNER constant, which is the single source of truth
   both this generator and Phase 1 reference.

This module performs NO PII detection and NO rule evaluation — it only
produces raw payloads and hands them to the normalizer. Field-name
alignment with Phase 1's registry is a data-shape concern, not a
compliance-logic concern; this layer makes no decisions about what it emits.
"""

from __future__ import annotations

import asyncio
import logging
import random
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, Tuple

from ingestion.config import IngestionConfig
from ingestion.fixtures import (
    CAMPAIGN_SEGMENTS,
    EVENT_TYPES,
    STALE_DELIVERY_PARTNER,
    fake_aadhaar,
    fake_bank_account,
    fake_name,
    fake_pan,
    fake_phone,
)
from ingestion.normalizer import normalize_api_event, validate_event
from schemas.models import SourceSystem

logger = logging.getLogger("ingestion.api_generator")


# ---------------------------------------------------------------------------
# Marketing-analytics payloads (purpose-limitation vector)
# ---------------------------------------------------------------------------

def _clean_marketing_payload() -> Tuple[Dict[str, Any], Dict[str, str]]:
    """A correctly-scoped marketing event: hashed/de-identified fields only."""
    hashed_id = f"hcid_{random.randint(10**9, 10**10 - 1):x}"
    segment = random.choice(CAMPAIGN_SEGMENTS)
    event_type = random.choice(EVENT_TYPES)

    payload = {
        "event": "marketing_engagement",
        "hashed_customer_id": hashed_id,
        "campaign_segment": segment,
        "event_type": event_type,
    }
    fields = {
        "hashed_customer_id": hashed_id,
        "campaign_segment": segment,
        "event_type": event_type,
    }
    return payload, fields


def _purpose_violation_marketing_payload() -> Tuple[Dict[str, Any], Dict[str, str]]:
    """
    A marketing event that leaks a raw identifier — the deliberate
    purpose-limitation violation. Field name 'phone' matches Phase 1's
    seeded marketing_events registry entry exactly (see
    registry/seed_registry.py MARKETING_EVENTS_ENTRIES), so
    get_registry_entry("phone", "marketing-analytics") resolves to a real
    entry and entry.forbids_raw_pii() is True downstream in Phase 4.
    """
    hashed_id = f"hcid_{random.randint(10**9, 10**10 - 1):x}"
    segment = random.choice(CAMPAIGN_SEGMENTS)
    event_type = random.choice(EVENT_TYPES)
    raw_phone = fake_phone()

    payload = {
        "event": "marketing_engagement",
        "hashed_customer_id": hashed_id,
        "campaign_segment": segment,
        "event_type": event_type,
        # Deliberate leak: raw PII in a schema that should be hashed-only.
        "phone": raw_phone,
    }
    fields = {
        "hashed_customer_id": hashed_id,
        "campaign_segment": segment,
        "event_type": event_type,
        "phone": raw_phone,
    }
    return payload, fields


# ---------------------------------------------------------------------------
# Delivery-partner-service payloads (retention vector)
# ---------------------------------------------------------------------------

def _fresh_delivery_partner_payload() -> Tuple[Dict[str, Any], Dict[str, str]]:
    """A normal, within-retention-window delivery partner API payload."""
    name = fake_name()
    aadhaar = fake_aadhaar()
    pan = fake_pan()
    bank = fake_bank_account()
    onboarded_days_ago = random.randint(1, 90)  # well within 180-day window
    created_at = (
        datetime.now(timezone.utc) - timedelta(days=onboarded_days_ago)
    ).isoformat()

    payload = {
        "event": "delivery_partner_profile_fetch",
        "partner_id": f"DP-{random.randint(1000,9999)}",
        "name": name,
        "aadhaar": aadhaar,
        "pan": pan,
        "bank_details": bank,
        "onboarded_at": created_at,
        "status": "active",
    }
    fields = {
        "name": name,
        "aadhaar": aadhaar,
        "pan": pan,
        "bank_details": bank,
        "onboarded_at": created_at,
    }
    return payload, fields


def _retention_violation_delivery_partner_payload() -> Tuple[Dict[str, Any], Dict[str, str]]:
    """
    A payload referencing the seeded stale delivery-partner record
    (DP-4471 / Suresh K., 240 days old, 60 days past the 180-day retention
    window) — the deliberate retention violation. Uses
    fixtures.STALE_DELIVERY_PARTNER as the single source of truth so this
    generator and Phase 1's registry seed never drift apart.
    """
    partner = STALE_DELIVERY_PARTNER
    created_at = (
        datetime.now(timezone.utc) - timedelta(days=partner["onboarded_days_ago"])
    ).isoformat()

    payload = {
        "event": "delivery_partner_profile_fetch",
        "partner_id": partner["partner_id"],
        "name": partner["name"],
        "aadhaar": partner["aadhaar"],
        "onboarded_at": created_at,
        "status": partner["status"],
    }
    fields = {
        "name": partner["name"],
        "aadhaar": partner["aadhaar"],
        "onboarded_at": created_at,
        "partner_id": partner["partner_id"],
    }
    return payload, fields


# ---------------------------------------------------------------------------
# Stretch stubs — Phase 2 stretch, not MVP-blocking
# ---------------------------------------------------------------------------

def _stub_cache_overretention_payload() -> None:
    """
    # Phase 2 stretch, not MVP-blocking.
    Would model a Redis-cached user object payload past its TTL — a cache
    that never expired and is now serving stale personal data long after
    it should have been evicted. Not implemented; pick up in Phase 9 if
    pursued. Left as a stub only, per the plan's explicit instruction not
    to build stretch vectors out fully.
    """
    raise NotImplementedError("Phase 2 stretch vector — not implemented, see docstring.")


def _stub_stale_staging_data_payload() -> None:
    """
    # Phase 2 stretch, not MVP-blocking.
    Would model a payload tagged env: staging that nonetheless carries
    production-shaped PII (e.g. a staging environment seeded from a prod
    snapshot that was never scrubbed). Not implemented; pick up in Phase 9
    if pursued.
    """
    raise NotImplementedError("Phase 2 stretch vector — not implemented, see docstring.")


# ---------------------------------------------------------------------------
# Generator loop
# ---------------------------------------------------------------------------

async def api_generator(
    queue: asyncio.Queue,
    config: Optional[IngestionConfig] = None,
    max_events: Optional[int] = None,
    rng: Optional[random.Random] = None,
) -> None:
    """
    Runs indefinitely (or until max_events is emitted), pushing normalized,
    schema-validated Event objects onto the shared queue at
    config.api_emit_interval_seconds intervals.

    Each tick, independently decides:
      - whether to emit a marketing-analytics event (and whether that
        event is a purpose-limitation violation), or
      - a delivery-partner-service event (and whether that event
        references the stale/retention-violating record).
    The two vectors alternate rather than compete, so both are reliably
    represented in any reasonably long run — important for demo repeatability.
    """
    cfg = config or IngestionConfig()
    r = rng or random.Random(cfg.random_seed) if rng is None else rng

    emitted = 0
    vector_toggle = 0  # alternates marketing vs delivery-partner emission

    while max_events is None or emitted < max_events:
        if vector_toggle % 2 == 0:
            source_system = SourceSystem.MARKETING_ANALYTICS
            is_violation = r.random() < cfg.api_marketing_purpose_violation_rate
            if is_violation:
                payload, fields = _purpose_violation_marketing_payload()
            else:
                payload, fields = _clean_marketing_payload()
        else:
            source_system = SourceSystem.DELIVERY_PARTNER
            is_violation = r.random() < cfg.api_retention_violation_rate
            if is_violation:
                payload, fields = _retention_violation_delivery_partner_payload()
            else:
                payload, fields = _fresh_delivery_partner_payload()

        vector_toggle += 1

        event_dict = normalize_api_event(
            payload=payload,
            source_system=source_system,
            fields=fields,
        )
        event = validate_event(event_dict)
        if event is not None:
            await queue.put(event)
            emitted += 1

        await asyncio.sleep(cfg.api_emit_interval_seconds)
