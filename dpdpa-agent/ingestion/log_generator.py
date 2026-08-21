"""
DPDPA Compliance Agent — Log Generator (Exposure Vector)
===========================================================
Emits realistic Blinkit-style log lines at a steady, configurable interval.
Mixes clean lines (no PII) with PII-dumping lines at a configurable
violation rate.

FRAMING (for Phase 8's pitch): this generator models the EXPOSURE vector —
debug logging that dumps a full customer/order object, greedy
serialization (`log.debug(f"...{customer_obj}")`), or a temporary debug
statement that never got removed before ship. This isn't modeled as
developer carelessness in isolation; it's structurally inevitable at
Blinkit's ship pace — every deploy under time pressure has a nonzero
chance of a debug line surviving into production logging. That's exactly
why a monitoring agent, not a linter, is the right layer to catch it.

source_system used: support-ticketing (primary) and order-service
(secondary), both valid Phase 0 SourceSystem enum values — no new values
invented. Chosen because these two systems are exactly where a support
agent or fulfillment engineer is most likely to be eyeballing a live
customer record while debugging, i.e. where a stray debug log is most
likely to originate in a real Blinkit-shaped codebase.

This module performs NO PII detection and NO rule evaluation — it only
produces raw log lines and hands them to the normalizer.
"""

from __future__ import annotations

import asyncio
import logging
import random
from datetime import datetime, timezone
from typing import Dict, Optional

from ingestion.config import IngestionConfig
from ingestion.fixtures import (
    fake_address,
    fake_email,
    fake_name,
    fake_order_id,
    fake_phone,
)
from ingestion.normalizer import normalize_log_event, validate_event
from schemas.models import SourceSystem

logger = logging.getLogger("ingestion.log_generator")

# Log-emitting systems for this generator (both valid Phase 0 enum values).
LOG_SOURCE_SYSTEMS = [SourceSystem.SUPPORT_TICKETING, SourceSystem.ORDER_SERVICE]

LOG_LEVELS = ["DEBUG", "INFO", "WARN"]


def _clean_log_line(source_system: SourceSystem) -> tuple[str, Dict[str, str]]:
    """A normal, PII-free log line — the 'happy path' majority case."""
    order_id = fake_order_id()
    level = random.choice(["INFO", "DEBUG"])
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    line = (
        f"[{ts}] {level} {source_system.value}: order {order_id} status updated "
        f"to '{random.choice(['confirmed', 'packed', 'out_for_delivery', 'delivered'])}'"
    )
    fields = {"order_id": order_id}
    return line, fields


def _exposure_violation_log_line(source_system: SourceSystem) -> tuple[str, Dict[str, str]]:
    """
    A log line that dumps raw customer PII — the deliberate exposure
    violation. Modeled as a DEBUG statement serializing a fetched customer
    record in full, exactly the pattern described in the module docstring.
    """
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    name = fake_name()
    phone = fake_phone()
    address = fake_address()

    line = (
        f'[{ts}] DEBUG {source_system.value}: fetched customer record '
        f'{{name: "{name}", phone: "{phone}", address: "{address}"}}'
    )
    fields = {"name": name, "phone": phone, "address": address}
    return line, fields


async def log_generator(
    queue: asyncio.Queue,
    config: Optional[IngestionConfig] = None,
    max_events: Optional[int] = None,
    rng: Optional[random.Random] = None,
) -> None:
    """
    Runs indefinitely (or until max_events is emitted), pushing normalized,
    schema-validated Event objects onto the shared queue at
    config.log_emit_interval_seconds intervals.

    max_events=None runs forever (real demo mode). A finite max_events is
    used by tests and by short rehearsal runs.

    rng: optional injected random.Random for deterministic test runs;
    falls back to the module-level `random` if not provided.
    """
    cfg = config or IngestionConfig()
    r = rng or random.Random(cfg.random_seed) if rng is None else rng

    emitted = 0
    while max_events is None or emitted < max_events:
        source_system = r.choice(LOG_SOURCE_SYSTEMS)

        is_violation = r.random() < cfg.log_exposure_violation_rate
        if is_violation:
            raw_line, fields = _exposure_violation_log_line(source_system)
        else:
            raw_line, fields = _clean_log_line(source_system)

        event_dict = normalize_log_event(
            raw_line=raw_line,
            source_system=source_system,
            fields=fields,
        )
        event = validate_event(event_dict)
        if event is not None:
            await queue.put(event)
            emitted += 1

        await asyncio.sleep(cfg.log_emit_interval_seconds)
