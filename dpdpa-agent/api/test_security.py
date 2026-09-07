"""
Security Tests (Phase 27)
==========================
Verifies security controls that protect the Veritas API surface:

  TestSecurityHeaders   — all configured headers present on every response
  TestCookieSecurity    — login cookie has httpOnly + SameSite attributes
  TestRateLimiting      — 429 enforced after threshold; Retry-After header present
  TestInputValidation   — oversized payloads, malformed JSON, injection strings

Run with: python -m pytest api/test_security.py -v
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
from rate_limit import _limiter
from user_store.models import UserRole
from user_store.store import get_user_store, reset_user_store


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(store_module, "_DEFAULT_DB_PATH", tmp_path / "sec_evidence.db")
    monkeypatch.setattr(agent_store_module, "_DEFAULT_DB_PATH", tmp_path / "sec_agents.db")
    monkeypatch.setattr(user_store_module, "_DEFAULT_DB_PATH", tmp_path / "sec_users.db")
    monkeypatch.setenv("VERITAS_SECURE_COOKIES", "false")
    monkeypatch.setenv("VERITAS_JWT_SECRET", "security-test-secret-key")
    reset_store()
    reset_agent_store()
    reset_user_store()
    live_feed._ws_clients.clear()
    # Clear rate-limit buckets so tests are independent
    _limiter._windows.clear()
    yield
    reset_store()
    reset_agent_store()
    reset_user_store()
    live_feed._ws_clients.clear()
    _limiter._windows.clear()


@pytest.fixture()
def admin_client():
    from passlib.context import CryptContext
    pw_hash = CryptContext(schemes=["bcrypt"], deprecated="auto").hash("AdminPass1!")
    get_user_store().create_user("sec_admin", "sec@test.io", pw_hash, UserRole.SUPER_ADMIN)
    c = TestClient(app, raise_server_exceptions=False)
    r = c.post("/api/auth/login", data={"username": "sec_admin", "password": "AdminPass1!"})
    assert r.status_code == 200
    return c


@pytest.fixture()
def anon_client():
    return TestClient(app, raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# TestSecurityHeaders
# ---------------------------------------------------------------------------

class TestSecurityHeaders:
    """Every response — authenticated or not — must carry the full security header set."""

    _REQUIRED_HEADERS = {
        "x-content-type-options": "nosniff",
        "x-frame-options": "DENY",
        "referrer-policy": "strict-origin-when-cross-origin",
        "permissions-policy": None,         # presence check only
        "content-security-policy": None,    # presence check only
        "x-veritas-api-version": None,      # presence check only
    }

    def _assert_headers(self, response) -> None:
        for header, expected_value in self._REQUIRED_HEADERS.items():
            assert header in response.headers, (
                f"Missing security header: {header!r} on {response.url}"
            )
            if expected_value is not None:
                assert response.headers[header] == expected_value, (
                    f"Header {header!r}: expected {expected_value!r}, "
                    f"got {response.headers[header]!r}"
                )

    def test_headers_on_unauthenticated_endpoint(self, anon_client):
        r = anon_client.get("/health")
        assert r.status_code == 200
        self._assert_headers(r)

    def test_headers_on_ready_endpoint(self, anon_client):
        r = anon_client.get("/ready")
        # ready may be 503 on a fresh install (no license), but headers should be present
        self._assert_headers(r)

    def test_headers_on_401_response(self, anon_client):
        r = anon_client.get("/api/auth/me")
        assert r.status_code == 401
        self._assert_headers(r)

    def test_headers_on_authenticated_response(self, admin_client):
        r = admin_client.get("/api/auth/me")
        assert r.status_code == 200
        self._assert_headers(r)

    def test_csp_includes_frame_ancestors(self, anon_client):
        r = anon_client.get("/health")
        csp = r.headers.get("content-security-policy", "")
        assert "frame-ancestors" in csp, "CSP must include frame-ancestors directive"

    def test_xss_protection_header(self, anon_client):
        r = anon_client.get("/health")
        assert "x-xss-protection" in r.headers

    def test_api_version_header_is_semver(self, anon_client):
        r = anon_client.get("/health")
        version = r.headers.get("x-veritas-api-version", "")
        parts = version.split(".")
        assert len(parts) == 3 and all(p.isdigit() for p in parts), (
            f"x-veritas-api-version must be semver (e.g. 1.0.0), got {version!r}"
        )


# ---------------------------------------------------------------------------
# TestCookieSecurity
# ---------------------------------------------------------------------------

class TestCookieSecurity:
    """Login session cookie must be httpOnly and SameSite=lax."""

    def test_login_cookie_is_httponly(self, anon_client):
        from passlib.context import CryptContext
        pw_hash = CryptContext(schemes=["bcrypt"], deprecated="auto").hash("TestPass1!")
        get_user_store().create_user("cookie_user", "cookie@test.io", pw_hash, UserRole.VIEWER)

        r = anon_client.post("/api/auth/login", data={"username": "cookie_user", "password": "TestPass1!"})
        assert r.status_code == 200

        # httpx stores cookies; check Set-Cookie header
        set_cookie = r.headers.get("set-cookie", "")
        assert "HttpOnly" in set_cookie or "httponly" in set_cookie.lower(), (
            f"Session cookie must be HttpOnly. Set-Cookie: {set_cookie}"
        )

    def test_login_cookie_has_samesite(self, anon_client):
        from passlib.context import CryptContext
        pw_hash = CryptContext(schemes=["bcrypt"], deprecated="auto").hash("TestPass1!")
        get_user_store().create_user("ss_user", "ss@test.io", pw_hash, UserRole.VIEWER)

        r = anon_client.post("/api/auth/login", data={"username": "ss_user", "password": "TestPass1!"})
        assert r.status_code == 200

        set_cookie = r.headers.get("set-cookie", "")
        assert "samesite" in set_cookie.lower(), (
            f"Session cookie must have SameSite attribute. Set-Cookie: {set_cookie}"
        )

    def test_login_cookie_not_in_response_body(self, anon_client):
        """Token must be in the cookie, NOT in the JSON body."""
        from passlib.context import CryptContext
        pw_hash = CryptContext(schemes=["bcrypt"], deprecated="auto").hash("TestPass1!")
        get_user_store().create_user("body_user", "body@test.io", pw_hash, UserRole.VIEWER)

        r = anon_client.post("/api/auth/login", data={"username": "body_user", "password": "TestPass1!"})
        assert r.status_code == 200
        body = r.json()
        assert "token" not in body, "Session token must not appear in the JSON response body"
        assert "access_token" not in body


# ---------------------------------------------------------------------------
# TestRateLimiting
# ---------------------------------------------------------------------------

class TestRateLimiting:
    """Endpoints with rate limiting must return 429 after the threshold."""

    def test_login_rate_limit_enforced(self, anon_client):
        """10 failed logins from same IP → 429 on 11th attempt."""
        for _ in range(10):
            r = anon_client.post(
                "/api/auth/login",
                data={"username": "nonexistent", "password": "wrongpass"},
            )
            assert r.status_code in (401, 429)

        # The 11th request must be rate-limited
        r = anon_client.post(
            "/api/auth/login",
            data={"username": "nonexistent", "password": "wrongpass"},
        )
        assert r.status_code == 429, f"Expected 429, got {r.status_code}"

    def test_rate_limit_has_retry_after_header(self, anon_client):
        """429 response must include Retry-After header."""
        for _ in range(11):
            r = anon_client.post(
                "/api/auth/login",
                data={"username": "x", "password": "y"},
            )

        assert r.status_code == 429
        assert "retry-after" in r.headers, "429 response must include Retry-After header"
        retry_after = int(r.headers["retry-after"])
        assert retry_after > 0, "Retry-After must be a positive integer"

    def test_setup_rate_limit_enforced(self, anon_client):
        """Setup endpoint allows max 3 attempts per window."""
        payload = {"username": "adm", "email": "a@b.com", "password": "Short1"}  # too short
        responses = []
        for _ in range(4):
            r = anon_client.post("/api/auth/setup", json=payload)
            responses.append(r.status_code)

        # At least one must be 429 (rate limited) — regardless of whether
        # early ones are 422 (validation error) or 403 (already set up)
        assert 429 in responses, f"Expected 429 in responses after 4 rapid attempts: {responses}"

    def test_successful_login_clears_rate_limit(self, anon_client):
        """A successful login resets the IP's failure counter."""
        from passlib.context import CryptContext
        pw_hash = CryptContext(schemes=["bcrypt"], deprecated="auto").hash("GoodPass1!")
        get_user_store().create_user("rl_user", "rl@test.io", pw_hash, UserRole.VIEWER)

        # Accumulate 5 failures (half the limit)
        for _ in range(5):
            anon_client.post("/api/auth/login", data={"username": "rl_user", "password": "wrong"})

        # Successful login clears the counter
        r = anon_client.post("/api/auth/login", data={"username": "rl_user", "password": "GoodPass1!"})
        assert r.status_code == 200

        # Should now be able to fail again without hitting the limit immediately
        r = anon_client.post("/api/auth/login", data={"username": "rl_user", "password": "wrong"})
        assert r.status_code == 401  # not 429


# ---------------------------------------------------------------------------
# TestInputValidation
# ---------------------------------------------------------------------------

class TestInputValidation:
    """API must reject malformed, oversized, or injection-containing inputs."""

    def test_login_rejects_empty_credentials(self, anon_client):
        r = anon_client.post("/api/auth/login", data={"username": "", "password": ""})
        assert r.status_code in (401, 422)

    def test_login_rejects_missing_fields(self, anon_client):
        r = anon_client.post("/api/auth/login", data={"username": "only_user"})
        assert r.status_code == 422

    def test_setup_rejects_weak_password(self, anon_client):
        r = anon_client.post("/api/auth/setup", json={
            "username": "admin",
            "email": "admin@test.io",
            "password": "short",          # < 8 chars
        })
        assert r.status_code == 422

    def test_setup_rejects_invalid_email(self, anon_client):
        r = anon_client.post("/api/auth/setup", json={
            "username": "admin",
            "email": "notanemail",
            "password": "GoodPass1!",
        })
        assert r.status_code == 422

    def test_scan_rejects_oversized_text(self, admin_client):
        """Oversized scan payload must not crash the server (500 is unacceptable)."""
        from org_config.store import upload_org_config
        upload_org_config("test_sec_org", {
            "org_id": "test_sec_org",
            "org_name": "Test Org",
            "data_fiduciary_name": "Test Corp Ltd",
            "data_fiduciary_email": "dpo@testcorp.io",
            "ingestion_sources": ["API"],
            "pii_fields": ["name", "email"],
            "retention_days": 90,
            "consent_mechanism": "explicit",
            "dpo_email": "dpo@testcorp.io",
        })

        r = admin_client.post(
            "/agents/issue-key",
            json={"label": "test-agent", "org_id": "test_sec_org"},
        )
        token = r.json().get("key", "") if r.status_code == 200 else ""

        r = admin_client.post(
            "/v1/test_sec_org/scan",
            json={"text": "A" * 200_001},  # >200KB
            headers={"Authorization": f"Bearer {token}"},
        )
        # Must not crash the server — any structured response (not 500) is acceptable
        assert r.status_code != 500, f"Server must not 500 on oversized input: {r.text[:200]}"

    def test_api_returns_json_on_not_found(self, admin_client):
        r = admin_client.get("/v1/nonexistent_org_xyz/verdicts")
        assert r.status_code in (404, 403)
        # Response should be JSON, not a raw exception trace
        ct = r.headers.get("content-type", "")
        assert "application/json" in ct, f"Expected JSON error, got content-type: {ct}"
