"""
Veritas DPDPA Agent — Integration API Layer (Phase 5)
=========================================================
Three integration modes, ONE detection+rule-engine path underneath — no
logic duplication between them:

  A1. Stream/Telemetry mode  — POST /v1/{org_id}/events
      Generalizes what the v2 build's ingestion was: before this phase,
      there was no callable HTTP ingestion endpoint at all — Blinkit-
      simulated telemetry was pushed directly into an in-process
      asyncio.Queue by ingestion/log_generator.py and ingestion/
      api_generator.py (see run_pipeline.py), never via an HTTP route a
      real external system could call. This endpoint is genuinely new,
      not a generalization of a prior route — flagged explicitly since
      the plan assumed one already existed to relocate.

  A2. Synchronous scan API  — POST /v1/{org_id}/scan
      Stateless w.r.t. correctness: does not require any prior Stream-mode
      event to function. See ScanRequest/_event_from_scan_request for how
      an ad-hoc scan request becomes a well-formed Event.

  Both A1 and A2 funnel through the SAME `_process_event()` helper below,
  which calls the exact same detect_event() / evaluate_event() /
  explain_verdict() / EvidenceStore.append() functions Stream mode's
  run_pipeline.py has always used — untouched, per this phase's explicit
  "do not touch detection/rule-engine logic" instruction.

  A3's CLI PoC (veritas_scan_cli.py, repo root) calls THIS module's /scan
  route over real HTTP — it does not import or reimplement anything here.

  Also: POST /v1/orgs/{org_id}/config — a minimal config-upload endpoint
  (explicit non-goal exception: Phase 5 depends on being able to register
  an org live during a demo, so a minimal version is in scope; no auth, no
  diffing, no rollback UI, exactly as instructed).

WRITE-TO-EVIDENCE-STORE DECISION (Part A2.5, stated explicitly per the
plan's request): /scan WRITES to the Evidence Store whenever it produces a
violation verdict, exactly like Stream mode — "every violation lands in
the tamper-evident store for the auditor" applies regardless of which
integration mode found it. This is implemented identically for both
/events and /scan via the same _process_event() call (write_to_store=True
in both routes) — see Part B3's cross-check.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import uuid4

from fastapi import APIRouter, Body, HTTPException
from pydantic import BaseModel, Field, model_validator

from config_loader import OrgConfigNotFoundError, load_org_config
from dashboard.live_feed import publish as publish_live_feed
from detection.engine import detect_event
from detection.models import DetectedEvent, MatchedEntity
from evidence_store.store import get_store
from llm_explainer.explainer import ExplainedVerdict, explain_verdict
from masking import mask_fields, mask_text
from org_config.store import list_registered_orgs, upload_org_config as store_upload_org_config
from registry.loader import reload_org_config
from rules.engine import evaluate_event
from schemas.models import Event, RuleId, SourceType, Verdict

logger = logging.getLogger("api.integration")

router = APIRouter(prefix="/v1", tags=["integration"])

# Sentinel source_system for scan requests that don't declare a real one.
# Never matches any org's actual registered source_system, so
# get_registry_entry() always misses for it — which is EXACTLY the
# "graceful degradation" the plan asks for (see module docstring on
# ScanRequest below): the existing, UNMODIFIED rule engine's Design
# Decision #2 (Check 2, rules/engine.py) already treats an unregistered
# field as a real PURPOSE_001 finding rather than crashing or fabricating
# a Retention verdict off a meaningless timestamp. No new rule-engine
# logic was written to achieve this — reusing what Phase 4 already built.
ADHOC_SCAN_SOURCE_SYSTEM = "adhoc_scan"


# ---------------------------------------------------------------------------
# Shared helper: org existence check
# ---------------------------------------------------------------------------

def _require_org_config(org_id: str):
    """
    Shared by every /v1/{org_id}/... route (Part A1's requirement,
    reused for A2 too since both need the identical check). Returns the
    loaded OrgConfig on success; raises a 404 with an actionable message
    on an unknown org_id — never lets an unknown org's events reach
    detection/the rule engine only to fail deeper in the pipeline.
    """
    try:
        return load_org_config(org_id)
    except OrgConfigNotFoundError:
        raise HTTPException(
            status_code=404,
            detail=(
                f"org_id {org_id!r} has no registered config. "
                f"Register one first via POST /v1/orgs/{org_id}/config."
            ),
        )


# ---------------------------------------------------------------------------
# Shared engine call — used by BOTH /events and /scan
# ---------------------------------------------------------------------------

async def _process_event(event: Event, *, write_to_store: bool) -> Dict[str, Any]:
    """
    THE one shared path: detect -> evaluate -> (explain -> store -> live
    feed) per verdict. Both /events and /scan call this with their own
    synthesized Event and nothing else — this is what "one engine, three
    integration points" means concretely.
    """
    detected: DetectedEvent = detect_event(event)
    verdicts: List[Verdict] = evaluate_event(detected)

    store = get_store() if write_to_store else None
    explained_and_stored: List[tuple[ExplainedVerdict, bool]] = []

    for v in verdicts:
        explained = explain_verdict(v)
        stored = False
        if store is not None:
            try:
                store.append(explained)
                stored = True
            except Exception as exc:
                logger.error("Failed to store verdict %s: %s", v.verdict_id, exc)
        explained_and_stored.append((explained, stored))

        # Same live-feed payload shape run_pipeline.py's Stream-mode
        # broadcaster already builds (see dashboard/live_feed.py) — so a
        # /scan-detected verdict renders identically to a /events-detected
        # one in the dashboard's Live Feed (Part B3).
        await publish_live_feed({
            **explained.verdict.model_dump(mode="json"),
            "explanation": explained.explanation,
            "section_cited": explained.section_cited,
            "confidence": explained.confidence,
            "used_fallback": explained.used_fallback,
        })

    return {"detected": detected, "explained": explained_and_stored}


def _verdict_summary(explained: ExplainedVerdict, stored: bool) -> Dict[str, Any]:
    """
    JSON-safe verdict summary for API responses. Deliberately does NOT
    include any raw PII value — Verdict never carried raw matched text to
    begin with (only field NAMES, categories, and registry metadata), so
    this is safe to return/log as-is.
    """
    v = explained.verdict
    return {
        "verdict_id": str(v.verdict_id),
        "rule_id": v.rule_id.value,
        "severity": v.severity.value,
        "field": v.field,
        "source_system": v.source_system,
        "breach_notification_candidate": v.breach_notification_candidate,
        "matched_registry_entry": v.matched_registry_entry,
        "explanation": explained.explanation,
        "section_cited": explained.section_cited,
        "stored_in_evidence_store": stored,
    }


def _entity_summary(m: MatchedEntity) -> Dict[str, Any]:
    """
    Entity summary WITHOUT matched_text — the whole point of this API
    (and the CLI built on top of it) is that raw PII never has to leave
    the response as cleartext for the caller to know what was found.
    """
    return {
        "field": m.field,
        "entity_type": m.entity_type,
        "confidence": m.confidence,
        "validation_status": m.validation_status,
    }


# ---------------------------------------------------------------------------
# A1 — Stream/Telemetry mode
# ---------------------------------------------------------------------------

class IngestEventRequest(BaseModel):
    source_type: SourceType
    source_system: str = Field(..., min_length=1)
    timestamp: Optional[datetime] = None
    raw_snippet: str = Field(..., min_length=1)
    fields: Dict[str, str] = Field(default_factory=dict)


@router.post("/{org_id}/events")
async def ingest_event(org_id: str, req: IngestEventRequest) -> Dict[str, Any]:
    """
    Stream/Telemetry mode. org_id comes ONLY from the URL path — never
    inferred from any default/config/global — and is what gets threaded
    into the synthesized Event's tenant_id, and from there into every
    downstream check, the Evidence Store write, and the live-feed
    broadcast room.
    """
    _require_org_config(org_id)

    event = Event(
        tenant_id=org_id,
        event_id=uuid4(),
        source_type=req.source_type,
        source_system=req.source_system,
        timestamp=req.timestamp or datetime.now(timezone.utc),
        raw_snippet=req.raw_snippet,
        fields=req.fields,
    )
    result = await _process_event(event, write_to_store=True)
    detected: DetectedEvent = result["detected"]

    return {
        "org_id": org_id,
        "event_id": str(event.event_id),
        "contains_pii": detected.contains_pii,
        "entities": [_entity_summary(m) for m in detected.matched_entities],
        "verdicts": [_verdict_summary(ev, stored) for ev, stored in result["explained"]],
    }


# ---------------------------------------------------------------------------
# A2 — Synchronous scan API
# ---------------------------------------------------------------------------

class ScanRequest(BaseModel):
    """
    Accepts `text` (unstructured — e.g. a pasted log line), `fields`
    (structured — matching the org's declared field names), or both.

    PRECEDENCE WHEN BOTH ARE GIVEN (documented per the plan's explicit
    request): both are processed and results are merged — detection runs
    over `fields` AND over `text`, deduplicated exactly the way Phase 3's
    detect_event() already deduplicates a structured field's value against
    its own serialized appearance in raw_snippet (see detection/engine.py).
    No new dedup logic was written for this — the existing engine already
    does the right thing when given both a `fields` dict and a `raw_snippet`
    string, which is exactly what this endpoint constructs.

    source_type CHOICE WHEN BOTH ARE GIVEN: `fields` presence means API
    (SourceType.API) even if `text` is also present — text-only means LOG
    (SourceType.LOG). Rationale: SourceType.LOG unconditionally satisfies
    Check 1 (Exposure) for EVERY match in the event, per the rule engine's
    own design (see rules/engine.py) — that's the right call for a bare
    pasted log line (the whole point of the log-exposure rule), but would
    be a surprising, overly aggressive side effect if it ALSO applied to
    legitimately-declared `fields` just because the caller happened to
    also paste an accompanying text blob in the same request.
    """
    text: Optional[str] = None
    fields: Optional[Dict[str, str]] = None
    source_system: Optional[str] = None

    @model_validator(mode="after")
    def _require_at_least_one_input(self) -> "ScanRequest":
        if not self.text and not self.fields:
            raise ValueError("At least one of 'text' or 'fields' must be provided.")
        return self


def _event_from_scan_request(org_id: str, req: ScanRequest) -> Event:
    fields = req.fields or {}
    only_text = bool(req.text) and not fields
    if req.text:
        raw_snippet = req.text
    else:
        # No text at all: raw_snippet must still be non-empty (Event's
        # schema requires it) — serialize fields, matching the existing
        # convention that API-sourced raw_snippet holds the JSON payload
        # (see detection/engine.py's module docstring on this exact point).
        raw_snippet = json.dumps(fields)

    return Event(
        tenant_id=org_id,
        event_id=uuid4(),
        source_type=SourceType.LOG if only_text else SourceType.API,
        source_system=req.source_system or ADHOC_SCAN_SOURCE_SYSTEM,
        timestamp=datetime.now(timezone.utc),
        raw_snippet=raw_snippet,
        fields=fields,
    )


@router.post("/{org_id}/scan")
async def scan(org_id: str, req: ScanRequest) -> Dict[str, Any]:
    """
    Synchronous scan — stateless w.r.t. correctness (no prior Stream-mode
    event required). See module docstring for the write-to-Evidence-Store
    decision (yes, same as Stream mode) and ScanRequest's docstring for the
    text+fields precedence rule.

    GRACEFUL DEGRADATION (Part A2.4): a scan request with no declared/
    recognized `source_system` gets ADHOC_SCAN_SOURCE_SYSTEM, which never
    matches any org's registry — every field is then correctly treated as
    "unregistered" by the UNCHANGED rule engine (Design Decision #2, Check
    2): it produces a real PURPOSE_001 finding rather than either crashing
    or fabricating a false RETENTION_001 off an age that has no real
    meaning for a bare pasted value. If the caller DOES pass a real
    `source_system` matching their own org's config, Checks 2/3 resolve
    against real registry entries exactly as they would for Stream mode.
    """
    _require_org_config(org_id)

    event = _event_from_scan_request(org_id, req)
    result = await _process_event(event, write_to_store=True)
    detected: DetectedEvent = result["detected"]
    explained_and_stored = result["explained"]

    all_matches = detected.matched_entities
    verdict_summaries = [_verdict_summary(ev, stored) for ev, stored in explained_and_stored]
    linkage_risks = [
        s for s, (ev, _) in zip(verdict_summaries, explained_and_stored)
        if ev.verdict.rule_id == RuleId.LINKAGE_001
    ]

    response: Dict[str, Any] = {
        "org_id": org_id,
        "event_id": str(event.event_id),
        "contains_pii": detected.contains_pii,
        "entities": [_entity_summary(m) for m in all_matches],
        "linkage_risks": linkage_risks,
        "verdicts": verdict_summaries,
    }
    if req.text is not None:
        response["masked_text"] = mask_text(req.text, all_matches)
    if req.fields:
        response["masked_fields"] = mask_fields(req.fields, all_matches)
    return response


# ---------------------------------------------------------------------------
# Org listing (powers the dashboard's org-selector — Part B2)
# ---------------------------------------------------------------------------

@router.get("/orgs")
async def list_orgs() -> Dict[str, Any]:
    """Every org_id with at least one stored config version. Read-only,
    no auth — same posture as the rest of this phase (see module docstring
    on the auth non-goal)."""
    return {"org_ids": list_registered_orgs()}


# ---------------------------------------------------------------------------
# Minimal org config upload (explicit non-goal exception — see module docstring)
# ---------------------------------------------------------------------------

@router.post("/orgs/{org_id}/config")
async def upload_org_config_endpoint(org_id: str, config: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    """
    Minimal config registration: validates via Phase 1's own
    validate_org_config (called internally by org_config.store.
    upload_org_config), stores it if valid, and returns success/errors.
    No auth, no diffing, no rollback UI — exactly as scoped.

    On success, immediately busts registry.loader's in-memory config cache
    for this org_id (reload_org_config) so the NEXT request against this
    org_id — /v1/{org_id}/events or /v1/{org_id}/scan — sees the new/
    updated config with no server restart, which is the whole point of
    supporting live org registration during a demo.
    """
    result = store_upload_org_config(org_id, config)
    if result["status"] == "ok":
        reload_org_config(org_id)
        logger.info("Registered/updated org config for org_id=%r, reloaded into rule engine cache.", org_id)
    return result
