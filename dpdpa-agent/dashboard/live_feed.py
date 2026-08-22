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

CROSS-THREAD FIX (found via live testing, not hypothetical): run_pipeline.py
runs uvicorn (this module's WebSocket clients + broadcaster_task) in its own
background OS thread with ITS OWN asyncio event loop, while the pipeline's
ingestion/detection/rule-engine/explainer coroutines run in the MAIN
thread's separate `asyncio.run()` loop. A plain `await _live_feed_queue.
put(...)` called from that main-thread loop touches an asyncio.Queue whose
waiting `.get()` (inside broadcaster_task, parked in the SERVER thread's
loop) can never be woken up cross-loop without `run_coroutine_threadsafe` —
the primitives asyncio.Queue uses to wake a waiter are not thread-safe on
their own. The practical symptom: verdicts still get detected and stored
correctly (that path doesn't go through this queue), but NOTHING ever
reaches the Live Feed, because the queue silently fills with items whose
consumer never wakes up. publish() below detects when it's being called
from a different loop than the one actually running the WebSocket
broadcaster (captured via set_server_loop(), called once from dashboard/
server.py's startup handler) and uses run_coroutine_threadsafe in that case
instead of a bare cross-loop await.
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from typing import Any, Dict, List, Optional

from fastapi import WebSocket

logger = logging.getLogger("dashboard.live_feed")

# Per-tenant WebSocket client rooms. A client connected under org_id "A" is
# only ever present in _ws_clients["A"] — never sees org "B"'s events.
_ws_clients: Dict[str, List[WebSocket]] = defaultdict(list)

_live_feed_queue: asyncio.Queue = asyncio.Queue()

# The event loop that actually owns the WebSocket connections and runs
# broadcaster_task() — set once by dashboard.server's startup handler. See
# the module docstring's "CROSS-THREAD FIX" note for why publish() needs
# this rather than just awaiting the queue directly.
_server_loop: Optional[asyncio.AbstractEventLoop] = None


def set_server_loop(loop: asyncio.AbstractEventLoop) -> None:
    global _server_loop
    _server_loop = loop


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

    Thread-safe: if the caller's running loop isn't the one actually
    driving the WebSocket broadcaster (see set_server_loop / the module
    docstring), the put is scheduled onto that loop via
    run_coroutine_threadsafe instead of awaited directly — a bare
    cross-loop await here is exactly what silently dropped every
    run_pipeline.py-sourced verdict from the Live Feed.
    """
    if _server_loop is not None and asyncio.get_running_loop() is not _server_loop:
        future = asyncio.run_coroutine_threadsafe(_live_feed_queue.put(payload), _server_loop)
        await asyncio.wrap_future(future)
    else:
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
