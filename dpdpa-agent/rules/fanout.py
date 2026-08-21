"""
DPDPA Compliance Agent — Verdict Fan-Out
=============================================
Verdicts must reach two consumers IN PARALLEL: the LLM Explainer
(Phase 5) and the Evidence Store (Phase 6). Neither exists in working
form yet. This module implements the fan-out as two independent
asyncio.Queues so each future phase can subscribe without this module
(or the Rule Engine) needing to know anything about their internals.

INTEGRATION POINT FOR PHASE 5 AND PHASE 6:
  Each phase should run its own consumer loop reading from its assigned
  queue (VerdictFanout.llm_explainer_queue / .evidence_store_queue).
  Pushing to both queues happens atomically per verdict (both puts
  happen before moving to the next verdict) so neither consumer can
  silently fall behind and miss verdicts relative to the other — but the
  two queues are otherwise fully independent; slow consumption on one
  queue does not block puts to the other beyond normal asyncio.Queue
  backpressure semantics (unbounded by default here — see
  VerdictFanout.__init__ maxsize note).

NO LLM. NO PERSISTENCE. This module only routes Verdict objects that
Phase 4's engine.py already constructed — it makes no compliance
decisions and stores nothing itself.
"""

from __future__ import annotations

import asyncio
import logging
from typing import List, Optional

from schemas.models import Verdict

logger = logging.getLogger("rules.fanout")


class VerdictFanout:
    """
    Holds the two downstream queues and provides a single push() method
    that fans a verdict out to both. Both queues are unbounded
    (maxsize=0) for MVP — a production system would want bounded queues
    with explicit backpressure/drop policy, out of scope here.
    """

    def __init__(self) -> None:
        self.llm_explainer_queue: "asyncio.Queue[Verdict]" = asyncio.Queue()
        self.evidence_store_queue: "asyncio.Queue[Verdict]" = asyncio.Queue()

    async def push(self, verdict: Verdict) -> None:
        """Fans a single verdict out to both downstream queues."""
        await self.llm_explainer_queue.put(verdict)
        await self.evidence_store_queue.put(verdict)

    async def push_many(self, verdicts: List[Verdict]) -> None:
        """Convenience helper — fans out every verdict produced for one event."""
        for v in verdicts:
            await self.push(v)


# ---------------------------------------------------------------------------
# Stub consumer interfaces — obvious integration points for Phase 5 / 6.
# These are NOT implementations; they exist so Phase 5 and Phase 6 have a
# concrete function signature to replace rather than inventing their own
# queue-consumption pattern from scratch.
# ---------------------------------------------------------------------------

async def stub_llm_explainer_consumer(
    fanout: VerdictFanout, max_verdicts: Optional[int] = None
) -> List[Verdict]:
    """
    STUB — Phase 5 replaces this with real LLM explanation logic.
    Demonstrates the expected consumption pattern: pull Verdict objects
    off fanout.llm_explainer_queue and process them. Currently just
    drains and returns them unmodified, for integration testing purposes
    only in this phase.
    """
    consumed: List[Verdict] = []
    while max_verdicts is None or len(consumed) < max_verdicts:
        if fanout.llm_explainer_queue.empty() and max_verdicts is None:
            break
        verdict = await fanout.llm_explainer_queue.get()
        consumed.append(verdict)
        if max_verdicts is not None and len(consumed) >= max_verdicts:
            break
    return consumed


async def stub_evidence_store_consumer(
    fanout: VerdictFanout, max_verdicts: Optional[int] = None
) -> List[Verdict]:
    """
    STUB — Phase 6 replaces this with real Evidence Store persistence
    (append-only SQLite write + hash chain). Currently just drains and
    returns verdicts unmodified, for integration testing purposes only
    in this phase.
    """
    consumed: List[Verdict] = []
    while max_verdicts is None or len(consumed) < max_verdicts:
        if fanout.evidence_store_queue.empty() and max_verdicts is None:
            break
        verdict = await fanout.evidence_store_queue.get()
        consumed.append(verdict)
        if max_verdicts is not None and len(consumed) >= max_verdicts:
            break
    return consumed
