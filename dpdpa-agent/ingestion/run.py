"""
DPDPA Compliance Agent — Ingestion Entrypoint
================================================
Runs the log generator and API generator concurrently, both pushing
normalized Event objects onto a single shared asyncio.Queue, and prints
each event to the console as it's produced.

This mirrors the Confluent Kafka+Faust raw-topic pattern (raw stream ->
detection app -> alert stream + clean stream) at hackathon scale, using
asyncio.Queue instead of a real Kafka broker. No message broker is
introduced here — see docs/architecture.md for why (WebSocket/in-process
queue chosen over Kafka for the MVP).

Run with:
    python -m ingestion.run
    python -m ingestion.run --violation-rate 0      # happy path, no violations
    python -m ingestion.run --violation-rate 1       # every event is a violation
    python -m ingestion.run --log-interval 0.5 --api-interval 1.0
    python -m ingestion.run --max-events 20          # finite run, then stop

This layer does NOT perform PII detection or rule evaluation — it only
produces and prints normalized events. A downstream consumer (Phase 3+)
would `await queue.get()` in its own loop; this entrypoint's own consumer
loop here exists purely to demonstrate/verify the stream for Phase 2's
exit criteria.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging

from ingestion.api_generator import api_generator
from ingestion.config import IngestionConfig
from ingestion.log_generator import log_generator
from schemas.models import Event

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("ingestion.run")


async def _console_consumer(queue: asyncio.Queue, max_events: int | None) -> None:
    """
    Pulls events off the shared queue and prints them as formatted JSON.
    Exists to satisfy Phase 2's exit criterion ("console output visibly
    shows normalized JSON events streaming from both generators") — this
    is a demonstration consumer, not a real downstream module.
    """
    consumed = 0
    while max_events is None or consumed < max_events:
        event: Event = await queue.get()
        print(json.dumps(event.model_dump(mode="json"), indent=2, default=str))
        print("-" * 70)
        consumed += 1


def _build_config(args: argparse.Namespace) -> IngestionConfig:
    cfg = IngestionConfig()
    if args.log_interval is not None:
        cfg.log_emit_interval_seconds = args.log_interval
    if args.api_interval is not None:
        cfg.api_emit_interval_seconds = args.api_interval
    if args.violation_rate is not None:
        # A single top-level override applies the same rate to all three
        # violation vectors — convenient for the "0 = happy path, 1 = every
        # event is a violation" testing/demo use case the plan calls out.
        cfg.log_exposure_violation_rate = args.violation_rate
        cfg.api_marketing_purpose_violation_rate = args.violation_rate
        cfg.api_retention_violation_rate = args.violation_rate
    if args.seed is not None:
        cfg.random_seed = args.seed
    return cfg


async def main_async(args: argparse.Namespace) -> None:
    cfg = _build_config(args)
    queue: asyncio.Queue = asyncio.Queue()

    # Each event-producing task gets its own max_events share so a finite
    # --max-events run stops deterministically rather than running forever
    # with only the consumer bounded.
    per_generator_max = None if args.max_events is None else (args.max_events // 2) + 1

    tasks = [
        asyncio.create_task(log_generator(queue, cfg, max_events=per_generator_max)),
        asyncio.create_task(api_generator(queue, cfg, max_events=per_generator_max)),
        asyncio.create_task(_console_consumer(queue, max_events=args.max_events)),
    ]

    if args.max_events is not None:
        # Wait for the consumer specifically; cancel producers once done.
        await tasks[2]
        for t in tasks[:2]:
            t.cancel()
    else:
        await asyncio.gather(*tasks)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run the DPDPA agent's Phase 2 ingestion generators (log + API)."
    )
    parser.add_argument("--log-interval", type=float, default=None,
                         help="Seconds between log generator emissions (default 1.0).")
    parser.add_argument("--api-interval", type=float, default=None,
                         help="Seconds between API generator emissions (default 1.5).")
    parser.add_argument("--violation-rate", type=float, default=None,
                         help="Override all violation rates at once (0.0-1.0). "
                              "0 = clean happy-path stream, 1 = every event is a violation.")
    parser.add_argument("--max-events", type=int, default=None,
                         help="Stop after this many total events (default: run forever).")
    parser.add_argument("--seed", type=int, default=None,
                         help="Random seed for reproducible demo runs.")
    args = parser.parse_args()

    try:
        asyncio.run(main_async(args))
    except KeyboardInterrupt:
        logger.info("Ingestion stopped by user.")


if __name__ == "__main__":
    main()
