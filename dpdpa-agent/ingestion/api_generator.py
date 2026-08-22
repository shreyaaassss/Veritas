"""
DPDPA Compliance Agent — API Traffic Generator (Purpose + Retention Vectors)
===============================================================================
Emits realistic API request/response JSON payloads at a steady, configurable
interval. Models TWO independent violation vectors:

1. PURPOSE-LIMITATION vector (marketing-analytics):
   Marketing events should carry only hashed/de-identified fields
   (hashed_customer_id, campaign_segment, event_type). At a configurable
   rate, this generator deliberately emits a raw phone number instead.
   FRAMING: modeled as "the analytics event schema got copy-pasted from an
   internal-only type that still had the raw customer object nested in it"
   — vendor/team over-sharing via schema reuse, not malice.

2. RETENTION vector (delivery-partner-service):
   Emits payloads that reference an onboarding record older than its
   180-day retention window. Uses the EXACT SAME seeded delivery partner
   (DP-4471 / "Suresh K." / aadhaar "5521 8890 3347" / 240 days old) as
   registry/seed_registry.py's seeded violation row — see fixtures.py's
   STALE_DELIVERY_PARTNER constant, the single source of truth.

Phase 0 change: source_system is now a plain string (no SourceSystem enum).
These specific source system strings match what blinkit.yaml will declare.
Phase 1 will read them from the org's config file.

This module performs NO PII detection and NO rule evaluation.
"""

from __future__ import annotations

import asyncio
import logging
import random
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, Tuple

import pipeline_control
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

logger = logging.getLogger("ingestion.api_generator")

# Phase 0: source_system values are plain strings (org-defined).
# These match the blinkit.yaml config's source_systems. Phase 1 will read from config.
_MARKETING_SOURCE = "marketing-analytics"
_DELIVERY_SOURCE = "delivery-partner-service"

# Tenant for this generator — blinkit for Phase 0 transitional state.
TENANT_ID = "blinkit"


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
    purpose-limitation violation. Field name 'phone' matches the seeded
    marketing_events registry entry exactly, so get_registry_entry("phone",
    "marketing-analytics") resolves to a real entry and forbids_raw_pii()
    is True downstream in Phase 4.
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
    fixtures.STALE_DELIVERY_PARTNER as the single source of truth.
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
    Would model a Redis-cached user object payload past its TTL. Not implemented.
    """
    raise NotImplementedError("Phase 2 stretch vector — not implemented, see docstring.")


def _stub_stale_staging_data_payload() -> None:
    """
    # Phase 2 stretch, not MVP-blocking.
    Would model a payload tagged env: staging with production PII. Not implemented.
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
    tenant_id: str = TENANT_ID,
) -> None:
    """
    Runs indefinitely (or until max_events is emitted), pushing normalized,
    schema-validated Event objects onto the shared queue at
    config.api_emit_interval_seconds intervals.

    Each tick alternates between marketing-analytics and delivery-partner-service
    events so both vectors are reliably represented in any reasonably long run.

    Phase 0: tenant_id parameter added — defaults to "blinkit" for the
    transitional period. Phase 1 will wire this to the config loader.
    """
    cfg = config or IngestionConfig()
    r = rng or random.Random(cfg.random_seed) if rng is None else rng

    emitted = 0
    vector_toggle = 0  # alternates marketing vs delivery-partner emission

    while max_events is None or emitted < max_events:
        if not pipeline_control.is_running():
            await asyncio.sleep(0.3)
            continue

        if vector_toggle % 2 == 0:
            source_system = _MARKETING_SOURCE
            is_violation = r.random() < cfg.api_marketing_purpose_violation_rate
            if is_violation:
                payload, fields = _purpose_violation_marketing_payload()
            else:
                payload, fields = _clean_marketing_payload()
        else:
            source_system = _DELIVERY_SOURCE
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
            tenant_id=tenant_id,
        )
        event = validate_event(event_dict)
        if event is not None:
            await queue.put(event)
            emitted += 1

        await asyncio.sleep(cfg.api_emit_interval_seconds)
