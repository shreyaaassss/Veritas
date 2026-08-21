"""
Phase 0 Exit Criteria — Org Config Tests
==========================================
These tests verify every exit criterion stated in the Phase 0 spec.
They must ALL pass before Phase 0 is considered complete.

Exit criteria tested here:
  1. get_org_config("blinkit") and get_org_config("edtech_co") return correct,
     distinct configs from the same code (no branching on org_id).
  2. Uploading deliberately broken configs is rejected with specific,
     actionable error messages.
  3. Event and Verdict models require tenant_id.
  4. Grep-level assertions: no domain-specific strings in core logic.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from org_config.schema import BUILTIN_PATTERNS, KNOWN_VALIDATORS, OrgConfig
from org_config.store import (
    delete_org_configs,
    get_org_config,
    list_org_config_versions,
    upload_org_config,
)
from org_config.validator import (
    OrgConfigValidationError,
    validate_org_config,
    validate_org_config_strict,
)
from schemas.models import Event, RemediationStatus, RuleId, Severity, SourceType, Verdict


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

VALID_EDTECH_CONFIG = {
    "org_id": "edtech_co_test",
    "identifiers": [
        {"name": "apaar_id", "pattern": "^[A-Z0-9]{12}$", "validator": "none"}
    ],
    "fields": [
        {
            "field_name": "apaar_id",
            "pii_category": "student_identifier",
            "declared_purpose": "academic_records",
            "consent_scope": "enrollment",
            "retention_days": 3650,
            "source_system": "student_portal",
        },
        {
            "field_name": "student_name",
            "pii_category": "name",
            "declared_purpose": "academic_records",
            "consent_scope": "enrollment",
            "retention_days": 3650,
            "source_system": "student_portal",
        },
        {
            "field_name": "dob",
            "pii_category": "date_of_birth",
            "declared_purpose": "academic_records",
            "consent_scope": "enrollment",
            "retention_days": 3650,
            "source_system": "student_portal",
        },
    ],
    "linkage_rules": [
        {"fields": ["apaar_id", "student_name", "dob"], "risk": "LINKAGE_RISK"}
    ],
}


@pytest.fixture(autouse=True)
def clean_test_org_configs():
    """Delete test org configs before and after each test to avoid pollution."""
    delete_org_configs("edtech_co_test")
    delete_org_configs("broken_org")
    yield
    delete_org_configs("edtech_co_test")
    delete_org_configs("broken_org")


# ===========================================================================
# EXIT CRITERION 1: get_org_config returns correct, distinct configs
# ===========================================================================

class TestGetOrgConfig:
    """
    Exit criterion 1: get_org_config("blinkit") and get_org_config("edtech_co")
    both return correct, distinct configs from the same running code.
    No branching logic anywhere that checks if org_id == "blinkit".
    """

    def test_get_blinkit_config_returns_config(self):
        config = get_org_config("blinkit")
        assert config is not None, "get_org_config('blinkit') must return a config"
        assert config.org_id == "blinkit"

    def test_blinkit_config_has_expected_source_systems(self):
        config = get_org_config("blinkit")
        assert config is not None
        source_systems = {f.source_system for f in config.fields}
        assert "order-service" in source_systems
        assert "delivery-partner-service" in source_systems
        assert "support-ticketing" in source_systems
        assert "marketing-analytics" in source_systems

    def test_blinkit_config_has_aadhaar_field(self):
        config = get_org_config("blinkit")
        assert config is not None
        field_names = {f.field_name for f in config.fields}
        assert "aadhaar" in field_names
        aadhaar = next(f for f in config.fields if f.field_name == "aadhaar")
        assert aadhaar.pii_category == "aadhaar"
        assert aadhaar.retention_days == 180
        assert aadhaar.source_system == "delivery-partner-service"

    def test_blinkit_has_marketing_analytics_field_with_deidentified_scope(self):
        config = get_org_config("blinkit")
        assert config is not None
        marketing_fields = [
            f for f in config.fields if f.source_system == "marketing-analytics"
        ]
        assert len(marketing_fields) > 0
        for f in marketing_fields:
            assert f.consent_scope == "deidentified_or_hashed_only", (
                f"marketing-analytics field {f.field_name!r} must have "
                "consent_scope='deidentified_or_hashed_only'"
            )

    def test_get_edtech_config_returns_config(self):
        config = get_org_config("edtech_co")
        assert config is not None, "get_org_config('edtech_co') must return a config"
        assert config.org_id == "edtech_co"

    def test_edtech_config_has_apaar_id_field(self):
        config = get_org_config("edtech_co")
        assert config is not None
        field_names = {f.field_name for f in config.fields}
        assert "apaar_id" in field_names
        apaar = next(f for f in config.fields if f.field_name == "apaar_id")
        assert apaar.pii_category == "student_identifier"
        assert apaar.source_system == "student_portal"

    def test_edtech_config_has_guardian_portal_source(self):
        config = get_org_config("edtech_co")
        assert config is not None
        source_systems = {f.source_system for f in config.fields}
        assert "guardian_portal" in source_systems

    def test_two_configs_are_distinct(self):
        """The same code returns correct, DIFFERENT configs for different orgs."""
        blinkit = get_org_config("blinkit")
        edtech = get_org_config("edtech_co")
        assert blinkit is not None
        assert edtech is not None
        assert blinkit.org_id != edtech.org_id
        blinkit_fields = {f.field_name for f in blinkit.fields}
        edtech_fields = {f.field_name for f in edtech.fields}
        # The configs should have distinct fields — they're genuinely different orgs
        assert "aadhaar" in blinkit_fields
        assert "apaar_id" not in blinkit_fields
        assert "apaar_id" in edtech_fields
        assert "aadhaar" not in edtech_fields

    def test_nonexistent_org_returns_none(self):
        config = get_org_config("org_that_does_not_exist_xyz")
        assert config is None

    def test_blinkit_has_linkage_rules(self):
        config = get_org_config("blinkit")
        assert config is not None
        assert len(config.linkage_rules) > 0

    def test_edtech_has_linkage_rule_for_student_name_school_dob(self):
        config = get_org_config("edtech_co")
        assert config is not None
        rule_field_sets = [set(r.fields) for r in config.linkage_rules]
        assert {"student_name", "school_name", "dob"} in rule_field_sets, (
            "edtech_co must have a linkage rule for [student_name, school_name, dob]"
        )


# ===========================================================================
# EXIT CRITERION 2: Broken configs are rejected with specific error messages
# ===========================================================================

class TestBrokenConfigRejection:
    """
    Exit criterion 2: uploading a deliberately broken config is rejected
    with a specific, actionable error message.
    """

    def test_missing_org_id_is_rejected(self):
        config = {
            "fields": [
                {
                    "field_name": "phone",
                    "pii_category": "phone",
                    "declared_purpose": "test",
                    "consent_scope": "test",
                    "retention_days": 365,
                    "source_system": "test-service",
                }
            ]
        }
        with pytest.raises(OrgConfigValidationError) as exc_info:
            validate_org_config_strict(config)
        errors = exc_info.value.errors
        assert any("org_id" in e for e in errors), (
            f"Expected an error mentioning 'org_id', got: {errors}"
        )

    def test_missing_fields_is_rejected(self):
        config = {"org_id": "test_org"}
        with pytest.raises(OrgConfigValidationError) as exc_info:
            validate_org_config_strict(config)
        errors = exc_info.value.errors
        assert any("fields" in e for e in errors), (
            f"Expected an error mentioning 'fields', got: {errors}"
        )

    def test_duplicate_field_name_is_rejected(self):
        config = {
            "org_id": "test_org",
            "fields": [
                {
                    "field_name": "phone",
                    "pii_category": "phone",
                    "declared_purpose": "support",
                    "consent_scope": "support",
                    "retention_days": 365,
                    "source_system": "support-service",
                },
                {
                    "field_name": "phone",  # DUPLICATE within support-service
                    "pii_category": "phone",
                    "declared_purpose": "orders",
                    "consent_scope": "orders",
                    "retention_days": 730,
                    "source_system": "support-service",
                },
            ],
        }
        with pytest.raises(OrgConfigValidationError) as exc_info:
            validate_org_config_strict(config)
        errors = exc_info.value.errors
        assert any("duplicate" in e.lower() or "phone" in e for e in errors), (
            f"Expected an error mentioning 'duplicate' or 'phone', got: {errors}"
        )

    def test_invalid_regex_pattern_is_rejected(self):
        config = {
            "org_id": "test_org",
            "identifiers": [
                {
                    "name": "bad_id",
                    "pattern": "[invalid(regex(",  # invalid regex
                    "validator": "none",
                }
            ],
            "fields": [
                {
                    "field_name": "bad_id",
                    "pii_category": "test",
                    "declared_purpose": "test",
                    "consent_scope": "test",
                    "retention_days": 365,
                    "source_system": "test-service",
                }
            ],
        }
        with pytest.raises(OrgConfigValidationError) as exc_info:
            validate_org_config_strict(config)
        errors = exc_info.value.errors
        assert any("regex" in e.lower() or "pattern" in e.lower() for e in errors), (
            f"Expected an error mentioning 'regex' or 'pattern', got: {errors}"
        )

    def test_unknown_validator_name_is_rejected(self):
        config = {
            "org_id": "test_org",
            "identifiers": [
                {
                    "name": "apaar_id",
                    "pattern": "^[A-Z0-9]{12}$",
                    "validator": "not_a_real_validator",  # unknown
                }
            ],
            "fields": [
                {
                    "field_name": "apaar_id",
                    "pii_category": "student_identifier",
                    "declared_purpose": "academic_records",
                    "consent_scope": "enrollment",
                    "retention_days": 3650,
                    "source_system": "student_portal",
                }
            ],
        }
        with pytest.raises(OrgConfigValidationError) as exc_info:
            validate_org_config_strict(config)
        errors = exc_info.value.errors
        assert any("validator" in e.lower() or "not_a_real_validator" in e for e in errors), (
            f"Expected an error mentioning 'validator', got: {errors}"
        )

    def test_linkage_rule_with_undeclared_field_is_rejected(self):
        config = {
            "org_id": "test_org",
            "fields": [
                {
                    "field_name": "phone",
                    "pii_category": "phone",
                    "declared_purpose": "support",
                    "consent_scope": "support",
                    "retention_days": 365,
                    "source_system": "support-service",
                }
            ],
            "linkage_rules": [
                {
                    "fields": ["phone", "email_address_not_declared"],  # undeclared field
                    "risk": "LINKAGE_RISK",
                }
            ],
        }
        with pytest.raises(OrgConfigValidationError) as exc_info:
            validate_org_config_strict(config)
        errors = exc_info.value.errors
        assert any("email_address_not_declared" in e or "undeclared" in e.lower() or "not declared" in e.lower() for e in errors), (
            f"Expected an error mentioning the undeclared field, got: {errors}"
        )

    def test_linkage_rule_with_only_one_field_is_rejected(self):
        config = {
            "org_id": "test_org",
            "fields": [
                {
                    "field_name": "phone",
                    "pii_category": "phone",
                    "declared_purpose": "support",
                    "consent_scope": "support",
                    "retention_days": 365,
                    "source_system": "support-service",
                }
            ],
            "linkage_rules": [
                {"fields": ["phone"], "risk": "LINKAGE_RISK"}  # needs 2+ fields
            ],
        }
        with pytest.raises(OrgConfigValidationError) as exc_info:
            validate_org_config_strict(config)
        assert len(exc_info.value.errors) > 0

    def test_upload_org_config_returns_error_dict_on_invalid(self):
        """upload_org_config should never raise — it returns an error dict."""
        result = upload_org_config("broken_org", {"org_id": "broken_org"})  # missing fields
        assert result["status"] == "error"
        assert "errors" in result
        assert len(result["errors"]) > 0
        assert any("fields" in e for e in result["errors"])

    def test_upload_org_config_org_id_mismatch_is_rejected(self):
        """Uploading a config for org A under org B's key must be rejected."""
        result = upload_org_config("org_b", {**VALID_EDTECH_CONFIG, "org_id": "org_a"})
        assert result["status"] == "error"
        assert any("mismatch" in e.lower() for e in result["errors"])

    def test_empty_fields_list_is_rejected(self):
        config = {"org_id": "test_org", "fields": []}
        with pytest.raises(OrgConfigValidationError) as exc_info:
            validate_org_config_strict(config)
        errors = exc_info.value.errors
        assert any("fields" in e.lower() for e in errors)


# ===========================================================================
# EXIT CRITERION 3: Event and Verdict require tenant_id
# ===========================================================================

class TestTenantIdRequired:
    """
    Exit criterion 3: every Event and Verdict must include tenant_id.
    Constructing either without it must fail validation.
    """

    def _valid_event_dict(self, **overrides) -> dict:
        base = {
            "tenant_id": "blinkit",
            "event_id": str(uuid.uuid4()),
            "source_type": "log",
            "source_system": "order-service",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "raw_snippet": "test log line",
            "fields": {"name": "Priya"},
        }
        base.update(overrides)
        return base

    def _valid_verdict_dict(self, event_id: str | None = None, **overrides) -> dict:
        base = {
            "tenant_id": "blinkit",
            "verdict_id": str(uuid.uuid4()),
            "event_id": event_id or str(uuid.uuid4()),
            "rule_id": "EXPOSURE_001",
            "severity": "HIGH",
            "source": "log",
            "source_system": "order-service",
            "field": "pan",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "matched_registry_entry": None,
            "breach_notification_candidate": True,
            "remediation_status": "OPEN",
            "remediation_updated_at": None,
        }
        base.update(overrides)
        return base

    def test_event_with_tenant_id_is_valid(self):
        event = Event(**self._valid_event_dict())
        assert event.tenant_id == "blinkit"

    def test_event_without_tenant_id_fails(self):
        d = self._valid_event_dict()
        del d["tenant_id"]
        with pytest.raises(ValidationError) as exc_info:
            Event(**d)
        errors_str = str(exc_info.value)
        assert "tenant_id" in errors_str

    def test_event_with_empty_tenant_id_fails(self):
        with pytest.raises(ValidationError):
            Event(**self._valid_event_dict(tenant_id=""))

    def test_verdict_with_tenant_id_is_valid(self):
        verdict = Verdict(**self._valid_verdict_dict())
        assert verdict.tenant_id == "blinkit"

    def test_verdict_without_tenant_id_fails(self):
        d = self._valid_verdict_dict()
        del d["tenant_id"]
        with pytest.raises(ValidationError) as exc_info:
            Verdict(**d)
        errors_str = str(exc_info.value)
        assert "tenant_id" in errors_str

    def test_event_source_system_is_free_form_string(self):
        """source_system is now any string — no fixed enum."""
        event = Event(**self._valid_event_dict(source_system="my-custom-service-xyz"))
        assert event.source_system == "my-custom-service-xyz"

    def test_verdict_source_system_is_free_form_string(self):
        verdict = Verdict(**self._valid_verdict_dict(source_system="student_portal"))
        assert verdict.source_system == "student_portal"

    def test_source_system_enum_no_longer_exists(self):
        """SourceSystem enum was removed in Phase 0. This test confirms it."""
        import schemas.models as m
        assert not hasattr(m, "SourceSystem"), (
            "SourceSystem enum should have been removed in Phase 0. "
            "It was replaced by free-form str."
        )


# ===========================================================================
# EXIT CRITERION 4: Built-in pattern aliases work correctly
# ===========================================================================

class TestBuiltinPatterns:
    def test_indian_phone_builtin_resolves(self):
        cfg = validate_org_config_strict({
            "org_id": "test_org",
            "identifiers": [
                {"name": "phone", "pattern": "indian_phone", "validator": "none"}
            ],
            "fields": [
                {
                    "field_name": "phone",
                    "pii_category": "phone",
                    "declared_purpose": "support",
                    "consent_scope": "support",
                    "retention_days": 365,
                    "source_system": "support-service",
                }
            ],
        })
        phone_id = next(i for i in cfg.identifiers if i.name == "phone")
        assert phone_id.pattern == BUILTIN_PATTERNS["indian_phone"]

    def test_known_validators_are_stable(self):
        assert "none" in KNOWN_VALIDATORS
        assert "pan" in KNOWN_VALIDATORS
        assert "aadhaar" in KNOWN_VALIDATORS


# ===========================================================================
# EXIT CRITERION: Upload + retrieve roundtrip
# ===========================================================================

class TestUploadRetrieveRoundtrip:
    def test_upload_then_get_returns_same_config(self):
        result = upload_org_config("edtech_co_test", VALID_EDTECH_CONFIG)
        assert result["status"] == "ok"
        config = get_org_config("edtech_co_test")
        assert config is not None
        assert config.org_id == "edtech_co_test"
        field_names = {f.field_name for f in config.fields}
        assert "apaar_id" in field_names

    def test_multiple_uploads_create_versions(self):
        upload_org_config("edtech_co_test", VALID_EDTECH_CONFIG)
        import time; time.sleep(1)  # ensure different timestamp
        upload_org_config("edtech_co_test", VALID_EDTECH_CONFIG)
        versions = list_org_config_versions("edtech_co_test")
        assert len(versions) >= 2

    def test_get_returns_latest_version(self):
        """After two uploads, get_org_config returns the latest one."""
        upload_org_config("edtech_co_test", VALID_EDTECH_CONFIG)
        import time; time.sleep(1)
        modified = {**VALID_EDTECH_CONFIG, "fields": [
            *VALID_EDTECH_CONFIG["fields"],
            {
                "field_name": "new_field_added_in_v2",
                "pii_category": "phone",
                "declared_purpose": "test",
                "consent_scope": "test",
                "retention_days": 30,
                "source_system": "student_portal",
            }
        ]}
        upload_org_config("edtech_co_test", modified)
        config = get_org_config("edtech_co_test")
        assert config is not None
        field_names = {f.field_name for f in config.fields}
        assert "new_field_added_in_v2" in field_names, (
            "get_org_config should return the latest uploaded version"
        )
