"""
Veritas Agent Store — SQLite persistence (Block 2)
===================================================
Two tables in agent_store/agents.db:

  registration_keys  — one-time keys issued to admins for Agent bootstrap.
                       Each key is stored as sha256(plaintext), never as
                       plaintext. Single-use and time-limited (30 minutes).

  agents             — registered Agent records. Auth tokens stored as
                       sha256(plaintext); plaintext returned exactly once at
                       registration and never stored. Revocation sets
                       status='REVOKED'; the row is kept for audit purposes.

DESIGN FOLLOWS evidence_store/store.py PATTERNS:
  - module-level singleton via get_agent_store() / reset_agent_store()
  - threading.Lock() guards every write (sufficient for single-process demo)
  - check_same_thread=False on sqlite3.connect()
  - _init_schema() is idempotent (CREATE TABLE IF NOT EXISTS)
  - defaults taken inside get_agent_store(), not at module load, for test isolation
"""

from __future__ import annotations

import hashlib
import logging
import secrets
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Optional
from uuid import uuid4

from agent_store.models import Agent, AgentStatus, RegistrationKey

logger = logging.getLogger("agent_store.store")

_KEY_TTL_MINUTES = 30
from runtime_paths import data_root as _data_root
_DEFAULT_DB_PATH = _data_root() / "agents.db"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _agent_id_from_uuid() -> str:
    """Generate a human-readable Agent ID like VERITAS-AGENT-7F82A1."""
    return "VERITAS-AGENT-" + uuid4().hex[:6].upper()


def _dt(value: Optional[str]) -> Optional[datetime]:
    """Parse ISO8601 string from SQLite into a timezone-aware datetime."""
    if value is None:
        return None
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


# ---------------------------------------------------------------------------
# AgentStore
# ---------------------------------------------------------------------------

class AgentStore:
    """
    SQLite-backed store for registration keys and Agent records.
    All writes are guarded by a threading.Lock; reads are not (SQLite WAL
    is not enabled here, but read-only contention is acceptable for demo).
    """

    def __init__(self, db_path: str | Path = _DEFAULT_DB_PATH) -> None:
        self.db_path = Path(db_path)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        with self._lock:
            self._conn.executescript("""
                CREATE TABLE IF NOT EXISTS registration_keys (
                    key_id      TEXT PRIMARY KEY,
                    org_id      TEXT NOT NULL,
                    key_hash    TEXT NOT NULL UNIQUE,
                    created_at  TEXT NOT NULL,
                    expires_at  TEXT NOT NULL,
                    used        INTEGER NOT NULL DEFAULT 0,
                    used_at     TEXT
                );

                CREATE TABLE IF NOT EXISTS agents (
                    agent_id            TEXT PRIMARY KEY,
                    org_id              TEXT NOT NULL,
                    token_hash          TEXT NOT NULL UNIQUE,
                    status              TEXT NOT NULL DEFAULT 'ACTIVE',
                    source_label        TEXT NOT NULL DEFAULT '',
                    created_at          TEXT NOT NULL,
                    last_heartbeat_at   TEXT,
                    events_received     INTEGER NOT NULL DEFAULT 0
                );

                CREATE INDEX IF NOT EXISTS idx_agents_org_id ON agents(org_id);
                CREATE INDEX IF NOT EXISTS idx_keys_org_id ON registration_keys(org_id);
            """)
            self._conn.commit()

    # ------------------------------------------------------------------
    # Registration keys
    # ------------------------------------------------------------------

    def issue_key(self, org_id: str) -> str:
        """
        Generate a one-time registration key for org_id.
        Stores sha256(key) — never the plaintext.
        Returns the plaintext key (show once to admin, then it's gone).
        """
        plaintext = secrets.token_urlsafe(16)
        key_hash = _sha256(plaintext)
        key_id = str(uuid4())
        now = _now()
        expires = now + timedelta(minutes=_KEY_TTL_MINUTES)

        with self._lock:
            self._conn.execute(
                """
                INSERT INTO registration_keys
                    (key_id, org_id, key_hash, created_at, expires_at, used, used_at)
                VALUES (?, ?, ?, ?, ?, 0, NULL)
                """,
                (key_id, org_id, key_hash, now.isoformat(), expires.isoformat()),
            )
            self._conn.commit()

        logger.info("Issued registration key %s for org %r (expires %s)", key_id, org_id, expires.isoformat())
        return plaintext

    def consume_key(self, plaintext_key: str) -> RegistrationKey:
        """
        Validate and consume a one-time registration key.
        Raises ValueError if the key is unknown, already used, or expired.
        Marks the key as used atomically inside the write lock.
        """
        key_hash = _sha256(plaintext_key)

        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM registration_keys WHERE key_hash = ?",
                (key_hash,),
            ).fetchone()

            if row is None:
                raise ValueError("Registration key is invalid or does not exist.")

            if row["used"]:
                raise ValueError("Registration key has already been used.")

            expires_at = _dt(row["expires_at"])
            if _now() > expires_at:
                raise ValueError(
                    f"Registration key expired at {expires_at.isoformat()}."
                )

            # Mark used atomically while holding the lock
            self._conn.execute(
                "UPDATE registration_keys SET used = 1, used_at = ? WHERE key_id = ?",
                (_now().isoformat(), row["key_id"]),
            )
            self._conn.commit()

        return RegistrationKey(
            key_id=row["key_id"],
            org_id=row["org_id"],
            created_at=_dt(row["created_at"]),
            expires_at=expires_at,
            used=True,
        )

    # ------------------------------------------------------------------
    # Agent creation & lookup
    # ------------------------------------------------------------------

    def create_agent(self, org_id: str, source_label: str = "") -> tuple[Agent, str]:
        """
        Create a new Agent for org_id.
        Returns (Agent, plaintext_auth_token). The plaintext token is returned
        exactly once — it is NOT stored. Only sha256(token) is persisted.
        """
        plaintext_token = secrets.token_urlsafe(32)
        token_hash = _sha256(plaintext_token)
        agent_id = _agent_id_from_uuid()
        now = _now()

        with self._lock:
            self._conn.execute(
                """
                INSERT INTO agents
                    (agent_id, org_id, token_hash, status, source_label,
                     created_at, last_heartbeat_at, events_received)
                VALUES (?, ?, ?, 'ACTIVE', ?, ?, NULL, 0)
                """,
                (agent_id, org_id, token_hash, source_label, now.isoformat()),
            )
            self._conn.commit()

        agent = Agent(
            agent_id=agent_id,
            org_id=org_id,
            status=AgentStatus.ACTIVE,
            source_label=source_label,
            created_at=now,
        )
        logger.info("Created agent %s for org %r", agent_id, org_id)
        return agent, plaintext_token

    def validate_token(self, plaintext_token: str) -> Optional[Agent]:
        """
        Look up an Agent by its auth token.
        Returns the Agent (regardless of status — caller checks status).
        Returns None if the token doesn't match any agent.
        """
        token_hash = _sha256(plaintext_token)
        row = self._conn.execute(
            "SELECT * FROM agents WHERE token_hash = ?",
            (token_hash,),
        ).fetchone()

        if row is None:
            return None

        return _row_to_agent(row)

    def get_agent(self, agent_id: str) -> Optional[Agent]:
        """Look up an Agent by its ID."""
        row = self._conn.execute(
            "SELECT * FROM agents WHERE agent_id = ?",
            (agent_id,),
        ).fetchone()
        return _row_to_agent(row) if row else None

    def list_agents(self, org_id: Optional[str] = None) -> List[Agent]:
        """List all agents, optionally filtered by org_id."""
        if org_id is not None:
            rows = self._conn.execute(
                "SELECT * FROM agents WHERE org_id = ? ORDER BY created_at",
                (org_id,),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM agents ORDER BY created_at"
            ).fetchall()
        return [_row_to_agent(r) for r in rows]

    # ------------------------------------------------------------------
    # Agent mutations
    # ------------------------------------------------------------------

    def revoke_agent(self, agent_id: str) -> bool:
        """
        Set agent status to REVOKED.
        Returns True if the agent was found and revoked, False if not found.
        """
        with self._lock:
            cursor = self._conn.execute(
                "UPDATE agents SET status = 'REVOKED' WHERE agent_id = ?",
                (agent_id,),
            )
            self._conn.commit()

        found = cursor.rowcount > 0
        if found:
            logger.info("Revoked agent %s", agent_id)
        return found

    def record_heartbeat(self, agent_id: str) -> None:
        """Update last_heartbeat_at for an agent."""
        with self._lock:
            self._conn.execute(
                "UPDATE agents SET last_heartbeat_at = ? WHERE agent_id = ?",
                (_now().isoformat(), agent_id),
            )
            self._conn.commit()

    def increment_events(self, agent_id: str) -> None:
        """Increment events_received counter for an agent."""
        with self._lock:
            self._conn.execute(
                "UPDATE agents SET events_received = events_received + 1 WHERE agent_id = ?",
                (agent_id,),
            )
            self._conn.commit()

    def close(self) -> None:
        self._conn.close()


# ---------------------------------------------------------------------------
# Row → model helper
# ---------------------------------------------------------------------------

def _row_to_agent(row: sqlite3.Row) -> Agent:
    return Agent(
        agent_id=row["agent_id"],
        org_id=row["org_id"],
        status=AgentStatus(row["status"]),
        source_label=row["source_label"],
        created_at=_dt(row["created_at"]),
        last_heartbeat_at=_dt(row["last_heartbeat_at"]),
        events_received=row["events_received"],
    )


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_store: Optional[AgentStore] = None


def get_agent_store(db_path: Optional[str | Path] = None) -> AgentStore:
    """
    Return the module-level AgentStore singleton, lazily initialised.
    db_path defaults are evaluated fresh on each call (not at import time)
    so tests can override with tmp_path before the first call.
    """
    global _store
    if _store is None:
        _store = AgentStore(db_path or _DEFAULT_DB_PATH)
    return _store


def reset_agent_store() -> None:
    """Close and discard the singleton. Used in tests for isolation."""
    global _store
    if _store is not None:
        try:
            _store.close()
        except Exception:
            pass
        _store = None
