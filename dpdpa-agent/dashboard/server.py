"""
DPDPA Compliance Agent — Dashboard Server (Phase 7, multi-tenant since Phase 5+6)
====================================================================================
FastAPI server providing:
  - GET /                          — serves the dashboard HTML
  - WebSocket /ws/{org_id}         — live feed of ExplainedVerdicts for ONE org
  - GET /api/{org_id}/verdicts     — Evidence Store query, scoped to org_id
  - GET /api/{org_id}/verdicts/{verdict_id} — single verdict detail, scoped
  - POST /api/{org_id}/verdicts/{verdict_id}/status — update remediation_status, scoped
  - GET /api/{org_id}/verify-chain — run verify_chain(org_id) and return result
  - GET /api/{org_id}/stats        — summary counts, scoped to org_id
  - PLUS everything mounted from api/integration.py (Phase 5): POST
    /v1/{org_id}/events, POST /v1/{org_id}/scan, POST /v1/orgs/{org_id}/config

PHASE 6 CHANGE — every one of the dashboard's own routes above now takes
org_id as a required path parameter and scopes its EvidenceStore call
accordingly (see evidence_store/store.py's own Phase 6 doc comment: every
read method there now REQUIRES tenant_id). Before this phase, none of
these routes took any org_id at all — GET /api/verdicts queried across
every tenant's data unconditionally, and GET /ws broadcast every org's
verdicts to every connected client. Both were real cross-tenant leaks;
both are fixed here. See dashboard/live_feed.py for the WebSocket-room fix.

The dashboard has no authentication/session model of any kind (checked
before choosing an approach, per the plan's instruction) — so tenant
scoping here is done via an explicit org_id in the URL, matched by a
dropdown/org-selector in the front-end (index.html), not by any inferred
identity. Real auth is flagged as a demo-readiness gap in the phase
report, not built here (explicit non-goal).

Run with:
    python -m dashboard.server
    python -m dashboard.server --port 8080

Or from the pipeline (run_pipeline.py) which starts it programmatically.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from pathlib import Path
from typing import Any, Optional

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Query
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from api.integration import router as integration_router
from dashboard.live_feed import broadcaster_task, get_live_feed_queue, register_client, unregister_client
from evidence_store.store import get_store

logger = logging.getLogger("dashboard.server")

app = FastAPI(title="DPDPA Compliance Agent — Dashboard", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(integration_router)


# ---------------------------------------------------------------------------
# Routes — dashboard page + WebSocket
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
async def serve_dashboard():
    html_path = Path(__file__).parent / "index.html"
    return HTMLResponse(html_path.read_text(encoding="utf-8"))


@app.websocket("/ws/{org_id}")
async def websocket_endpoint(ws: WebSocket, org_id: str):
    """
    Live feed for exactly ONE org's verdicts. A client connected here for
    org_id="A" is registered only in dashboard.live_feed's "A" room and
    will never receive a payload whose tenant_id is anything else — see
    live_feed.broadcast()'s per-room routing.
    """
    await ws.accept()
    register_client(org_id, ws)
    logger.info("WebSocket client connected for org_id=%r.", org_id)
    try:
        while True:
            # Keep connection alive; data flows from broadcaster_task via publish().
            await asyncio.sleep(30)
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        unregister_client(org_id, ws)
        logger.info("WebSocket client disconnected for org_id=%r.", org_id)


# ---------------------------------------------------------------------------
# Routes — Evidence Store queries, all org_id-scoped
# ---------------------------------------------------------------------------

@app.get("/api/{org_id}/verdicts")
async def list_verdicts(
    org_id: str,
    source_system: Optional[str] = Query(None),
    severity: Optional[str] = Query(None),
    remediation_status: Optional[str] = Query(None),
    date_start: Optional[str] = Query(None),
    date_end: Optional[str] = Query(None),
):
    store = get_store()
    date_range = (date_start, date_end) if date_start and date_end else None
    rows = store.query(
        org_id,
        date_range=date_range,
        source_system=source_system,
        severity=severity,
        remediation_status=remediation_status,
    )
    return {"verdicts": rows, "count": len(rows)}


@app.get("/api/{org_id}/verdicts/{verdict_id}")
async def get_verdict(org_id: str, verdict_id: str):
    store = get_store()
    row = store.get_by_verdict_id(org_id, verdict_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"Verdict {verdict_id!r} not found for org_id {org_id!r}")
    return row


@app.get("/api/{org_id}/violations/{violation_id}")
async def get_violation_by_number(org_id: str, violation_id: int):
    """
    The "tag #N and ask what the breach is" lookup — a plain,
    human-referenceable per-tenant sequential number (1, 2, 3, ...) rather
    than the full verdict_id UUID. Returns the same row shape as
    GET /api/{org_id}/verdicts/{verdict_id}, already carrying the stored,
    grounding-checked `explanation`/`section_cited` from detection time —
    no new LLM call happens on lookup, it's a direct evidence-store read.
    """
    store = get_store()
    row = store.get_by_violation_id(org_id, violation_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"Violation #{violation_id} not found for org_id {org_id!r}")
    return row


class StatusUpdate(BaseModel):
    status: str


@app.post("/api/{org_id}/verdicts/{verdict_id}/status")
async def update_verdict_status(org_id: str, verdict_id: str, body: StatusUpdate):
    store = get_store()
    try:
        store.update_status(org_id, verdict_id, body.status)
        row = store.get_by_verdict_id(org_id, verdict_id)
        return {"ok": True, "verdict": row}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/{org_id}/verify-chain")
async def verify_chain(org_id: str):
    store = get_store()
    result = store.verify_chain(org_id)
    return result


@app.get("/api/{org_id}/stats")
async def get_stats(org_id: str):
    store = get_store()
    rows = store.query(org_id)
    stats: dict[str, Any] = {
        "total": len(rows),
        "by_rule": {},
        "by_severity": {},
        "by_source_system": {},
        "by_status": {},
        "breach_candidates": 0,
    }
    for row in rows:
        for key, field in [
            ("by_rule", "rule_id"),
            ("by_severity", "severity"),
            ("by_source_system", "source_system"),
        ]:
            val = row.get(field, "unknown")
            stats[key][val] = stats[key].get(val, 0) + 1
        status = row.get("remediation_status", "OPEN")
        stats["by_status"][status] = stats["by_status"].get(status, 0) + 1
        if row.get("breach_notification_candidate"):
            stats["breach_candidates"] += 1
    return stats


# ---------------------------------------------------------------------------
# Server lifecycle: start broadcaster on startup
# ---------------------------------------------------------------------------

@app.on_event("startup")
async def startup_event():
    asyncio.create_task(broadcaster_task())
    logger.info("Dashboard server started. Tenant-scoped WebSocket broadcaster running.")


# ---------------------------------------------------------------------------
# CLI entrypoint
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="DPDPA Dashboard Server (Phase 7)")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    uvicorn.run("dashboard.server:app", host=args.host, port=args.port, reload=False)


if __name__ == "__main__":
    main()
