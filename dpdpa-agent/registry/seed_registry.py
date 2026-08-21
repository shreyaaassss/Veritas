"""
DPDPA Compliance Agent — Registry Seed Data
=============================================
Seed data for the four Blinkit-realistic mock tables. This is the ONLY file
that should contain hand-authored registry rows — loader.py just assembles
these into a RegistryStore.

Reference timestamp used throughout for "now" when computing seeded
violations: 2026-08-21T00:00:00Z (matches project's current date context).
Do not silently change this without checking every relative-date comment below.

See /registry/seeded_violations.md for the auditor-facing index of every
deliberately-seeded violation and why it exists.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from registry.models import (
    MARKETING_EVENTS_ALLOWED_SCOPE,
    RegistryEntry,
    TABLE_TO_SOURCE_SYSTEM,
)

# Fixed "now" for reproducible seed-data violation windows.
SEED_NOW = datetime(2026, 8, 21, 0, 0, 0, tzinfo=timezone.utc)


def _days_ago(n: int) -> datetime:
    return SEED_NOW - timedelta(days=n)


# ---------------------------------------------------------------------------
# Table 1 — customers
# ---------------------------------------------------------------------------
# declared_purpose: order_fulfillment
# Retention: 1095 days (3 years) post last order activity. Chosen because
# Blinkit customers reasonably expect their delivery history to be usable
# for reorders, dispute resolution, and refund windows well beyond a single
# transaction — 3 years is a defensible ceiling that still has a real limit,
# unlike "account lifetime" which in practice never expires anything.
CUSTOMERS_RETENTION_DAYS = 1095

CUSTOMERS_ENTRIES = [
    RegistryEntry(
        field_name="name",
        pii_category="name",
        declared_purpose="order_fulfillment",
        consent_scope="order_fulfillment",
        retention_days=CUSTOMERS_RETENTION_DAYS,
        created_at=_days_ago(120),
        source_system=TABLE_TO_SOURCE_SYSTEM["customers"],
        table_name="customers",
    ),
    RegistryEntry(
        field_name="phone",
        pii_category="phone",
        declared_purpose="order_fulfillment",
        consent_scope="order_fulfillment",
        retention_days=CUSTOMERS_RETENTION_DAYS,
        created_at=_days_ago(120),
        source_system=TABLE_TO_SOURCE_SYSTEM["customers"],
        table_name="customers",
    ),
    RegistryEntry(
        field_name="delivery_address",
        pii_category="address",
        declared_purpose="order_fulfillment",
        consent_scope="order_fulfillment",
        retention_days=CUSTOMERS_RETENTION_DAYS,
        created_at=_days_ago(120),
        source_system=TABLE_TO_SOURCE_SYSTEM["customers"],
        table_name="customers",
    ),
    RegistryEntry(
        field_name="order_history",
        pii_category="order_history",
        declared_purpose="order_fulfillment",
        consent_scope="order_fulfillment",
        retention_days=CUSTOMERS_RETENTION_DAYS,
        created_at=_days_ago(120),
        source_system=TABLE_TO_SOURCE_SYSTEM["customers"],
        table_name="customers",
    ),
]


# ---------------------------------------------------------------------------
# Table 2 — delivery_partners
# ---------------------------------------------------------------------------
# declared_purpose: onboarding_kyc
# Retention: TIGHT — 180 days post-engagement. KYC documents (Aadhaar, PAN,
# bank details) are high-sensitivity financial/identity data; DPDPA data
# minimization expects these purged promptly once the KYC purpose is served
# and the partner relationship data is no longer active.
DELIVERY_PARTNERS_RETENTION_DAYS = 180

DELIVERY_PARTNERS_ENTRIES = [
    RegistryEntry(
        field_name="aadhaar",
        pii_category="aadhaar",
        declared_purpose="onboarding_kyc",
        consent_scope="onboarding_kyc",
        retention_days=DELIVERY_PARTNERS_RETENTION_DAYS,
        created_at=_days_ago(45),
        source_system=TABLE_TO_SOURCE_SYSTEM["delivery_partners"],
        table_name="delivery_partners",
    ),
    RegistryEntry(
        field_name="pan",
        pii_category="pan",
        declared_purpose="onboarding_kyc",
        consent_scope="onboarding_kyc",
        retention_days=DELIVERY_PARTNERS_RETENTION_DAYS,
        created_at=_days_ago(45),
        source_system=TABLE_TO_SOURCE_SYSTEM["delivery_partners"],
        table_name="delivery_partners",
    ),
    RegistryEntry(
        field_name="bank_details",
        pii_category="financial_account",
        declared_purpose="onboarding_kyc",
        consent_scope="onboarding_kyc",
        retention_days=DELIVERY_PARTNERS_RETENTION_DAYS,
        created_at=_days_ago(45),
        source_system=TABLE_TO_SOURCE_SYSTEM["delivery_partners"],
        table_name="delivery_partners",
    ),
    RegistryEntry(
        field_name="address",
        pii_category="address",
        declared_purpose="onboarding_kyc",
        consent_scope="onboarding_kyc",
        retention_days=DELIVERY_PARTNERS_RETENTION_DAYS,
        created_at=_days_ago(45),
        source_system=TABLE_TO_SOURCE_SYSTEM["delivery_partners"],
        table_name="delivery_partners",
    ),

    # ------------------------------------------------------------------
    # >>> SEEDED VIOLATION #1 — RETENTION_001 <<<
    # A former delivery partner ("Suresh K.", inactive) whose Aadhaar was
    # collected 240 days ago — 60 days past the 180-day KYC retention window.
    # This is the required Phase 1 retention-violation seed for Phase 4's
    # rule engine to catch. is_seeded_violation=True and violation_note
    # make this unambiguous for anyone reading the seed data or writing
    # rule engine tests against it.
    # ------------------------------------------------------------------
    RegistryEntry(
        field_name="aadhaar",
        pii_category="aadhaar",
        declared_purpose="onboarding_kyc",
        consent_scope="onboarding_kyc",
        retention_days=DELIVERY_PARTNERS_RETENTION_DAYS,
        created_at=_days_ago(240),  # 240 > 180 retention_days -> VIOLATION
        source_system=TABLE_TO_SOURCE_SYSTEM["delivery_partners"],
        table_name="delivery_partners",
        is_seeded_violation=True,
        violation_note=(
            "Inactive delivery partner's Aadhaar record is 240 days old, "
            "60 days past the 180-day KYC retention window. Should have "
            "been purged. RETENTION_001 candidate."
        ),
    ),
]


# ---------------------------------------------------------------------------
# Table 3 — support_tickets
# ---------------------------------------------------------------------------
# declared_purpose: customer_support
# HIGHEST EXPOSURE RISK: PII "rides along" unintentionally in free-text notes
# fields (e.g. an agent pastes a customer's phone number into a resolution
# note). Phase 3's PII detection pass over raw_snippet matters most here,
# since regex/NER has to find PII embedded in unstructured text rather than
# a clean structured field.
SUPPORT_TICKETS_RETENTION_DAYS = 730  # 2 years — dispute/audit trail window

SUPPORT_TICKETS_ENTRIES = [
    RegistryEntry(
        field_name="name",
        pii_category="name",
        declared_purpose="customer_support",
        consent_scope="customer_support",
        retention_days=SUPPORT_TICKETS_RETENTION_DAYS,
        created_at=_days_ago(10),
        source_system=TABLE_TO_SOURCE_SYSTEM["support_tickets"],
        table_name="support_tickets",
    ),
    RegistryEntry(
        field_name="phone",
        pii_category="phone",
        declared_purpose="customer_support",
        consent_scope="customer_support",
        retention_days=SUPPORT_TICKETS_RETENTION_DAYS,
        created_at=_days_ago(10),
        source_system=TABLE_TO_SOURCE_SYSTEM["support_tickets"],
        table_name="support_tickets",
    ),
    RegistryEntry(
        field_name="order_reference",
        pii_category="order_reference",
        declared_purpose="customer_support",
        consent_scope="customer_support",
        retention_days=SUPPORT_TICKETS_RETENTION_DAYS,
        created_at=_days_ago(10),
        source_system=TABLE_TO_SOURCE_SYSTEM["support_tickets"],
        table_name="support_tickets",
    ),
    # NOTE: 'notes' (free text) is intentionally NOT given its own clean
    # registry entry the way structured fields are — free text is not a
    # single declared PII field, it's an unstructured blob that MAY contain
    # PII incidentally. Phase 3's detector must scan raw_snippet content
    # for this table rather than relying on a fields['notes'] registry hit.
    # This is why this table is flagged as highest exposure-risk: the
    # registry alone cannot catch what Presidio/regex must find at
    # detection time.
]


# ---------------------------------------------------------------------------
# Table 4 — marketing_events
# ---------------------------------------------------------------------------
# declared_purpose: marketing_analytics
# STRUCTURAL INVARIANT: every entry here carries
# consent_scope == MARKETING_EVENTS_ALLOWED_SCOPE ("deidentified_or_hashed_only").
# This is not a per-row exception — it holds for the whole table by
# construction. Any raw PII (unhashed name/phone/aadhaar/pan/email) observed
# in a marketing-analytics event is automatically a PURPOSE_001 violation,
# because no marketing_events registry entry ever permits raw identifiers.
# Phase 4 should call RegistryEntry.forbids_raw_pii() rather than
# re-deriving this by string-comparing purpose/scope itself.
MARKETING_EVENTS_RETENTION_DAYS = 365

MARKETING_EVENTS_ENTRIES = [
    RegistryEntry(
        field_name="hashed_customer_id",
        pii_category="hashed_identifier",
        declared_purpose="marketing_analytics",
        consent_scope=MARKETING_EVENTS_ALLOWED_SCOPE,
        retention_days=MARKETING_EVENTS_RETENTION_DAYS,
        created_at=_days_ago(30),
        source_system=TABLE_TO_SOURCE_SYSTEM["marketing_events"],
        table_name="marketing_events",
    ),
    RegistryEntry(
        field_name="campaign_segment",
        pii_category="hashed_identifier",
        declared_purpose="marketing_analytics",
        consent_scope=MARKETING_EVENTS_ALLOWED_SCOPE,
        retention_days=MARKETING_EVENTS_RETENTION_DAYS,
        created_at=_days_ago(30),
        source_system=TABLE_TO_SOURCE_SYSTEM["marketing_events"],
        table_name="marketing_events",
    ),
    RegistryEntry(
        field_name="event_type",
        pii_category="hashed_identifier",
        declared_purpose="marketing_analytics",
        consent_scope=MARKETING_EVENTS_ALLOWED_SCOPE,
        retention_days=MARKETING_EVENTS_RETENTION_DAYS,
        created_at=_days_ago(30),
        source_system=TABLE_TO_SOURCE_SYSTEM["marketing_events"],
        table_name="marketing_events",
    ),

    # ------------------------------------------------------------------
    # >>> SEEDED INVARIANT-CARRIER — NOT itself a stored violation row <<<
    # This entry documents the registry-side expectation for a raw 'phone'
    # field IF one were ever declared under marketing_analytics. It exists
    # so Phase 4 has a concrete RegistryEntry to retrieve when Phase 2's
    # event generator occasionally injects a raw phone/Aadhaar into a
    # marketing event per Phase 2's spec — the lookup succeeds, returns
    # this entry, and entry.forbids_raw_pii() is True, giving Phase 4 a
    # clean structural reason to flag PURPOSE_001, rather than a registry
    # miss (which would be a different, weaker signal).
    # ------------------------------------------------------------------
    RegistryEntry(
        field_name="phone",
        pii_category="phone",
        declared_purpose="marketing_analytics",
        consent_scope=MARKETING_EVENTS_ALLOWED_SCOPE,
        retention_days=MARKETING_EVENTS_RETENTION_DAYS,
        created_at=_days_ago(30),
        source_system=TABLE_TO_SOURCE_SYSTEM["marketing_events"],
        table_name="marketing_events",
        is_seeded_violation=True,
        violation_note=(
            "marketing_events structurally forbids raw PII (consent_scope="
            f"'{MARKETING_EVENTS_ALLOWED_SCOPE}'). If a raw 'phone' value "
            "is ever observed flowing through a marketing-analytics event, "
            "this registry entry is what Phase 4 retrieves — "
            "forbids_raw_pii() returns True, and it flags PURPOSE_001. "
            "This entry exists so that lookup succeeds (rather than "
            "returning None), giving Phase 4 a definitive registry-backed "
            "reason rather than an ambiguous 'field not found' case."
        ),
    ),
]


# ---------------------------------------------------------------------------
# Aggregate — everything the loader needs
# ---------------------------------------------------------------------------

ALL_SEED_ENTRIES = (
    CUSTOMERS_ENTRIES
    + DELIVERY_PARTNERS_ENTRIES
    + SUPPORT_TICKETS_ENTRIES
    + MARKETING_EVENTS_ENTRIES
)
