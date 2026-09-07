"""
RBAC Enforcement Tests
======================
Verifies that role-based access control is enforced server-side on every
protected endpoint. Tests confirm that:
  - Authenticated users with insufficient roles receive 403
  - SUPER_ADMIN can perform all operations
  - VIEWER cannot modify anything
  - AUDITOR cannot modify anything but can read and investigate

Run with: python -m pytest api/test_rbac.py -v
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import agent_store.store as agent_store_module
import evidence_store.store as store_module
import user_store.store as user_store_module
from agent_store.store import reset_agent_store
from dashboard import live_feed
from dashboard.server import app
from evidence_store.store import reset_store
from user_store.models import UserRole
from user_store.store import get_user_store, reset_user_store


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _create_client_for_role(role: UserRole, grant_orgs=None) -> TestClient:
    """Returns an authenticated TestClient for the given role.
    Non-SUPER_ADMIN roles get explicit org grants for blinkit + edtech_co by default
    so they can exercise org-scoped endpoints in tests."""
    from passlib.context import CryptContext
    username = f"test_{role.value.lower()}"
    pw_hash = CryptContext(schemes=["bcrypt"], deprecated="auto").hash("testpass!")
    u = get_user_store().create_user(username, f"{username}@test.io", pw_hash, role)
    if role != UserRole.SUPER_ADMIN:
        for oid in (grant_orgs or ["blinkit", "edtech_co"]):
            get_user_store().grant_org_access(u.user_id, oid)
    c = TestClient(app)
    r = c.post("/api/auth/login", data={"username": username, "password": "testpass!"})
    assert r.status_code == 200, f"Login failed for {role.value}: {r.text}"
    return c


@pytest.fixture(autouse=True)
def isolated_all(tmp_path, monkeypatch):
    monkeypatch.setattr(store_module, "_DEFAULT_DB_PATH", tmp_path / "rbac_evidence.db")
    monkeypatch.setattr(agent_store_module, "_DEFAULT_DB_PATH", tmp_path / "rbac_agents.db")
    monkeypatch.setattr(user_store_module, "_DEFAULT_DB_PATH", tmp_path / "rbac_users.db")
    monkeypatch.setenv("VERITAS_SECURE_COOKIES", "false")
    monkeypatch.setenv("VERITAS_JWT_SECRET", "rbac-test-secret")
    reset_store()
    reset_agent_store()
    reset_user_store()
    live_feed._ws_clients.clear()
    yield
    reset_store()
    reset_agent_store()
    reset_user_store()
    live_feed._ws_clients.clear()


@pytest.fixture()
def super_admin():
    return _create_client_for_role(UserRole.SUPER_ADMIN)


@pytest.fixture()
def compliance_admin():
    return _create_client_for_role(UserRole.COMPLIANCE_ADMIN)


@pytest.fixture()
def auditor():
    return _create_client_for_role(UserRole.AUDITOR)


@pytest.fixture()
def viewer():
    return _create_client_for_role(UserRole.VIEWER)


# ---------------------------------------------------------------------------
# Authentication boundary
# ---------------------------------------------------------------------------

class TestUnauthenticated:

    def test_unauthenticated_api_returns_401(self):
        fresh = TestClient(app)
        r = fresh.get("/v1/orgs")
        assert r.status_code == 401

    def test_unauthenticated_scan_returns_401(self):
        fresh = TestClient(app)
        r = fresh.post("/v1/blinkit/scan", json={"text": "hello"})
        assert r.status_code == 401

    def test_unauthenticated_agents_returns_401(self):
        fresh = TestClient(app)
        r = fresh.get("/agents")
        assert r.status_code == 401


# ---------------------------------------------------------------------------
# VIEWER — read-only, cannot modify
# ---------------------------------------------------------------------------

class TestViewerPermissions:

    def test_viewer_can_read_orgs(self, viewer):
        r = viewer.get("/v1/orgs")
        assert r.status_code == 200

    def test_viewer_can_read_agents(self, viewer):
        r = viewer.get("/agents")
        assert r.status_code == 200

    def test_viewer_cannot_scan(self, viewer):
        r = viewer.post("/v1/blinkit/scan", json={"text": "test"})
        assert r.status_code == 403

    def test_viewer_cannot_issue_key(self, viewer):
        r = viewer.post("/agents/issue-key", json={"org_id": "blinkit"})
        assert r.status_code == 403

    def test_viewer_cannot_revoke_agent(self, viewer):
        r = viewer.post("/agents/VERITAS-AGENT-FAKE00/revoke")
        assert r.status_code == 403

    def test_viewer_cannot_upload_org_config(self, viewer):
        r = viewer.post("/v1/orgs/test/config", json={"org_id": "test", "fields": []})
        assert r.status_code == 403

    def test_viewer_cannot_update_verdict_status(self, viewer):
        r = viewer.post("/api/blinkit/verdicts/fake-id/status", json={"status": "ACKNOWLEDGED"})
        assert r.status_code == 403

    def test_viewer_cannot_investigate(self, viewer):
        r = viewer.post("/v1/blinkit/investigate", json={"text": "@1 what happened?"})
        assert r.status_code == 403

    def test_viewer_cannot_manage_users(self, viewer):
        r = viewer.get("/api/auth/users")
        assert r.status_code == 403


# ---------------------------------------------------------------------------
# AUDITOR — read + investigate, cannot modify
# ---------------------------------------------------------------------------

class TestAuditorPermissions:

    def test_auditor_can_read_orgs(self, auditor):
        r = auditor.get("/v1/orgs")
        assert r.status_code == 200

    def test_auditor_can_scan(self, auditor):
        r = auditor.post("/v1/blinkit/scan", json={"text": "clean log line"})
        assert r.status_code == 200

    def test_auditor_can_read_verdicts(self, auditor):
        r = auditor.get("/api/blinkit/verdicts")
        assert r.status_code == 200

    def test_auditor_can_verify_chain(self, auditor):
        r = auditor.get("/api/blinkit/verify-chain")
        assert r.status_code == 200

    def test_auditor_cannot_issue_key(self, auditor):
        r = auditor.post("/agents/issue-key", json={"org_id": "blinkit"})
        assert r.status_code == 403

    def test_auditor_cannot_revoke_agent(self, auditor):
        r = auditor.post("/agents/VERITAS-AGENT-FAKE00/revoke")
        assert r.status_code == 403

    def test_auditor_cannot_update_verdict_status(self, auditor):
        r = auditor.post("/api/blinkit/verdicts/fake-id/status", json={"status": "ACKNOWLEDGED"})
        assert r.status_code == 403

    def test_auditor_cannot_manage_users(self, auditor):
        r = auditor.get("/api/auth/users")
        assert r.status_code == 403


# ---------------------------------------------------------------------------
# COMPLIANCE_ADMIN — can modify org + agents, cannot manage users
# ---------------------------------------------------------------------------

class TestComplianceAdminPermissions:

    def test_compliance_admin_can_scan(self, compliance_admin):
        r = compliance_admin.post("/v1/blinkit/scan", json={"text": "test"})
        assert r.status_code == 200

    def test_compliance_admin_can_issue_key(self, compliance_admin):
        r = compliance_admin.post("/agents/issue-key", json={"org_id": "blinkit"})
        assert r.status_code == 200

    def test_compliance_admin_can_revoke_agent(self, compliance_admin):
        # Attempt revoke of nonexistent agent → 404 (not 403)
        r = compliance_admin.post("/agents/VERITAS-AGENT-FAKE00/revoke")
        assert r.status_code == 404

    def test_compliance_admin_cannot_manage_users(self, compliance_admin):
        r = compliance_admin.get("/api/auth/users")
        assert r.status_code == 403


# ---------------------------------------------------------------------------
# SUPER_ADMIN — full access
# ---------------------------------------------------------------------------

class TestSuperAdminPermissions:

    def test_super_admin_can_read_orgs(self, super_admin):
        assert super_admin.get("/v1/orgs").status_code == 200

    def test_super_admin_can_scan(self, super_admin):
        assert super_admin.post("/v1/blinkit/scan", json={"text": "test"}).status_code == 200

    def test_super_admin_can_issue_key(self, super_admin):
        assert super_admin.post("/agents/issue-key", json={"org_id": "blinkit"}).status_code == 200

    def test_super_admin_can_manage_users(self, super_admin):
        r = super_admin.get("/api/auth/users")
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_super_admin_can_create_user(self, super_admin):
        r = super_admin.post("/api/auth/users", json={
            "username": "newuser", "email": "new@test.io",
            "password": "strongpass!", "role": "AUDITOR",
        })
        assert r.status_code == 201
        assert r.json()["role"] == "AUDITOR"

    def test_super_admin_can_update_user_role(self, super_admin):
        # Create a user first
        super_admin.post("/api/auth/users", json={
            "username": "target", "email": "target@test.io",
            "password": "strongpass!", "role": "VIEWER",
        })
        users = super_admin.get("/api/auth/users").json()
        target = next(u for u in users if u["username"] == "target")
        r = super_admin.patch(f"/api/auth/users/{target['user_id']}", json={"role": "AUDITOR"})
        assert r.status_code == 200
        assert r.json()["role"] == "AUDITOR"


# ---------------------------------------------------------------------------
# Cross-role isolation: one user cannot escalate to another role
# ---------------------------------------------------------------------------

class TestRoleEscalation:

    def test_viewer_cannot_escalate_own_role(self, viewer):
        me = viewer.get("/api/auth/me").json()
        # Try to change own role (should fail — only SUPER_ADMIN can do this)
        r = viewer.patch(f"/api/auth/users/{me['user_id']}", json={"role": "SUPER_ADMIN"})
        assert r.status_code == 403

    def test_auditor_cannot_create_super_admin(self, auditor):
        r = auditor.post("/api/auth/users", json={
            "username": "evil", "email": "evil@test.io",
            "password": "pass1234!", "role": "SUPER_ADMIN",
        })
        assert r.status_code == 403


# ---------------------------------------------------------------------------
# Org isolation — user cannot access orgs they have no membership for
# ---------------------------------------------------------------------------

class TestOrgIsolation:

    def test_user_blocked_from_org_they_have_no_access_to(self, tmp_path):
        """An AUDITOR granted access to org A cannot access org B's data."""
        from passlib.context import CryptContext
        pw_hash = CryptContext(schemes=["bcrypt"], deprecated="auto").hash("testpass!")
        # Create user with access to blinkit only
        u = get_user_store().create_user("auditor_a", "a@test.io", pw_hash, UserRole.AUDITOR)
        get_user_store().grant_org_access(u.user_id, "blinkit")
        c = TestClient(app)
        r = c.post("/api/auth/login", data={"username": "auditor_a", "password": "testpass!"})
        assert r.status_code == 200

        # Can access blinkit
        r = c.get("/api/blinkit/verdicts")
        assert r.status_code == 200

        # Cannot access edtech_co (no membership)
        r = c.get("/api/edtech_co/verdicts")
        assert r.status_code == 403

        # Cannot scan for edtech_co
        r = c.post("/v1/edtech_co/scan", json={"text": "test"})
        assert r.status_code == 403

    def test_super_admin_can_access_all_orgs(self, super_admin):
        """SUPER_ADMIN has wildcard access — no explicit memberships needed."""
        assert super_admin.get("/api/blinkit/verdicts").status_code == 200
        assert super_admin.get("/api/edtech_co/verdicts").status_code == 200

    def test_my_orgs_returns_only_accessible_orgs(self, tmp_path):
        """GET /api/auth/my-orgs returns only the user's assigned orgs."""
        from passlib.context import CryptContext
        pw_hash = CryptContext(schemes=["bcrypt"], deprecated="auto").hash("testpass!")
        u = get_user_store().create_user("limited", "lim@test.io", pw_hash, UserRole.COMPLIANCE_ADMIN)
        get_user_store().grant_org_access(u.user_id, "blinkit")  # only blinkit, not edtech_co
        c = TestClient(app)
        c.post("/api/auth/login", data={"username": "limited", "password": "testpass!"})
        r = c.get("/api/auth/my-orgs")
        assert r.status_code == 200
        assert r.json()["org_ids"] == ["blinkit"]
        assert "edtech_co" not in r.json()["org_ids"]

    def test_grant_and_revoke_org_access(self, super_admin):
        """SUPER_ADMIN can grant and revoke org access for another user."""
        from passlib.context import CryptContext
        pw_hash = CryptContext(schemes=["bcrypt"], deprecated="auto").hash("testpass!")
        u = get_user_store().create_user("target2", "t2@test.io", pw_hash, UserRole.AUDITOR)

        # Grant access
        r = super_admin.post(f"/api/auth/users/{u.user_id}/orgs/blinkit")
        assert r.status_code == 201

        # Verify access
        assert get_user_store().has_org_access(u.user_id, "blinkit")

        # Revoke access
        r = super_admin.delete(f"/api/auth/users/{u.user_id}/orgs/blinkit")
        assert r.status_code == 200
        assert not get_user_store().has_org_access(u.user_id, "blinkit")
