"""
Tests for the Breach Investigation Layer (investigation.py + the
POST /v1/{org_id}/investigate route in api/integration.py).

Mirrors llm_explainer/test_explainer.py's pattern: mock the LLM call
directly (patch investigation._call_llm_for_investigation) so these tests
are deterministic, free, and don't depend on network/API-key availability
— the one real end-to-end LLM call was verified manually against a live
OpenAI key (see the session's own report), not re-run here on every CI run.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

import evidence_store.store as store_module
from evidence_store.store import EvidenceStore
from llm_explainer.explainer import ExplainedVerdict
from schemas.models import RemediationStatus, RuleId, Severity, SourceType, Verdict

from investigation import investigate, parse_reference


def _make_explained_verdict(tenant_id: str = "blinkit", rule_id: RuleId = RuleId.EXPOSURE_001) -> ExplainedVerdict:
    verdict = Verdict(
        tenant_id=tenant_id,
        verdict_id=uuid.uuid4(),
        event_id=uuid.uuid4(),
        rule_id=rule_id,
        severity=Severity.HIGH,
        source=SourceType.LOG,
        source_system="order-service",
        field="pan",
        timestamp=datetime.now(timezone.utc),
        matched_registry_entry=None,
        breach_notification_candidate=True,
        remediation_status=RemediationStatus.OPEN,
        remediation_updated_at=None,
    )
    return ExplainedVerdict(
        verdict=verdict,
        explanation="A PAN was exposed in application logs.",
        section_cited="DPDPA 2023 § 8(1) — Security Safeguards",
        confidence=1.0,
        used_fallback=False,
    )


@pytest.fixture
def store(tmp_path):
    s = EvidenceStore(db_path=tmp_path / "investigation_test.db")
    yield s
    s.close()


# ---------------------------------------------------------------------------
# parse_reference
# ---------------------------------------------------------------------------

class TestParseReference:

    def test_leading_reference_extracted(self):
        assert parse_reference("@01 What exactly is the breach here?") == (1, "What exactly is the breach here?")

    def test_no_leading_zero_required(self):
        assert parse_reference("@7 explain") == (7, "explain")

    def test_reference_anywhere_in_text(self):
        vid, q = parse_reference("regarding @3, why is this a violation?")
        assert vid == 3

    def test_no_reference_returns_none(self):
        assert parse_reference("what is going on") == (None, "what is going on")

    def test_zero_is_not_a_valid_reference(self):
        """violation_id numbering starts at 1 — '@0' should not parse as a reference."""
        vid, _ = parse_reference("@0 what happened")
        assert vid is None


# ---------------------------------------------------------------------------
# investigate() — core logic, LLM mocked
# ---------------------------------------------------------------------------

class TestInvestigateCore:

    def test_unknown_violation_id_returns_not_found_without_calling_llm(self, store):
        # investigate() resolves get_store() internally via evidence_store.store.get_store —
        # patch the module-level singleton so it points at our tmp store.
        with patch("evidence_store.store.get_store", return_value=store), \
             patch("investigation._call_llm_for_investigation") as mock_llm:
            result = investigate("blinkit", 999, "what happened")
        assert result.used_fallback is True
        assert "999" in result.answer
        mock_llm.assert_not_called()

    def test_successful_llm_answer_used(self, store):
        store.append(_make_explained_verdict("blinkit"))
        with patch("evidence_store.store.get_store", return_value=store), \
             patch("investigation._call_llm_for_investigation", return_value="This is a HIGH severity exposure of a PAN."):
            result = investigate("blinkit", 1, "What exactly is the breach here?")
        assert result.used_fallback is False
        assert result.answer == "This is a HIGH severity exposure of a PAN."
        assert result.section_cited == "DPDPA 2023 § 8(1) — Security Safeguards"
        assert result.breach_summary["rule_id"] == "EXPOSURE_001"

    def test_none_from_llm_falls_back(self, store):
        store.append(_make_explained_verdict("blinkit"))
        with patch("evidence_store.store.get_store", return_value=store), \
             patch("investigation._call_llm_for_investigation", return_value=None):
            result = investigate("blinkit", 1, "what happened")
        assert result.used_fallback is True
        assert "already-verified explanation" in result.answer

    def test_wrong_statute_citation_falls_back(self, store):
        """The model citing a DIFFERENT section than this violation's own
        grounded citation must be discarded, not shown."""
        store.append(_make_explained_verdict("blinkit"))
        with patch("evidence_store.store.get_store", return_value=store), \
             patch("investigation._call_llm_for_investigation", return_value="This violates § 99 of some other Act."):
            result = investigate("blinkit", 1, "which rule does this violate?")
        assert result.used_fallback is True

    def test_answer_with_no_citation_at_all_is_accepted(self, store):
        """Most questions (remediation, impact, etc.) won't cite a section
        at all — that's fine, only a WRONG citation is rejected."""
        store.append(_make_explained_verdict("blinkit"))
        with patch("evidence_store.store.get_store", return_value=store), \
             patch("investigation._call_llm_for_investigation", return_value="You should rotate credentials and audit access logs."):
            result = investigate("blinkit", 1, "how do I fix this?")
        assert result.used_fallback is False

    def test_force_llm_fallback_env_var(self, store, monkeypatch):
        monkeypatch.setenv("FORCE_LLM_FALLBACK", "1")
        store.append(_make_explained_verdict("blinkit"))
        with patch("evidence_store.store.get_store", return_value=store), \
             patch("investigation._call_llm_for_investigation") as mock_llm:
            result = investigate("blinkit", 1, "what happened")
        mock_llm.assert_not_called()
        assert result.used_fallback is True

    def test_cross_tenant_isolation(self, store):
        """org_b must never be able to look up org_a's violation #1 —
        the whole point of per-tenant numbering."""
        store.append(_make_explained_verdict("org_a"))
        with patch("evidence_store.store.get_store", return_value=store), \
             patch("investigation._call_llm_for_investigation") as mock_llm:
            result = investigate("org_b", 1, "what happened")
        assert result.used_fallback is True
        assert "org_b" in result.answer or "1" in result.answer
        mock_llm.assert_not_called()

    def test_history_is_forwarded_to_the_llm_call(self, store):
        store.append(_make_explained_verdict("blinkit"))
        with patch("evidence_store.store.get_store", return_value=store), \
             patch("investigation._call_llm_for_investigation", return_value="Follow-up answer.") as mock_llm:
            history = [{"role": "user", "content": "@01 what happened"}, {"role": "assistant", "content": "It was exposed."}]
            investigate("blinkit", 1, "why is that serious?", history=history)
        args, kwargs = mock_llm.call_args
        assert history in args or history in kwargs.values()


# ---------------------------------------------------------------------------
# API endpoint
# ---------------------------------------------------------------------------

class TestInvestigateEndpoint:

    @pytest.fixture(autouse=True)
    def isolated_store(self, tmp_path, monkeypatch):
        import user_store.store as user_store_module
        from user_store.store import get_user_store, reset_user_store
        from user_store.models import UserRole
        monkeypatch.setattr(store_module, "_DEFAULT_DB_PATH", tmp_path / "api_investigation_test.db")
        monkeypatch.setattr(user_store_module, "_DEFAULT_DB_PATH", tmp_path / "api_invest_users.db")
        monkeypatch.setenv("VERITAS_SECURE_COOKIES", "false")
        monkeypatch.setenv("VERITAS_JWT_SECRET", "test-jwt-secret-for-unit-tests-only")
        store_module.reset_store()
        reset_user_store()

        # Create admin + store auth client
        from passlib.context import CryptContext
        from dashboard.server import app
        pw_hash = CryptContext(schemes=["bcrypt"], deprecated="auto").hash("testpass123!")
        get_user_store().create_user("inv_admin", "inv@test.io", pw_hash, UserRole.SUPER_ADMIN)
        self._client = TestClient(app)
        r = self._client.post("/api/auth/login", data={"username": "inv_admin", "password": "testpass123!"})
        assert r.status_code == 200, f"Login failed: {r.text}"

        yield
        store_module.reset_store()
        reset_user_store()

    def test_text_form_with_reference(self):
        self._client.post("/v1/blinkit/scan", json={"text": "leaked employee id: ABCDE1234F"})

        with patch("investigation._call_llm_for_investigation", return_value="It was exposed in a log."):
            r = self._client.post("/v1/blinkit/investigate", json={"text": "@1 what happened?"})
        assert r.status_code == 200
        assert r.json()["answer"] == "It was exposed in a log."

    def test_structured_form(self):
        self._client.post("/v1/blinkit/scan", json={"text": "leaked employee id: ABCDE1234F"})

        with patch("investigation._call_llm_for_investigation", return_value="It was exposed."):
            r = self._client.post("/v1/blinkit/investigate", json={"violation_id": 1, "question": "what happened?"})
        assert r.status_code == 200
        assert r.json()["violation_id"] == 1

    def test_missing_reference_returns_400(self):
        r = self._client.post("/v1/blinkit/investigate", json={"text": "no reference here"})
        assert r.status_code == 400

    def test_unknown_org_returns_404(self):
        r = self._client.post("/v1/nonexistent_org/investigate", json={"text": "@1 what happened"})
        assert r.status_code == 404
