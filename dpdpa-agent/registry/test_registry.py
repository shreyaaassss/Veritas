"""
Phase 1 Registry Validation Tests
===================================
Covers:
  (a) Correct lookup for at least one field per table (all 4 tables)
  (b) None returned for an unknown field/source_system combo
  (c) The seeded retention violation is present and past its window
  (d) The marketing_events raw-PII invariant is checkable

Run with: python -m pytest registry/test_registry.py -v
Or standalone: python registry/test_registry.py
"""

import sys
from datetime import datetime, timezone

import pytest

from registry.loader import (
    _reset_cache,
    get_registry_entry,
    list_registry_entries,
    load_registry,
)
from registry.models import MARKETING_EVENTS_ALLOWED_SCOPE, TABLE_TO_SOURCE_SYSTEM
from registry.seed_registry import SEED_NOW
from schemas.models import SourceSystem


@pytest.fixture(autouse=True)
def fresh_registry():
    """Ensure every test starts with a clean, freshly-loaded registry."""
    _reset_cache()
    load_registry(force_reload=True)
    yield
    _reset_cache()


# ---------------------------------------------------------------------------
# (a) Correct lookup for at least one field per table
# ---------------------------------------------------------------------------

class TestPerTableLookup:

    def test_customers_lookup(self):
        """customers.name must resolve under order-service."""
        entry = get_registry_entry("name", "order-service")
        assert entry is not None
        assert entry.table_name == "customers"
        assert entry.declared_purpose == "order_fulfillment"
        assert entry.pii_category == "name"

    def test_delivery_partners_lookup(self):
        """delivery_partners.pan must resolve under delivery-partner-service."""
        entry = get_registry_entry("pan", "delivery-partner-service")
        assert entry is not None
        assert entry.table_name == "delivery_partners"
        assert entry.declared_purpose == "onboarding_kyc"
        assert entry.retention_days == 180

    def test_support_tickets_lookup(self):
        """support_tickets.phone must resolve under support-ticketing."""
        entry = get_registry_entry("phone", "support-ticketing")
        assert entry is not None
        assert entry.table_name == "support_tickets"
        assert entry.declared_purpose == "customer_support"

    def test_marketing_events_lookup(self):
        """marketing_events.hashed_customer_id must resolve under marketing-analytics."""
        entry = get_registry_entry("hashed_customer_id", "marketing-analytics")
        assert entry is not None
        assert entry.table_name == "marketing_events"
        assert entry.declared_purpose == "marketing_analytics"
        assert entry.consent_scope == MARKETING_EVENTS_ALLOWED_SCOPE

    def test_all_four_tables_represented_in_full_listing(self):
        """list_registry_entries() with no filter must include all 4 table names."""
        all_entries = list_registry_entries()
        table_names = {e.table_name for e in all_entries}
        assert table_names == {
            "customers", "delivery_partners", "support_tickets", "marketing_events"
        }

    def test_table_to_source_system_mapping_matches_schema_enum(self):
        """
        Every value in TABLE_TO_SOURCE_SYSTEM must be a real SourceSystem enum
        member — guards against the registry silently drifting from Phase 0's
        locked Event schema enum.
        """
        valid_values = {s.value for s in SourceSystem}
        for table, source_system in TABLE_TO_SOURCE_SYSTEM.items():
            assert source_system.value in valid_values, (
                f"{table} maps to '{source_system}', which is not a valid "
                f"SourceSystem enum value from schemas.models"
            )


# ---------------------------------------------------------------------------
# (b) None returned for unknown field/source_system combo
# ---------------------------------------------------------------------------

class TestUnknownLookups:

    def test_unknown_field_returns_none(self):
        """An unregistered field name must return None, not raise."""
        result = get_registry_entry("credit_score", "order-service")
        assert result is None

    def test_unknown_source_system_string_returns_none(self):
        """A source_system string with no matching entries must return None."""
        result = get_registry_entry("name", "payments-service")
        assert result is None

    def test_valid_field_wrong_source_system_returns_none(self):
        """
        A field that IS registered, but under a different source_system,
        must return None for the mismatched combo (fields are scoped per
        source_system, not global).
        """
        # 'aadhaar' exists under delivery-partner-service, not order-service
        result = get_registry_entry("aadhaar", "order-service")
        assert result is None

    def test_lookup_never_raises_on_miss(self):
        """Explicit non-exception contract check for Phase 4's benefit."""
        try:
            result = get_registry_entry("totally_made_up_field", "order-service")
        except Exception as e:
            pytest.fail(f"get_registry_entry raised {type(e).__name__} instead of returning None: {e}")
        assert result is None


# ---------------------------------------------------------------------------
# (c) Seeded retention violation present and past its window
# ---------------------------------------------------------------------------

class TestSeededRetentionViolation:

    def test_seeded_violation_row_exists(self):
        """At least one delivery_partners entry must be flagged is_seeded_violation."""
        dp_entries = list_registry_entries("delivery-partner-service")
        violations = [e for e in dp_entries if e.is_seeded_violation]
        assert len(violations) >= 1, "Expected at least one seeded violation in delivery_partners"

    def test_seeded_violation_is_past_retention_window(self):
        """
        The seeded violation's age (as of SEED_NOW) must exceed its
        retention_days — this is what makes it a genuine RETENTION_001
        candidate rather than just a flag with no substance behind it.
        """
        dp_entries = list_registry_entries("delivery-partner-service")
        violation = next(e for e in dp_entries if e.is_seeded_violation)

        age_days = (SEED_NOW - violation.created_at).days
        assert age_days > violation.retention_days, (
            f"Seeded violation age ({age_days} days) should exceed "
            f"retention_days ({violation.retention_days}) to be a valid RETENTION_001 case"
        )
        # Concretely pin the numbers from the seed data so a silent edit
        # to seed_registry.py that weakens the violation gets caught.
        assert age_days == 240
        assert violation.retention_days == 180
        assert violation.violation_note is not None

    def test_is_past_retention_helper_confirms_violation(self):
        """RegistryEntry.is_past_retention() must independently confirm the violation."""
        dp_entries = list_registry_entries("delivery-partner-service")
        violation = next(e for e in dp_entries if e.is_seeded_violation)
        assert violation.is_past_retention(as_of=SEED_NOW) is True

    def test_non_violation_delivery_partner_entries_are_within_window(self):
        """Sanity check: the OTHER delivery_partners entries must NOT be flagged as violations."""
        dp_entries = list_registry_entries("delivery-partner-service")
        non_violations = [e for e in dp_entries if not e.is_seeded_violation]
        assert len(non_violations) >= 1
        for entry in non_violations:
            assert entry.is_past_retention(as_of=SEED_NOW) is False, (
                f"{entry.field_name} was not seeded as a violation but is past retention anyway"
            )


# ---------------------------------------------------------------------------
# (d) marketing_events raw-PII invariant is checkable
# ---------------------------------------------------------------------------

class TestMarketingEventsInvariant:

    def test_all_marketing_events_entries_use_restrictive_scope(self):
        """
        Every marketing_events entry must carry the restrictive consent_scope
        — this is the structural (not per-row) invariant Phase 4 depends on.
        """
        mkt_entries = list_registry_entries("marketing-analytics")
        assert len(mkt_entries) >= 1
        for entry in mkt_entries:
            assert entry.consent_scope == MARKETING_EVENTS_ALLOWED_SCOPE

    def test_forbids_raw_pii_true_for_marketing_entries(self):
        """forbids_raw_pii() must return True for every marketing_events entry."""
        mkt_entries = list_registry_entries("marketing-analytics")
        for entry in mkt_entries:
            assert entry.forbids_raw_pii() is True

    def test_forbids_raw_pii_false_for_non_marketing_entries(self):
        """
        Sanity check: forbids_raw_pii() must be False for entries outside
        marketing_analytics — this isn't a global flag, it's scope-specific.
        """
        customer_entry = get_registry_entry("name", "order-service")
        assert customer_entry.forbids_raw_pii() is False

    def test_seeded_marketing_phone_entry_is_retrievable(self):
        """
        The seeded 'phone' entry under marketing-analytics must be retrievable
        (not None) so Phase 4 gets a definitive registry-backed reason when
        Phase 2's generator injects raw PII into a marketing event, rather
        than an ambiguous 'field not found' case.
        """
        entry = get_registry_entry("phone", "marketing-analytics")
        assert entry is not None
        assert entry.is_seeded_violation is True
        assert entry.forbids_raw_pii() is True


# ---------------------------------------------------------------------------
# Standalone runner (no pytest)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("Running Phase 1 registry validation tests...\n")

    test_classes = [
        TestPerTableLookup,
        TestUnknownLookups,
        TestSeededRetentionViolation,
        TestMarketingEventsInvariant,
    ]

    passed = 0
    failed = 0

    for cls in test_classes:
        instance = cls()
        methods = [m for m in dir(instance) if m.startswith("test_")]
        for method_name in methods:
            _reset_cache()
            load_registry(force_reload=True)
            try:
                getattr(instance, method_name)()
                print(f"  ✅ {cls.__name__}::{method_name}")
                passed += 1
            except Exception as e:
                print(f"  ❌ {cls.__name__}::{method_name}")
                print(f"     {type(e).__name__}: {e}")
                failed += 1
            finally:
                _reset_cache()

    print(f"\n{'='*60}")
    print(f"Results: {passed} passed, {failed} failed")

    if failed > 0:
        sys.exit(1)
    else:
        print("Phase 1 registry contracts verified. ✅")
