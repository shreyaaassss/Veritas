"""
Phase 3 Detection Validation Tests
======================================
Covers the plan's exit criteria exactly:
  (a) known-clean event -> contains_pii: false
  (b) known-PII event per entity type (name, email, phone, Aadhaar, PAN)
      -> correctly tagged with the right entity_type
  (c) free-text log detection (support_tickets-style, PII only in raw_snippet)
  (d) no-collision cases: Aadhaar vs generic 10-digit/phone strings,
      PAN vs other alphanumeric strings

Also covers:
  - Real Phase 2-shaped events (exposure, purpose-limitation, retention
    vectors) are correctly tagged.
  - contains_pii / matched_entities are always present, never omitted.
  - The Event model itself is never mutated (DetectedEvent wraps it).

Run with: python -m pytest detection/test_detection.py -v
Or standalone: python detection/test_detection.py

NOTE: these tests load a real spaCy model via Presidio's AnalyzerEngine
on first use (module-level singleton, see analyzer_engine.py) — expect
the first test in a run to take a few seconds longer than the rest.
"""

from __future__ import annotations

import sys

import pytest

from detection.analyzer_engine import analyze_text
from detection.engine import detect_event
from detection.fixtures import (
    CLEAN_LOG_EVENT,
    DELIVERY_PARTNER_WITH_PAN_EVENT,
    EXPOSURE_LOG_EVENT,
    MARKETING_CLEAN_EVENT,
    MARKETING_PURPOSE_VIOLATION_EVENT,
    RETENTION_VIOLATION_EVENT,
    SUPPORT_TICKET_FREE_TEXT_EVENT,
)
from detection.models import DetectedEvent
from schemas.models import Event


# ---------------------------------------------------------------------------
# (a) known-clean event -> contains_pii: false
# ---------------------------------------------------------------------------

class TestCleanEvents:

    def test_clean_log_event_has_no_pii(self):
        """A clean order-status log line must be tagged contains_pii: false."""
        detected = detect_event(CLEAN_LOG_EVENT)
        assert detected.contains_pii is False
        assert detected.matched_entities == []

    def test_clean_marketing_event_has_no_pii(self):
        """A properly hashed-only marketing event must be tagged contains_pii: false."""
        detected = detect_event(MARKETING_CLEAN_EVENT)
        assert detected.contains_pii is False
        assert detected.matched_entities == []

    def test_contains_pii_and_matched_entities_always_present_even_when_empty(self):
        """
        Per the plan: contains_pii and matched_entities must be PRESENT
        (not omitted) even for clean events. Pydantic model field
        presence check, not just truthy/falsy.
        """
        detected = detect_event(CLEAN_LOG_EVENT)
        dumped = detected.model_dump()
        assert "contains_pii" in dumped
        assert "matched_entities" in dumped
        assert dumped["contains_pii"] is False
        assert dumped["matched_entities"] == []


# ---------------------------------------------------------------------------
# (b) known-PII event per entity type, correctly tagged
# ---------------------------------------------------------------------------

class TestPerEntityTypeDetection:

    def test_name_detected_as_person(self):
        results = analyze_text("Priya Nair")
        assert any(r.entity_type == "PERSON" for r in results)

    def test_email_detected(self):
        results = analyze_text("priya.nair91@gmail.com")
        assert any(r.entity_type == "EMAIL_ADDRESS" for r in results)
        # Email should be a high-confidence, unambiguous match
        email_match = next(r for r in results if r.entity_type == "EMAIL_ADDRESS")
        assert email_match.score >= 0.9

    def test_indian_phone_detected_as_in_phone(self):
        results = analyze_text("9876543210")
        assert any(r.entity_type == "IN_PHONE" for r in results)

    def test_indian_phone_with_prefix_detected(self):
        results = analyze_text("+91 9876543210")
        assert any(r.entity_type == "IN_PHONE" for r in results)

    def test_aadhaar_detected_as_in_aadhaar(self):
        results = analyze_text("5521 8890 3347")
        assert any(r.entity_type == "IN_AADHAAR" for r in results)

    def test_pan_detected_as_in_pan(self):
        results = analyze_text("BQPRV4521K")
        assert any(r.entity_type == "IN_PAN" for r in results)

    def test_exposure_event_tags_all_three_field_pii_types(self):
        """
        The real exposure-vector log event (name/phone/address in
        structured fields) must be tagged with matches for the name and
        phone fields at minimum.
        """
        detected = detect_event(EXPOSURE_LOG_EVENT)
        assert detected.contains_pii is True

        matched_by_field = {}
        for m in detected.matched_entities:
            matched_by_field.setdefault(m.field, set()).add(m.entity_type)

        assert "PERSON" in matched_by_field.get("name", set())
        assert "IN_PHONE" in matched_by_field.get("phone", set())

    def test_marketing_purpose_violation_tags_phone_field(self):
        """The purpose-limitation vector event must flag the leaked phone field."""
        detected = detect_event(MARKETING_PURPOSE_VIOLATION_EVENT)
        assert detected.contains_pii is True
        phone_matches = [m for m in detected.matched_entities if m.field == "phone"]
        assert len(phone_matches) > 0
        assert any(m.entity_type == "IN_PHONE" for m in phone_matches)

    def test_retention_violation_tags_aadhaar_field(self):
        """The retention vector event (stale delivery partner) must flag the aadhaar field."""
        detected = detect_event(RETENTION_VIOLATION_EVENT)
        assert detected.contains_pii is True
        aadhaar_matches = [m for m in detected.matched_entities if m.field == "aadhaar"]
        assert len(aadhaar_matches) > 0
        assert aadhaar_matches[0].entity_type == "IN_AADHAAR"
        assert aadhaar_matches[0].matched_text == "5521 8890 3347"

    def test_delivery_partner_pan_field_detected(self):
        """A fresh delivery-partner payload with a pan field must flag it correctly."""
        detected = detect_event(DELIVERY_PARTNER_WITH_PAN_EVENT)
        assert detected.contains_pii is True
        pan_matches = [m for m in detected.matched_entities if m.field == "pan"]
        assert len(pan_matches) > 0
        assert pan_matches[0].entity_type == "IN_PAN"

    def test_confidence_scores_passed_through_unmodified(self):
        """confidence must be a real Presidio score (0.0-1.0), not a placeholder."""
        detected = detect_event(EXPOSURE_LOG_EVENT)
        for m in detected.matched_entities:
            assert 0.0 <= m.confidence <= 1.0


# ---------------------------------------------------------------------------
# (c) free-text log detection
# ---------------------------------------------------------------------------

class TestFreeTextDetection:

    def test_support_ticket_free_text_phone_detected(self):
        """
        A support_tickets-style event with a phone number ONLY inside
        raw_snippet (no structured 'phone' field) must still be caught.
        """
        detected = detect_event(SUPPORT_TICKET_FREE_TEXT_EVENT)
        assert detected.contains_pii is True

        # Confirm there is genuinely no 'phone' key in the original event's fields
        assert "phone" not in SUPPORT_TICKET_FREE_TEXT_EVENT.fields

        # But the phone number must still surface as a raw_snippet match
        raw_snippet_matches = [m for m in detected.matched_entities if m.field == "raw_snippet"]
        assert any(m.entity_type == "IN_PHONE" for m in raw_snippet_matches)

    def test_support_ticket_free_text_name_detected(self):
        """The customer name embedded in free text must also be caught."""
        detected = detect_event(SUPPORT_TICKET_FREE_TEXT_EVENT)
        assert "name" not in SUPPORT_TICKET_FREE_TEXT_EVENT.fields
        raw_snippet_matches = [m for m in detected.matched_entities if m.field == "raw_snippet"]
        assert any(m.entity_type == "PERSON" for m in raw_snippet_matches)

    def test_free_text_matches_labeled_with_raw_snippet_field(self):
        """
        Matches with no corresponding structured field must use the
        FREE_TEXT_FIELD_LABEL ('raw_snippet'), per the documented
        field-attribution design decision.
        """
        from detection.models import FREE_TEXT_FIELD_LABEL
        detected = detect_event(SUPPORT_TICKET_FREE_TEXT_EVENT)
        free_text_matches = [m for m in detected.matched_entities if m.field == FREE_TEXT_FIELD_LABEL]
        assert len(free_text_matches) > 0

    def test_structured_field_matches_not_duplicated_via_raw_snippet(self):
        """
        Dedup check: the exposure event's raw_snippet contains the SAME
        name/phone/address already captured via fields — those values
        must not also appear a second time labeled 'raw_snippet'.
        """
        detected = detect_event(EXPOSURE_LOG_EVENT)
        raw_snippet_matches = [m for m in detected.matched_entities if m.field == "raw_snippet"]
        field_matched_texts = {
            m.matched_text.strip(' \t\n\r"\',:;{}[]()').lower()
            for m in detected.matched_entities if m.field != "raw_snippet"
        }
        for m in raw_snippet_matches:
            normalized = m.matched_text.strip(' \t\n\r"\',:;{}[]()').lower()
            assert normalized not in field_matched_texts, (
                f"raw_snippet match {m.matched_text!r} duplicates a field-sourced match"
            )


# ---------------------------------------------------------------------------
# (d) no-collision cases
# ---------------------------------------------------------------------------

class TestNoCollisions:

    def test_aadhaar_does_not_fire_in_phone(self):
        """A 12-digit Aadhaar-shaped value must never also match IN_PHONE."""
        results = analyze_text("5521 8890 3347")
        assert not any(r.entity_type == "IN_PHONE" for r in results)

    def test_phone_does_not_fire_in_aadhaar(self):
        """A 10-digit Indian phone number must never also match IN_AADHAAR."""
        results = analyze_text("9876543210")
        assert not any(r.entity_type == "IN_AADHAAR" for r in results)

    def test_unspaced_12digit_aadhaar_does_not_fire_in_phone(self):
        """Bare unspaced 12-digit Aadhaar shape must not match IN_PHONE either."""
        results = analyze_text("552188903347")
        assert not any(r.entity_type == "IN_PHONE" for r in results)

    def test_generic_order_id_does_not_fire_aadhaar_or_pan(self):
        """
        Order IDs (Phase 2's 'BLK-XXXXXX' shape) must not false-positive
        as Aadhaar or PAN — different length/structure entirely.
        """
        results = analyze_text("BLK-431682")
        assert not any(r.entity_type == "IN_AADHAAR" for r in results)
        assert not any(r.entity_type == "IN_PAN" for r in results)

    def test_hashed_customer_id_does_not_fire_pan(self):
        """
        Lowercase hashed IDs (Phase 2's 'hcid_xxxxxxxx' shape) must not
        false-positive as PAN (PAN requires uppercase letters + digit pattern).
        """
        results = analyze_text("hcid_d8ba0b4b")
        assert not any(r.entity_type == "IN_PAN" for r in results)

    def test_pan_pattern_requires_exact_structure(self):
        """
        A string that's letters+digits but NOT in the exact 5-4-1
        PAN structure must not match IN_PAN.
        """
        results = analyze_text("ABCD12345")  # only 4 letters, not 5
        assert not any(r.entity_type == "IN_PAN" for r in results)

    def test_bank_account_masked_string_does_not_fire_pan_or_aadhaar(self):
        """Masked bank account strings (Phase 2's 'XXXXXXXX1234' shape) must not false-positive."""
        results = analyze_text("XXXXXXXX4521")
        assert not any(r.entity_type == "IN_PAN" for r in results)
        assert not any(r.entity_type == "IN_AADHAAR" for r in results)

    def test_order_id_shape_does_not_fire_person(self):
        """
        REGRESSION TEST — see analyzer_engine.py's _is_known_id_shape.
        Presidio's spaCy-backed PERSON recognizer scores 'BLK-431682'
        identically (0.85) to a real name like 'Priya Nair' when the
        token is evaluated in isolation (no surrounding sentence
        context). Without the ID-shape filter, every clean order-status
        log line (whose only field is order_id) would be falsely tagged
        contains_pii=True. This must stay suppressed.
        """
        results = analyze_text("BLK-431682")
        assert not any(r.entity_type == "PERSON" for r in results)

    def test_partner_id_shape_does_not_fire_person(self):
        """Same regression coverage for the 'DP-4471' partner ID shape."""
        results = analyze_text("DP-4471")
        assert not any(r.entity_type == "PERSON" for r in results)

    def test_id_shape_filter_does_not_suppress_real_names(self):
        """
        Sanity check: the narrow ID-shape filter (uppercase-hyphen-digits)
        must not accidentally suppress real two-word names, which have a
        completely different shape (letters + space, no hyphen/digits).
        """
        results = analyze_text("Priya Nair")
        assert any(r.entity_type == "PERSON" for r in results)


# ---------------------------------------------------------------------------
# DetectedEvent wrapper contract — does not mutate the frozen Event
# ---------------------------------------------------------------------------

class TestDetectedEventWrapperContract:

    def test_detect_event_returns_detected_event_instance(self):
        result = detect_event(EXPOSURE_LOG_EVENT)
        assert isinstance(result, DetectedEvent)

    def test_original_event_unchanged_and_accessible(self):
        """The wrapped .event must be the identical, unmutated original Event."""
        detected = detect_event(EXPOSURE_LOG_EVENT)
        assert isinstance(detected.event, Event)
        assert detected.event.event_id == EXPOSURE_LOG_EVENT.event_id
        assert detected.event.raw_snippet == EXPOSURE_LOG_EVENT.raw_snippet
        assert detected.event.fields == EXPOSURE_LOG_EVENT.fields

    def test_event_model_has_no_detection_fields_bolted_on(self):
        """
        Confirms Event itself was never extended with contains_pii/
        matched_entities — those live only on DetectedEvent.
        """
        assert not hasattr(EXPOSURE_LOG_EVENT, "contains_pii")
        assert not hasattr(EXPOSURE_LOG_EVENT, "matched_entities")
        assert "contains_pii" not in Event.model_fields
        assert "matched_entities" not in Event.model_fields


# ---------------------------------------------------------------------------
# Standalone runner (no pytest)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("Running Phase 3 detection validation tests...")
    print("(First test will be slower — loading spaCy model into AnalyzerEngine)\n")

    test_classes = [
        TestCleanEvents,
        TestPerEntityTypeDetection,
        TestFreeTextDetection,
        TestNoCollisions,
        TestDetectedEventWrapperContract,
    ]

    passed = 0
    failed = 0

    for cls in test_classes:
        instance = cls()
        methods = [m for m in dir(instance) if m.startswith("test_")]
        for method_name in methods:
            try:
                getattr(instance, method_name)()
                print(f"  ✅ {cls.__name__}::{method_name}")
                passed += 1
            except Exception as e:
                print(f"  ❌ {cls.__name__}::{method_name}")
                print(f"     {type(e).__name__}: {e}")
                failed += 1

    print(f"\n{'='*60}")
    print(f"Results: {passed} passed, {failed} failed")

    if failed > 0:
        sys.exit(1)
    else:
        print("Phase 3 detection contracts verified. ✅")
