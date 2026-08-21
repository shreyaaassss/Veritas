"""
Phase 1 Registry Validation Tests (Phase 0 Generalised)
=========================================================
Covers:
  (a) Correct lookup for at least one field per table (all 4 tables)
  (b) None returned for an unknown field/source_system combo
  (c) The seeded retention violation is present and past its window
  (d) The marketing_events raw-PII invariant is checkable

Run with: python -m pytest registry/test_registry.py -v
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
from registry.models import DEIDENTIFIED_ONLY_SCOPE, MARKETING_EVENTS_ALLOWED_SCOPE
from registry.seed_registry import SEED_NOW


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

    def test_all_entries_have_valid_source_systems(self):
        """Every entry must have a non-empty string source_system."""
        for entry in list_registry_entries():
            assert isinstance(entry.source_system, str)
            assert len(entry.source_system) > 0


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
        must return None for the mismatched combo.
        """
        result = get_registry_entry("aadhaar", "order-service")
        assert result is None

    def test_lookup_never_raises_on_miss(self):
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
        dp_entries = list_registry_entries("delivery-partner-service")
        violations = [e for e in dp_entries if e.is_seeded_violation]
        assert len(violations) >= 1

    def test_seeded_violation_is_past_retention_window(self):
        dp_entries = list_registry_entries("delivery-partner-service")
        violation = next(e for e in dp_entries if e.is_seeded_violation)

        age_days = (SEED_NOW - violation.created_at).days
        assert age_days > violation.retention_days
        assert age_days == 240
        assert violation.retention_days == 180
        assert violation.violation_note is not None

    def test_is_past_retention_helper_confirms_violation(self):
        dp_entries = list_registry_entries("delivery-partner-service")
        violation = next(e for e in dp_entries if e.is_seeded_violation)
        assert violation.is_past_retention(as_of=SEED_NOW) is True

    def test_non_violation_delivery_partner_entries_are_within_window(self):
        dp_entries = list_registry_entries("delivery-partner-service")
        non_violations = [e for e in dp_entries if not e.is_seeded_violation]
        assert len(non_violations) >= 1
        for entry in non_violations:
            assert entry.is_past_retention(as_of=SEED_NOW) is False


# ---------------------------------------------------------------------------
# (d) marketing_events raw-PII invariant is checkable
# ---------------------------------------------------------------------------

class TestMarketingEventsInvariant:

    def test_all_marketing_events_entries_use_restrictive_scope(self):
        mkt_entries = list_registry_entries("marketing-analytics")
        assert len(mkt_entries) >= 1
        for entry in mkt_entries:
            assert entry.consent_scope == MARKETING_EVENTS_ALLOWED_SCOPE

    def test_forbids_raw_pii_true_for_marketing_entries(self):
        mkt_entries = list_registry_entries("marketing-analytics")
        for entry in mkt_entries:
            assert entry.forbids_raw_pii() is True

    def test_forbids_raw_pii_false_for_non_marketing_entries(self):
        customer_entry = get_registry_entry("name", "order-service")
        assert customer_entry.forbids_raw_pii() is False

    def test_seeded_marketing_phone_entry_is_retrievable(self):
        entry = get_registry_entry("phone", "marketing-analytics")
        assert entry is not None
        assert entry.is_seeded_violation is True
        assert entry.forbids_raw_pii() is True
