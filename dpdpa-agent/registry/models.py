"""
DPDPA Compliance Agent — Consent Registry Models (Phase 0 — Generalised)
=========================================================================
Defines RegistryEntry: the ground-truth record of what a given org's
source_system is declared to collect, for what purpose, under what consent
scope, and for how long.

WHAT CHANGED IN PHASE 0:
  - `SourceSystem` enum import and `TABLE_TO_SOURCE_SYSTEM` dict are REMOVED.
    Both were Blinkit-specific. `source_system` is now a free-form `str`
    whose valid values are org-defined (from Org Config). The loader no
    longer needs to translate from mock table names to enum values — it
    reads source_system directly from the org's config file.
  - `table_name` is retained as Optional[str] for backward compatibility
    with existing seed data, but is not required. New config-driven entries
    do not need it.

Phase 4's Rule Engine calls get_registry_entry() synchronously, per event,
on a live stream. This module must stay fast (in-memory) and side-effect free.
"""

from __future__ import annotations

from datetime import datetime
from typing import Dict, List, Optional

from pydantic import BaseModel, Field, field_validator


# ---------------------------------------------------------------------------
# RegistryEntry model
# ---------------------------------------------------------------------------

class RegistryEntry(BaseModel):
    """
    Ground truth for a single field within a single source_system: what it is,
    why it was collected, what scope it may be used under, and how long it
    may legally be retained.

    Designed to be trivially backed by a SQLite table later without changing
    the public interface (get_registry_entry, list_registry_entries) that
    Phase 4 depends on.

    Phase 0: source_system is now a free-form str (org-defined).
    """

    field_name: str = Field(
        ...,
        min_length=1,
        description="The data field this entry governs, e.g. 'phone', 'apaar_id'."
    )
    pii_category: str = Field(
        ...,
        min_length=1,
        description=(
            "PII type this field belongs to, e.g. 'name', 'email', 'phone', "
            "'aadhaar', 'pan', 'address', 'student_identifier'. Free-form — "
            "the org's config declares the category; the sensitivity module "
            "maps categories to severity tiers."
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
            "The consent boundary this field's use is confined to. Mirrors "
            "declared_purpose closely for most fields; deliberately restrictive "
            "for analytics/deidentified contexts."
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
    source_system: str = Field(
        ...,
        min_length=1,
        description=(
            "The source system this entry governs. Free-form string — value is "
            "org-defined (from Org Config). Phase 4 looks up entries by "
            "(field_name, source_system) — this value must match exactly what "
            "the org's events carry in their source_system field."
        )
    )
    table_name: Optional[str] = Field(
        default=None,
        description=(
            "Optional: the underlying table or data store name for auditor-readable "
            "reporting. Not required for config-driven entries."
        )
    )
    is_seeded_violation: bool = Field(
        default=False,
        description=(
            "True if this specific entry was deliberately seeded to be caught "
            "as a violation by Phase 4's rule engine (demo/test purposes)."
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
        Returns True if this entry's age exceeds retention_days.
        Phase 4's rule engine may reimplement this logic against live event
        timestamps rather than this registry snapshot's created_at.
        """
        reference = as_of or datetime.now(self.created_at.tzinfo)
        age_days = (reference - self.created_at).days
        return age_days > self.retention_days

    def forbids_raw_pii(self) -> bool:
        """
        Structural check Phase 4 can call: True if this entry's consent_scope
        means raw PII is never allowed for this field under this purpose.
        Any entry where consent_scope == DEIDENTIFIED_ONLY_SCOPE structurally
        forbids raw PII.
        """
        return self.consent_scope == DEIDENTIFIED_ONLY_SCOPE


# The consent_scope value that structurally forbids raw PII (e.g. analytics).
# Any raw PII (name/phone/aadhaar/pan/email in cleartext) observed flowing
# through a source_system whose registry entries carry this scope is a
# PURPOSE_001 violation. Phase 4 keys off entry.forbids_raw_pii().
DEIDENTIFIED_ONLY_SCOPE = "deidentified_or_hashed_only"

# Backward-compat alias — existing code that referenced MARKETING_EVENTS_ALLOWED_SCOPE
# can still import this name. Points to the same value.
MARKETING_EVENTS_ALLOWED_SCOPE = DEIDENTIFIED_ONLY_SCOPE


# ---------------------------------------------------------------------------
# RegistryStore — in-memory container, SQLite-migration-friendly
# ---------------------------------------------------------------------------

class RegistryStore(BaseModel):
    """
    In-memory registry store. Keyed internally by (source_system, field_name)
    for O(1) lookup. Public interface (get_registry_entry, list_registry_entries
    in loader.py) is what Phase 4 depends on.

    Phase 0: source_system is now a plain str — no .value access needed.
    """

    entries: List[RegistryEntry] = Field(default_factory=list)

    def _index(self) -> Dict[tuple, RegistryEntry]:
        """Build a lookup index. Recomputed on demand — fine at MVP seed-data scale."""
        return {(e.source_system, e.field_name): e for e in self.entries}

    def get(self, field_name: str, source_system: str) -> Optional[RegistryEntry]:
        """Synchronous, in-memory lookup. Returns None (never raises) on miss."""
        return self._index().get((source_system, field_name))

    def list(self, source_system: Optional[str] = None) -> List[RegistryEntry]:
        """Return all entries, optionally filtered by source_system."""
        if source_system is None:
            return list(self.entries)
        return [e for e in self.entries if e.source_system == source_system]
