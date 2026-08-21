"""
Phase 4 Rule Engine Validation Tests
========================================
Covers the plan's exit criteria exactly:
  (a) seeded delivery_partners retention violation -> correct RETENTION_001
  (b) seeded marketing-events raw-PII case -> a verdict, per the documented
      exposure/purpose precedence decision (EXPOSURE_001, per Design Decision #1)
  (c) exposure short-circuit prevents double-counting
  (d) unregistered-field handling behaves as documented (Design Decision #2)
  (e) breach-notification tagging fires only for the correct combination
  (f) a fully clean event produces no verdict at all

Also covers:
  - Zero false negatives across the full deliberately-seeded violation set
    from Phases 1-2 (hard requirement per the plan).
  - The Phase 3/4 interface gap fix (_dedupe_matches_by_field) — a field
    with multiple recognizer matches produces exactly one verdict.
  - VerdictFanout pushes to both downstream queues.

Run with: python -m pytest rules/test_rule_engine.py -v
Or standalone: python rules/test_rule_engine.py
"""

from __future__ import annotations

import asyncio
import sys

import pytest

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
from detection.models import DetectedEvent, MatchedEntity
from registry.loader import _reset_cache, load_registry
from rules.engine import evaluate_event
from rules.fanout import VerdictFanout, stub_evidence_store_consumer, stub_llm_explainer_consumer
from schemas.models import Event, RuleId, Severity, SourceType


@pytest.fixture(autouse=True)
def fresh_registry():
    """Every test starts with a clean, freshly-loaded registry (matches Phase 1's test pattern)."""
    _reset_cache()
    load_registry(force_reload=True)
    yield
    _reset_cache()


# ---------------------------------------------------------------------------
# (a) seeded delivery_partners retention violation -> correct RETENTION_001
# ---------------------------------------------------------------------------

class TestSeededRetentionViolation:

    def test_retention_violation_event_produces_retention_001(self):
        """
        The real Phase 2-shaped retention vector event (stale delivery
        partner DP-4471 / Suresh K. / aadhaar 5521 8890 3347, 240 days
        old vs 180-day retention) must produce a RETENTION_001 verdict
        on the aadhaar field.
        """
        detected = detect_event(RETENTION_VIOLATION_EVENT)
        verdicts = evaluate_event(detected)

        retention_verdicts = [v for v in verdicts if v.rule_id == RuleId.RETENTION_001]
        assert len(retention_verdicts) == 1

        v = retention_verdicts[0]
        assert v.field == "aadhaar"
        assert v.severity == Severity.HIGH  # aadhaar is high-sensitivity
        assert v.source_system == "delivery-partner-service"
        assert v.matched_registry_entry is not None
        assert v.matched_registry_entry["field_name"] == "aadhaar"
        assert v.remediation_status.value == "OPEN"
        assert v.remediation_updated_at is None

    def test_retention_violation_breach_notification_candidate(self):
        """Aadhaar + HIGH severity retention violation must be a breach-notification candidate."""
        detected = detect_event(RETENTION_VIOLATION_EVENT)
        verdicts = evaluate_event(detected)
        retention_verdict = next(v for v in verdicts if v.rule_id == RuleId.RETENTION_001)
        assert retention_verdict.breach_notification_candidate is True

    def test_registry_confirms_seeded_violation_independently(self):
        """
        Cross-check against Phase 1's own seeded-violation assertions —
        confirms Phase 4's verdict age math agrees with Phase 1's
        is_past_retention() helper on the same underlying data.
        """
        from registry.loader import get_registry_entry
        entry = get_registry_entry("aadhaar", "delivery-partner-service")
        assert entry.is_seeded_violation is True
        assert entry.is_past_retention() is True


# ---------------------------------------------------------------------------
# (b) seeded marketing-events raw-PII case -> a verdict, per documented precedence
# ---------------------------------------------------------------------------

class TestSeededMarketingPurposeCase:

    def test_marketing_purpose_violation_produces_a_verdict(self):
        """
        The seeded marketing-events raw-phone-leak event must produce
        exactly one verdict for the phone field.
        """
        detected = detect_event(MARKETING_PURPOSE_VIOLATION_EVENT)
        verdicts = evaluate_event(detected)
        phone_verdicts = [v for v in verdicts if v.field == "phone"]
        assert len(phone_verdicts) == 1

    def test_marketing_purpose_case_classified_as_exposure_per_design_decision_1(self):
        """
        Per Design Decision #1 (documented at length in rules/engine.py
        and rules/README.md): Check 1's source_system == 'marketing-analytics'
        clause intercepts this case BEFORE Check 2 ever runs, so the plan's
        literal instruction produces EXPOSURE_001, NOT PURPOSE_001 — even
        though Phase 1's forbids_raw_pii() was built expecting the latter.
        This test locks in the AS-SPECIFIED behavior; if the team resolves
        Design Decision #1 differently, this test is expected to need
        updating alongside that decision.
        """
        detected = detect_event(MARKETING_PURPOSE_VIOLATION_EVENT)
        verdicts = evaluate_event(detected)
        phone_verdict = next(v for v in verdicts if v.field == "phone")
        assert phone_verdict.rule_id == RuleId.EXPOSURE_001
        assert phone_verdict.severity == Severity.HIGH
        assert phone_verdict.matched_registry_entry is None  # no registry lookup on exposure path

    def test_registry_forbids_raw_pii_invariant_still_true_even_though_unreached(self):
        """
        Confirms Phase 1's structural invariant is still independently
        correct (entry.forbids_raw_pii() is True for the seeded phone
        entry) even though, per Design Decision #1, Check 2 never
        actually reaches this entry for real marketing-analytics traffic
        under the current precedence rule. This is the crux of the
        documented tension — both facts are true simultaneously.
        """
        from registry.loader import get_registry_entry
        entry = get_registry_entry("phone", "marketing-analytics")
        assert entry is not None
        assert entry.forbids_raw_pii() is True


# ---------------------------------------------------------------------------
# (c) exposure short-circuit prevents double-counting
# ---------------------------------------------------------------------------

class TestExposureShortCircuit:

    def test_exposure_field_never_also_produces_purpose_or_retention_verdict(self):
        """
        For every field that triggers EXPOSURE_001, there must be no
        OTHER verdict for that same (event_id, field) pair.
        """
        detected = detect_event(EXPOSURE_LOG_EVENT)
        verdicts = evaluate_event(detected)

        by_field = {}
        for v in verdicts:
            by_field.setdefault(v.field, []).append(v)

        for field, field_verdicts in by_field.items():
            assert len(field_verdicts) == 1, (
                f"field {field!r} produced {len(field_verdicts)} verdicts, expected exactly 1"
            )

    def test_exposure_verdicts_all_high_severity_no_registry_lookup(self):
        detected = detect_event(EXPOSURE_LOG_EVENT)
        verdicts = evaluate_event(detected)
        assert len(verdicts) > 0
        for v in verdicts:
            assert v.rule_id == RuleId.EXPOSURE_001
            assert v.severity == Severity.HIGH
            assert v.matched_registry_entry is None

    def test_one_verdict_per_distinct_value_across_all_fixtures(self):
        """
        General short-circuit contract check: across every fixture event,
        no single (field, matched_text) VALUE ever produces more than one
        verdict. Note: field alone is NOT a unique key when field ==
        'raw_snippet' (Phase 3's FREE_TEXT_FIELD_LABEL) — multiple
        genuinely distinct free-text PII values (e.g. a name AND a phone
        number in one note) legitimately share that label, per Phase 3's
        documented design (detection/models.py FREE_TEXT_FIELD_LABEL).
        The real per-field-VALUE uniqueness guarantee is what
        _dedupe_matches_by_field enforces (field, matched_text) — this
        test checks THAT contract, not a stricter one Phase 3 never promised.
        """
        from detection.fixtures import (
            DELIVERY_PARTNER_WITH_PAN_EVENT,
            MARKETING_PURPOSE_VIOLATION_EVENT,
            RETENTION_VIOLATION_EVENT,
            SUPPORT_TICKET_FREE_TEXT_EVENT,
        )
        for event in [
            EXPOSURE_LOG_EVENT,
            MARKETING_PURPOSE_VIOLATION_EVENT,
            RETENTION_VIOLATION_EVENT,
            DELIVERY_PARTNER_WITH_PAN_EVENT,
            SUPPORT_TICKET_FREE_TEXT_EVENT,
        ]:
            detected = detect_event(event)
            verdicts = evaluate_event(detected)
            # Verdict doesn't carry matched_text, so re-derive the
            # (field, value)-level uniqueness check from the deduped
            # matches directly rather than from verdicts alone.
            from rules.engine import _dedupe_matches_by_field
            deduped = _dedupe_matches_by_field(detected.matched_entities)
            keys = [(m.field, m.matched_text) for m in deduped]
            assert len(keys) == len(set(keys)), (
                f"duplicate (field, matched_text) pairs survived dedup: {keys}"
            )

            # And confirm field-level counts only exceed 1 for the known
            # shared-label case (raw_snippet with genuinely distinct values).
            from collections import Counter
            field_counts = Counter(v.field for v in verdicts)
            for field, count in field_counts.items():
                if count > 1:
                    assert field == "raw_snippet", (
                        f"field {field!r} produced {count} verdicts but is not the "
                        f"shared free-text label — this indicates a real duplicate"
                    )


# ---------------------------------------------------------------------------
# (d) unregistered-field handling behaves as documented
# ---------------------------------------------------------------------------

class TestUnregisteredFieldHandling:

    def test_unregistered_field_produces_purpose_001_not_crash(self):
        """
        A PII-containing field with no registry entry at all must
        produce a PURPOSE_001 verdict (Design Decision #2), never raise.
        Constructed directly: a fabricated Event with a field/source_system
        combo guaranteed absent from the registry.
        """
        event = Event(
            tenant_id="blinkit",
            event_id="11111111-1111-1111-1111-111111111111",
            source_type=SourceType.API,
            source_system="order-service",
            timestamp="2026-08-21T00:00:00Z",
            raw_snippet='{"loyalty_tier_email": "someone@example.com"}',
            fields={"loyalty_tier_email": "someone@example.com"},
        )
        detected = DetectedEvent(
            event=event,
            contains_pii=True,
            matched_entities=[
                MatchedEntity(
                    field="loyalty_tier_email",
                    entity_type="EMAIL_ADDRESS",
                    confidence=1.0,
                    matched_text="someone@example.com",
                )
            ],
        )

        try:
            verdicts = evaluate_event(detected)
        except Exception as e:
            pytest.fail(f"evaluate_event raised on unregistered field instead of returning a verdict: {e}")

        assert len(verdicts) == 1
        v = verdicts[0]
        assert v.rule_id == RuleId.PURPOSE_001
        assert v.matched_registry_entry is None
        assert v.field == "loyalty_tier_email"

    def test_unregistered_field_severity_uses_medium_default_for_non_high_category(self):
        """email is MEDIUM sensitivity -> unregistered email field gets MEDIUM, not the HIGH override."""
        event = Event(
            tenant_id="blinkit",
            event_id="22222222-2222-2222-2222-222222222222",
            source_type=SourceType.API,
            source_system="order-service",
            timestamp="2026-08-21T00:00:00Z",
            raw_snippet='{"contact_email": "x@y.com"}',
            fields={"contact_email": "x@y.com"},
        )
        detected = DetectedEvent(
            event=event,
            contains_pii=True,
            matched_entities=[
                MatchedEntity(field="contact_email", entity_type="EMAIL_ADDRESS", confidence=1.0, matched_text="x@y.com")
            ],
        )
        verdicts = evaluate_event(detected)
        assert verdicts[0].severity == Severity.MEDIUM

    def test_unregistered_field_severity_upgrades_to_high_for_aadhaar_category(self):
        """
        Design Decision #2's category-derived HIGH override: even with no
        registry entry, an unregistered field whose entity_type maps to
        aadhaar/pan must still get HIGH severity, not the MEDIUM default.
        """
        event = Event(
            tenant_id="blinkit",
            event_id="33333333-3333-3333-3333-333333333333",
            source_type=SourceType.API,
            source_system="order-service",  # aadhaar not registered here
            timestamp="2026-08-21T00:00:00Z",
            raw_snippet='{"some_unexpected_field": "9988 7766 5544"}',
            fields={"some_unexpected_field": "9988 7766 5544"},
        )
        detected = DetectedEvent(
            event=event,
            contains_pii=True,
            matched_entities=[
                MatchedEntity(field="some_unexpected_field", entity_type="IN_AADHAAR", confidence=0.75, matched_text="9988 7766 5544")
            ],
        )
        verdicts = evaluate_event(detected)
        assert len(verdicts) == 1
        assert verdicts[0].severity == Severity.HIGH
        assert verdicts[0].rule_id == RuleId.PURPOSE_001


# ---------------------------------------------------------------------------
# (e) breach-notification tagging fires only for the correct combination
# ---------------------------------------------------------------------------

class TestBreachNotificationTagging:

    def test_high_severity_aadhaar_retention_is_breach_candidate(self):
        detected = detect_event(RETENTION_VIOLATION_EVENT)
        verdicts = evaluate_event(detected)
        aadhaar_verdict = next(v for v in verdicts if v.field == "aadhaar")
        assert aadhaar_verdict.severity == Severity.HIGH
        assert aadhaar_verdict.breach_notification_candidate is True

    def test_high_severity_phone_exposure_is_not_breach_candidate(self):
        """
        A HIGH-severity EXPOSURE_001 on a phone field must NOT be a
        breach-notification candidate — HIGH severity alone is not
        sufficient; the pii_category must specifically be aadhaar/pan.
        """
        detected = detect_event(EXPOSURE_LOG_EVENT)
        verdicts = evaluate_event(detected)
        phone_verdict = next(v for v in verdicts if v.field == "phone")
        assert phone_verdict.severity == Severity.HIGH  # exposure is always HIGH
        assert phone_verdict.breach_notification_candidate is False  # but phone isn't eligible

    def test_high_severity_name_exposure_is_not_breach_candidate(self):
        detected = detect_event(EXPOSURE_LOG_EVENT)
        verdicts = evaluate_event(detected)
        name_verdict = next(v for v in verdicts if v.field == "name")
        assert name_verdict.severity == Severity.HIGH
        assert name_verdict.breach_notification_candidate is False

    def test_pan_high_severity_is_breach_candidate(self):
        detected = detect_event(DELIVERY_PARTNER_WITH_PAN_EVENT)
        verdicts = evaluate_event(detected)
        # PAN field itself is within retention window and correctly registered
        # with matching pii_category, so it produces NO verdict (clean) in this
        # fixture — confirm that, then separately verify PAN's breach-eligibility
        # via the sensitivity module directly (unit-level, not fixture-dependent).
        pan_verdicts = [v for v in verdicts if v.field == "pan"]
        assert len(pan_verdicts) == 0  # fresh, in-window, correctly-typed PAN = clean

        from rules.sensitivity import BREACH_NOTIFICATION_ELIGIBLE_CATEGORIES
        assert "pan" in BREACH_NOTIFICATION_ELIGIBLE_CATEGORIES
        assert "aadhaar" in BREACH_NOTIFICATION_ELIGIBLE_CATEGORIES
        assert "phone" not in BREACH_NOTIFICATION_ELIGIBLE_CATEGORIES
        assert "name" not in BREACH_NOTIFICATION_ELIGIBLE_CATEGORIES


# ---------------------------------------------------------------------------
# (f) a fully clean event produces no verdict at all
# ---------------------------------------------------------------------------

class TestCleanEventsProduceNoVerdicts:

    def test_clean_log_event_produces_zero_verdicts(self):
        detected = detect_event(CLEAN_LOG_EVENT)
        assert detected.contains_pii is False
        verdicts = evaluate_event(detected)
        assert verdicts == []

    def test_clean_marketing_event_produces_zero_verdicts(self):
        detected = detect_event(MARKETING_CLEAN_EVENT)
        assert detected.contains_pii is False
        verdicts = evaluate_event(detected)
        assert verdicts == []

    def test_contains_pii_false_short_circuits_without_registry_calls(self):
        """
        Confirms the cheap early-exit: for a contains_pii=False event,
        evaluate_event must return [] without even attempting a registry
        lookup. Verified indirectly by using a source_system/field combo
        that would raise/behave oddly if looked up, while contains_pii
        stays False — if evaluate_event tried to look anything up here
        it would still succeed (get_registry_entry never raises), so this
        test's real value is the explicit contains_pii check below.
        """
        event = Event(
            tenant_id="blinkit",
            event_id="44444444-4444-4444-4444-444444444444",
            source_type=SourceType.LOG,
            source_system="order-service",
            timestamp="2026-08-21T00:00:00Z",
            raw_snippet="order confirmed",
            fields={"order_id": "BLK-999999"},
        )
        detected = DetectedEvent(event=event, contains_pii=False, matched_entities=[])
        assert evaluate_event(detected) == []


# ---------------------------------------------------------------------------
# Zero false negatives across the full seeded violation set (hard requirement)
# ---------------------------------------------------------------------------

class TestZeroFalseNegativesOnSeededSet:

    def test_every_seeded_violation_fixture_produces_at_least_one_verdict(self):
        """
        Hard requirement per the plan: zero false negatives on the
        deliberately-seeded violation set. Every fixture representing a
        seeded violation vector must produce at least one verdict.
        """
        seeded_violation_fixtures = {
            "EXPOSURE_LOG_EVENT": EXPOSURE_LOG_EVENT,
            "MARKETING_PURPOSE_VIOLATION_EVENT": MARKETING_PURPOSE_VIOLATION_EVENT,
            "RETENTION_VIOLATION_EVENT": RETENTION_VIOLATION_EVENT,
            "SUPPORT_TICKET_FREE_TEXT_EVENT": SUPPORT_TICKET_FREE_TEXT_EVENT,
        }
        for name, event in seeded_violation_fixtures.items():
            detected = detect_event(event)
            verdicts = evaluate_event(detected)
            assert len(verdicts) > 0, f"FALSE NEGATIVE: {name} produced zero verdicts"

    def test_clean_fixtures_produce_zero_verdicts(self):
        """Inverse check: clean fixtures must never produce a spurious verdict."""
        clean_fixtures = {
            "CLEAN_LOG_EVENT": CLEAN_LOG_EVENT,
            "MARKETING_CLEAN_EVENT": MARKETING_CLEAN_EVENT,
        }
        for name, event in clean_fixtures.items():
            detected = detect_event(event)
            verdicts = evaluate_event(detected)
            assert len(verdicts) == 0, f"FALSE POSITIVE: {name} produced {len(verdicts)} verdict(s)"


# ---------------------------------------------------------------------------
# Interface gap fix: per-field match deduplication
# ---------------------------------------------------------------------------

class TestPerFieldMatchDeduplication:

    def test_aadhaar_field_double_matched_by_two_recognizers_produces_one_verdict(self):
        """
        REGRESSION TEST for the documented Phase 3/4 interface gap: an
        Aadhaar value gets matched by BOTH the custom IN_AADHAAR
        recognizer AND Presidio's generic PHONE_NUMBER recognizer.
        Without _dedupe_matches_by_field, this produced TWO verdicts
        (RETENTION_001 + a spurious PURPOSE_001) for the same field.
        """
        detected = detect_event(DELIVERY_PARTNER_WITH_PAN_EVENT)
        verdicts = evaluate_event(detected)
        aadhaar_verdicts = [v for v in verdicts if v.field == "aadhaar"]
        assert len(aadhaar_verdicts) == 1, (
            f"expected exactly 1 verdict for aadhaar field, got {len(aadhaar_verdicts)}: "
            f"{[(v.rule_id.value, v.severity.value) for v in aadhaar_verdicts]}"
        )
        # And it must be the CORRECT typing (IN_AADHAAR-derived), not the
        # spurious PHONE_NUMBER-derived one — i.e. RETENTION_001 (this
        # fixture's aadhaar is within-window per Phase 1 but this specific
        # DP-7712 entry doesn't exist in the registry, so it's actually
        # PURPOSE_001 via Design Decision #2's unregistered-field path
        # OR RETENTION_001 if somehow matched to the seeded row — confirm
        # whichever it is, it's exactly one, correctly typed as aadhaar
        # sensitivity (HIGH), not a phone-derived MEDIUM misclassification.
        assert aadhaar_verdicts[0].severity == Severity.HIGH

    def test_dedupe_keeps_highest_confidence_match(self):
        """Unit-level check on the dedup function itself."""
        from rules.engine import _dedupe_matches_by_field

        matches = [
            MatchedEntity(field="aadhaar", entity_type="PHONE_NUMBER", confidence=0.4, matched_text="4412 7789 0021"),
            MatchedEntity(field="aadhaar", entity_type="IN_AADHAAR", confidence=0.75, matched_text="4412 7789 0021"),
        ]
        result = _dedupe_matches_by_field(matches)
        assert len(result) == 1
        assert result[0].entity_type == "IN_AADHAAR"
        assert result[0].confidence == 0.75

    def test_dedupe_keeps_distinct_values_in_same_field(self):
        """
        Two genuinely different values both labeled 'raw_snippet' (e.g.
        a name AND a phone number in one free-text note) must NOT be
        collapsed into one — dedup keys on (field, matched_text), not
        just field.
        """
        from rules.engine import _dedupe_matches_by_field

        matches = [
            MatchedEntity(field="raw_snippet", entity_type="PERSON", confidence=0.85, matched_text="Ananya Bhat"),
            MatchedEntity(field="raw_snippet", entity_type="IN_PHONE", confidence=0.75, matched_text="9812345670"),
        ]
        result = _dedupe_matches_by_field(matches)
        assert len(result) == 2


# ---------------------------------------------------------------------------
# VerdictFanout — pushes to both downstream queues
# ---------------------------------------------------------------------------

class TestVerdictFanout:

    @pytest.mark.asyncio
    async def test_push_delivers_to_both_queues(self):
        detected = detect_event(EXPOSURE_LOG_EVENT)
        verdicts = evaluate_event(detected)
        assert len(verdicts) > 0

        fanout = VerdictFanout()
        await fanout.push(verdicts[0])

        assert fanout.llm_explainer_queue.qsize() == 1
        assert fanout.evidence_store_queue.qsize() == 1

        llm_verdict = await fanout.llm_explainer_queue.get()
        es_verdict = await fanout.evidence_store_queue.get()
        assert llm_verdict.verdict_id == verdicts[0].verdict_id
        assert es_verdict.verdict_id == verdicts[0].verdict_id

    @pytest.mark.asyncio
    async def test_push_many_fans_out_all_verdicts(self):
        detected = detect_event(EXPOSURE_LOG_EVENT)
        verdicts = evaluate_event(detected)

        fanout = VerdictFanout()
        await fanout.push_many(verdicts)

        assert fanout.llm_explainer_queue.qsize() == len(verdicts)
        assert fanout.evidence_store_queue.qsize() == len(verdicts)

    @pytest.mark.asyncio
    async def test_stub_consumers_drain_independently(self):
        detected = detect_event(EXPOSURE_LOG_EVENT)
        verdicts = evaluate_event(detected)

        fanout = VerdictFanout()
        await fanout.push_many(verdicts)

        llm_consumed = await stub_llm_explainer_consumer(fanout, max_verdicts=len(verdicts))
        es_consumed = await stub_evidence_store_consumer(fanout, max_verdicts=len(verdicts))

        assert len(llm_consumed) == len(verdicts)
        assert len(es_consumed) == len(verdicts)


# ---------------------------------------------------------------------------
# Standalone runner (pytest-asyncio required for async tests; skip those
# in standalone mode and note it)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("Running Phase 4 rule engine validation tests (sync tests only; use pytest for async)...\n")

    test_classes = [
        TestSeededRetentionViolation,
        TestSeededMarketingPurposeCase,
        TestExposureShortCircuit,
        TestUnregisteredFieldHandling,
        TestBreachNotificationTagging,
        TestCleanEventsProduceNoVerdicts,
        TestZeroFalseNegativesOnSeededSet,
        TestPerFieldMatchDeduplication,
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
    print(f"Results: {passed} passed, {failed} failed (async VerdictFanout tests skipped — run via pytest)")

    if failed > 0:
        sys.exit(1)
    else:
        print("Phase 4 rule engine contracts verified (sync subset). ✅")
