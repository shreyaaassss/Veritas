"""
User Store — Unit Tests
========================
Tests for user_store/store.py and api/auth.py functionality.
Run with: python -m pytest user_store/test_user_store.py -v
"""

from __future__ import annotations

import pytest
import user_store.store as store_module
from user_store.models import UserRole
from user_store.store import get_user_store, reset_user_store


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def isolated_user_store(tmp_path, monkeypatch):
    monkeypatch.setattr(store_module, "_DEFAULT_DB_PATH", tmp_path / "test_users.db")
    reset_user_store()
    yield
    reset_user_store()


@pytest.fixture()
def store():
    return get_user_store()


def _hash(pw: str) -> str:
    from passlib.context import CryptContext
    return CryptContext(schemes=["bcrypt"], deprecated="auto").hash(pw)


# ---------------------------------------------------------------------------
# Creation
# ---------------------------------------------------------------------------

class TestCreateUser:

    def test_create_basic_user(self, store):
        u = store.create_user("alice", "alice@test.io", _hash("pass123!"), UserRole.VIEWER)
        assert u.username == "alice"
        assert u.role == UserRole.VIEWER
        assert u.is_active is True
        assert u.last_login is None

    def test_username_case_insensitive_unique(self, store):
        store.create_user("bob", "bob@test.io", _hash("pass1"), UserRole.VIEWER)
        with pytest.raises(ValueError, match="already exists"):
            store.create_user("BOB", "bob2@test.io", _hash("pass2"), UserRole.VIEWER)

    def test_email_unique(self, store):
        store.create_user("user1", "shared@test.io", _hash("pass1"), UserRole.VIEWER)
        with pytest.raises(ValueError, match="already exists"):
            store.create_user("user2", "shared@test.io", _hash("pass2"), UserRole.VIEWER)

    def test_password_not_stored_plaintext(self, store):
        u = store.create_user("carol", "carol@test.io", _hash("secret"), UserRole.AUDITOR)
        assert "secret" not in u.password_hash
        assert u.password_hash.startswith("$2")  # bcrypt prefix

    def test_default_role_is_viewer(self, store):
        u = store.create_user("dave", "dave@test.io", _hash("pass"))
        assert u.role == UserRole.VIEWER

    def test_super_admin_role(self, store):
        u = store.create_user("admin", "admin@test.io", _hash("admin!123"), UserRole.SUPER_ADMIN)
        assert u.role == UserRole.SUPER_ADMIN


# ---------------------------------------------------------------------------
# Lookups
# ---------------------------------------------------------------------------

class TestLookups:

    def test_get_by_username(self, store):
        store.create_user("eve", "eve@test.io", _hash("p"), UserRole.VIEWER)
        u = store.get_by_username("eve")
        assert u is not None
        assert u.username == "eve"

    def test_get_by_username_case_insensitive(self, store):
        store.create_user("Frank", "frank@test.io", _hash("p"), UserRole.VIEWER)
        assert store.get_by_username("frank") is not None
        assert store.get_by_username("FRANK") is not None

    def test_get_by_username_not_found(self, store):
        assert store.get_by_username("nobody") is None

    def test_get_by_id(self, store):
        u = store.create_user("grace", "grace@test.io", _hash("p"), UserRole.VIEWER)
        fetched = store.get_by_id(u.user_id)
        assert fetched is not None
        assert fetched.user_id == u.user_id

    def test_get_by_id_not_found(self, store):
        assert store.get_by_id("nonexistent-uuid") is None

    def test_list_users(self, store):
        store.create_user("u1", "u1@test.io", _hash("p"), UserRole.VIEWER)
        store.create_user("u2", "u2@test.io", _hash("p"), UserRole.AUDITOR)
        users = store.list_users()
        assert len(users) == 2

    def test_count_users_empty(self, store):
        assert store.count_users() == 0

    def test_count_users(self, store):
        store.create_user("x", "x@test.io", _hash("p"), UserRole.VIEWER)
        assert store.count_users() == 1


# ---------------------------------------------------------------------------
# Mutations
# ---------------------------------------------------------------------------

class TestMutations:

    def test_set_active_false(self, store):
        u = store.create_user("henry", "henry@test.io", _hash("p"), UserRole.VIEWER)
        result = store.set_active(u.user_id, False)
        assert result is True
        fetched = store.get_by_id(u.user_id)
        assert fetched.is_active is False

    def test_set_active_unknown_user(self, store):
        assert store.set_active("no-such-id", False) is False

    def test_update_last_login(self, store):
        u = store.create_user("ida", "ida@test.io", _hash("p"), UserRole.VIEWER)
        assert u.last_login is None
        store.update_last_login(u.user_id)
        fetched = store.get_by_id(u.user_id)
        assert fetched.last_login is not None

    def test_update_role(self, store):
        u = store.create_user("jake", "jake@test.io", _hash("p"), UserRole.VIEWER)
        store.update_role(u.user_id, UserRole.COMPLIANCE_ADMIN)
        fetched = store.get_by_id(u.user_id)
        assert fetched.role == UserRole.COMPLIANCE_ADMIN

    def test_update_password(self, store):
        u = store.create_user("kim", "kim@test.io", _hash("old"), UserRole.VIEWER)
        new_hash = _hash("new")
        store.update_password(u.user_id, new_hash)
        fetched = store.get_by_id(u.user_id)
        assert fetched.password_hash == new_hash


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

class TestSingleton:

    def test_same_instance(self):
        s1 = get_user_store()
        s2 = get_user_store()
        assert s1 is s2

    def test_reset_clears(self, tmp_path, monkeypatch):
        monkeypatch.setattr(store_module, "_DEFAULT_DB_PATH", tmp_path / "s.db")
        s1 = get_user_store()
        reset_user_store()
        s2 = get_user_store()
        assert s1 is not s2
