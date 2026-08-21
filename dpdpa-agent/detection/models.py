"""
DPDPA Compliance Agent — Detected Event Model
=================================================
Wraps a Phase 0 Event with detection results, WITHOUT mutating the frozen
Event schema itself. Phase 4's Rule Engine (and any later phase) should
consume DetectedEvent, not raw Event, once this phase is wired in.
"""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field

from schemas.models import Event


class MatchedEntity(BaseModel):
    """
    A single PII entity match found within one event.

    field: the field_name this match came from. For matches found in a
    structured `fields` value, this is the real field name (e.g. "phone",
    "aadhaar") — the same field_name Phase 1's registry and Phase 4's rule
    engine key off of. For matches found only in free-text `raw_snippet`
    with no corresponding structured field, this is the literal string
    "raw_snippet" — see FREE_TEXT_FIELD_LABEL and the module note below on
    why no fancier field-name inference is attempted.
    """

    field: str = Field(..., description="Field name the match was found in, or 'raw_snippet' for free-text matches.")
    entity_type: str = Field(..., description="Presidio entity type, e.g. 'PHONE_NUMBER', 'IN_AADHAAR', 'PERSON'.")
    confidence: float = Field(..., ge=0.0, le=1.0, description="Presidio's match score, passed through unmodified.")
    matched_text: str = Field(..., description="The actual substring that matched, for audit/debug purposes.")


class DetectedEvent(BaseModel):
    """
    Wraps an Event with PII detection results. Does not mutate or extend
    the Event model itself — Event stays exactly as Phase 0 froze it.

    contains_pii / matched_entities are ALWAYS present (never omitted),
    per the plan's explicit instruction — contains_pii: false and
    matched_entities: [] for clean events, not absent fields.

    INTERFACE NOTE FOR PHASE 4:
    Phase 4's rule engine should short-circuit immediately on
    contains_pii == False — a clean event cannot violate EXPOSURE_001,
    PURPOSE_001, or RETENTION_001 in a way this agent can detect, since
    all three rules are keyed off PII fields. This short-circuit is NOT
    implemented here; Phase 3 only tags, Phase 4 decides what to do with
    the tag. Events with contains_pii == False still flow through the
    queue/pipeline untouched (pass-through), so Phase 4 can reason about
    the full stream if it ever needs to (e.g. for volume metrics).
    """

    event: Event
    contains_pii: bool = Field(..., description="True if at least one PII entity was matched anywhere in this event.")
    matched_entities: List[MatchedEntity] = Field(
        default_factory=list,
        description="All PII matches found. Empty list (not omitted) when contains_pii is False.",
    )


# The field label used for matches found only in free-text raw_snippet
# with no corresponding structured field. Presidio gives us character
# offsets into the text it analyzed, not a field name — for raw_snippet
# scans we have no structured field to map back to (that's the whole
# point of scanning raw_snippet: catching PII that "rode along" in free
# text rather than a clean key-value field, e.g. support_tickets notes).
# We deliberately do NOT attempt fuzzy mapping (e.g. guessing "this looks
# like it's near a 'phone:' label so call it 'phone'") because that
# heuristic would be unreliable and would blur the distinction Phase 4
# needs between "a declared field leaked" (clean registry lookup) and
# "PII surfaced in unstructured text with no declared field at all"
# (which itself is diagnostic — see detection/README.md).
FREE_TEXT_FIELD_LABEL = "raw_snippet"
