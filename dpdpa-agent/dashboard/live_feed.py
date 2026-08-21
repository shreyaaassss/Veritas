"""
Veritas DPDPA Agent — Tenant-Scoped Live Feed Pub/Sub (Phase 6)
====================================================================
Before this phase, the WebSocket broadcaster was a single global list of
clients (`_ws_clients` in dashboard/server.py) — ANY connected client
received EVERY org's verdicts, with zero tenant scoping. This module fixes
that by keeping one client "room" per org_id.

Deliberately extracted into its own module (not left inline in
dashboard/server.py): api/integration.py (Phase 5's /events and /scan
routes) needs to publish into the exact same live feed run_pipeline.py's
Stream mode already uses (Part B3 — no divergent code path between scan-
detected and stream-detected verdicts), and dashboard/server.py needs to
be the module that OWNS the FastAPI app and includes api/integration.py's
router. If this pub/sub lived inside dashboard/server.py, api/integration.py
would have to import dashboard.server, which imports api.integration's
router — a circular import. Living here, both sides import this one small,
dependency-free module cleanly.
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from typing import Any, Dict, List

from fastapi import WebSocket

logger = logging.getLogger("dashboard.live_feed")

# Per-tenant WebSocket client rooms. A client connected under org_id "A" is
# only ever present in _ws_clients["A"] — never sees org "B"'s events.
_ws_clients: Dict[str, List[WebSocket]] = defaultdict(list)

_live_feed_queue: asyncio.Queue = asyncio.Queue()


def get_live_feed_queue() -> asyncio.Queue:
    return _live_feed_queue


async def publish(payload: Dict[str, Any]) -> None:
    """
    Enqueue a verdict payload for broadcast. `payload` MUST carry a
    top-level "tenant_id" key (every Verdict already does — see
    schemas.models.Verdict) — broadcast() uses it to route to the correct
    room. Used by both run_pipeline.py's Stream-mode broadcaster and
    api/integration.py's /scan and /events handlers, so both integration
    modes feed the exact same live-feed pipe.
    """
    await _live_feed_queue.put(payload)


def register_client(org_id: str, ws: WebSocket) -> None:
    _ws_clients[org_id].append(ws)


def unregister_client(org_id: str, ws: WebSocket) -> None:
    clients = _ws_clients.get(org_id)
    if clients and ws in clients:
        clients.remove(ws)


async def broadcast(payload: Dict[str, Any]) -> None:
    """
    Sends `payload` ONLY to clients registered under payload['tenant_id'] —
    never to any other org's room. A payload with no tenant_id is dropped
    (logged) rather than broadcast to everyone, since "no tenant_id" is
    never a valid state for a real Verdict and silently broadcasting it
    globally would be exactly the kind of leak this module exists to close.
    """
    org_id = payload.get("tenant_id")
    if not org_id:
        logger.warning("Dropping live-feed payload with no tenant_id: %r", payload)
        return
    dead = []
    for ws in list(_ws_clients.get(org_id, [])):
        try:
            await ws.send_json(payload)
        except Exception:
            dead.append(ws)
    for ws in dead:
        unregister_client(org_id, ws)


async def broadcaster_task() -> None:
    """Runs forever as a background task: drains the queue, broadcasts each
    payload to its tenant's room only."""
    while True:
        payload = await _live_feed_queue.get()
        await broadcast(payload)
