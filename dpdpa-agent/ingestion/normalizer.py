"""
DPDPA Compliance Agent — Event Normalizer
============================================
Converts each generator's raw output shape into the EXACT Phase 0 Event
schema, and validates every event against the Pydantic Event model before
it is allowed onto the shared queue.

DESIGN DECISION — raw_snippet for API events:
  For API-sourced events, raw_snippet holds the raw JSON payload
  serialized as a string (not left empty). Rationale: Phase 3's PII
  detection and Phase 6's Evidence Store both want a human/audit-readable
  "what actually happened" record, and support_tickets-style free-text
  scanning (per Phase 1's registry notes) generalizes better if
  raw_snippet is never empty for any event, regardless of source_type.

VALIDATION CONTRACT:
  If normalization ever produces a schema-invalid Event, that is a BUG in
  this phase — not something Phase 3/4 should have to defend against.
  normalize_* functions raise on invalid input from generators (a
  programmer error), but validate_event() below is the last-line check
  used by the generator loops: it logs + drops rather than crashing the
  loop, per the plan's "fail loudly, don't crash" instruction.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from pydantic import ValidationError

from schemas.models import Event, SourceSystem, SourceType

logger = logging.getLogger("ingestion.normalizer")


def _new_event_id() -> str:
    return str(uuid.uuid4())


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_log_event(
    raw_line: str,
    source_system: SourceSystem,
    fields: Dict[str, str],
    timestamp: Optional[datetime] = None,
) -> Dict[str, Any]:
    """
    Normalize a raw log line into a Phase 0 Event-shaped dict.

    raw_snippet = the full log line as emitted.
    fields = parsed key-values extracted from that line by the caller
             (the log generator is responsible for parsing its own
             output shape; this function does not do PII detection).
    """
    return {
        "event_id": _new_event_id(),
        "source_type": SourceType.LOG.value,
        "source_system": source_system.value,
        "timestamp": (timestamp or datetime.now(timezone.utc)).isoformat(),
        "raw_snippet": raw_line,
        "fields": fields,
    }


def normalize_api_event(
    payload: Dict[str, Any],
    source_system: SourceSystem,
    fields: Dict[str, str],
    timestamp: Optional[datetime] = None,
) -> Dict[str, Any]:
    """
    Normalize a raw API request/response payload into a Phase 0
    Event-shaped dict.

    raw_snippet = the raw JSON payload, serialized to a string (see
    module docstring for why this is not left empty).
    fields = parsed key-values extracted from the payload by the caller.
    """
    return {
        "event_id": _new_event_id(),
        "source_type": SourceType.API.value,
        "source_system": source_system.value,
        "timestamp": (timestamp or datetime.now(timezone.utc)).isoformat(),
        "raw_snippet": json.dumps(payload, ensure_ascii=False),
        "fields": fields,
    }


def validate_event(event_dict: Dict[str, Any]) -> Optional[Event]:
    """
    Last-line validation before an event is allowed onto the shared queue.

    Returns the validated Event on success. On failure, logs loudly and
    returns None — callers (the generator loops) must drop the event and
    continue, never crash. A validation failure here indicates a bug in
    this phase's normalization logic, since generators should never be
    able to produce a schema-invalid dict in the first place.
    """
    try:
        return Event(**event_dict)
    except ValidationError as e:
        logger.error(
            "NORMALIZATION BUG: produced a schema-invalid Event — dropping it. "
            "event_dict=%s errors=%s",
            event_dict,
            e.errors(),
        )
        return None
