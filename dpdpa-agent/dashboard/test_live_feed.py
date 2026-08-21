"""
Phase 6 — Live Feed Per-Tenant WebSocket Room Tests
========================================================
Tests dashboard/live_feed.py's routing logic directly against fake
WebSocket-like objects, rather than through a real TestClient WebSocket
connection. Deliberate choice: starlette's TestClient WebSocketTestSession
has no receive-with-timeout, so a "confirm client B receives NOTHING"
assertion can't be written safely against it (it would block forever on a
true negative). Testing dashboard.live_feed's actual routing function
directly is both simpler and more precise — it's the exact logic at risk
of a cross-tenant leak, independent of the transport layer around it.

(End-to-end confirmation that a live WebSocket connection actually
receives messages was done manually — see the Phase 5+6 report.)
"""

from __future__ import annotations

import pytest

from dashboard import live_feed


class _FakeWebSocket:
    def __init__(self, fail: bool = False):
        self.received: list[dict] = []
        self.fail = fail

    async def send_json(self, data: dict) -> None:
        if self.fail:
            raise ConnectionError("simulated dead connection")
        self.received.append(data)


def _drain_queue():
    q = live_feed.get_live_feed_queue()
    while not q.empty():
        q.get_nowait()


@pytest.fixture(autouse=True)
def clean_rooms():
    """
    live_feed's client rooms AND its live-feed queue are module-level
    state, shared across the whole test session (e.g. api/test_integration.py
    also publishes into this exact queue). Reset both around every test —
    without draining the queue, a stale item published by an EARLIER
    test file (queue order in a shared pytest session is not test-scoped)
    would be the one this file's own .get() calls pick up instead of the
    item this test just published.
    """
    live_feed._ws_clients.clear()
    _drain_queue()
    yield
    live_feed._ws_clients.clear()
    _drain_queue()


class TestPerTenantRouting:

    @pytest.mark.asyncio
    async def test_broadcast_reaches_only_the_matching_tenant_room(self):
        ws_a = _FakeWebSocket()
        ws_b = _FakeWebSocket()
        live_feed.register_client("org_a", ws_a)
        live_feed.register_client("org_b", ws_b)

        await live_feed.broadcast({"tenant_id": "org_a", "rule_id": "EXPOSURE_001"})

        assert len(ws_a.received) == 1
        assert ws_a.received[0]["rule_id"] == "EXPOSURE_001"
        assert len(ws_b.received) == 0, "org_b's client must never receive org_a's payload"

    @pytest.mark.asyncio
    async def test_multiple_clients_in_the_same_room_all_receive_it(self):
        ws_a1 = _FakeWebSocket()
        ws_a2 = _FakeWebSocket()
        ws_b = _FakeWebSocket()
        live_feed.register_client("org_a", ws_a1)
        live_feed.register_client("org_a", ws_a2)
        live_feed.register_client("org_b", ws_b)

        await live_feed.broadcast({"tenant_id": "org_a", "rule_id": "RETENTION_001"})

        assert len(ws_a1.received) == 1
        assert len(ws_a2.received) == 1
        assert len(ws_b.received) == 0

    @pytest.mark.asyncio
    async def test_payload_missing_tenant_id_is_dropped_not_broadcast_everywhere(self):
        """A payload with no tenant_id must never be sent to ANY room —
        the fail-safe direction matters: silently broadcasting it to
        everyone would itself be a cross-tenant leak."""
        ws_a = _FakeWebSocket()
        ws_b = _FakeWebSocket()
        live_feed.register_client("org_a", ws_a)
        live_feed.register_client("org_b", ws_b)

        await live_feed.broadcast({"rule_id": "EXPOSURE_001"})  # no tenant_id

        assert ws_a.received == []
        assert ws_b.received == []

    @pytest.mark.asyncio
    async def test_dead_client_is_removed_and_does_not_affect_other_clients(self):
        ws_dead = _FakeWebSocket(fail=True)
        ws_alive = _FakeWebSocket()
        live_feed.register_client("org_a", ws_dead)
        live_feed.register_client("org_a", ws_alive)

        await live_feed.broadcast({"tenant_id": "org_a", "rule_id": "PURPOSE_001"})

        assert ws_alive.received == [{"tenant_id": "org_a", "rule_id": "PURPOSE_001"}]
        assert ws_dead not in live_feed._ws_clients["org_a"]

    def test_unregister_client_removes_only_that_client(self):
        ws_a1 = _FakeWebSocket()
        ws_a2 = _FakeWebSocket()
        live_feed.register_client("org_a", ws_a1)
        live_feed.register_client("org_a", ws_a2)

        live_feed.unregister_client("org_a", ws_a1)

        assert ws_a1 not in live_feed._ws_clients["org_a"]
        assert ws_a2 in live_feed._ws_clients["org_a"]

    @pytest.mark.asyncio
    async def test_publish_then_broadcaster_task_drains_queue_to_correct_room(self):
        """End-to-end within this module: publish() enqueues, and manually
        draining one item (rather than running the infinite broadcaster_task
        loop) proves the queue -> broadcast wiring is correct."""
        ws_a = _FakeWebSocket()
        ws_b = _FakeWebSocket()
        live_feed.register_client("org_a", ws_a)
        live_feed.register_client("org_b", ws_b)

        await live_feed.publish({"tenant_id": "org_a", "rule_id": "LINKAGE_001"})
        payload = await live_feed.get_live_feed_queue().get()
        await live_feed.broadcast(payload)

        assert len(ws_a.received) == 1
        assert len(ws_b.received) == 0
