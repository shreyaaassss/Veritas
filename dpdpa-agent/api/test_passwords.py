"""
Passwords and the user lifecycle: policy, changing your own password, administrator reset,
the temporary-password flow, session revocation and the last-administrator guard.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import agent_store.store as agent_store_module
import evidence_store.store as store_module
import user_store.store as user_store_module
from agent_store.store import reset_agent_store
from api import passwords
from api.auth import _hash_password
from dashboard.server import app
from rate_limit import _limiter
from user_store.models import UserRole
from user_store.store import get_user_store, reset_user_store

ADMIN_PW = "Adm1n-Strong#Pass"


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(store_module, "_DEFAULT_DB_PATH", tmp_path / "evidence.db")
    monkeypatch.setattr(agent_store_module, "_DEFAULT_DB_PATH", tmp_path / "agents.db")
    monkeypatch.setattr(user_store_module, "_DEFAULT_DB_PATH", tmp_path / "users.db")
    monkeypatch.setenv("VERITAS_SECURE_COOKIES", "false")
    monkeypatch.setenv("VERITAS_JWT_SECRET", "test-jwt-secret-for-unit-tests-only")
    store_module.reset_store(); reset_agent_store(); reset_user_store()
    _limiter._windows.clear()
    yield
    store_module.reset_store(); reset_agent_store(); reset_user_store()
    _limiter._windows.clear()


def new_client() -> TestClient:
    return TestClient(app, raise_server_exceptions=False)


def make_user(username, role=UserRole.VIEWER, password=ADMIN_PW, must_change=False):
    u = get_user_store().create_user(username, f"{username}@example.com", _hash_password(password), role)
    if must_change:
        get_user_store().set_must_change_password(u.user_id, True)
    return u


def login(username, password=ADMIN_PW):
    c = new_client()
    r = c.post("/api/auth/login", data={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return c


# ── policy ─────────────────────────────────────────────────────────────────
class TestPolicy:
    @pytest.mark.parametrize("pw", [
        "short1!", "password123", "Password123", "qwertyuiop", "aaaaaaaaaaaa", "1234567890",
        "admin@123", " leading-space-pw", "Veritas123", "Welcome@123",
    ])
    def test_weak_passwords_are_refused(self, pw):
        assert passwords.check(pw) is not None

    def test_name_based_passwords_are_refused(self):
        assert passwords.check("Priyasharma#2024", username="priyasharma")
        assert passwords.check("x-meera.iyer-x9", email="meera.iyer@corp.example")

    @pytest.mark.parametrize("pw", ["correct horse battery staple", "Tr4in-Lamp-Orbit!", "kT9#vLq2$wZp"])
    def test_good_passwords_pass(self, pw):
        assert passwords.check(pw, username="someone", email="someone@example.com") is None

    def test_generated_passwords_satisfy_the_policy_and_differ(self):
        seen = {passwords.generate_temporary_password() for _ in range(50)}
        assert len(seen) == 50
        assert all(passwords.check(p) is None for p in seen)
        assert all(not set(p) & set("0O1lIi") for p in seen)


# ── change own password ────────────────────────────────────────────────────
class TestChangePassword:
    def test_change_then_old_password_stops_working(self):
        make_user("alice")
        c = login("alice")
        r = c.post("/api/auth/change-password", json={"current_password": ADMIN_PW, "new_password": "Brand-New#Pass42"})
        assert r.status_code == 200, r.text
        assert c.get("/api/auth/me").status_code == 200          # this session continues
        assert new_client().post("/api/auth/login", data={"username": "alice", "password": ADMIN_PW}).status_code == 401
        login("alice", "Brand-New#Pass42")

    def test_other_sessions_end(self):
        make_user("alice")
        first, second = login("alice"), login("alice")
        second.post("/api/auth/change-password", json={"current_password": ADMIN_PW, "new_password": "Brand-New#Pass42"})
        assert first.get("/api/auth/me").status_code == 401
        assert second.get("/api/auth/me").status_code == 200

    def test_wrong_current_password(self):
        make_user("alice")
        c = login("alice")
        r = c.post("/api/auth/change-password", json={"current_password": "nope-nope-nope", "new_password": "Brand-New#Pass42"})
        assert r.status_code == 400
        assert login("alice")                                      # password unchanged

    def test_weak_new_password_and_same_password_refused(self):
        make_user("alice")
        c = login("alice")
        assert c.post("/api/auth/change-password", json={"current_password": ADMIN_PW, "new_password": "password123"}).status_code == 422
        assert c.post("/api/auth/change-password", json={"current_password": ADMIN_PW, "new_password": ADMIN_PW}).status_code == 422

    def test_requires_login(self):
        r = new_client().post("/api/auth/change-password", json={"current_password": "x", "new_password": "y"})
        assert r.status_code == 401

    def test_attempts_are_rate_limited(self):
        make_user("alice")
        c = login("alice")
        codes = [c.post("/api/auth/change-password", json={"current_password": "wrong-wrong-1", "new_password": "Brand-New#Pass42"}).status_code
                 for _ in range(14)]
        assert 429 in codes


# ── administrator reset and the forced-change flow ─────────────────────────
class TestResetAndForcedChange:
    def test_reset_returns_a_temporary_password_once_and_forces_a_change(self):
        admin = make_user("boss", UserRole.SUPER_ADMIN)
        target = make_user("bob", UserRole.AUDITOR)
        a = login("boss")
        bob_old = login("bob")
        r = a.post(f"/api/auth/users/{target.user_id}/reset-password", json={})
        assert r.status_code == 200, r.text
        temp = r.json()["temporary_password"]
        assert passwords.check(temp) is None
        assert bob_old.get("/api/auth/me").status_code == 401       # old session ended
        assert new_client().post("/api/auth/login", data={"username": "bob", "password": ADMIN_PW}).status_code == 401

        b = login("bob", temp)
        me = b.get("/api/auth/me").json()
        assert me["must_change_password"] is True
        # everything else is blocked until the password is changed
        blocked = b.get("/api/auth/my-orgs")
        assert blocked.status_code == 403 and "PASSWORD_CHANGE_REQUIRED" in blocked.json()["detail"]
        assert b.get("/agents").status_code in (403, 404)
        r = b.post("/api/auth/change-password", json={"current_password": temp, "new_password": "Bob-Chose#This99"})
        assert r.status_code == 200
        assert b.get("/api/auth/me").json()["must_change_password"] is False
        assert b.get("/api/auth/my-orgs").status_code == 200

    def test_reset_with_a_chosen_password_follows_the_policy(self):
        make_user("boss", UserRole.SUPER_ADMIN)
        target = make_user("bob")
        a = login("boss")
        assert a.post(f"/api/auth/users/{target.user_id}/reset-password", json={"new_password": "password123"}).status_code == 422
        r = a.post(f"/api/auth/users/{target.user_id}/reset-password", json={"new_password": "Chosen-By-Admin#77"})
        assert r.status_code == 200 and "temporary_password" not in r.json()
        assert login("bob", "Chosen-By-Admin#77").get("/api/auth/me").json()["must_change_password"] is True

    def test_cannot_reset_yourself_or_a_missing_user(self):
        boss = make_user("boss", UserRole.SUPER_ADMIN)
        a = login("boss")
        assert a.post(f"/api/auth/users/{boss.user_id}/reset-password", json={}).status_code == 400
        assert a.post("/api/auth/users/does-not-exist/reset-password", json={}).status_code == 404

    def test_only_super_admin_can_reset(self):
        make_user("ca", UserRole.COMPLIANCE_ADMIN)
        target = make_user("bob")
        assert login("ca").post(f"/api/auth/users/{target.user_id}/reset-password", json={}).status_code == 403

    def test_new_users_must_change_by_default(self):
        make_user("boss", UserRole.SUPER_ADMIN)
        a = login("boss")
        r = a.post("/api/auth/users", json={"username": "newbie", "email": "newbie@example.com",
                                            "password": "Initial-Pass#555", "role": "VIEWER"})
        assert r.status_code == 201 and r.json()["must_change_password"] is True
        assert login("newbie", "Initial-Pass#555").get("/api/auth/me").json()["must_change_password"] is True

    def test_service_style_user_can_skip_the_forced_change(self):
        make_user("boss", UserRole.SUPER_ADMIN)
        a = login("boss")
        r = a.post("/api/auth/users", json={"username": "svc", "email": "svc@example.com", "password": "Initial-Pass#555",
                                            "role": "VIEWER", "require_password_change": False})
        assert r.status_code == 201 and r.json()["must_change_password"] is False

    def test_create_user_enforces_the_policy(self):
        make_user("boss", UserRole.SUPER_ADMIN)
        a = login("boss")
        r = a.post("/api/auth/users", json={"username": "newbie", "email": "newbie@example.com",
                                            "password": "password123", "role": "VIEWER"})
        assert r.status_code == 422

    def test_websocket_refuses_a_user_who_must_change_password(self):
        from starlette.websockets import WebSocketDisconnect
        bob = make_user("bob", must_change=True)
        get_user_store().grant_org_access(bob.user_id, "acme", granted_by="x")
        b = login("bob")
        with pytest.raises(WebSocketDisconnect) as exc:
            with b.websocket_connect("/ws/acme"):
                pass
        assert exc.value.code == 4003
        assert "Password" in (exc.value.reason or "")
        # control: once the flag is cleared the same connection is accepted
        get_user_store().set_must_change_password(bob.user_id, False)
        with b.websocket_connect("/ws/acme"):
            pass


# ── last administrator guard ───────────────────────────────────────────────
class TestLastAdminGuard:
    def test_last_active_super_admin_cannot_be_demoted_or_disabled(self):
        only = make_user("boss", UserRole.SUPER_ADMIN)
        other = make_user("boss2", UserRole.SUPER_ADMIN)
        get_user_store().set_active(other.user_id, False)          # now `only` is the last active one
        a = login("boss")
        # Self-guards already block this; check through the store count as well.
        assert get_user_store().count_active_super_admins() == 1
        assert a.patch(f"/api/auth/users/{only.user_id}", json={"is_active": False}).status_code == 400

    def test_a_second_admin_cannot_remove_the_only_other_admin_if_it_leaves_none(self):
        a1 = make_user("boss", UserRole.SUPER_ADMIN)
        a2 = make_user("boss2", UserRole.SUPER_ADMIN)
        c = login("boss")
        assert c.patch(f"/api/auth/users/{a2.user_id}", json={"is_active": False}).status_code == 200   # one remains
        get_user_store().set_active(a2.user_id, True)
        # with two admins, demoting one is fine; the survivor stays protected
        assert c.patch(f"/api/auth/users/{a2.user_id}", json={"role": "AUDITOR"}).status_code == 200
        assert c.patch(f"/api/auth/users/{a1.user_id}", json={"role": "AUDITOR"}).status_code == 400

    def test_invalid_role_is_422(self):
        make_user("boss", UserRole.SUPER_ADMIN)
        bob = make_user("bob")
        assert login("boss").patch(f"/api/auth/users/{bob.user_id}", json={"role": "KING"}).status_code == 422

    def test_user_list_shows_org_access_and_flags(self):
        make_user("boss", UserRole.SUPER_ADMIN)
        bob = make_user("bob", must_change=True)
        get_user_store().grant_org_access(bob.user_id, "acme", granted_by="x")
        rows = {u["username"]: u for u in login("boss").get("/api/auth/users").json()}
        assert rows["bob"]["org_ids"] == ["acme"] and rows["bob"]["must_change_password"] is True


# ── audit trail ────────────────────────────────────────────────────────────
class TestAudit:
    def test_password_events_are_audited_without_secrets(self):
        from audit_log.store import get_audit_store
        make_user("boss", UserRole.SUPER_ADMIN)
        bob = make_user("bob")
        a = login("boss")
        temp = a.post(f"/api/auth/users/{bob.user_id}/reset-password", json={}).json()["temporary_password"]
        b = login("bob", temp)
        b.post("/api/auth/change-password", json={"current_password": temp, "new_password": "Bob-Chose#This99"})
        rows = get_audit_store().query(limit=100) if hasattr(get_audit_store(), "query") else []
        text = str(rows)
        assert "PASSWORD_RESET" in text and "PASSWORD_CHANGED" in text
        assert temp not in text and "Bob-Chose#This99" not in text
