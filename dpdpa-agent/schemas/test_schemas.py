"""
Phase 0 Schema Validation Tests (Generalised)
==============================================
Confirms that Event and Verdict Pydantic models:
  1. Accept valid data with tenant_id correctly.
  2. Accept free-form source_system strings.
  3. Reject missing tenant_id and invalid enum values.
  4. Document the immutability contract.

Run with: python -m pytest schemas/test_schemas.py -v
"""

import sys
import uuid
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from schemas.models import (
    Event,
    RemediationStatus,
    RuleId,
    Severity,
    SourceType,
    Verdict,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_valid_event(**overrides) -> dict:
    base = {
        "tenant_id": "blinkit",
        "event_id": str(uuid.uuid4()),
        "source_type": "log",
        "source_system": "order-service",
        "timestamp": "2026-08-21T10:34:12Z",
        "raw_snippet": '[2026-08-21 10:34:12] Payment processed {"pan":"ABCDE1234F"}',
        "fields": {"pan": "ABCDE1234F", "amount": "5000"},
    }
    base.update(overrides)
    return base


def make_valid_verdict(**overrides) -> dict:
    base = {
        "tenant_id": "blinkit",
        "verdict_id": str(uuid.uuid4()),
        "event_id": str(uuid.uuid4()),
        "rule_id": "EXPOSURE_001",
        "severity": "HIGH",
        "source": "log",
        "source_system": "order-service",
        "field": "pan",
        "timestamp": "2026-08-21T10:34:12.500Z",
        "matched_registry_entry": None,
        "breach_notification_candidate": True,
        "remediation_status": "OPEN",
        "remediation_updated_at": None,
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Event tests
# ---------------------------------------------------------------------------

class TestEventValidation:

    def test_valid_event_instantiates(self):
        """A correctly formed event must instantiate without errors."""
        event = Event(**make_valid_event())
        assert event.tenant_id == "blinkit"
        assert isinstance(event.event_id, uuid.UUID)
        assert event.source_type == SourceType.LOG
        assert event.source_system == "order-service"
        assert isinstance(event.timestamp, datetime)
        assert event.fields["pan"] == "ABCDE1234F"

    def test_valid_event_all_source_types(self):
        """Both source_type values must be accepted."""
        for st in ["log", "api"]:
            event = Event(**make_valid_event(source_type=st))
            assert event.source_type == SourceType(st)

    def test_valid_event_accepts_any_source_system_string(self):
        """Source systems are free-form strings, org-defined."""
        for ss in ["order-service", "delivery-partner-service",
                   "student_portal", "custom_erp_system"]:
            event = Event(**make_valid_event(source_system=ss))
            assert event.source_system == ss

    def test_invalid_source_type_raises(self):
        """A bad source_type value must raise ValidationError."""
        with pytest.raises(ValidationError) as exc_info:
            Event(**make_valid_event(source_type="database"))
        errors = exc_info.value.errors()
        assert any("source_type" in str(e) for e in errors)

    def test_empty_source_system_raises(self):
        """Empty source_system must raise ValidationError."""
        with pytest.raises(ValidationError):
            Event(**make_valid_event(source_system=""))

    def test_empty_raw_snippet_raises(self):
        """raw_snippet must not be empty."""
        with pytest.raises(ValidationError):
            Event(**make_valid_event(raw_snippet=""))

    def test_missing_required_field_raises(self):
        """Missing event_id must raise ValidationError."""
        data = make_valid_event()
        del data["event_id"]
        with pytest.raises(ValidationError):
            Event(**data)

    def test_missing_tenant_id_raises(self):
        """Missing tenant_id must raise ValidationError."""
        data = make_valid_event()
        del data["tenant_id"]
        with pytest.raises(ValidationError):
            Event(**data)

    def test_timestamp_iso8601_string_accepted(self):
        """ISO8601 string timestamps must be parsed into datetime objects."""
        event = Event(**make_valid_event(timestamp="2026-01-15T08:30:00+05:30"))
        assert isinstance(event.timestamp, datetime)

    def test_fields_open_map(self):
        """fields must accept any string key-value pairs."""
        event = Event(**make_valid_event(fields={
            "aadhaar": "1234 5678 9012",
            "phone": "9876543210",
            "apaar_id": "AB12345678CD",
            "name": "Priya Nair",
        }))
        assert event.fields["aadhaar"] == "1234 5678 9012"
        assert len(event.fields) == 4


# ---------------------------------------------------------------------------
# Verdict tests
# ---------------------------------------------------------------------------

class TestVerdictValidation:

    def test_valid_verdict_instantiates(self):
        """A correctly formed verdict must instantiate without errors."""
        verdict = Verdict(**make_valid_verdict())
        assert verdict.tenant_id == "blinkit"
        assert isinstance(verdict.verdict_id, uuid.UUID)
        assert verdict.rule_id == RuleId.EXPOSURE_001
        assert verdict.severity == Severity.HIGH
        assert verdict.breach_notification_candidate is True
        assert verdict.remediation_status == RemediationStatus.OPEN
        assert verdict.remediation_updated_at is None

    def test_valid_verdict_all_rule_ids(self):
        """All three rule_id values must be accepted."""
        for rule in ["EXPOSURE_001", "PURPOSE_001", "RETENTION_001"]:
            verdict = Verdict(**make_valid_verdict(rule_id=rule))
            assert verdict.rule_id == RuleId(rule)

    def test_valid_verdict_all_severities(self):
        """All three severity values must be accepted."""
        for sev in ["LOW", "MEDIUM", "HIGH"]:
            verdict = Verdict(**make_valid_verdict(severity=sev))
            assert verdict.severity == Severity(sev)

    def test_valid_verdict_all_remediation_statuses(self):
        """All three remediation_status values must be accepted."""
        for status in ["OPEN", "ACKNOWLEDGED", "RESOLVED"]:
            verdict = Verdict(**make_valid_verdict(remediation_status=status))
            assert verdict.remediation_status == RemediationStatus(status)

    def test_invalid_rule_id_raises(self):
        """A bad rule_id value must raise ValidationError."""
        with pytest.raises(ValidationError) as exc_info:
            Verdict(**make_valid_verdict(rule_id="CONSENT_001"))
        errors = exc_info.value.errors()
        assert any("rule_id" in str(e) for e in errors)

    def test_invalid_severity_raises(self):
        """A bad severity value must raise ValidationError."""
        with pytest.raises(ValidationError) as exc_info:
            Verdict(**make_valid_verdict(severity="CRITICAL"))
        errors = exc_info.value.errors()
        assert any("severity" in str(e) for e in errors)

    def test_invalid_remediation_status_raises(self):
        """A bad remediation_status value must raise ValidationError."""
        with pytest.raises(ValidationError):
            Verdict(**make_valid_verdict(remediation_status="PENDING"))

    def test_default_remediation_status_is_open(self):
        """remediation_status must default to OPEN when not supplied."""
        data = make_valid_verdict()
        del data["remediation_status"]
        verdict = Verdict(**data)
        assert verdict.remediation_status == RemediationStatus.OPEN

    def test_matched_registry_entry_accepts_none(self):
        """matched_registry_entry may be None."""
        verdict = Verdict(**make_valid_verdict(matched_registry_entry=None))
        assert verdict.matched_registry_entry is None

    def test_matched_registry_entry_accepts_dict(self):
        """matched_registry_entry must accept a dict."""
        registry_stub = {
            "entry_id": "reg-001",
            "purpose": "order_delivery",
            "allowed_fields": ["name", "phone", "address"]
        }
        verdict = Verdict(**make_valid_verdict(matched_registry_entry=registry_stub))
        assert verdict.matched_registry_entry["purpose"] == "order_delivery"


# ---------------------------------------------------------------------------
# Immutability contract documentation test
# ---------------------------------------------------------------------------

class TestImmutabilityContract:

    MUTABLE_FIELDS = {"remediation_status", "remediation_updated_at"}

    def test_only_mutable_fields_are_documented_as_mutable(self):
        """
        Confirms that exactly remediation_status and remediation_updated_at
        are the only fields marked MUTABLE in the Verdict model.
        """
        verdict_fields = Verdict.model_fields

        actually_mutable = set()
        for field_name, field_info in verdict_fields.items():
            description = field_info.description or ""
            if "MUTABLE" in description and "IMMUTABLE" not in description:
                actually_mutable.add(field_name)

        assert actually_mutable == self.MUTABLE_FIELDS

    def test_all_other_verdict_fields_documented_as_immutable(self):
        """
        Every Verdict field that is NOT in MUTABLE_FIELDS must have IMMUTABLE
        in its description.
        """
        verdict_fields = Verdict.model_fields
        immutable_fields = set(verdict_fields.keys()) - self.MUTABLE_FIELDS

        missing_immutable_doc = []
        for field_name in immutable_fields:
            description = verdict_fields[field_name].description or ""
            if "IMMUTABLE" not in description:
                missing_immutable_doc.append(field_name)

        assert not missing_immutable_doc, f"Fields {missing_immutable_doc} are missing IMMUTABLE in doc."

    def test_remediation_status_can_be_updated_post_creation(self):
        verdict = Verdict(**make_valid_verdict())
        assert verdict.remediation_status == RemediationStatus.OPEN

        verdict.remediation_status = RemediationStatus.ACKNOWLEDGED
        verdict.remediation_updated_at = datetime.now(timezone.utc)

        assert verdict.remediation_status == RemediationStatus.ACKNOWLEDGED
        assert verdict.remediation_updated_at is not None
