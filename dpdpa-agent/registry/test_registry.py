"""
Phase 1 Registry Validation & Config Loader Tests
===================================================
Covers:
  1. load_org_config loads valid configs and raises OrgConfigNotFoundError on missing orgs.
  2. get_registry_entry serves lookups keyed by org_id first, raising FieldNotRegisteredError
     on missing fields.
  3. reload_org_config forces re-read bypassing the cache (hot-reload).
  4. validate_org_config granular checks (bad regex, duplicate identifier, duplicate field,
     missing keys, non-positive retention, invalid org_id, undeclared linkage field).
  5. Regression test: Blinkit reference config reproduces 100% of old registry lookups.
  6. Multi-tenant isolation: simultaneous querying of distinct orgs in the same process.

Run with: python -m pytest registry/test_registry.py -v
"""

from __future__ import annotations

import copy
from datetime import datetime, timezone

import pytest

from registry.loader import (
    FieldNotRegisteredError,
    OrgConfigNotFoundError,
    _reset_cache,
    get_registry_entry,
    list_registry_entries,
    load_org_config,
    load_registry,
    reload_org_config,
    validate_org_config,
)
from registry.models import DEIDENTIFIED_ONLY_SCOPE, MARKETING_EVENTS_ALLOWED_SCOPE
from registry.seed_registry import SEED_NOW


@pytest.fixture(autouse=True)
def fresh_registry():
    """Ensure every test starts with a clean cache."""
    _reset_cache()
    yield
    _reset_cache()


# ---------------------------------------------------------------------------
# 1. Config Loader Public API Tests
# ---------------------------------------------------------------------------

class TestConfigLoaderAPI:

    def test_load_valid_blinkit_config(self):
        config = load_org_config("blinkit")
        assert config is not None
        assert config.org_id == "blinkit"
        assert len(config.fields) > 0

    def test_load_valid_edtech_config(self):
        config = load_org_config("edtech_co")
        assert config is not None
        assert config.org_id == "edtech_co"
        field_names = {f.field_name for f in config.fields}
        assert "apaar_id" in field_names

    def test_load_nonexistent_org_raises_org_config_not_found(self):
        with pytest.raises(OrgConfigNotFoundError) as exc_info:
            load_org_config("nonexistent_corp_xyz_99")
        assert "nonexistent_corp_xyz_99" in str(exc_info.value)

    def test_get_registry_entry_unregistered_field_raises_field_not_registered(self):
        with pytest.raises(FieldNotRegisteredError) as exc_info:
            get_registry_entry("blinkit", "credit_score", "order-service")
        assert "credit_score" in str(exc_info.value)
        assert "order-service" in str(exc_info.value)

    def test_get_registry_entry_wrong_source_system_raises_field_not_registered(self):
        # aadhaar exists under delivery-partner-service, not order-service
        with pytest.raises(FieldNotRegisteredError) as exc_info:
            get_registry_entry("blinkit", "aadhaar", "order-service")
        assert "aadhaar" in str(exc_info.value)

    def test_get_registry_entry_optional_missing_mode_returns_none(self):
        entry = get_registry_entry("blinkit", "credit_score", "order-service", raise_on_missing=False)
        assert entry is None

    def test_reload_org_config_updates_cache(self):
        cfg1 = load_org_config("blinkit")
        reloaded = reload_org_config("blinkit")
        assert reloaded.org_id == "blinkit"


# ---------------------------------------------------------------------------
# 2. Granular validate_org_config Tests
# ---------------------------------------------------------------------------

class TestValidateOrgConfig:

    VALID_CONFIG = {
        "org_id": "test_org",
        "identifiers": [
            {"name": "national_id", "pattern": "^[A-Z0-9]{10}$", "validator": "none"}
        ],
        "fields": [
            {
                "field_name": "national_id",
                "pii_category": "identity",
                "declared_purpose": "kyc",
                "consent_scope": "kyc",
                "retention_days": 365,
                "source_system": "auth_service",
            },
            {
                "field_name": "phone",
                "pii_category": "phone",
                "declared_purpose": "contact",
                "consent_scope": "contact",
                "retention_days": 180,
                "source_system": "auth_service",
            },
        ],
        "linkage_rules": [
            {"fields": ["national_id", "phone"], "risk": "LINKAGE_RISK"}
        ],
    }

    def test_valid_config_returns_empty_error_list(self):
        errors = validate_org_config(self.VALID_CONFIG)
        assert errors == []

    def test_missing_org_id_rejected(self):
        bad = copy.deepcopy(self.VALID_CONFIG)
        del bad["org_id"]
        errors = validate_org_config(bad)
        assert any("org_id" in e for e in errors)

    def test_invalid_org_id_pattern_rejected(self):
        bad = copy.deepcopy(self.VALID_CONFIG)
        bad["org_id"] = "Invalid Org ID!"
        errors = validate_org_config(bad)
        assert any("org_id" in e for e in errors)

    def test_missing_fields_list_rejected(self):
        bad = copy.deepcopy(self.VALID_CONFIG)
        del bad["fields"]
        errors = validate_org_config(bad)
        assert any("fields" in e for e in errors)

    def test_empty_fields_list_rejected(self):
        bad = copy.deepcopy(self.VALID_CONFIG)
        bad["fields"] = []
        errors = validate_org_config(bad)
        assert any("fields" in e for e in errors)

    def test_bad_regex_pattern_rejected(self):
        bad = copy.deepcopy(self.VALID_CONFIG)
        bad["identifiers"][0]["pattern"] = "[invalid(regex("
        errors = validate_org_config(bad)
        assert any("pattern" in e or "regex" in e for e in errors)

    def test_duplicate_identifier_name_rejected(self):
        bad = copy.deepcopy(self.VALID_CONFIG)
        bad["identifiers"].append(
            {"name": "national_id", "pattern": "^[0-9]+$", "validator": "none"}
        )
        errors = validate_org_config(bad)
        assert any("duplicate identifier" in e.lower() for e in errors)

    def test_duplicate_field_name_under_same_system_rejected(self):
        bad = copy.deepcopy(self.VALID_CONFIG)
        bad["fields"].append(
            {
                "field_name": "phone",
                "pii_category": "phone",
                "declared_purpose": "sms",
                "consent_scope": "sms",
                "retention_days": 90,
                "source_system": "auth_service",  # same source_system + field_name
            }
        )
        errors = validate_org_config(bad)
        assert any("duplicate entry for field" in e.lower() for e in errors)

    def test_non_positive_retention_days_rejected(self):
        bad = copy.deepcopy(self.VALID_CONFIG)
        bad["fields"][0]["retention_days"] = 0
        errors = validate_org_config(bad)
        assert any("retention_days" in e for e in errors)

        bad["fields"][0]["retention_days"] = -30
        errors = validate_org_config(bad)
        assert any("retention_days" in e for e in errors)

    def test_linkage_rule_with_undeclared_field_rejected(self):
        bad = copy.deepcopy(self.VALID_CONFIG)
        bad["linkage_rules"] = [
            {"fields": ["national_id", "undeclared_field_xyz"], "risk": "LINKAGE_RISK"}
        ]
        errors = validate_org_config(bad)
        assert any("undeclared_field_xyz" in e for e in errors)

    def test_linkage_rule_with_less_than_two_fields_rejected(self):
        bad = copy.deepcopy(self.VALID_CONFIG)
        bad["linkage_rules"] = [
            {"fields": ["national_id"], "risk": "LINKAGE_RISK"}
        ]
        errors = validate_org_config(bad)
        assert any("at least 2" in e for e in errors)


# ---------------------------------------------------------------------------
# 3. Blinkit Reference Config Regression Test
# ---------------------------------------------------------------------------

class TestBlinkitRegressionEquality:
    """
    Verifies that loading the Blinkit reference config reproduces 100% of
    the fields and expectations that the legacy demo hardcoded registry provided.
    """

    def test_customers_table_fields(self):
        name_entry = get_registry_entry("blinkit", "name", "order-service")
        assert name_entry.pii_category == "name"
        assert name_entry.declared_purpose == "order_fulfillment"
        assert name_entry.retention_days == 1095

        phone_entry = get_registry_entry("blinkit", "phone", "order-service")
        assert phone_entry.pii_category == "phone"
        assert phone_entry.declared_purpose == "order_fulfillment"
        assert phone_entry.retention_days == 1095

        addr_entry = get_registry_entry("blinkit", "delivery_address", "order-service")
        assert addr_entry.pii_category == "address"
        assert addr_entry.retention_days == 1095

        hist_entry = get_registry_entry("blinkit", "order_history", "order-service")
        assert hist_entry.pii_category == "order_history"
        assert hist_entry.retention_days == 1095

    def test_delivery_partners_table_fields(self):
        pan_entry = get_registry_entry("blinkit", "pan", "delivery-partner-service")
        assert pan_entry.pii_category == "pan"
        assert pan_entry.declared_purpose == "onboarding_kyc"
        assert pan_entry.retention_days == 180

        aadhaar_entry = get_registry_entry("blinkit", "aadhaar", "delivery-partner-service")
        assert aadhaar_entry.pii_category == "aadhaar"
        assert aadhaar_entry.declared_purpose == "onboarding_kyc"
        assert aadhaar_entry.retention_days == 180
        assert aadhaar_entry.is_seeded_violation is True

        bank_entry = get_registry_entry("blinkit", "bank_details", "delivery-partner-service")
        assert bank_entry.pii_category == "financial_account"
        assert bank_entry.retention_days == 180

        address_entry = get_registry_entry("blinkit", "address", "delivery-partner-service")
        assert address_entry.pii_category == "address"
        assert address_entry.retention_days == 180

    def test_support_tickets_table_fields(self):
        name_entry = get_registry_entry("blinkit", "name", "support-ticketing")
        assert name_entry.declared_purpose == "customer_support"
        assert name_entry.retention_days == 730

        phone_entry = get_registry_entry("blinkit", "phone", "support-ticketing")
        assert phone_entry.declared_purpose == "customer_support"
        assert phone_entry.retention_days == 730

        order_ref_entry = get_registry_entry("blinkit", "order_reference", "support-ticketing")
        assert order_ref_entry.declared_purpose == "customer_support"
        assert order_ref_entry.retention_days == 730

    def test_marketing_events_table_fields(self):
        hashed_id_entry = get_registry_entry("blinkit", "hashed_customer_id", "marketing-analytics")
        assert hashed_id_entry.declared_purpose == "marketing_analytics"
        assert hashed_id_entry.consent_scope == DEIDENTIFIED_ONLY_SCOPE
        assert hashed_id_entry.forbids_raw_pii() is True

        segment_entry = get_registry_entry("blinkit", "campaign_segment", "marketing-analytics")
        assert segment_entry.declared_purpose == "marketing_analytics"
        assert segment_entry.forbids_raw_pii() is True

        event_type_entry = get_registry_entry("blinkit", "event_type", "marketing-analytics")
        assert event_type_entry.declared_purpose == "marketing_analytics"
        assert event_type_entry.forbids_raw_pii() is True

        phone_entry = get_registry_entry("blinkit", "phone", "marketing-analytics")
        assert phone_entry.declared_purpose == "marketing_analytics"
        assert phone_entry.forbids_raw_pii() is True
        assert phone_entry.is_seeded_violation is True

    def test_seeded_retention_violation_is_past_window(self):
        aadhaar_entry = get_registry_entry("blinkit", "aadhaar", "delivery-partner-service")
        assert aadhaar_entry.is_past_retention(as_of=SEED_NOW) is True


# ---------------------------------------------------------------------------
# 4. Multi-Tenant Isolation Test
# ---------------------------------------------------------------------------

class TestMultiTenantIsolation:
    """
    Ensures that multiple org configs loaded in the same process do not
    cross-contaminate each other.
    """

    def test_blinkit_and_edtech_query_isolation(self):
        # edtech_co has apaar_id, student_phone, guardian_contact
        apaar = get_registry_entry("edtech_co", "apaar_id", "student_portal")
        assert apaar.declared_purpose == "academic_records"
        assert apaar.pii_category == "student_identifier"

        # apaar_id must NOT exist in blinkit
        with pytest.raises(FieldNotRegisteredError):
            get_registry_entry("blinkit", "apaar_id", "student_portal")

        # aadhaar must NOT exist in edtech_co
        with pytest.raises(FieldNotRegisteredError):
            get_registry_entry("edtech_co", "aadhaar", "delivery-partner-service")

        # phone exists in both, but under different source systems and purposes
        blinkit_phone = get_registry_entry("blinkit", "phone", "order-service")
        edtech_phone = get_registry_entry("edtech_co", "student_phone", "student_portal")

        assert blinkit_phone.declared_purpose == "order_fulfillment"
        assert edtech_phone.declared_purpose == "academic_records"
        assert blinkit_phone.retention_days == 1095
        assert edtech_phone.retention_days == 3650

    def test_list_registry_entries_scoped_by_org(self):
        blinkit_entries = list_registry_entries("blinkit")
        edtech_entries = list_registry_entries("edtech_co")

        blinkit_fields = {e.field_name for e in blinkit_entries}
        edtech_fields = {e.field_name for e in edtech_entries}

        assert "aadhaar" in blinkit_fields
        assert "apaar_id" not in blinkit_fields

        assert "apaar_id" in edtech_fields
        assert "aadhaar" not in edtech_fields
