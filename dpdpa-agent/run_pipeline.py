"""
DPDPA Compliance Agent — End-to-End Pipeline Runner (Phase 8)
==============================================================
Wires all phases together in a single command:

    python run_pipeline.py                        # run forever, 70% violation rate
    python run_pipeline.py --violation-rate 0.7   # same
    python run_pipeline.py --max-events 20        # finite run for testing
    python run_pipeline.py --port 8080            # custom dashboard port

Automatically loads .env from the project root (OPENAI_API_KEY etc.).

Pipeline topology (mirrors the Phase 0 system flow diagram):

  registry.load_registry()
       ↓
  Ingestion (log_generator + api_generator)
       ↓  asyncio.Queue (raw events)
  Detection (detect_from_queue)
       ↓  asyncio.Queue (DetectedEvents)
  Rule Engine (evaluate_from_queue → VerdictFanout.push_many)
       ↓  two parallel queues
  ┌────┴─────────────────┐
  Explanation            Evidence Store (direct, not via LLM queue)
  (defer_explanation_from_queue —   ↑
   deterministic template, NO   │
   automatic LLM call — see  via broadcaster_task
   llm_explainer/explainer.py)
       ↓  out_queue
  Evidence Store
  (store_from_queue)
       ↓
  Dashboard WebSocket broadcaster
  (broadcast_from_queue)

Real, statute-grounded LLM explanations now happen ONLY on demand, one
call per human-asked question, via investigation.py's "@N <question>"
endpoint (POST /v1/{org_id}/investigate) — not automatically per verdict.

Run with uvicorn serving the FastAPI dashboard on --port (default 8000).
Open http://localhost:8000 in a browser to see the live dashboard.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
from pathlib import Path

# Load .env file automatically
try:
    from dotenv import load_dotenv
    env_path = Path(__file__).parent / ".env"
    if env_path.exists():
        load_dotenv(env_path)
except ImportError:
    # Manual .env reader fallback if python-dotenv is not installed
    env_path = Path(__file__).parent / ".env"
    if env_path.exists():
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip())

import uvicorn

from detection.engine import detect_from_queue
from evidence_store.store import get_store
from ingestion.api_generator import api_generator
from ingestion.config import IngestionConfig
from ingestion.log_generator import log_generator
from llm_explainer.explainer import defer_explanation_from_queue, ExplainedVerdict
from registry.loader import load_registry
from rules.fanout import VerdictFanout
from schemas.models import Verdict

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("pipeline")


async def evaluate_from_queue(
    detected_queue: asyncio.Queue,
    fanout: VerdictFanout,
    max_events: int | None = None,
) -> None:
    """
    Pulls DetectedEvents off detected_queue, evaluates each with the Rule Engine,
    and fans out resulting Verdicts to the VerdictFanout.
    """
    from rules.engine import evaluate_event

    processed = 0
    while max_events is None or processed < max_events:
        detected = await detected_queue.get()
        verdicts = evaluate_event(detected)
        if verdicts:
            await fanout.push_many(verdicts)
            logger.info("Rule engine produced %d verdict(s) for event %s", len(verdicts), detected.event.event_id)
        processed += 1


async def broadcast_from_queue(
    explained_queue: asyncio.Queue,
    store,
    max_items: int | None = None,
) -> None:
    """
    Pulls ExplainedVerdicts off explained_queue:
      1. Appends to Evidence Store (if not already stored)
      2. Broadcasts to WebSocket clients via the dashboard broadcaster
    """
    from dashboard.live_feed import publish as publish_live_feed

    processed = 0
    while max_items is None or processed < max_items:
        ev: ExplainedVerdict = await explained_queue.get()
        # Store
        try:
            store.append(ev)
        except Exception as exc:
            logger.warning("Evidence store append failed (possible duplicate): %s", exc)

        # Broadcast to this verdict's tenant's WebSocket room only (Phase 6 —
        # dashboard.live_feed routes by payload["tenant_id"], see that module).
        try:
            payload = {
                **ev.verdict.model_dump(mode="json"),
                "explanation": ev.explanation,
                "section_cited": ev.section_cited,
                "confidence": ev.confidence,
                "used_fallback": ev.used_fallback,
            }
            await publish_live_feed(payload)
        except Exception as exc:
            logger.warning("WebSocket broadcast failed: %s", exc)

        processed += 1


async def run_pipeline(
    violation_rate: float = 0.7,
    log_interval: float = 1.0,
    api_interval: float = 1.5,
    max_events: int | None = None,
    random_seed: int | None = None,
) -> None:
    """Main async pipeline coroutine. Runs all modules concurrently."""
    logger.info("Loading compliance registry…")
    registry = load_registry()
    logger.info("Registry loaded: %d entries", len(registry.entries))

    # Ingestion config
    cfg = IngestionConfig()
    cfg.log_emit_interval_seconds = log_interval
    cfg.api_emit_interval_seconds = api_interval
    cfg.log_exposure_violation_rate = violation_rate
    cfg.api_marketing_purpose_violation_rate = violation_rate
    cfg.api_retention_violation_rate = violation_rate
    if random_seed is not None:
        cfg.random_seed = random_seed

    # Queues
    raw_queue: asyncio.Queue = asyncio.Queue()
    detected_queue: asyncio.Queue = asyncio.Queue()
    explained_queue: asyncio.Queue = asyncio.Queue()

    # Fanout (Phase 4 → Phase 5 only; Phase 6 gets ExplainedVerdicts from Phase 5)
    fanout = VerdictFanout()

    store = get_store()

    per_gen_max = None if max_events is None else (max_events // 2) + 1

    tasks = [
        # Ingestion
        asyncio.create_task(log_generator(raw_queue, cfg, max_events=per_gen_max), name="log_gen"),
        asyncio.create_task(api_generator(raw_queue, cfg, max_events=per_gen_max), name="api_gen"),
        # Detection
        asyncio.create_task(detect_from_queue(raw_queue, detected_queue, max_events=max_events), name="detection"),
        # Rule Engine
        asyncio.create_task(evaluate_from_queue(detected_queue, fanout, max_events=max_events), name="rule_engine"),
        # Explanation — deferred/template only, no automatic LLM call per
        # verdict (see llm_explainer/explainer.py's "AUTOMATIC EXPLANATION
        # REMOVED" note). Real, statute-grounded explanations now happen
        # on demand via the "@N <question>" investigation endpoint.
        asyncio.create_task(defer_explanation_from_queue(fanout.llm_explainer_queue, explained_queue, max_verdicts=None), name="explainer"),
        # Evidence Store + Broadcaster
        asyncio.create_task(broadcast_from_queue(explained_queue, store, max_items=None), name="broadcaster"),
    ]

    logger.info("🚀 Pipeline running. Open http://localhost:8000 in your browser.")
    try:
        if max_events is not None:
            # Wait only for bounded tasks
            await asyncio.gather(*tasks[:4])
            for t in tasks[4:]:
                t.cancel()
        else:
            await asyncio.gather(*tasks)
    except asyncio.CancelledError:
        pass
    except Exception as exc:
        logger.error("Pipeline error: %s", exc, exc_info=True)


def main():
    parser = argparse.ArgumentParser(
        description="DPDPA Compliance Agent — Full Pipeline (Phases 0–8)"
    )
    parser.add_argument("--violation-rate", type=float, default=0.7,
                        help="Fraction of events that are violations (0–1). Default 0.7")
    parser.add_argument("--log-interval", type=float, default=1.0,
                        help="Seconds between log generator events. Default 1.0")
    parser.add_argument("--api-interval", type=float, default=1.5,
                        help="Seconds between API generator events. Default 1.5")
    parser.add_argument("--max-events", type=int, default=None,
                        help="Stop after N events (default: run forever)")
    parser.add_argument("--port", type=int, default=8000,
                        help="Dashboard port. Default 8000")
    parser.add_argument("--seed", type=int, default=None,
                        help="Random seed for reproducible runs")
    args = parser.parse_args()

    import threading

    # Run the FastAPI server in a background thread
    def _run_server():
        from dashboard.server import app
        uvicorn.run(app, host="0.0.0.0", port=args.port, log_level="warning")

    server_thread = threading.Thread(target=_run_server, daemon=True)
    server_thread.start()

    logger.info("Dashboard available at http://localhost:%d", args.port)

    import time; time.sleep(1.5)  # let server boot before pipeline starts

    asyncio.run(run_pipeline(
        violation_rate=args.violation_rate,
        log_interval=args.log_interval,
        api_interval=args.api_interval,
        max_events=args.max_events,
        random_seed=args.seed,
    ))


if __name__ == "__main__":
    main()
