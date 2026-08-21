"""
DPDPA Compliance Agent — Ingestion Configuration
==================================================
All tunable parameters for the two generators live here, NOT hardcoded
inline in generator logic. Phase 8's demo rehearsal needs to control
pacing and violation rates without touching generator code.

Nothing here performs detection or rule evaluation — this is purely
"how fast do we emit, and how often do we deliberately inject a violation."
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class IngestionConfig:
    """
    Tunable knobs for both generators. Pass an instance of this to
    LogGenerator / ApiGenerator, or use IngestionConfig() for defaults.
    """

    # --- Pacing ---
    log_emit_interval_seconds: float = 1.0
    """How often the log generator emits a line. Plan range: 0.5-2s."""

    api_emit_interval_seconds: float = 1.5
    """How often the API generator emits a payload."""

    # --- Violation rates (0.0 = never, 1.0 = always) ---
    log_exposure_violation_rate: float = 0.175
    """
    Fraction of log lines that dump raw PII (the exposure vector).
    Plan default: 15-20%. Set to 0.0 for a clean happy-path stream;
    set to 1.0 to force every log line to be a violation (useful for
    deterministic demo cues and for the rate=0 vs rate=1 test).
    """

    api_marketing_purpose_violation_rate: float = 0.10
    """
    Fraction of marketing-analytics events that carry raw PII instead of
    hashed-only fields (the purpose-limitation vector). Plan default: 10%.
    """

    api_retention_violation_rate: float = 0.20
    """
    Fraction of delivery-partner-service events that reference the stale,
    past-retention-window onboarding record (the retention vector).
    Not specified by the plan as a fixed number — chosen to be visible
    often enough for a live demo without dominating the stream.
    """

    # --- Determinism (optional, useful for tests / demo rehearsal) ---
    random_seed: int | None = None
    """
    If set, both generators seed their local random.Random with this value
    for reproducible demo runs. None = normal nondeterministic randomness.
    """


DEFAULT_CONFIG = IngestionConfig()
