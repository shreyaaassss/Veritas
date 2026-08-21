"""
Phase 5+6 — Integration API Tests
======================================
Covers the plan's exit criteria for Part A (endpoints work correctly) and
the cross-tenant portion of Part B/C (dashboard reads never leak across
orgs when reached via the real HTTP layer, using the real blinkit/
edtech_co seed configs — same convention Phase 4's tests already used).

Uses a temp-file-backed EvidenceStore for the whole module (monkeypatched
in via evidence_store.store's module globals) so these tests never read
or write the real evidence_store/evidence.db.

Run with: python -m pytest api/test_integration.py -v
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import evidence_store.store as store_module
from dashboard import live_feed
from dashboard.server import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def isolated_store(tmp_path, monkeypatch):
    """Every test gets a fresh, isolated Evidence Store file — never the
    real on-disk evidence.db."""
    monkeypatch.setattr(store_module, "_DEFAULT_DB_PATH", tmp_path / "api_test_evidence.db")
    store_module.reset_store()
    live_feed._ws_clients.clear()
    yield
    store_module.reset_store()
    live_feed._ws_clients.clear()


class TestOrgValidation:

    def test_scan_unknown_org_returns_404(self):
        r = client.post("/v1/definitely_not_a_registered_org/scan", json={"text": "hello"})
        assert r.status_code == 404
        assert "definitely_not_a_registered_org" in r.json()["detail"]

    def test_events_unknown_org_returns_404(self):
        r = client.post(
            "/v1/definitely_not_a_registered_org/events",
            json={"source_type": "log", "source_system": "x", "raw_snippet": "hello"},
        )
        assert r.status_code == 404

    def test_scan_known_org_accepted(self):
        r = client.post("/v1/blinkit/scan", json={"text": "just a clean log line, nothing sensitive"})
        assert r.status_code == 200


class TestScanInputShapes:

    def test_scan_rejects_empty_request(self):
        r = client.post("/v1/blinkit/scan", json={})
        assert r.status_code == 422  # neither text nor fields given

    def test_scan_text_only_detects_and_masks(self):
        r = client.post("/v1/blinkit/scan", json={"text": "employee record lookup: ABCDE1234F"})
        assert r.status_code == 200
        data = r.json()
        assert data["contains_pii"] is True
        assert any(e["entity_type"] == "IN_PAN" for e in data["entities"])
        assert "ABCDE1234F" not in data["masked_text"]
        assert "[PAN_REDACTED]" in data["masked_text"]
        # Raw PII value must never appear anywhere in the response.
        assert "ABCDE1234F" not in str(data)

    def test_scan_fields_only_detects_and_masks(self):
        r = client.post(
            "/v1/blinkit/scan",
            json={"fields": {"pan": "ABCDE1234F"}, "source_system": "delivery-partner-service"},
        )
        assert r.status_code == 200
        data = r.json()
        assert data["masked_fields"]["pan"] == "[PAN_REDACTED]"
        assert "masked_text" not in data  # no text was given

    def test_scan_both_text_and_fields_merges_results(self):
        r = client.post(
            "/v1/blinkit/scan",
            json={
                "text": "order note: customer pan is ABCDE1234F",
                "fields": {"phone": "9876543210"},
                "source_system": "order-service",
            },
        )
        assert r.status_code == 200
        data = r.json()
        assert "masked_text" in data and "masked_fields" in data
        assert "[PAN_REDACTED]" in data["masked_text"]
        assert data["masked_fields"]["phone"] == "[PHONE_REDACTED]"

    def test_unregistered_source_system_degrades_gracefully_not_crash(self):
        """Part A2.4: a scan with no real source_system must never crash
        and must never fabricate a false RETENTION_001 — it degrades to
        the existing PURPOSE_001 unregistered-field path."""
        r = client.post("/v1/blinkit/scan", json={"fields": {"pan": "ABCDE1234F"}})
        assert r.status_code == 200
        data = r.json()
        rule_ids = {v["rule_id"] for v in data["verdicts"]}
        assert "RETENTION_001" not in rule_ids
        if data["verdicts"]:
            assert rule_ids <= {"PURPOSE_001", "EXPOSURE_001"}


class TestOrgConfigUpload:

    def test_valid_config_accepted_and_immediately_usable(self):
        config = {
            "org_id": "api_test_org",
            "fields": [{
                "field_name": "contact_phone", "pii_category": "phone",
                "declared_purpose": "support", "consent_scope": "support",
                "retention_days": 90, "source_system": "helpdesk",
            }],
        }
        try:
            r = client.post("/v1/orgs/api_test_org/config", json=config)
            assert r.status_code == 200
            assert r.json()["status"] == "ok"

            # Immediately usable, no restart:
            r2 = client.post(
                "/v1/api_test_org/scan",
                json={"fields": {"contact_phone": "9876543210"}, "source_system": "helpdesk"},
            )
            assert r2.status_code == 200
        finally:
            from org_config.store import delete_org_configs
            delete_org_configs("api_test_org")
            from registry.loader import _reset_cache
            _reset_cache()

    def test_invalid_config_rejected_with_errors(self):
        r = client.post("/v1/orgs/broken_api_test_org/config", json={"org_id": "broken_api_test_org"})
        assert r.status_code == 200  # per org_config.store's contract: never raises, returns error dict
        body = r.json()
        assert body["status"] == "error"
        assert len(body["errors"]) > 0

    def test_orgs_listing_includes_seed_orgs(self):
        r = client.get("/v1/orgs")
        assert r.status_code == 200
        assert "blinkit" in r.json()["org_ids"]
        assert "edtech_co" in r.json()["org_ids"]


class TestCrossTenantDashboardIsolation:
    """
    Highest-value coverage for Part B: scan into TWO different real orgs
    through the actual HTTP layer, then confirm every dashboard read route
    only ever sees its own org's data — the full request-to-storage-to-
    query path, not just the EvidenceStore unit tested separately.
    """

    def test_verdicts_and_verify_chain_never_cross_tenant(self):
        # Text-only scans -> SourceType.LOG -> Check 1 guarantees EXPOSURE_001
        # deterministically for both orgs, independent of either org's
        # registry/retention aging state (see test file's other notes on
        # why fields-based scans against seed data aren't reliably violating).
        r_blinkit = client.post("/v1/blinkit/scan", json={"text": "leaked employee id: ABCDE1234F"})
        r_edtech = client.post("/v1/edtech_co/scan", json={"text": "customer email leaked: student@example.com"})
        assert r_blinkit.status_code == 200 and r_edtech.status_code == 200

        blinkit_verdicts = client.get("/api/blinkit/verdicts").json()["verdicts"]
        edtech_verdicts = client.get("/api/edtech_co/verdicts").json()["verdicts"]

        assert len(blinkit_verdicts) >= 1
        assert len(edtech_verdicts) >= 1
        assert all(v["tenant_id"] == "blinkit" for v in blinkit_verdicts)
        assert all(v["tenant_id"] == "edtech_co" for v in edtech_verdicts)

        # verify-chain for each org must be valid and unaffected by the other.
        assert client.get("/api/blinkit/verify-chain").json()["valid"] is True
        assert client.get("/api/edtech_co/verify-chain").json()["valid"] is True

    def test_verdict_detail_not_reachable_across_tenant_boundary(self):
        # Text-only scan -> SourceType.LOG -> Check 1 guarantees EXPOSURE_001
        # deterministically, independent of either org's registry aging state.
        r = client.post("/v1/blinkit/scan", json={"text": "leaked employee id: ABCDE1234F"})
        verdict_id = r.json()["verdicts"][0]["verdict_id"]

        own_org = client.get(f"/api/blinkit/verdicts/{verdict_id}")
        other_org = client.get(f"/api/edtech_co/verdicts/{verdict_id}")

        assert own_org.status_code == 200
        assert other_org.status_code == 404, "a blinkit verdict_id must not be fetchable under edtech_co's scope"

    def test_status_update_across_tenant_boundary_rejected(self):
        r = client.post("/v1/blinkit/scan", json={"text": "leaked employee id: ABCDE1234F"})
        verdict_id = r.json()["verdicts"][0]["verdict_id"]

        r2 = client.post(f"/api/edtech_co/verdicts/{verdict_id}/status", json={"status": "ACKNOWLEDGED"})
        assert r2.status_code == 400

    def test_stats_are_tenant_scoped(self):
        client.post("/v1/blinkit/scan", json={"text": "leaked employee id: ABCDE1234F"})
        stats_blinkit = client.get("/api/blinkit/stats").json()
        stats_edtech = client.get("/api/edtech_co/stats").json()
        assert stats_blinkit["total"] >= 1
        assert stats_edtech["total"] == 0


class TestViolationNumberLookup:
    """GET /api/{org_id}/violations/{violation_id} — the "@N" lookup."""

    def test_lookup_by_number_returns_the_right_violation_with_explanation(self):
        r = client.post("/v1/blinkit/scan", json={"text": "leaked employee id: ABCDE1234F"})
        verdict_id = r.json()["verdicts"][0]["verdict_id"]

        looked_up = client.get("/api/blinkit/violations/1")
        assert looked_up.status_code == 200
        body = looked_up.json()
        assert body["verdict_id"] == verdict_id
        assert body["violation_id"] == 1
        assert body["explanation"]  # already populated, no extra call needed
        assert body["section_cited"]

    def test_numbering_is_per_tenant_across_the_real_http_layer(self):
        client.post("/v1/blinkit/scan", json={"text": "leaked employee id: ABCDE1234F"})
        client.post("/v1/edtech_co/scan", json={"text": "customer email leaked: student@example.com"})

        blinkit_1 = client.get("/api/blinkit/violations/1").json()
        edtech_1 = client.get("/api/edtech_co/violations/1").json()
        assert blinkit_1["tenant_id"] == "blinkit"
        assert edtech_1["tenant_id"] == "edtech_co"
        assert blinkit_1["verdict_id"] != edtech_1["verdict_id"]

    def test_unknown_number_returns_404(self):
        r = client.get("/api/blinkit/violations/999")
        assert r.status_code == 404


class TestScanAndEventsShareTheSameStore:
    """Part B3: violations from /scan and /events must land in the same,
    correctly-scoped Evidence Store — no divergent storage path."""

    def test_scan_and_events_verdicts_both_appear_in_the_same_org_query(self):
        client.post("/v1/blinkit/scan", json={"text": "leaked employee id: ABCDE1234F"})
        client.post(
            "/v1/blinkit/events",
            json={
                "source_type": "log", "source_system": "support-ticketing",
                "raw_snippet": "agent note: aadhaar 2345 6789 0124 read aloud to customer",
            },
        )
        rows = client.get("/api/blinkit/verdicts").json()["verdicts"]
        rule_ids = {r["rule_id"] for r in rows}
        assert "EXPOSURE_001" in rule_ids  # from the /events log-sourced call
        assert len(rows) >= 2
