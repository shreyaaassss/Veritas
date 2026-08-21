"""
Phase 2 Ingestion Validation Tests
=====================================
Covers:
  (a) Every event pushed to the queue validates against the Phase 0 Event model
  (b) The violation-rate parameter measurably changes how often PII-containing
      events are produced (rate=0 vs rate=1 comparison)
  (c) source_system / source_type values never fall outside the locked enums

Also covers the plan's specific "at least one deliberately-generated event
per vector exists and is inspectable" requirement: (a) exposure-vector log
line with raw PII, (b) purpose-limitation API event with raw PII in a
marketing context, (c) retention-vector API event referencing the stale
delivery-partner record.

Run with: python -m pytest ingestion/test_ingestion.py -v
Or standalone: python ingestion/test_ingestion.py
"""

from __future__ import annotations

import asyncio
import random
import sys

import pytest

from ingestion.api_generator import api_generator
from ingestion.config import IngestionConfig
from ingestion.fixtures import STALE_DELIVERY_PARTNER
from ingestion.log_generator import log_generator
from schemas.models import Event, SourceSystem, SourceType


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _drain_queue(queue: asyncio.Queue) -> list[Event]:
    events = []
    while not queue.empty():
        events.append(queue.get_nowait())
    return events


async def _run_log_generator(n: int, rate: float, seed: int = 42) -> list[Event]:
    queue: asyncio.Queue = asyncio.Queue()
    cfg = IngestionConfig(log_emit_interval_seconds=0.0, log_exposure_violation_rate=rate)
    await log_generator(queue, cfg, max_events=n, rng=random.Random(seed))
    return await _drain_queue(queue)


async def _run_api_generator(
    n: int,
    marketing_rate: float = 0.0,
    retention_rate: float = 0.0,
    seed: int = 42,
) -> list[Event]:
    queue: asyncio.Queue = asyncio.Queue()
    cfg = IngestionConfig(
        api_emit_interval_seconds=0.0,
        api_marketing_purpose_violation_rate=marketing_rate,
        api_retention_violation_rate=retention_rate,
    )
    await api_generator(queue, cfg, max_events=n, rng=random.Random(seed))
    return await _drain_queue(queue)


def _has_raw_pii_fields(event: Event, pii_field_names: set[str]) -> bool:
    return any(f in event.fields for f in pii_field_names)


# ---------------------------------------------------------------------------
# (a) Every event validates against the Phase 0 Event model
# ---------------------------------------------------------------------------

class TestSchemaValidity:

    @pytest.mark.asyncio
    async def test_log_generator_events_are_valid_event_instances(self):
        """Every event the log generator produces must already BE a valid Event."""
        events = await _run_log_generator(n=20, rate=0.5)
        assert len(events) == 20
        for e in events:
            assert isinstance(e, Event)

    @pytest.mark.asyncio
    async def test_api_generator_events_are_valid_event_instances(self):
        """Every event the API generator produces must already BE a valid Event."""
        events = await _run_api_generator(n=20, marketing_rate=0.5, retention_rate=0.5)
        assert len(events) == 20
        for e in events:
            assert isinstance(e, Event)

    @pytest.mark.asyncio
    async def test_log_events_have_nonempty_raw_snippet(self):
        events = await _run_log_generator(n=10, rate=0.5)
        for e in events:
            assert len(e.raw_snippet) > 0

    @pytest.mark.asyncio
    async def test_api_events_have_nonempty_raw_snippet(self):
        """Per normalizer.py's design decision, API raw_snippet is never empty."""
        events = await _run_api_generator(n=10, marketing_rate=0.5, retention_rate=0.5)
        for e in events:
            assert len(e.raw_snippet) > 0
            # Should be valid JSON since normalize_api_event uses json.dumps
            import json
            json.loads(e.raw_snippet)

    @pytest.mark.asyncio
    async def test_all_events_have_unique_event_ids(self):
        log_events = await _run_log_generator(n=15, rate=0.3)
        api_events = await _run_api_generator(n=15, marketing_rate=0.3, retention_rate=0.3)
        all_ids = [str(e.event_id) for e in log_events + api_events]
        assert len(all_ids) == len(set(all_ids)), "Duplicate event_id detected"

    @pytest.mark.asyncio
    async def test_log_event_source_type_is_log(self):
        events = await _run_log_generator(n=10, rate=0.2)
        for e in events:
            assert e.source_type == SourceType.LOG

    @pytest.mark.asyncio
    async def test_api_event_source_type_is_api(self):
        events = await _run_api_generator(n=10, marketing_rate=0.2, retention_rate=0.2)
        for e in events:
            assert e.source_type == SourceType.API


# ---------------------------------------------------------------------------
# (b) Violation-rate parameter measurably changes PII production
# ---------------------------------------------------------------------------

class TestViolationRateControl:

    @pytest.mark.asyncio
    async def test_log_rate_zero_produces_no_pii(self):
        """rate=0 must produce a clean stream — no name/phone/address fields at all."""
        events = await _run_log_generator(n=30, rate=0.0)
        pii_fields = {"name", "phone", "address"}
        violating = [e for e in events if _has_raw_pii_fields(e, pii_fields)]
        assert len(violating) == 0, "rate=0 produced PII-containing log lines"

    @pytest.mark.asyncio
    async def test_log_rate_one_produces_all_pii(self):
        """rate=1 must make every single log line a PII-dumping violation."""
        events = await _run_log_generator(n=30, rate=1.0)
        pii_fields = {"name", "phone", "address"}
        violating = [e for e in events if _has_raw_pii_fields(e, pii_fields)]
        assert len(violating) == len(events), "rate=1 did not produce all violations"

    @pytest.mark.asyncio
    async def test_log_violation_rate_measurably_differs(self):
        """Core contract test: rate=0 and rate=1 must produce measurably different outcomes."""
        clean_events = await _run_log_generator(n=30, rate=0.0)
        violating_events = await _run_log_generator(n=30, rate=1.0)

        pii_fields = {"name", "phone", "address"}
        clean_violations = sum(1 for e in clean_events if _has_raw_pii_fields(e, pii_fields))
        all_violations = sum(1 for e in violating_events if _has_raw_pii_fields(e, pii_fields))

        assert clean_violations == 0
        assert all_violations == 30
        assert clean_violations < all_violations

    @pytest.mark.asyncio
    async def test_marketing_purpose_rate_zero_produces_no_raw_phone(self):
        """marketing purpose violation rate=0 must never leak a raw phone field."""
        events = await _run_api_generator(n=30, marketing_rate=0.0, retention_rate=0.0)
        marketing_events = [e for e in events if e.source_system == SourceSystem.MARKETING_ANALYTICS]
        assert len(marketing_events) > 0
        for e in marketing_events:
            assert "phone" not in e.fields, "rate=0 should never leak raw phone in marketing events"

    @pytest.mark.asyncio
    async def test_marketing_purpose_rate_one_always_leaks_phone(self):
        """marketing purpose violation rate=1 must leak a raw phone in every marketing event."""
        events = await _run_api_generator(n=30, marketing_rate=1.0, retention_rate=0.0)
        marketing_events = [e for e in events if e.source_system == SourceSystem.MARKETING_ANALYTICS]
        assert len(marketing_events) > 0
        for e in marketing_events:
            assert "phone" in e.fields

    @pytest.mark.asyncio
    async def test_retention_rate_zero_never_references_stale_partner(self):
        """retention violation rate=0 must never reference the seeded stale partner_id."""
        events = await _run_api_generator(n=30, marketing_rate=0.0, retention_rate=0.0)
        dp_events = [e for e in events if e.source_system == SourceSystem.DELIVERY_PARTNER]
        assert len(dp_events) > 0
        stale_id = STALE_DELIVERY_PARTNER["partner_id"]
        for e in dp_events:
            assert e.fields.get("partner_id") != stale_id

    @pytest.mark.asyncio
    async def test_retention_rate_one_always_references_stale_partner(self):
        """retention violation rate=1 must reference the seeded stale partner_id every time."""
        events = await _run_api_generator(n=30, marketing_rate=0.0, retention_rate=1.0)
        dp_events = [e for e in events if e.source_system == SourceSystem.DELIVERY_PARTNER]
        assert len(dp_events) > 0
        stale_id = STALE_DELIVERY_PARTNER["partner_id"]
        for e in dp_events:
            assert e.fields.get("partner_id") == stale_id


# ---------------------------------------------------------------------------
# (c) source_system / source_type never fall outside locked enums
# ---------------------------------------------------------------------------

class TestEnumSafety:

    @pytest.mark.asyncio
    async def test_log_generator_source_systems_are_locked_enum_values(self):
        events = await _run_log_generator(n=30, rate=0.3)
        valid_values = {s.value for s in SourceSystem}
        for e in events:
            assert e.source_system.value in valid_values

    @pytest.mark.asyncio
    async def test_api_generator_source_systems_are_locked_enum_values(self):
        events = await _run_api_generator(n=30, marketing_rate=0.3, retention_rate=0.3)
        valid_values = {s.value for s in SourceSystem}
        for e in events:
            assert e.source_system.value in valid_values

    @pytest.mark.asyncio
    async def test_api_generator_only_uses_marketing_and_delivery_partner(self):
        """
        This generator specifically alternates marketing-analytics and
        delivery-partner-service — confirm it never drifts to emitting
        support-ticketing or order-service (those belong to the log generator).
        """
        events = await _run_api_generator(n=30, marketing_rate=0.3, retention_rate=0.3)
        seen = {e.source_system for e in events}
        assert seen <= {SourceSystem.MARKETING_ANALYTICS, SourceSystem.DELIVERY_PARTNER}

    @pytest.mark.asyncio
    async def test_log_generator_only_uses_support_and_order_service(self):
        events = await _run_log_generator(n=30, rate=0.3)
        seen = {e.source_system for e in events}
        assert seen <= {SourceSystem.SUPPORT_TICKETING, SourceSystem.ORDER_SERVICE}


# ---------------------------------------------------------------------------
# Per-vector inspectable event requirements
# ---------------------------------------------------------------------------

class TestPerVectorViolationsExist:

    @pytest.mark.asyncio
    async def test_exposure_vector_log_line_inspectable(self):
        """(a) An exposure-vector log line with raw PII must be producible and inspectable."""
        events = await _run_log_generator(n=10, rate=1.0)
        violation = events[0]
        assert violation.source_type == SourceType.LOG
        assert "name" in violation.fields
        assert "phone" in violation.fields
        assert "address" in violation.fields
        assert violation.fields["name"] in violation.raw_snippet
        assert violation.fields["phone"] in violation.raw_snippet

    @pytest.mark.asyncio
    async def test_purpose_limitation_vector_api_event_inspectable(self):
        """(b) A purpose-limitation API event with raw PII in marketing context."""
        events = await _run_api_generator(n=10, marketing_rate=1.0, retention_rate=0.0)
        marketing_events = [e for e in events if e.source_system == SourceSystem.MARKETING_ANALYTICS]
        assert len(marketing_events) > 0
        violation = marketing_events[0]
        assert violation.source_type == SourceType.API
        assert "phone" in violation.fields
        assert "hashed_customer_id" in violation.fields  # still carries the legit fields too

    @pytest.mark.asyncio
    async def test_retention_vector_api_event_inspectable(self):
        """(c) A retention-vector API event referencing the stale delivery-partner record."""
        events = await _run_api_generator(n=10, marketing_rate=0.0, retention_rate=1.0)
        dp_events = [e for e in events if e.source_system == SourceSystem.DELIVERY_PARTNER]
        assert len(dp_events) > 0
        violation = dp_events[0]
        assert violation.source_type == SourceType.API
        assert violation.fields["partner_id"] == STALE_DELIVERY_PARTNER["partner_id"]
        assert violation.fields["aadhaar"] == STALE_DELIVERY_PARTNER["aadhaar"]


# ---------------------------------------------------------------------------
# Standalone runner (no pytest-asyncio dependency needed)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("Running Phase 2 ingestion validation tests (standalone async runner)...\n")

    test_classes = [
        TestSchemaValidity,
        TestViolationRateControl,
        TestEnumSafety,
        TestPerVectorViolationsExist,
    ]

    passed = 0
    failed = 0

    for cls in test_classes:
        instance = cls()
        methods = [m for m in dir(instance) if m.startswith("test_")]
        for method_name in methods:
            try:
                coro = getattr(instance, method_name)()
                asyncio.run(coro)
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
        print("Phase 2 ingestion contracts verified. ✅")
