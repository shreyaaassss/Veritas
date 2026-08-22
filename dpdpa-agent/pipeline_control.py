"""
Veritas DPDPA Agent — Pipeline Pause/Resume Control
=======================================================
Lets the dashboard's "Pause/Resume Live Feed" button stop and restart the
SYNTHETIC event generators (ingestion/log_generator.py, ingestion/
api_generator.py) without killing the process — useful for a live demo
where you want to freeze the feed to talk through an entry, then resume.

threading.Event, not an asyncio.Event: run_pipeline.py runs the dashboard's
API routes (which flip this flag) in a background OS thread with its own
event loop, while the generators run in the main thread's asyncio.run()
loop (see dashboard/live_feed.py's "CROSS-THREAD FIX" note for the same
topology). threading.Event.set()/.clear()/.is_set() are safe to call from
either thread with no event-loop coordination needed — unlike an
asyncio.Event, which is only safe within a single loop.

Defaults to RUNNING (set()) so tests and any code calling the generators
directly, without ever touching this module, see the exact same unpaused
behavior they always have — this control is opt-in, additive only.
"""

from __future__ import annotations

import threading

_running = threading.Event()
_running.set()


def pause() -> None:
    _running.clear()


def resume() -> None:
    _running.set()


def is_running() -> bool:
    return _running.is_set()
