"""
Phase 2 Detection Wiring Tests — Identifier Validation Layer
================================================================
Confirms detect_event() correctly tags matched_entities with the 3-way
validation_status (pattern_match / validated / failed_validation) driven
by the org's config-declared identifier validators. See validators.py
and detection/engine.py's _resolve_validation_status.

Uses the real "blinkit" seed org config (org_config/configs/blinkit/),
which already declares validator: aadhaar for the "aadhaar" identifier,
validator: pan for "pan", and validator: none for "phone" — exactly the
three cases this phase's exit criteria call for.

Run with: python -m pytest detection/test_phase2_validation.py -v
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

from schemas.models import Event, SourceType

from detection.engine import detect_event
from detection.models import FAILED_VALIDATION, PATTERN_MATCH, VALIDATED

TENANT_ID = "blinkit"

# Same synthetic, self-computed (not a real government ID) Verhoeff-valid
# Aadhaar-shaped value used in test_validators.py.
VALID_AADHAAR = "2345 6789 0124"
CHECKSUM_BROKEN_AADHAAR = "2345 6789 0125"


def _delivery_partner_event(aadhaar_value: str) -> Event:
    return Event(
        tenant_id=TENANT_ID,
        event_id=str(uuid4()),
        source_type=SourceType.API,
        source_system="delivery-partner-service",
        timestamp=datetime.now(timezone.utc).isoformat(),
        raw_snippet=f'{{"event": "delivery_partner_profile_fetch", "aadhaar": "{aadhaar_value}"}}',
        fields={"aadhaar": aadhaar_value},
    )


class TestValidatedAadhaar:

    def test_checksum_valid_aadhaar_tagged_validated(self):
        detected = detect_event(_delivery_partner_event(VALID_AADHAAR))
        aadhaar_matches = [m for m in detected.matched_entities if m.field == "aadhaar"]
        assert len(aadhaar_matches) > 0
        assert all(m.validation_status == VALIDATED for m in aadhaar_matches)


class TestFailedValidationAadhaar:

    def test_checksum_broken_aadhaar_tagged_failed_validation(self):
        detected = detect_event(_delivery_partner_event(CHECKSUM_BROKEN_AADHAAR))
        aadhaar_matches = [m for m in detected.matched_entities if m.field == "aadhaar"]
        assert len(aadhaar_matches) > 0
        assert all(m.validation_status == FAILED_VALIDATION for m in aadhaar_matches)
        # Must not be silently downgraded to pattern_match — it's a distinct signal.
        assert not any(m.validation_status == PATTERN_MATCH for m in aadhaar_matches)


class TestValidatorNoneStillDetects:

    def test_phone_field_with_validator_none_detects_as_pattern_match(self):
        """Blinkit's 'phone' identifier declares validator: none — must still
        detect fine, tagged pattern_match (Phase 2 must not regress Phase 3)."""
        event = Event(
            tenant_id=TENANT_ID,
            event_id=str(uuid4()),
            source_type=SourceType.API,
            source_system="order-service",
            timestamp=datetime.now(timezone.utc).isoformat(),
            raw_snippet='{"event": "order_placed", "phone": "9876543210"}',
            fields={"phone": "9876543210"},
        )
        detected = detect_event(event)
        phone_matches = [m for m in detected.matched_entities if m.field == "phone"]
        assert len(phone_matches) > 0
        assert all(m.validation_status == PATTERN_MATCH for m in phone_matches)


class TestUnregisteredValidatorFallsBackGracefully:
    """
    org_config/schema.py's KNOWN_VALIDATORS allowlist currently only
    permits {"none", "pan", "aadhaar"} at config-parse time, so a live
    YAML config can't yet declare an unbuilt validator name like "apaar"
    (see the deviations note in the Phase 2 summary). We exercise
    detection/engine.py's fallback path directly by monkeypatching
    load_org_config to return a config-shaped stub that DOES declare one,
    proving detection never crashes on an unregistered validator name.
    """

    def test_unregistered_validator_name_falls_back_to_pattern_match_and_warns(self, monkeypatch, caplog):
        stub_config = SimpleNamespace(
            identifiers=[SimpleNamespace(name="apaar_id", pattern="^[A-Z0-9]{12}$", validator="apaar")]
        )
        monkeypatch.setattr("detection.engine.load_org_config", lambda org_id: stub_config)

        event = Event(
            tenant_id=TENANT_ID,
            event_id=str(uuid4()),
            source_type=SourceType.API,
            source_system="student_portal",
            timestamp=datetime.now(timezone.utc).isoformat(),
            raw_snippet='{"event": "profile_fetch", "apaar_id": "AB12CD34EF56"}',
            fields={"apaar_id": "AB12CD34EF56"},
        )

        with caplog.at_level(logging.WARNING, logger="detection.engine"):
            detected = detect_event(event)  # must not raise

        apaar_matches = [m for m in detected.matched_entities if m.field == "apaar_id"]
        # The 12-char alphanumeric shape isn't covered by any of our custom
        # recognizers, so it may or may not surface a match at all — the
        # point of this test is "no crash + correct fallback if it does",
        # not "apaar_id is detected" (that's out of Phase 2 scope).
        assert all(m.validation_status == PATTERN_MATCH for m in apaar_matches)
        if apaar_matches:
            assert any("apaar" in r.message and "not" in r.message.lower() for r in caplog.records)
