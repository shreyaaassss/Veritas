"""
DPDPA Compliance Agent — Evidence Store (Phase 6)
==================================================
Append-only, SHA-256 hash-chained SQLite store for ExplainedVerdict objects.

DESIGN DECISIONS (documented per the plan's requirement):

1. IMMUTABILITY + MUTABILITY SPLIT:
   `remediation_status` and `remediation_updated_at` live in SEPARATE COLUMNS
   outside the hashed payload. The hash covers everything else — the full
   ExplainedVerdict JSON serialised deterministically. Updating remediaton
   status NEVER changes the stored hash, so verify_chain() passes before and
   after a status update. This is the simplest correct option.

2. HASH CHAIN:
   Each row stores sha256(json(immutable_fields) + previous_row_hash).
   The genesis hash (first row's "previous") is the fixed string GENESIS_HASH.
   Any retroactive tamper with a past row's immutable content is detectable
   by replaying the chain from row 1.

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

# The fixed genesis string hashed as the "previous hash" for the very first row.
# Hard-coded here so any implementation that reconstructs the chain can verify
# the first row without needing a separate genesis record.
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
        "verdict_id": str(verdict.verdict_id),
        "event_id": str(verdict.event_id),
        "rule_id": verdict.rule_id.value,
        "severity": verdict.severity.value,
        "source": verdict.source.value,
        "source_system": verdict.source_system.value,
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
    Append-only, hash-chained SQLite Evidence Store.

    Public interface:
        append(ev: ExplainedVerdict) -> int (row_index, 0-based)
        verify_chain() -> dict with {valid, first_broken_index}
        update_status(verdict_id, new_status) -> None (raises on invalid transition)
        query(...) -> list[dict]

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
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_verdict_id ON evidence(verdict_id)"
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_remediation_status ON evidence(remediation_status)"
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_source_system ON evidence(payload_json)"
            )
            self._conn.commit()

    def _get_last_hash(self) -> str:
        """Returns the last row's hash, or GENESIS_HASH if the table is empty."""
        cursor = self._conn.execute(
            "SELECT row_hash FROM evidence ORDER BY row_index DESC LIMIT 1"
        )
        row = cursor.fetchone()
        return row["row_hash"] if row else GENESIS_HASH

    def append(self, ev: ExplainedVerdict) -> int:
        """
        Append an ExplainedVerdict to the store. Returns the 0-based row_index
        of the newly inserted row.
        Raises sqlite3.IntegrityError if verdict_id already exists (duplicate).
        """
        with self._lock:
            previous_hash = self._get_last_hash()
            payload_json = _serialize_immutable(ev)
            row_hash = _compute_hash(payload_json, previous_hash)
            appended_at = datetime.now(timezone.utc).isoformat()

            self._conn.execute("""
                INSERT INTO evidence
                    (verdict_id, payload_json, row_hash, previous_hash,
                     remediation_status, remediation_updated_at, appended_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                str(ev.verdict.verdict_id),
                payload_json,
                row_hash,
                previous_hash,
                ev.verdict.remediation_status.value,
                ev.verdict.remediation_updated_at.isoformat() if ev.verdict.remediation_updated_at else None,
                appended_at,
            ))
            self._conn.commit()

            cursor = self._conn.execute(
                "SELECT row_index FROM evidence WHERE verdict_id = ?",
                (str(ev.verdict.verdict_id),)
            )
            return cursor.fetchone()["row_index"] - 1  # convert to 0-based

    def verify_chain(self) -> dict:
        """
        Recompute every row's expected hash from its stored payload_json +
        previous_hash and compare against the stored row_hash.

        Returns:
            {"valid": True, "first_broken_index": None}  — clean chain
            {"valid": False, "first_broken_index": N}    — first broken row (0-based)

        This is what the auditor runs as monthly sign-off.
        """
        cursor = self._conn.execute(
            "SELECT row_index, payload_json, row_hash, previous_hash "
            "FROM evidence ORDER BY row_index ASC"
        )
        rows = cursor.fetchall()

        expected_previous = GENESIS_HASH
        for i, row in enumerate(rows):
            expected_hash = _compute_hash(row["payload_json"], expected_previous)
            if expected_hash != row["row_hash"]:
                logger.warning(
                    "Chain broken at 0-based index %d (row_index=%d)", i, row["row_index"]
                )
                return {"valid": False, "first_broken_index": i}
            if row["previous_hash"] != expected_previous:
                logger.warning(
                    "previous_hash mismatch at 0-based index %d", i
                )
                return {"valid": False, "first_broken_index": i}
            expected_previous = row["row_hash"]

        return {"valid": True, "first_broken_index": None}

    def update_status(self, verdict_id: str, new_status: str) -> None:
        """
        Update remediation_status for a verdict. Only valid transitions:
            OPEN → ACKNOWLEDGED → RESOLVED

        Raises:
            ValueError if verdict not found.
            ValueError if the transition is not allowed.

        IMMUTABILITY PRESERVED: this only touches `remediation_status` and
        `remediation_updated_at` columns — never `payload_json` or `row_hash`.
        """
        new_status = new_status.upper()
        if new_status not in ("OPEN", "ACKNOWLEDGED", "RESOLVED"):
            raise ValueError(f"Invalid status: {new_status!r}")

        with self._lock:
            cursor = self._conn.execute(
                "SELECT remediation_status FROM evidence WHERE verdict_id = ?",
                (verdict_id,)
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
                "WHERE verdict_id = ?",
                (new_status, updated_at, verdict_id)
            )
            self._conn.commit()
            logger.info(
                "Updated verdict %s status: %s → %s", verdict_id, current, new_status
            )

    def query(
        self,
        date_range: tuple[str, str] | None = None,
        source_system: str | None = None,
        severity: str | None = None,
        remediation_status: str | None = None,
    ) -> list[dict]:
        """
        Query the evidence store. All filters are optional and ANDed together.

        date_range: (start_iso, end_iso) inclusive strings filtering on the
                    verdict's timestamp (parsed from payload_json).
        source_system: filter on source_system value in payload_json.
        severity: filter on severity value in payload_json.
        remediation_status: filter on the mutable remediation_status column.

        Returns a list of dicts with all stored fields + parsed payload_json fields.
        """
        where_clauses = []
        params: list = []

        if remediation_status:
            where_clauses.append("remediation_status = ?")
            params.append(remediation_status.upper())

        base_sql = "SELECT * FROM evidence"
        if where_clauses:
            base_sql += " WHERE " + " AND ".join(where_clauses)
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

    def get_by_verdict_id(self, verdict_id: str) -> Optional[dict]:
        """Return a single row by verdict_id, or None if not found."""
        cursor = self._conn.execute(
            "SELECT * FROM evidence WHERE verdict_id = ?", (verdict_id,)
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

    def count(self) -> int:
        """Return total number of rows in the store."""
        cursor = self._conn.execute("SELECT COUNT(*) as n FROM evidence")
        return cursor.fetchone()["n"]

    def close(self) -> None:
        """Close the SQLite connection."""
        self._conn.close()


# ---------------------------------------------------------------------------
# Module-level singleton (used by the server / pipeline)
# ---------------------------------------------------------------------------

_store: Optional[EvidenceStore] = None


def get_store(db_path: str | Path = _DEFAULT_DB_PATH) -> EvidenceStore:
    """Return (or lazily create) the module-level EvidenceStore singleton."""
    global _store
    if _store is None:
        _store = EvidenceStore(db_path=db_path)
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
