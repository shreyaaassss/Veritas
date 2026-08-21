"""
DPDPA Compliance Agent — Evidence Store (Phase 6, multi-tenant since Phase 5+6)
=================================================================================
Append-only, SHA-256 hash-chained SQLite store for ExplainedVerdict objects.

DESIGN DECISIONS (documented per the plan's requirement):

1. IMMUTABILITY + MUTABILITY SPLIT:
   `remediation_status` and `remediation_updated_at` live in SEPARATE COLUMNS
   outside the hashed payload. The hash covers everything else — the full
   ExplainedVerdict JSON serialised deterministically. Updating remediaton
   status NEVER changes the stored hash, so verify_chain() passes before and
   after a status update. This is the simplest correct option.

2. HASH CHAIN — NOW PER-TENANT (Phase 5+6 change):
   Each row stores sha256(json(immutable_fields) + previous_row_hash). Before
   this phase, "previous_row_hash" was the immediately preceding row GLOBALLY
   (SELECT ... ORDER BY row_index DESC LIMIT 1, no tenant filter) — meaning
   org A's rows and org B's rows were interleaved into ONE chain, so
   verifying org A's evidence required touching org B's rows too. That broke
   the tenant-isolation goal: an org's auditor could not verify their own
   evidence independent of another org's data (or its presence/absence).

   FIX: the "previous hash" a new row chains into is now the last row
   belonging to the SAME tenant_id (_get_last_hash(tenant_id) — see below),
   falling back to GENESIS_HASH if this is that tenant's first-ever row. Each
   org therefore has its own independent hash chain, embedded in the same
   physical table (still one SQLite file, one row_index sequence — rows from
   different tenants can be physically interleaved by insertion order — but
   logically chained only within their own tenant's row sequence).
   verify_chain(tenant_id) replays ONLY that tenant's rows, in row_index
   order, starting from GENESIS_HASH. This is a pure query/chaining-scope
   change — the hash algorithm, what fields get hashed, and the append-only
   property are all unchanged (explicit non-goal of this phase).

   MIGRATION NOTE: a `tenant_id` column did not previously exist. On an
   existing on-disk DB (created before this phase), `_init_schema()` adds it
   and backfills every existing row's tenant_id from its own payload_json
   (which has always carried `verdict.tenant_id`, per Phase 0's schema — see
   `_serialize_immutable` below). Existing rows' `row_hash`/`previous_hash`
   values are NOT recomputed (that would violate the append-only/tamper-
   evidence guarantee retroactively) — they keep whatever chain they were
   originally written with. In this codebase's actual history, every row
   written before this phase was blinkit's (Phase 5 is the first time a
   non-blinkit tenant's verdicts could ever reach this store), so the
   pre-existing chain is — and remains, unchanged — exactly blinkit's
   per-tenant chain; no discontinuity is introduced for the common case.

3. STATUS TRANSITIONS:
   Only OPEN→ACKNOWLEDGED and ACKNOWLEDGED→RESOLVED are permitted. Attempting
   to go backwards (e.g. RESOLVED→OPEN) or skip (OPEN→RESOLVED) is rejected
   with a ValueError. ACKNOWLEDGED→ACKNOWLEDGED is also explicitly rejected
   (idempotent updates aren't meaningful here).

4. SQLITE CONCURRENCY:
   check_same_thread=False + a module-level threading.Lock guard every write.
   Sufficient for the hackathon single-process model.

5. SCHEMA EVOLUTION:
   All immutable verdict fields + explanation fields are stored as a single
   JSON blob column (`payload_json`) for simplicity. The mutable fields are
   their own indexed columns so queries and updates touch only those.
   `tenant_id` is ALSO its own indexed column (denormalised out of
   payload_json) purely so every read path can filter/chain efficiently
   without parsing JSON per row.

6. TENANT SCOPING (Phase 5+6):
   Every read (and write-adjacent) method — append, verify_chain, query,
   get_by_verdict_id, update_status, count — now takes tenant_id as a
   REQUIRED parameter (append derives it from the verdict itself; the rest
   take it explicitly) and scopes its SQL accordingly. There is no "list
   across all tenants" method — that was a deliberate removal, not an
   oversight: any such method would be exactly the kind of cross-tenant leak
   surface this phase exists to close. A caller that genuinely needs a
   cross-tenant admin view would need a new, explicitly-named method — none
   of Part A/B's callers need one, so none was added.
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from llm_explainer.explainer import ExplainedVerdict
from schemas.models import RemediationStatus

logger = logging.getLogger("evidence_store.store")

# The fixed genesis string hashed as the "previous hash" for the very first
# row OF EACH TENANT'S OWN CHAIN (see module docstring, point 2). The same
# literal genesis string is reused per-tenant (it is not itself
# tenant-specific data) — each tenant's chain independently starts here.
GENESIS_HASH = "DPDPA-AGENT-GENESIS-2026"

# Allowed remediation status transitions (source → allowed targets)
_VALID_TRANSITIONS: dict[str, list[str]] = {
    "OPEN": ["ACKNOWLEDGED"],
    "ACKNOWLEDGED": ["RESOLVED"],
    "RESOLVED": [],  # terminal state
}

_DEFAULT_DB_PATH = Path(__file__).parent / "evidence.db"


def _serialize_immutable(ev: ExplainedVerdict) -> str:
    """
    Deterministic JSON serialization of the immutable portion of an
    ExplainedVerdict (everything except remediation_status and
    remediation_updated_at). This is what gets hashed.

    Uses sort_keys=True and no extra whitespace so the output is stable
    across Python versions and machine architectures. Uses `default=str`
    to handle datetime/UUID objects.
    """
    verdict = ev.verdict
    payload = {
        # Verdict immutable fields
        "tenant_id": verdict.tenant_id,
        "verdict_id": str(verdict.verdict_id),
        "event_id": str(verdict.event_id),
        "rule_id": verdict.rule_id.value,
        "severity": verdict.severity.value,
        "source": verdict.source.value,
        "source_system": verdict.source_system,
        "field": verdict.field,
        "timestamp": verdict.timestamp.isoformat(),
        "matched_registry_entry": verdict.matched_registry_entry,
        "breach_notification_candidate": verdict.breach_notification_candidate,
        # Explanation fields (also immutable post-write)
        "explanation": ev.explanation,
        "section_cited": ev.section_cited,
        "confidence": ev.confidence,
        "used_fallback": ev.used_fallback,
    }
    return json.dumps(payload, sort_keys=True, default=str)


def _compute_hash(payload_json: str, previous_hash: str) -> str:
    """sha256(payload_json + previous_hash) → hex digest."""
    content = payload_json + previous_hash
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


class EvidenceStore:
    """
    Append-only, hash-chained SQLite Evidence Store. Every chain, and every
    read, is scoped to a single tenant_id — see module docstring point 6.

    Public interface:
        append(ev: ExplainedVerdict) -> int (row_index, 0-based; tenant_id
            is read off ev.verdict.tenant_id, not passed separately)
        verify_chain(tenant_id: str) -> dict with {valid, first_broken_index}
        update_status(tenant_id: str, verdict_id: str, new_status: str) -> None
        query(tenant_id: str, ...) -> list[dict]
        get_by_verdict_id(tenant_id: str, verdict_id: str) -> Optional[dict]
        count(tenant_id: str) -> int

    Thread-safe for single-process use via a write lock.
    """

    def __init__(self, db_path: str | Path = _DEFAULT_DB_PATH) -> None:
        self.db_path = Path(db_path)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        with self._lock:
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS evidence (
                    row_index           INTEGER PRIMARY KEY AUTOINCREMENT,
                    verdict_id          TEXT NOT NULL UNIQUE,
                    payload_json        TEXT NOT NULL,
                    row_hash            TEXT NOT NULL,
                    previous_hash       TEXT NOT NULL,
                    remediation_status  TEXT NOT NULL DEFAULT 'OPEN',
                    remediation_updated_at TEXT,
                    appended_at         TEXT NOT NULL
                )
            """)
            self._migrate_add_tenant_id_column()
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_verdict_id ON evidence(verdict_id)"
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_remediation_status ON evidence(remediation_status)"
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_source_system ON evidence(payload_json)"
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_tenant_id ON evidence(tenant_id)"
            )
            self._conn.commit()

    def _migrate_add_tenant_id_column(self) -> None:
        """
        Phase 5+6 migration: add `tenant_id` if this DB predates it, and
        backfill existing rows from their own payload_json (which has always
        carried tenant_id — see _serialize_immutable). Does NOT touch
        row_hash/previous_hash — see module docstring point 2's migration
        note on why recomputing historical hashes would be wrong.
        Idempotent — safe to call on every startup.
        """
        existing_cols = {row["name"] for row in self._conn.execute("PRAGMA table_info(evidence)")}
        if "tenant_id" in existing_cols:
            return

        logger.info("Migrating evidence store: adding tenant_id column and backfilling from payload_json.")
        self._conn.execute("ALTER TABLE evidence ADD COLUMN tenant_id TEXT")
        rows = self._conn.execute("SELECT row_index, payload_json FROM evidence").fetchall()
        for row in rows:
            payload = json.loads(row["payload_json"])
            tenant_id = payload.get("tenant_id", "")
            self._conn.execute(
                "UPDATE evidence SET tenant_id = ? WHERE row_index = ?",
                (tenant_id, row["row_index"]),
            )
        if rows:
            logger.info("Backfilled tenant_id for %d existing row(s).", len(rows))

    def _get_last_hash(self, tenant_id: str) -> str:
        """Returns this tenant's last row's hash, or GENESIS_HASH if this
        tenant has no rows yet — the per-tenant chain start point."""
        cursor = self._conn.execute(
            "SELECT row_hash FROM evidence WHERE tenant_id = ? ORDER BY row_index DESC LIMIT 1",
            (tenant_id,),
        )
        row = cursor.fetchone()
        return row["row_hash"] if row else GENESIS_HASH

    def append(self, ev: ExplainedVerdict) -> int:
        """
        Append an ExplainedVerdict to the store. tenant_id is read from
        ev.verdict.tenant_id (never passed separately — there is exactly one
        source of truth for which tenant a verdict belongs to: the verdict
        itself). Chains into that tenant's own previous row (or GENESIS_HASH
        if this is their first). Returns the 0-based row_index of the new row.
        Raises sqlite3.IntegrityError if verdict_id already exists (duplicate).
        """
        tenant_id = ev.verdict.tenant_id
        with self._lock:
            previous_hash = self._get_last_hash(tenant_id)
            payload_json = _serialize_immutable(ev)
            row_hash = _compute_hash(payload_json, previous_hash)
            appended_at = datetime.now(timezone.utc).isoformat()

            self._conn.execute("""
                INSERT INTO evidence
                    (verdict_id, payload_json, row_hash, previous_hash,
                     remediation_status, remediation_updated_at, appended_at, tenant_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                str(ev.verdict.verdict_id),
                payload_json,
                row_hash,
                previous_hash,
                ev.verdict.remediation_status.value,
                ev.verdict.remediation_updated_at.isoformat() if ev.verdict.remediation_updated_at else None,
                appended_at,
                tenant_id,
            ))
            self._conn.commit()

            cursor = self._conn.execute(
                "SELECT row_index FROM evidence WHERE verdict_id = ?",
                (str(ev.verdict.verdict_id),)
            )
            return cursor.fetchone()["row_index"] - 1  # convert to 0-based

    def verify_chain(self, tenant_id: str) -> dict:
        """
        Recompute THIS TENANT's own chain — every row where tenant_id
        matches, in row_index order — and compare against stored hashes.
        Never touches or is affected by any other tenant's rows (see module
        docstring point 2): an org can independently verify its own evidence
        without needing access to, or trusting, another org's data.

        Returns:
            {"valid": True, "first_broken_index": None}  — clean chain
            {"valid": False, "first_broken_index": N}    — first broken row,
                0-based WITHIN THIS TENANT'S OWN row sequence (not the
                global row_index — a caller scoped to one tenant has no
                business reasoning about other tenants' row positions).

        This is what the auditor runs as monthly sign-off, for their own org.
        """
        cursor = self._conn.execute(
            "SELECT row_index, payload_json, row_hash, previous_hash "
            "FROM evidence WHERE tenant_id = ? ORDER BY row_index ASC",
            (tenant_id,),
        )
        rows = cursor.fetchall()

        expected_previous = GENESIS_HASH
        for i, row in enumerate(rows):
            expected_hash = _compute_hash(row["payload_json"], expected_previous)
            if expected_hash != row["row_hash"]:
                logger.warning(
                    "Chain broken for tenant_id=%r at tenant-local index %d (global row_index=%d)",
                    tenant_id, i, row["row_index"],
                )
                return {"valid": False, "first_broken_index": i}
            if row["previous_hash"] != expected_previous:
                logger.warning(
                    "previous_hash mismatch for tenant_id=%r at tenant-local index %d",
                    tenant_id, i,
                )
                return {"valid": False, "first_broken_index": i}
            expected_previous = row["row_hash"]

        return {"valid": True, "first_broken_index": None}

    def update_status(self, tenant_id: str, verdict_id: str, new_status: str) -> None:
        """
        Update remediation_status for a verdict — but ONLY if it belongs to
        tenant_id. A verdict_id belonging to a different tenant is treated
        identically to a nonexistent one (ValueError "Verdict not found") —
        deliberately not distinguished, so a caller cannot probe whether a
        given verdict_id exists under someone else's tenant.

        Only valid transitions: OPEN → ACKNOWLEDGED → RESOLVED

        Raises:
            ValueError if verdict not found (or not owned by tenant_id).
            ValueError if the transition is not allowed.

        IMMUTABILITY PRESERVED: this only touches `remediation_status` and
        `remediation_updated_at` columns — never `payload_json` or `row_hash`.
        """
        new_status = new_status.upper()
        if new_status not in ("OPEN", "ACKNOWLEDGED", "RESOLVED"):
            raise ValueError(f"Invalid status: {new_status!r}")

        with self._lock:
            cursor = self._conn.execute(
                "SELECT remediation_status FROM evidence WHERE verdict_id = ? AND tenant_id = ?",
                (verdict_id, tenant_id)
            )
            row = cursor.fetchone()
            if row is None:
                raise ValueError(f"Verdict not found: {verdict_id!r}")

            current = row["remediation_status"]
            allowed = _VALID_TRANSITIONS.get(current, [])
            if new_status not in allowed:
                raise ValueError(
                    f"Invalid status transition: {current!r} → {new_status!r}. "
                    f"Allowed: {allowed}"
                )

            updated_at = datetime.now(timezone.utc).isoformat()
            self._conn.execute(
                "UPDATE evidence SET remediation_status = ?, remediation_updated_at = ? "
                "WHERE verdict_id = ? AND tenant_id = ?",
                (new_status, updated_at, verdict_id, tenant_id)
            )
            self._conn.commit()
            logger.info(
                "Updated verdict %s (tenant_id=%r) status: %s → %s", verdict_id, tenant_id, current, new_status
            )

    def query(
        self,
        tenant_id: str,
        date_range: tuple[str, str] | None = None,
        source_system: str | None = None,
        severity: str | None = None,
        remediation_status: str | None = None,
    ) -> list[dict]:
        """
        Query the evidence store, scoped to tenant_id. All other filters are
        optional and ANDed together.

        date_range: (start_iso, end_iso) inclusive strings filtering on the
                    verdict's timestamp (parsed from payload_json).
        source_system: filter on source_system value in payload_json.
        severity: filter on severity value in payload_json.
        remediation_status: filter on the mutable remediation_status column.

        Returns a list of dicts with all stored fields + parsed payload_json fields.
        """
        where_clauses = ["tenant_id = ?"]
        params: list = [tenant_id]

        if remediation_status:
            where_clauses.append("remediation_status = ?")
            params.append(remediation_status.upper())

        base_sql = "SELECT * FROM evidence WHERE " + " AND ".join(where_clauses)
        base_sql += " ORDER BY row_index ASC"

        cursor = self._conn.execute(base_sql, params)
        rows = cursor.fetchall()

        results = []
        for row in rows:
            payload = json.loads(row["payload_json"])

            # Apply payload-level filters (can't use SQL efficiently on JSON blob)
            if source_system and payload.get("source_system") != source_system:
                continue
            if severity and payload.get("severity") != severity.upper():
                continue
            if date_range:
                ts = payload.get("timestamp", "")
                start, end = date_range
                if not (start <= ts <= end + "z"):  # ISO string comparison works lexicographically
                    continue

            result = {
                "row_index": row["row_index"] - 1,  # 0-based
                "row_hash": row["row_hash"],
                "appended_at": row["appended_at"],
                "remediation_status": row["remediation_status"],
                "remediation_updated_at": row["remediation_updated_at"],
                **payload,
            }
            results.append(result)

        return results

    def get_by_verdict_id(self, tenant_id: str, verdict_id: str) -> Optional[dict]:
        """Return a single row by verdict_id, scoped to tenant_id. Returns
        None both when the verdict_id doesn't exist AND when it exists but
        belongs to a different tenant — same non-distinguishing behavior as
        update_status, for the same reason."""
        cursor = self._conn.execute(
            "SELECT * FROM evidence WHERE verdict_id = ? AND tenant_id = ?", (verdict_id, tenant_id)
        )
        row = cursor.fetchone()
        if row is None:
            return None
        payload = json.loads(row["payload_json"])
        return {
            "row_index": row["row_index"] - 1,
            "row_hash": row["row_hash"],
            "appended_at": row["appended_at"],
            "remediation_status": row["remediation_status"],
            "remediation_updated_at": row["remediation_updated_at"],
            **payload,
        }

    def count(self, tenant_id: str) -> int:
        """Return total number of rows in the store for tenant_id."""
        cursor = self._conn.execute("SELECT COUNT(*) as n FROM evidence WHERE tenant_id = ?", (tenant_id,))
        return cursor.fetchone()["n"]

    def close(self) -> None:
        """Close the SQLite connection."""
        self._conn.close()


# ---------------------------------------------------------------------------
# Module-level singleton (used by the server / pipeline)
# ---------------------------------------------------------------------------

_store: Optional[EvidenceStore] = None


def get_store(db_path: str | Path | None = None) -> EvidenceStore:
    """
    Return (or lazily create) the module-level EvidenceStore singleton.

    db_path defaults to None (not `_DEFAULT_DB_PATH` directly) so the
    module-level `_DEFAULT_DB_PATH` is looked up FRESH on every call
    rather than bound once at function-definition time (a Python default-
    argument gotcha: `def get_store(db_path=_DEFAULT_DB_PATH)` would
    capture whatever `_DEFAULT_DB_PATH` was when this module was first
    imported, silently ignoring any later `monkeypatch.setattr(store_
    module, "_DEFAULT_DB_PATH", ...)` a test tries to apply — which is
    exactly the isolation tests in api/test_integration.py rely on).
    """
    global _store
    if _store is None:
        _store = EvidenceStore(db_path=db_path if db_path is not None else _DEFAULT_DB_PATH)
    return _store


def reset_store() -> None:
    """Test-only: close and clear the singleton."""
    global _store
    if _store is not None:
        _store.close()
        _store = None


# ---------------------------------------------------------------------------
# Queue consumer — Phase 5's output → Evidence Store
# ---------------------------------------------------------------------------

async def store_from_queue(
    in_queue,
    store: Optional[EvidenceStore] = None,
    max_items: int | None = None,
) -> None:
    """
    Async consumer: pulls ExplainedVerdict objects off in_queue and appends
    each to the Evidence Store. Errors on individual items are logged and
    skipped (store is best-effort for the pipeline's liveness; evidence
    integrity is maintained for successfully-stored items).
    """
    if store is None:
        store = get_store()
    processed = 0
    while max_items is None or processed < max_items:
        ev = await in_queue.get()
        try:
            idx = store.append(ev)
            logger.info("Stored ExplainedVerdict at row_index=%d", idx)
        except Exception as exc:
            logger.error("Failed to store ExplainedVerdict: %s", exc)
        processed += 1
