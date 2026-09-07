"""
Veritas User Store — SQLite persistence
=========================================
Stores human user accounts. Completely separate from agent_store (which holds
Veritas Agent registrations/tokens).

Schema: users.db → table `users`

Design decisions:
- password_hash: bcrypt hash via passlib, never plaintext
- username and email: UNIQUE constraints
- is_active: soft-disable without deleting (preserves audit trail)
- threading.Lock(): same pattern as evidence_store and agent_store
- Singleton via get_user_store() / reset_user_store() for test isolation
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional
from uuid import uuid4

from user_store.models import User, UserRole

logger = logging.getLogger("user_store.store")

_DEFAULT_DB_PATH: Path  # set at module load via data_root()


def _init_default_path() -> Path:
    from runtime_paths import data_root
    return data_root() / "users.db"


_DEFAULT_DB_PATH = _init_default_path()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _dt(value: Optional[str]) -> Optional[datetime]:
    if value is None:
        return None
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


# ---------------------------------------------------------------------------
# UserStore
# ---------------------------------------------------------------------------

class UserStore:
    """SQLite-backed user account store. Thread-safe for single-process use."""

    def __init__(self, db_path: Path = _DEFAULT_DB_PATH) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        with self._lock:
            self._conn.executescript("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id       TEXT PRIMARY KEY,
                    username      TEXT NOT NULL UNIQUE COLLATE NOCASE,
                    email         TEXT NOT NULL UNIQUE COLLATE NOCASE,
                    password_hash TEXT NOT NULL,
                    role          TEXT NOT NULL DEFAULT 'VIEWER',
                    is_active     INTEGER NOT NULL DEFAULT 1,
                    created_at    TEXT NOT NULL,
                    last_login    TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_users_username ON users(username COLLATE NOCASE);
                CREATE INDEX IF NOT EXISTS idx_users_email    ON users(email COLLATE NOCASE);

                -- Org access memberships (SUPER_ADMIN has implicit wildcard — no row needed)
                CREATE TABLE IF NOT EXISTS user_org_memberships (
                    membership_id TEXT PRIMARY KEY,
                    user_id       TEXT NOT NULL,
                    org_id        TEXT NOT NULL COLLATE NOCASE,
                    granted_by    TEXT,
                    granted_at    TEXT NOT NULL,
                    UNIQUE(user_id, org_id)
                );

                CREATE INDEX IF NOT EXISTS idx_memberships_user ON user_org_memberships(user_id);
                CREATE INDEX IF NOT EXISTS idx_memberships_org  ON user_org_memberships(org_id);
            """)
            self._conn.commit()

    # ------------------------------------------------------------------
    # Write operations
    # ------------------------------------------------------------------

    def create_user(
        self,
        username: str,
        email: str,
        password_hash: str,
        role: UserRole = UserRole.VIEWER,
    ) -> User:
        """Create a new user. Raises ValueError if username or email already exists."""
        user_id  = str(uuid4())
        now      = _now()

        with self._lock:
            try:
                self._conn.execute(
                    """
                    INSERT INTO users
                        (user_id, username, email, password_hash, role, is_active, created_at, last_login)
                    VALUES (?, ?, ?, ?, ?, 1, ?, NULL)
                    """,
                    (user_id, username.strip(), email.strip().lower(), password_hash, role.value, now.isoformat()),
                )
                self._conn.commit()
            except sqlite3.IntegrityError as exc:
                raise ValueError(f"Username or email already exists: {exc}") from exc

        logger.info("Created user %r (role=%s)", username, role.value)
        return User(
            user_id=user_id, username=username.strip(), email=email.strip().lower(),
            password_hash=password_hash, role=role, is_active=True, created_at=now,
        )

    def update_last_login(self, user_id: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE users SET last_login = ? WHERE user_id = ?",
                (_now().isoformat(), user_id),
            )
            self._conn.commit()

    def set_active(self, user_id: str, active: bool) -> bool:
        """Enable or disable a user. Returns False if user not found."""
        with self._lock:
            cursor = self._conn.execute(
                "UPDATE users SET is_active = ? WHERE user_id = ?",
                (1 if active else 0, user_id),
            )
            self._conn.commit()
        return cursor.rowcount > 0

    def update_password(self, user_id: str, new_hash: str) -> bool:
        with self._lock:
            cursor = self._conn.execute(
                "UPDATE users SET password_hash = ? WHERE user_id = ?",
                (new_hash, user_id),
            )
            self._conn.commit()
        return cursor.rowcount > 0

    def update_role(self, user_id: str, role: UserRole) -> bool:
        with self._lock:
            cursor = self._conn.execute(
                "UPDATE users SET role = ? WHERE user_id = ?",
                (role.value, user_id),
            )
            self._conn.commit()
        return cursor.rowcount > 0

    # ------------------------------------------------------------------
    # Org membership operations
    # ------------------------------------------------------------------

    def grant_org_access(self, user_id: str, org_id: str, granted_by: Optional[str] = None) -> None:
        """Grant a user access to an org. Silently succeeds if already granted."""
        with self._lock:
            try:
                self._conn.execute(
                    """
                    INSERT OR IGNORE INTO user_org_memberships
                        (membership_id, user_id, org_id, granted_by, granted_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (str(uuid4()), user_id, org_id.lower(), granted_by, _now().isoformat()),
                )
                self._conn.commit()
            except Exception:
                pass

    def revoke_org_access(self, user_id: str, org_id: str) -> bool:
        """Revoke a user's access to an org. Returns True if membership existed."""
        with self._lock:
            cursor = self._conn.execute(
                "DELETE FROM user_org_memberships WHERE user_id = ? AND org_id = ? COLLATE NOCASE",
                (user_id, org_id),
            )
            self._conn.commit()
        return cursor.rowcount > 0

    def has_org_access(self, user_id: str, org_id: str) -> bool:
        """True if the user has explicit org membership (SUPER_ADMIN is handled by caller)."""
        row = self._conn.execute(
            "SELECT 1 FROM user_org_memberships WHERE user_id = ? AND org_id = ? COLLATE NOCASE",
            (user_id, org_id),
        ).fetchone()
        return row is not None

    def get_user_orgs(self, user_id: str) -> List[str]:
        """Return list of org_ids the user has explicit access to."""
        rows = self._conn.execute(
            "SELECT org_id FROM user_org_memberships WHERE user_id = ? ORDER BY granted_at",
            (user_id,),
        ).fetchall()
        return [r["org_id"] for r in rows]

    def get_org_users(self, org_id: str) -> List[str]:
        """Return list of user_ids with explicit access to the org."""
        rows = self._conn.execute(
            "SELECT user_id FROM user_org_memberships WHERE org_id = ? COLLATE NOCASE",
            (org_id,),
        ).fetchall()
        return [r["user_id"] for r in rows]

    # ------------------------------------------------------------------
    # Read operations
    # ------------------------------------------------------------------

    def get_by_id(self, user_id: str) -> Optional[User]:
        row = self._conn.execute(
            "SELECT * FROM users WHERE user_id = ?", (user_id,)
        ).fetchone()
        return _row_to_user(row) if row else None

    def get_by_username(self, username: str) -> Optional[User]:
        row = self._conn.execute(
            "SELECT * FROM users WHERE username = ? COLLATE NOCASE", (username.strip(),)
        ).fetchone()
        return _row_to_user(row) if row else None

    def get_by_email(self, email: str) -> Optional[User]:
        row = self._conn.execute(
            "SELECT * FROM users WHERE email = ? COLLATE NOCASE", (email.strip().lower(),)
        ).fetchone()
        return _row_to_user(row) if row else None

    def list_users(self) -> List[User]:
        rows = self._conn.execute(
            "SELECT * FROM users ORDER BY created_at"
        ).fetchall()
        return [_row_to_user(r) for r in rows]

    def count_users(self) -> int:
        return self._conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]

    def close(self) -> None:
        self._conn.close()


def _row_to_user(row: sqlite3.Row) -> User:
    return User(
        user_id=row["user_id"],
        username=row["username"],
        email=row["email"],
        password_hash=row["password_hash"],
        role=UserRole(row["role"]),
        is_active=bool(row["is_active"]),
        created_at=_dt(row["created_at"]),
        last_login=_dt(row["last_login"]),
    )


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_store: Optional[UserStore] = None


def get_user_store(db_path: Optional[Path] = None) -> UserStore:
    global _store
    if _store is None:
        _store = UserStore(db_path or _DEFAULT_DB_PATH)
    return _store


def reset_user_store() -> None:
    """Close and discard singleton. Used in tests for isolation."""
    global _store
    if _store is not None:
        try:
            _store.close()
        except Exception:
            pass
        _store = None
