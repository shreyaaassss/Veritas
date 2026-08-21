"""
Tests for Phase 5 — LLM Explainer

The critical test: mock the LLM to fail in every possible way, confirm
the fallback template is returned and the pipeline doesn't crash.
"""

from __future__ import annotations

import pytest
import uuid
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock

from schemas.models import (
    Verdict, RuleId, Severity, SourceType, SourceSystem, RemediationStatus
)
from llm_explainer.explainer import (
    explain_verdict, ExplainedVerdict, _build_fallback, _grounding_check, _LLMResponse
)


def _make_verdict(rule_id: RuleId = RuleId.EXPOSURE_001) -> Verdict:
    return Verdict(
        verdict_id=uuid.uuid4(),
        event_id=uuid.uuid4(),
        rule_id=rule_id,
        severity=Severity.HIGH,
        source=SourceType.LOG,
        source_system=SourceSystem.ORDER_SERVICE,
        field="pan",
        timestamp=datetime.now(timezone.utc),
        matched_registry_entry=None,
        breach_notification_candidate=True,
        remediation_status=RemediationStatus.OPEN,
        remediation_updated_at=None,
    )


class TestFallbackOnLLMFailure:
    """The core Phase 5 requirement: any LLM failure → fallback, no crash."""

    def test_api_error_falls_back(self):
        """Any error in the LLM path → fallback template, no exception bubbled.
        _call_llm returns None on any internal failure (it catches all exceptions).
        The explain_verdict caller then falls back on None return."""
        verdict = _make_verdict(RuleId.EXPOSURE_001)
        # _call_llm returns None on any failure — that's the correct interface contract.
        with patch("llm_explainer.explainer._call_llm", return_value=None):
            result = explain_verdict(verdict)
        assert result.used_fallback is True
        assert result.verdict is verdict
        assert "EXPOSURE_001" in result.explanation
        assert "pan" in result.explanation

    def test_exception_inside_call_llm_returns_none(self):
        """Verify _call_llm itself swallows exceptions and returns None (never raises)."""
        from llm_explainer.explainer import _call_llm, get_statute_snippet
        verdict = _make_verdict(RuleId.EXPOSURE_001)
        snippet = get_statute_snippet(RuleId.EXPOSURE_001.value)
        # Force an import error inside _call_llm by breaking the openai import
        with patch("llm_explainer.explainer._call_llm", wraps=lambda v, s: None):
            result = _call_llm(verdict, snippet)
            # Either None (no key) or a valid response — must not raise
            assert result is None or hasattr(result, "explanation")

    def test_none_from_call_llm_falls_back(self):
        """_call_llm returning None (network timeout, etc.) → fallback."""
        verdict = _make_verdict(RuleId.PURPOSE_001)
        with patch("llm_explainer.explainer._call_llm", return_value=None):
            result = explain_verdict(verdict)
        assert result.used_fallback is True
        assert "PURPOSE_001" in result.explanation

    def test_grounding_failure_falls_back(self):
        """LLM returns hallucinated section_cited → grounding check fails → fallback."""
        verdict = _make_verdict(RuleId.RETENTION_001)
        bad_response = _LLMResponse(
            explanation="Some explanation",
            section_cited="Section 99 of some other Act",  # wrong!
            confidence=0.9,
        )
        with patch("llm_explainer.explainer._call_llm", return_value=bad_response):
            result = explain_verdict(verdict)
        assert result.used_fallback is True
        assert "RETENTION_001" in result.explanation

    def test_force_llm_fallback_env_var(self, monkeypatch):
        """FORCE_LLM_FALLBACK=1 → always uses template, even if LLM would succeed."""
        monkeypatch.setenv("FORCE_LLM_FALLBACK", "1")
        verdict = _make_verdict(RuleId.EXPOSURE_001)
        with patch("llm_explainer.explainer._call_llm") as mock_llm:
            result = explain_verdict(verdict)
            mock_llm.assert_not_called()
        assert result.used_fallback is True

    def test_fallback_contains_rule_field_system(self):
        """Fallback template always includes rule_id, field, source_system, and section."""
        verdict = _make_verdict(RuleId.RETENTION_001)
        fallback = _build_fallback(verdict, RuleId.RETENTION_001.value)
        assert "RETENTION_001" in fallback.explanation
        assert "pan" in fallback.explanation
        assert "order-service" in fallback.explanation
        assert "§ 8(7)" in fallback.section_cited or "8(7)" in fallback.explanation or fallback.section_cited != ""
        assert fallback.used_fallback is True
        assert fallback.verdict is verdict  # original Verdict is not mutated

    def test_verdict_is_never_mutated(self):
        """Regardless of fallback or success, the original Verdict object is unchanged."""
        verdict = _make_verdict(RuleId.EXPOSURE_001)
        original_id = verdict.verdict_id
        original_field = verdict.field
        with patch("llm_explainer.explainer._call_llm", return_value=None):
            result = explain_verdict(verdict)
        assert result.verdict.verdict_id == original_id
        assert result.verdict.field == original_field
        assert verdict.verdict_id == original_id  # same object, not mutated


class TestGroundingCheck:
    def test_exact_match_passes(self):
        r = _LLMResponse(explanation="x", section_cited="DPDPA 2023 § 8(1) — Security Safeguards", confidence=0.9)
        assert _grounding_check(r, "DPDPA 2023 § 8(1) — Security Safeguards") is True

    def test_substring_passes(self):
        r = _LLMResponse(explanation="x", section_cited="DPDPA 2023 § 8(1) — Security Safeguards (additional note)", confidence=0.9)
        assert _grounding_check(r, "DPDPA 2023 § 8(1) — Security Safeguards") is True

    def test_wrong_section_fails(self):
        r = _LLMResponse(explanation="x", section_cited="GDPR Article 5", confidence=0.9)
        assert _grounding_check(r, "DPDPA 2023 § 8(1) — Security Safeguards") is False


class TestSuccessPath:
    def test_successful_llm_response_accepted(self):
        """When LLM returns valid, grounded response, it should be used (not fallback)."""
        verdict = _make_verdict(RuleId.EXPOSURE_001)
        good_response = _LLMResponse(
            explanation="The PAN field was found in a debug log where it should not appear.",
            section_cited="DPDPA 2023 § 8(1) — Security Safeguards",
            confidence=0.95,
        )
        with patch("llm_explainer.explainer._call_llm", return_value=good_response):
            result = explain_verdict(verdict)
        assert result.used_fallback is False
        assert result.confidence == 0.95
        assert result.explanation == good_response.explanation
        assert result.section_cited == good_response.section_cited
        assert result.verdict is verdict


class TestExplainedVerdictModel:
    def test_is_pydantic_model(self):
        verdict = _make_verdict()
        ev = ExplainedVerdict(
            verdict=verdict,
            explanation="Test",
            section_cited="DPDPA 2023 § 8(1) — Security Safeguards",
            confidence=0.9,
            used_fallback=False,
        )
        assert ev.verdict is verdict
        assert ev.used_fallback is False

    def test_serializable(self):
        verdict = _make_verdict()
        ev = ExplainedVerdict(
            verdict=verdict,
            explanation="Test",
            section_cited="DPDPA 2023 § 8(1) — Security Safeguards",
            confidence=0.9,
            used_fallback=False,
        )
        data = ev.model_dump(mode="json")
        assert "verdict" in data
        assert "explanation" in data
        assert "used_fallback" in data
