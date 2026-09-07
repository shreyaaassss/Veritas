"""
Agent Management API — Tests (Block 2)
=======================================
Covers all six agent endpoints plus the auth guard on /v1/{org_id}/events.

Run with: python -m pytest api/test_agents.py -v
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import agent_store.store as agent_store_module
import evidence_store.store as store_module
import user_store.store as user_store_module
from agent_store.store import get_agent_store, reset_agent_store
from dashboard import live_feed
from dashboard.server import app
from rate_limit import _limiter
from user_store.store import get_user_store, reset_user_store
from user_store.models import UserRole

client = TestClient(app)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_admin():
    from passlib.context import CryptContext
    pw_hash = CryptContext(schemes=["bcrypt"], deprecated="auto").hash("testpass123!")
    get_user_store().create_user("testadmin", "admin@test.io", pw_hash, UserRole.SUPER_ADMIN)
    r = client.post("/api/auth/login", data={"username": "testadmin", "password": "testpass123!"})
    assert r.status_code == 200, f"Login failed in test setup: {r.text}"


@pytest.fixture(autouse=True)
def isolated_stores(tmp_path, monkeypatch):
    """Fresh, isolated stores for every test + auto-login as SUPER_ADMIN."""
    monkeypatch.setattr(store_module, "_DEFAULT_DB_PATH", tmp_path / "test_evidence.db")
    monkeypatch.setattr(agent_store_module, "_DEFAULT_DB_PATH", tmp_path / "test_agents.db")
    monkeypatch.setattr(user_store_module, "_DEFAULT_DB_PATH", tmp_path / "test_users.db")
    monkeypatch.setenv("VERITAS_SECURE_COOKIES", "false")
    monkeypatch.setenv("VERITAS_JWT_SECRET", "test-jwt-secret-for-unit-tests-only")
    store_module.reset_store()
    reset_agent_store()
    reset_user_store()
    live_feed._ws_clients.clear()
    _limiter._windows.clear()   # prevent rate-limit state leaking between tests

    _make_admin()

    yield
    store_module.reset_store()
    reset_agent_store()
    reset_user_store()
    live_feed._ws_clients.clear()
    _limiter._windows.clear()
    client.cookies.clear()


def _register_agent(org_id: str = "blinkit", source_label: str = "test-service") -> tuple[str, str]:
    """Helper: issue key → register agent. Returns (agent_id, auth_token)."""
    # Issue key via API
    key_resp = client.post("/agents/issue-key", json={"org_id": org_id})
    assert key_resp.status_code == 200
    key = key_resp.json()["key"]

    # Register via API
    reg_resp = client.post("/agent/register", json={
        "registration_key": key,
        "source_label": source_label,
    })
    assert reg_resp.status_code == 200
    data = reg_resp.json()
    return data["agent_id"], data["auth_token"]


# ---------------------------------------------------------------------------
# POST /agents/issue-key
# ---------------------------------------------------------------------------

class TestIssueKey:

    def test_valid_org_returns_key(self):
        r = client.post("/agents/issue-key", json={"org_id": "blinkit"})
        assert r.status_code == 200
        data = r.json()
        assert "key" in data
        assert data["org_id"] == "blinkit"
        assert data["expires_in_seconds"] == 1800

    def test_unknown_org_returns_404(self):
        r = client.post("/agents/issue-key", json={"org_id": "nonexistent_org_xyz"})
        assert r.status_code == 404
        assert "nonexistent_org_xyz" in r.json()["detail"]

    def test_keys_are_different_on_each_call(self):
        r1 = client.post("/agents/issue-key", json={"org_id": "blinkit"})
        r2 = client.post("/agents/issue-key", json={"org_id": "blinkit"})
        assert r1.json()["key"] != r2.json()["key"]

    def test_missing_org_id_returns_422(self):
        r = client.post("/agents/issue-key", json={})
        assert r.status_code == 422


# ---------------------------------------------------------------------------
# POST /agent/register
# ---------------------------------------------------------------------------

class TestRegisterAgent:

    def test_valid_key_returns_identity(self):
        key_resp = client.post("/agents/issue-key", json={"org_id": "blinkit"})
        key = key_resp.json()["key"]

        r = client.post("/agent/register", json={"registration_key": key, "source_label": "order-service"})
        assert r.status_code == 200
        data = r.json()
        assert data["agent_id"].startswith("VERITAS-AGENT-")
        assert len(data["auth_token"]) > 0
        assert data["org_id"] == "blinkit"
        assert data["event_endpoint"] == "/v1/blinkit/events"

    def test_used_key_rejected(self):
        key_resp = client.post("/agents/issue-key", json={"org_id": "blinkit"})
        key = key_resp.json()["key"]

        client.post("/agent/register", json={"registration_key": key})
        r = client.post("/agent/register", json={"registration_key": key})
        assert r.status_code == 400
        assert "already been used" in r.json()["detail"]

    def test_fake_key_rejected(self):
        r = client.post("/agent/register", json={"registration_key": "totally-fake-key-xyz"})
        assert r.status_code == 400
        assert "invalid" in r.json()["detail"].lower()

    def test_expired_key_rejected(self, monkeypatch):
        from datetime import timedelta, timezone
        import agent_store.store as store_mod

        key_resp = client.post("/agents/issue-key", json={"org_id": "blinkit"})
        key = key_resp.json()["key"]

        # Advance clock past expiry
        future = __import__("datetime").datetime.now(timezone.utc) + timedelta(hours=2)
        monkeypatch.setattr(store_mod, "_now", lambda: future)

        r = client.post("/agent/register", json={"registration_key": key})
        assert r.status_code == 400
        assert "expired" in r.json()["detail"].lower()

    def test_source_label_optional(self):
        key_resp = client.post("/agents/issue-key", json={"org_id": "blinkit"})
        key = key_resp.json()["key"]
        r = client.post("/agent/register", json={"registration_key": key})
        assert r.status_code == 200


# ---------------------------------------------------------------------------
# POST /agent/heartbeat
# ---------------------------------------------------------------------------

class TestHeartbeat:

    def test_valid_token_updates_heartbeat(self):
        agent_id, token = _register_agent()
        r = client.post(
            "/agent/heartbeat",
            json={"agent_id": agent_id},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200
        assert r.json()["ok"] is True

    def test_invalid_token_returns_401(self):
        agent_id, _ = _register_agent()
        r = client.post(
            "/agent/heartbeat",
            json={"agent_id": agent_id},
            headers={"Authorization": "Bearer invalid-token-xyz"},
        )
        assert r.status_code == 401

    def test_no_token_returns_401(self):
        agent_id, _ = _register_agent()
        r = client.post("/agent/heartbeat", json={"agent_id": agent_id})
        assert r.status_code == 401

    def test_mismatched_agent_id_returns_403(self):
        agent_id, token = _register_agent()
        agent_id2, _ = _register_agent(source_label="other-service")
        r = client.post(
            "/agent/heartbeat",
            json={"agent_id": agent_id2},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 403


# ---------------------------------------------------------------------------
# GET /agents
# ---------------------------------------------------------------------------

class TestListAgents:

    def test_returns_empty_list_initially(self):
        r = client.get("/agents")
        assert r.status_code == 200
        assert r.json()["agents"] == []

    def test_returns_registered_agents(self):
        _register_agent(source_label="svc-a")
        _register_agent(source_label="svc-b")
        r = client.get("/agents")
        assert r.status_code == 200
        assert len(r.json()["agents"]) == 2

    def test_org_id_filter_works(self):
        _register_agent("blinkit", "svc-a")
        _register_agent("edtech_co", "svc-b")

        r_blinkit = client.get("/agents?org_id=blinkit")
        r_edtech = client.get("/agents?org_id=edtech_co")

        blinkit_agents = r_blinkit.json()["agents"]
        edtech_agents = r_edtech.json()["agents"]

        assert len(blinkit_agents) == 1
        assert blinkit_agents[0]["org_id"] == "blinkit"
        assert len(edtech_agents) == 1
        assert edtech_agents[0]["org_id"] == "edtech_co"


# ---------------------------------------------------------------------------
# GET /agents/{agent_id}
# ---------------------------------------------------------------------------

class TestGetAgent:

    def test_found_returns_detail(self):
        agent_id, _ = _register_agent(source_label="order-service")
        r = client.get(f"/agents/{agent_id}")
        assert r.status_code == 200
        data = r.json()
        assert data["agent_id"] == agent_id
        assert data["org_id"] == "blinkit"
        assert data["source_label"] == "order-service"
        assert data["status"] == "ACTIVE"

    def test_not_found_returns_404(self):
        r = client.get("/agents/VERITAS-AGENT-FAKE00")
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# POST /agents/{agent_id}/revoke
# ---------------------------------------------------------------------------

class TestRevokeAgent:

    def test_revoke_sets_status_to_revoked(self):
        agent_id, _ = _register_agent()
        r = client.post(f"/agents/{agent_id}/revoke")
        assert r.status_code == 200
        assert r.json()["status"] == "REVOKED"

        detail = client.get(f"/agents/{agent_id}")
        assert detail.json()["status"] == "REVOKED"

    def test_revoke_unknown_agent_returns_404(self):
        r = client.post("/agents/VERITAS-AGENT-FAKE00/revoke")
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# Auth guard on POST /v1/{org_id}/events
# ---------------------------------------------------------------------------

class TestEventsEndpointAuth:

    def test_no_auth_header_returns_401(self):
        r = client.post(
            "/v1/blinkit/events",
            json={"source_type": "log", "source_system": "x", "raw_snippet": "hello"},
        )
        assert r.status_code == 401

    def test_invalid_token_returns_401(self):
        r = client.post(
            "/v1/blinkit/events",
            headers={"Authorization": "Bearer not-a-real-token"},
            json={"source_type": "log", "source_system": "x", "raw_snippet": "hello"},
        )
        assert r.status_code == 401

    def test_revoked_agent_returns_403(self):
        agent_id, token = _register_agent()
        client.post(f"/agents/{agent_id}/revoke")

        r = client.post(
            "/v1/blinkit/events",
            headers={"Authorization": f"Bearer {token}"},
            json={"source_type": "log", "source_system": "x", "raw_snippet": "hello"},
        )
        assert r.status_code == 403
        assert "revoked" in r.json()["detail"].lower()

    def test_token_from_wrong_org_returns_403(self):
        # Register agent for edtech_co, try to submit to blinkit
        _, edtech_token = _register_agent("edtech_co", "edtech-svc")

        r = client.post(
            "/v1/blinkit/events",
            headers={"Authorization": f"Bearer {edtech_token}"},
            json={"source_type": "log", "source_system": "x", "raw_snippet": "hello"},
        )
        assert r.status_code == 403
        assert "not authorized" in r.json()["detail"].lower() or "edtech_co" in r.json()["detail"]

    def test_valid_token_and_correct_org_accepted(self):
        _, token = _register_agent("blinkit")

        r = client.post(
            "/v1/blinkit/events",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "source_type": "log",
                "source_system": "order-service",
                "raw_snippet": "clean log line with no PII",
            },
        )
        assert r.status_code == 200

    def test_valid_token_increments_event_counter(self):
        agent_id, token = _register_agent("blinkit")

        for _ in range(3):
            client.post(
                "/v1/blinkit/events",
                headers={"Authorization": f"Bearer {token}"},
                json={"source_type": "log", "source_system": "x", "raw_snippet": "clean line"},
            )

        detail = client.get(f"/agents/{agent_id}").json()
        assert detail["events_received"] == 3

    def test_malformed_bearer_header_returns_401(self):
        r = client.post(
            "/v1/blinkit/events",
            headers={"Authorization": "Token abc123"},  # wrong scheme
            json={"source_type": "log", "source_system": "x", "raw_snippet": "hello"},
        )
        assert r.status_code == 401
