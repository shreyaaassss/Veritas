"""
DPDPA Compliance Agent — Registry Seed Data (Phase 0 — Generalised)
=====================================================================
Transitional seed data for the four legacy tables. In Phase 1, this file
will be replaced entirely by the config loader reading configs/blinkit.yaml.
For Phase 0, it is kept so existing tests pass — the only change is that
source_system values are now plain strings instead of SourceSystem enum values.

Reference timestamp used throughout for "now" when computing seeded
violations: 2026-08-21T00:00:00Z (matches project's current date context).

See /registry/seeded_violations.md for the auditor-facing index of every
deliberately-seeded violation and why it exists.

PHASE 1 NOTE: This file is a transitional artifact. When Phase 1 implements
the config-driven registry loader (reading from org_config/configs/blinkit.yaml),
this file should be deleted and replaced by the YAML-based loader.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from registry.models import (
    DEIDENTIFIED_ONLY_SCOPE,
    RegistryEntry,
)

# Fixed "now" for reproducible seed-data violation windows.
SEED_NOW = datetime(2026, 8, 21, 0, 0, 0, tzinfo=timezone.utc)


def _days_ago(n: int) -> datetime:
    return SEED_NOW - timedelta(days=n)


# ---------------------------------------------------------------------------
# Table 1 — customers (source_system: "order-service")
# ---------------------------------------------------------------------------
# declared_purpose: order_fulfillment
# Retention: 1095 days (3 years) post last order activity.
CUSTOMERS_RETENTION_DAYS = 1095

CUSTOMERS_ENTRIES = [
    RegistryEntry(
        field_name="name",
        pii_category="name",
        declared_purpose="order_fulfillment",
        consent_scope="order_fulfillment",
        retention_days=CUSTOMERS_RETENTION_DAYS,
        created_at=_days_ago(120),
        source_system="order-service",
        table_name="customers",
    ),
    RegistryEntry(
        field_name="phone",
        pii_category="phone",
        declared_purpose="order_fulfillment",
        consent_scope="order_fulfillment",
        retention_days=CUSTOMERS_RETENTION_DAYS,
        created_at=_days_ago(120),
        source_system="order-service",
        table_name="customers",
    ),
    RegistryEntry(
        field_name="delivery_address",
        pii_category="address",
        declared_purpose="order_fulfillment",
        consent_scope="order_fulfillment",
        retention_days=CUSTOMERS_RETENTION_DAYS,
        created_at=_days_ago(120),
        source_system="order-service",
        table_name="customers",
    ),
    RegistryEntry(
        field_name="order_history",
        pii_category="order_history",
        declared_purpose="order_fulfillment",
        consent_scope="order_fulfillment",
        retention_days=CUSTOMERS_RETENTION_DAYS,
        created_at=_days_ago(120),
        source_system="order-service",
        table_name="customers",
    ),
]


# ---------------------------------------------------------------------------
# Table 2 — delivery_partners (source_system: "delivery-partner-service")
# ---------------------------------------------------------------------------
# declared_purpose: onboarding_kyc
# Retention: TIGHT — 180 days post-engagement.
DELIVERY_PARTNERS_RETENTION_DAYS = 180

DELIVERY_PARTNERS_ENTRIES = [
    RegistryEntry(
        field_name="aadhaar",
        pii_category="aadhaar",
        declared_purpose="onboarding_kyc",
        consent_scope="onboarding_kyc",
        retention_days=DELIVERY_PARTNERS_RETENTION_DAYS,
        created_at=_days_ago(45),
        source_system="delivery-partner-service",
        table_name="delivery_partners",
    ),
    RegistryEntry(
        field_name="pan",
        pii_category="pan",
        declared_purpose="onboarding_kyc",
        consent_scope="onboarding_kyc",
        retention_days=DELIVERY_PARTNERS_RETENTION_DAYS,
        created_at=_days_ago(45),
        source_system="delivery-partner-service",
        table_name="delivery_partners",
    ),
    RegistryEntry(
        field_name="bank_details",
        pii_category="financial_account",
        declared_purpose="onboarding_kyc",
        consent_scope="onboarding_kyc",
        retention_days=DELIVERY_PARTNERS_RETENTION_DAYS,
        created_at=_days_ago(45),
        source_system="delivery-partner-service",
        table_name="delivery_partners",
    ),
    RegistryEntry(
        field_name="address",
        pii_category="address",
        declared_purpose="onboarding_kyc",
        consent_scope="onboarding_kyc",
        retention_days=DELIVERY_PARTNERS_RETENTION_DAYS,
        created_at=_days_ago(45),
        source_system="delivery-partner-service",
        table_name="delivery_partners",
    ),

    # ------------------------------------------------------------------
    # >>> SEEDED VIOLATION #1 — RETENTION_001 <<<
    # A former delivery partner whose aadhaar was collected 240 days ago —
    # 60 days past the 180-day KYC retention window.
    # ------------------------------------------------------------------
    RegistryEntry(
        field_name="aadhaar",
        pii_category="aadhaar",
        declared_purpose="onboarding_kyc",
        consent_scope="onboarding_kyc",
        retention_days=DELIVERY_PARTNERS_RETENTION_DAYS,
        created_at=_days_ago(240),  # 240 > 180 retention_days -> VIOLATION
        source_system="delivery-partner-service",
        table_name="delivery_partners",
        is_seeded_violation=True,
        violation_note=(
            "Inactive delivery partner's aadhaar record is 240 days old, "
            "60 days past the 180-day KYC retention window. Should have "
            "been purged. RETENTION_001 candidate."
        ),
    ),
]


# ---------------------------------------------------------------------------
# Table 3 — support_tickets (source_system: "support-ticketing")
# ---------------------------------------------------------------------------
# declared_purpose: customer_support
# HIGHEST EXPOSURE RISK: PII rides along in free-text notes fields.
SUPPORT_TICKETS_RETENTION_DAYS = 730  # 2 years — dispute/audit trail window

SUPPORT_TICKETS_ENTRIES = [
    RegistryEntry(
        field_name="name",
        pii_category="name",
        declared_purpose="customer_support",
        consent_scope="customer_support",
        retention_days=SUPPORT_TICKETS_RETENTION_DAYS,
        created_at=_days_ago(10),
        source_system="support-ticketing",
        table_name="support_tickets",
    ),
    RegistryEntry(
        field_name="phone",
        pii_category="phone",
        declared_purpose="customer_support",
        consent_scope="customer_support",
        retention_days=SUPPORT_TICKETS_RETENTION_DAYS,
        created_at=_days_ago(10),
        source_system="support-ticketing",
        table_name="support_tickets",
    ),
    RegistryEntry(
        field_name="order_reference",
        pii_category="order_reference",
        declared_purpose="customer_support",
        consent_scope="customer_support",
        retention_days=SUPPORT_TICKETS_RETENTION_DAYS,
        created_at=_days_ago(10),
        source_system="support-ticketing",
        table_name="support_tickets",
    ),
]


# ---------------------------------------------------------------------------
# Table 4 — marketing_events (source_system: "marketing-analytics")
# ---------------------------------------------------------------------------
# declared_purpose: marketing_analytics
# STRUCTURAL INVARIANT: every entry here carries
# consent_scope == DEIDENTIFIED_ONLY_SCOPE.
# Any raw PII observed in a marketing-analytics event is automatically a
# PURPOSE_001 violation. Phase 4 calls RegistryEntry.forbids_raw_pii().
MARKETING_EVENTS_RETENTION_DAYS = 365

MARKETING_EVENTS_ENTRIES = [
    RegistryEntry(
        field_name="hashed_customer_id",
        pii_category="hashed_identifier",
        declared_purpose="marketing_analytics",
        consent_scope=DEIDENTIFIED_ONLY_SCOPE,
        retention_days=MARKETING_EVENTS_RETENTION_DAYS,
        created_at=_days_ago(30),
        source_system="marketing-analytics",
        table_name="marketing_events",
    ),
    RegistryEntry(
        field_name="campaign_segment",
        pii_category="hashed_identifier",
        declared_purpose="marketing_analytics",
        consent_scope=DEIDENTIFIED_ONLY_SCOPE,
        retention_days=MARKETING_EVENTS_RETENTION_DAYS,
        created_at=_days_ago(30),
        source_system="marketing-analytics",
        table_name="marketing_events",
    ),
    RegistryEntry(
        field_name="event_type",
        pii_category="hashed_identifier",
        declared_purpose="marketing_analytics",
        consent_scope=DEIDENTIFIED_ONLY_SCOPE,
        retention_days=MARKETING_EVENTS_RETENTION_DAYS,
        created_at=_days_ago(30),
        source_system="marketing-analytics",
        table_name="marketing_events",
    ),

    # ------------------------------------------------------------------
    # >>> SEEDED INVARIANT-CARRIER — NOT itself a stored violation row <<<
    # This entry documents the registry-side expectation for a raw 'phone'
    # field if one were ever declared under marketing_analytics. It exists
    # so Phase 4 has a concrete RegistryEntry to retrieve when the event
    # generator injects a raw phone into a marketing event — the lookup
    # succeeds, returns this entry, and entry.forbids_raw_pii() is True.
    # ------------------------------------------------------------------
    RegistryEntry(
        field_name="phone",
        pii_category="phone",
        declared_purpose="marketing_analytics",
        consent_scope=DEIDENTIFIED_ONLY_SCOPE,
        retention_days=MARKETING_EVENTS_RETENTION_DAYS,
        created_at=_days_ago(30),
        source_system="marketing-analytics",
        table_name="marketing_events",
        is_seeded_violation=True,
        violation_note=(
            "marketing_events structurally forbids raw PII (consent_scope="
            f"'{DEIDENTIFIED_ONLY_SCOPE}'). If a raw 'phone' value "
            "is observed flowing through a marketing-analytics event, "
            "this registry entry is what Phase 4 retrieves — "
            "forbids_raw_pii() returns True, and it flags PURPOSE_001."
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
