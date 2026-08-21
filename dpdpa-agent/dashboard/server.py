"""
DPDPA Compliance Agent — Dashboard Server (Phase 7)
====================================================
FastAPI server providing:
  - GET /           — serves the dashboard HTML
  - WebSocket /ws   — live feed of ExplainedVerdicts as they arrive
  - GET /api/verdicts — Evidence Store query with filters
  - GET /api/verdicts/{verdict_id} — single verdict detail
  - POST /api/verdicts/{verdict_id}/status — update remediation_status
  - GET /api/verify-chain — run verify_chain() and return result
  - GET /api/stats  — summary counts by rule_type, severity, source_system

Run with:
    python -m dashboard.server
    python -m dashboard.server --port 8080

Or from the pipeline (run.py) which starts it programmatically.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Query
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from evidence_store.store import EvidenceStore, get_store

logger = logging.getLogger("dashboard.server")

app = FastAPI(title="DPDPA Compliance Agent — Dashboard", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Global state: connected WebSocket clients + the live-feed queue
# ---------------------------------------------------------------------------

_ws_clients: list[WebSocket] = []
_live_feed_queue: asyncio.Queue = asyncio.Queue()


def get_live_feed_queue() -> asyncio.Queue:
    return _live_feed_queue


async def broadcast_to_websockets(data: dict) -> None:
    """Send a JSON payload to all connected WebSocket clients."""
    dead = []
    for ws in list(_ws_clients):
        try:
            await ws.send_json(data)
        except Exception:
            dead.append(ws)
    for ws in dead:
        if ws in _ws_clients:
            _ws_clients.remove(ws)


# ---------------------------------------------------------------------------
# WebSocket broadcaster background task
# ---------------------------------------------------------------------------

async def _ws_broadcaster() -> None:
    """
    Reads from _live_feed_queue and broadcasts to all connected WebSocket clients.
    Runs forever as a background task once the server starts.
    """
    while True:
        data = await _live_feed_queue.get()
        await broadcast_to_websockets(data)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
async def serve_dashboard():
    html_path = Path(__file__).parent / "index.html"
    return HTMLResponse(html_path.read_text(encoding="utf-8"))


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    _ws_clients.append(ws)
    logger.info("WebSocket client connected. Total: %d", len(_ws_clients))
    try:
        while True:
            # Keep connection alive; data flows from broadcaster
            await asyncio.sleep(30)
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        if ws in _ws_clients:
            _ws_clients.remove(ws)
        logger.info("WebSocket client disconnected. Total: %d", len(_ws_clients))


@app.get("/api/verdicts")
async def list_verdicts(
    source_system: Optional[str] = Query(None),
    severity: Optional[str] = Query(None),
    remediation_status: Optional[str] = Query(None),
    date_start: Optional[str] = Query(None),
    date_end: Optional[str] = Query(None),
):
    store = get_store()
    date_range = (date_start, date_end) if date_start and date_end else None
    rows = store.query(
        date_range=date_range,
        source_system=source_system,
        severity=severity,
        remediation_status=remediation_status,
    )
    return {"verdicts": rows, "count": len(rows)}


@app.get("/api/verdicts/{verdict_id}")
async def get_verdict(verdict_id: str):
    store = get_store()
    row = store.get_by_verdict_id(verdict_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"Verdict {verdict_id!r} not found")
    return row


class StatusUpdate(BaseModel):
    status: str


@app.post("/api/verdicts/{verdict_id}/status")
async def update_verdict_status(verdict_id: str, body: StatusUpdate):
    store = get_store()
    try:
        store.update_status(verdict_id, body.status)
        row = store.get_by_verdict_id(verdict_id)
        return {"ok": True, "verdict": row}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/verify-chain")
async def verify_chain():
    store = get_store()
    result = store.verify_chain()
    return result


@app.get("/api/stats")
async def get_stats():
    store = get_store()
    rows = store.query()
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
    asyncio.create_task(_ws_broadcaster())
    logger.info("Dashboard server started. WebSocket broadcaster running.")


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
