"""
Phase 5+6 — Evidence Store Multi-Tenant Isolation Tests
============================================================
Lean, targeted coverage for what this phase actually changed in
evidence_store/store.py: per-tenant hash chaining and tenant-scoped reads.
Does NOT duplicate test_evidence_store.py's existing hash-chain-integrity
or status-transition coverage (still fully exercised there, just now with
tenant_id threaded through — see that file's own Phase 5+6 update).

This is the highest-value new coverage for the combined phase: proving one
org's data can never be touched by, returned to, or corrupted alongside
another org's, even in the same physical SQLite file/table.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

from evidence_store.store import GENESIS_HASH, EvidenceStore
from llm_explainer.explainer import ExplainedVerdict
from schemas.models import RemediationStatus, RuleId, Severity, SourceType, Verdict


def _make_explained_verdict(tenant_id: str, rule_id: RuleId = RuleId.EXPOSURE_001) -> ExplainedVerdict:
    verdict = Verdict(
        tenant_id=tenant_id,
        verdict_id=uuid.uuid4(),
        event_id=uuid.uuid4(),
        rule_id=rule_id,
        severity=Severity.HIGH,
        source=SourceType.LOG,
        source_system="some-system",
        field="some_field",
        timestamp=datetime.now(timezone.utc),
        matched_registry_entry=None,
        breach_notification_candidate=False,
        remediation_status=RemediationStatus.OPEN,
        remediation_updated_at=None,
    )
    return ExplainedVerdict(
        verdict=verdict, explanation="x", section_cited="x", confidence=1.0, used_fallback=True,
    )


@pytest.fixture
def store(tmp_path):
    s = EvidenceStore(db_path=tmp_path / "multi_tenant_test.db")
    yield s
    s.close()


class TestPerTenantChaining:

    def test_each_tenant_starts_its_own_chain_at_genesis(self, store):
        """Tenant B's first row must chain into GENESIS_HASH, not tenant
        A's last row — proving chains are independent, not one global
        sequence split by tenant_id after the fact."""
        store.append(_make_explained_verdict("org_a"))
        store.append(_make_explained_verdict("org_a"))
        ev_b = _make_explained_verdict("org_b")
        store.append(ev_b)

        row_b = store.get_by_verdict_id("org_b", str(ev_b.verdict.verdict_id))
        # previous_hash isn't exposed in the query() dict by name, so
        # verify indirectly: org_b's chain (1 row) verifies clean on its own.
        result_b = store.verify_chain("org_b")
        assert result_b == {"valid": True, "first_broken_index": None}
        # And org_b's row count is exactly 1, independent of org_a's 2.
        assert store.count("org_b") == 1
        assert store.count("org_a") == 2

    def test_corrupting_one_tenants_row_does_not_break_the_others_chain(self, store):
        """The core isolation guarantee for verify_chain(): an auditor for
        org_a must be able to trust a clean verify_chain('org_a') result
        completely independent of whether org_b's data is tampered."""
        for _ in range(3):
            store.append(_make_explained_verdict("org_a"))
        for _ in range(3):
            store.append(_make_explained_verdict("org_b"))

        # Corrupt org_b's SECOND row directly.
        rows = store.query("org_b")
        target_row_index = rows[1]["row_index"] + 1  # back to 1-based SQL row_index
        store._conn.execute(
            "UPDATE evidence SET payload_json = ? WHERE row_index = ?",
            ('{"tampered": "org_b data"}', target_row_index),
        )
        store._conn.commit()

        result_a = store.verify_chain("org_a")
        result_b = store.verify_chain("org_b")
        assert result_a == {"valid": True, "first_broken_index": None}, "org_a's chain must be unaffected by org_b's corruption"
        assert result_b["valid"] is False
        assert result_b["first_broken_index"] == 1  # tenant-local index, not global row_index

    def test_interleaved_appends_produce_two_independent_valid_chains(self, store):
        """Appends alternating between two tenants (as a real interleaved
        request stream would produce) must still yield two fully valid,
        independent per-tenant chains."""
        for _ in range(4):
            store.append(_make_explained_verdict("org_a"))
            store.append(_make_explained_verdict("org_b"))

        assert store.verify_chain("org_a") == {"valid": True, "first_broken_index": None}
        assert store.verify_chain("org_b") == {"valid": True, "first_broken_index": None}
        assert store.count("org_a") == 4
        assert store.count("org_b") == 4


class TestTenantScopedReads:

    def test_query_never_returns_another_tenants_rows(self, store):
        store.append(_make_explained_verdict("org_a"))
        store.append(_make_explained_verdict("org_a"))
        store.append(_make_explained_verdict("org_b"))

        results_a = store.query("org_a")
        results_b = store.query("org_b")
        assert len(results_a) == 2
        assert len(results_b) == 1
        assert all(r["tenant_id"] == "org_a" for r in results_a)
        assert all(r["tenant_id"] == "org_b" for r in results_b)

    def test_get_by_verdict_id_returns_none_for_wrong_tenant(self, store):
        """A verdict_id that genuinely exists — but under a DIFFERENT
        tenant — must resolve identically to a verdict_id that doesn't
        exist at all: None. Never leak "it exists, just not for you"."""
        ev = _make_explained_verdict("org_a")
        store.append(ev)
        vid = str(ev.verdict.verdict_id)

        assert store.get_by_verdict_id("org_a", vid) is not None
        assert store.get_by_verdict_id("org_b", vid) is None

    def test_update_status_rejects_cross_tenant_verdict_id(self, store):
        """org_b must not be able to acknowledge/resolve org_a's verdict
        by guessing or otherwise obtaining its verdict_id."""
        ev = _make_explained_verdict("org_a")
        store.append(ev)
        vid = str(ev.verdict.verdict_id)

        with pytest.raises(ValueError, match="Verdict not found"):
            store.update_status("org_b", vid, "ACKNOWLEDGED")

        # And org_a itself can still update it normally.
        store.update_status("org_a", vid, "ACKNOWLEDGED")
        assert store.get_by_verdict_id("org_a", vid)["remediation_status"] == "ACKNOWLEDGED"

    def test_count_is_tenant_scoped(self, store):
        for _ in range(5):
            store.append(_make_explained_verdict("org_a"))
        for _ in range(2):
            store.append(_make_explained_verdict("org_b"))
        assert store.count("org_a") == 5
        assert store.count("org_b") == 2
        assert store.count("org_c_never_seen") == 0


class TestMigration:

    def test_legacy_db_without_tenant_id_column_migrates_cleanly(self, tmp_path):
        """
        Simulates a pre-Phase-5+6 database: a hand-built 'evidence' table
        with the OLD schema (no tenant_id column). Opening it via
        EvidenceStore must add the column and backfill every row's
        tenant_id from its own payload_json, without touching row_hash/
        previous_hash (see store.py's migration docstring — recomputing
        historical hashes would break tamper-evidence retroactively).
        """
        import hashlib
        import json
        import sqlite3

        db_path = tmp_path / "legacy.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("""
            CREATE TABLE evidence (
                row_index           INTEGER PRIMARY KEY AUTOINCREMENT,
                verdict_id          TEXT NOT NULL UNIQUE,
                payload_json        TEXT NOT NULL,
                row_hash            TEXT NOT NULL,
                previous_hash       TEXT NOT NULL,
                remediation_status  TEXT NOT NULL DEFAULT 'OPEN',
                remediation_updated_at TEXT,
                appended_at         TEXT NOT NULL
            )
        """)
        payload = json.dumps({"tenant_id": "legacy_org", "verdict_id": "abc-123", "field": "x"}, sort_keys=True)
        row_hash = hashlib.sha256((payload + GENESIS_HASH).encode()).hexdigest()
        conn.execute(
            "INSERT INTO evidence (verdict_id, payload_json, row_hash, previous_hash, appended_at) "
            "VALUES (?, ?, ?, ?, ?)",
            ("abc-123", payload, row_hash, GENESIS_HASH, datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
        conn.close()

        store = EvidenceStore(db_path=db_path)
        try:
            assert store.count("legacy_org") == 1
            result = store.verify_chain("legacy_org")
            assert result == {"valid": True, "first_broken_index": None}, (
                "migrated row's original hash must still verify — migration must not recompute it"
            )
            # A brand-new tenant appended after migration must chain independently.
            store.append(_make_explained_verdict("new_org"))
            assert store.verify_chain("new_org") == {"valid": True, "first_broken_index": None}
            assert store.count("legacy_org") == 1  # unaffected by the new tenant's append
        finally:
            store.close()
