"""
DPDPA Compliance Agent — Consent Registry Models
=================================================
Defines RegistryEntry: the ground-truth record of what data a Blinkit
source_system is declared to collect, for what purpose, under what consent
scope, and for how long.

Phase 4's Rule Engine calls get_registry_entry() synchronously, per event,
on a live stream. This module must stay fast (in-memory) and side-effect free.

CONSISTENCY WITH PHASE 0:
  - `pii_category` reuses the PII types locked in /docs/scope.md:
        name, email, phone, aadhaar, pan, address
    (`address` is added here because customers/delivery_partners carry it;
    it is not a "detected PII type" for Phase 3's regex/Presidio pass in the
    same way Aadhaar/PAN are, but it is a registry-tracked category so the
    Rule Engine can still reason about it. See /registry/README.md.)
  - `source_system` reuses schemas.models.SourceSystem exactly. Do NOT
    invent new values here. See TABLE_TO_SOURCE_SYSTEM below for the mapping
    from mock table name to the Phase 0 locked enum.
"""

from __future__ import annotations

from datetime import datetime
from typing import Dict, List, Optional

from pydantic import BaseModel, Field, field_validator

from schemas.models import SourceSystem


# ---------------------------------------------------------------------------
# Table name -> source_system mapping
# ---------------------------------------------------------------------------
# The four Blinkit-realistic mock tables seeded in Phase 1 do NOT share names
# with schemas.models.SourceSystem's enum values 1:1. This mapping makes the
# relationship explicit so Phase 2 (ingestion) and Phase 4 (rule engine) never
# have to guess which source_system a given table's data flows through.
#
#   customers          -> order-service               (order_fulfillment data)
#   delivery_partners   -> delivery-partner-service     (onboarding/KYC data)
#   support_tickets     -> support-ticketing            (support flows)
#   marketing_events    -> marketing-analytics          (campaign/analytics data)
#
# This mapping is the single source of truth — reference it, don't duplicate it.
TABLE_TO_SOURCE_SYSTEM: Dict[str, SourceSystem] = {
    "customers":         SourceSystem.ORDER_SERVICE,
    "delivery_partners":  SourceSystem.DELIVERY_PARTNER,
    "support_tickets":    SourceSystem.SUPPORT_TICKETING,
    "marketing_events":   SourceSystem.MARKETING_ANALYTICS,
}


# ---------------------------------------------------------------------------
# RegistryEntry model
# ---------------------------------------------------------------------------

class RegistryEntry(BaseModel):
    """
    Ground truth for a single field within a single source_system: what it is,
    why it was collected, what scope it may be used under, and how long it
    may legally be retained.

    Designed to be trivially backed by a SQLite table later (per the locked
    tech stack) without changing the public interface (get_registry_entry,
    list_registry_entries) that Phase 4 depends on.
    """

    field_name: str = Field(
        ...,
        min_length=1,
        description="The data field this entry governs, e.g. 'aadhaar', 'phone', 'name'."
    )
    pii_category: str = Field(
        ...,
        min_length=1,
        description=(
            "PII type this field belongs to. Reuses Phase 0's locked types "
            "(name, email, phone, aadhaar, pan) plus 'address', which the "
            "registry tracks even though Phase 3's detector treats it separately."
        )
    )
    declared_purpose: str = Field(
        ...,
        min_length=1,
        description=(
            "The single declared reason this field is collected, e.g. "
            "'order_fulfillment', 'onboarding_kyc', 'marketing_analytics'. "
            "Phase 4 compares this against where the field is actually observed."
        )
    )
    consent_scope: str = Field(
        ...,
        min_length=1,
        description=(
            "The consent boundary this field's use is confined to. For most "
            "tables this mirrors declared_purpose closely; for marketing_events "
            "it is deliberately restrictive (see MARKETING_EVENTS_ALLOWED_SCOPE)."
        )
    )
    retention_days: int = Field(
        ...,
        ge=0,
        description=(
            "Maximum number of days this field may be retained after "
            "created_at before it becomes a RETENTION_001 violation."
        )
    )
    created_at: datetime = Field(
        ...,
        description=(
            "ISO8601 UTC timestamp of when this data record was created/collected. "
            "Used with retention_days to compute whether a record is past its window."
        )
    )
    source_system: SourceSystem = Field(
        ...,
        description=(
            "Must match schemas.models.SourceSystem exactly. See "
            "TABLE_TO_SOURCE_SYSTEM for the mock-table-to-enum mapping."
        )
    )
    table_name: str = Field(
        ...,
        min_length=1,
        description=(
            "The mock Blinkit table this entry originates from "
            "(customers | delivery_partners | support_tickets | marketing_events). "
            "Kept alongside source_system for auditor-readable reporting, since "
            "'delivery-partner-service' alone is less legible to Priya than "
            "'delivery_partners table'."
        )
    )
    is_seeded_violation: bool = Field(
        default=False,
        description=(
            "True if this specific entry was deliberately seeded to be caught "
            "as a violation by Phase 4's rule engine (demo/test purposes). "
            "See /registry/seeded_violations.md for the full index."
        )
    )
    violation_note: Optional[str] = Field(
        default=None,
        description="If is_seeded_violation is True, a short human-readable note on why."
    )

    @field_validator("created_at", mode="before")
    @classmethod
    def parse_created_at(cls, v):
        if isinstance(v, datetime):
            return v
        if isinstance(v, str):
            return datetime.fromisoformat(v.replace("Z", "+00:00"))
        raise ValueError(f"created_at must be ISO8601 string or datetime, got {type(v)}")

    def is_past_retention(self, as_of: Optional[datetime] = None) -> bool:
        """
        Convenience helper (not used by Phase 4 directly, but useful for tests
        and reporting): returns True if this entry's age exceeds retention_days.
        Phase 4's rule engine may reimplement this logic against live event
        timestamps rather than this registry snapshot's created_at — this
        method operates on the registry's own created_at for registry-level checks.
        """
        reference = as_of or datetime.now(self.created_at.tzinfo)
        age_days = (reference - self.created_at).days
        return age_days > self.retention_days

    def forbids_raw_pii(self) -> bool:
        """
        Structural check Phase 4 can call directly: True if this entry's
        consent_scope means raw PII is never allowed for this field under
        this purpose — i.e. the marketing_events invariant.

        Any RegistryEntry where declared_purpose == 'marketing_analytics'
        and consent_scope == MARKETING_EVENTS_ALLOWED_SCOPE structurally
        forbids raw PII. This is not a per-row exception; it holds for
        every marketing_events entry by construction.
        """
        return (
            self.declared_purpose == "marketing_analytics"
            and self.consent_scope == MARKETING_EVENTS_ALLOWED_SCOPE
        )


# The only consent_scope value marketing_events entries may carry.
# Any raw PII (name/phone/aadhaar/pan/email in cleartext) observed flowing
# through a source_system == marketing-analytics event is, by construction,
# a PURPOSE_001 violation — because no field governed by this scope permits
# raw identifiers. Phase 4 keys off entry.forbids_raw_pii() rather than
# re-deriving this string comparison itself.
MARKETING_EVENTS_ALLOWED_SCOPE = "deidentified_or_hashed_only"


# ---------------------------------------------------------------------------
# RegistryStore — in-memory container, SQLite-migration-friendly
# ---------------------------------------------------------------------------

class RegistryStore(BaseModel):
    """
    In-memory registry store. Keyed internally by (source_system, field_name)
    for O(1) lookup. Public interface (get_registry_entry, list_registry_entries
    module-level functions in loader.py) is what Phase 4 depends on — this
    class's internals could be swapped for a SQLite-backed store without
    changing that interface.
    """

    entries: List[RegistryEntry] = Field(default_factory=list)

    def _index(self) -> Dict[tuple, RegistryEntry]:
        """Build a lookup index. Recomputed on demand — fine at MVP seed-data scale."""
        return {(e.source_system.value, e.field_name): e for e in self.entries}

    def get(self, field_name: str, source_system: str) -> Optional[RegistryEntry]:
        """Synchronous, in-memory lookup. Returns None (never raises) on miss."""
        return self._index().get((source_system, field_name))

    def list(self, source_system: Optional[str] = None) -> List[RegistryEntry]:
        """Return all entries, optionally filtered by source_system."""
        if source_system is None:
            return list(self.entries)
        return [e for e in self.entries if e.source_system.value == source_system]
