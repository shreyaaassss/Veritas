# Phase 2 — Ingestion Layer

**Status: Implemented.**

Produces simulated Blinkit telemetry — realistic log lines and API payloads
— normalized into schema-valid Phase 0 `Event` objects and pushed onto a
shared in-process `asyncio.Queue`. This layer performs **no PII detection
and no rule evaluation** — it only produces and normalizes.

---

## Files

| File | Purpose |
|---|---|
| `config.py` | `IngestionConfig` — all tunable pacing/violation-rate parameters, nothing hardcoded inline |
| `fixtures.py` | Fake-but-realistic Blinkit content: names, phone/Aadhaar/PAN-shaped strings, addresses; `STALE_DELIVERY_PARTNER` — single source of truth shared with Phase 1's registry seed |
| `normalizer.py` | Converts raw generator output into Phase 0 `Event`-shaped dicts and validates every event before it's queued |
| `log_generator.py` | Exposure-vector generator — emits log lines from `support-ticketing` / `order-service`, mixing clean lines with PII-dumping debug lines at a configurable rate |
| `api_generator.py` | Purpose-limitation + retention vector generator — alternates `marketing-analytics` (hashed vs raw-PII-leaking) and `delivery-partner-service` (fresh vs stale/retention-violating) payloads |
| `run.py` | Entrypoint — runs both generators concurrently against the shared queue, prints normalized JSON events to console |
| `test_ingestion.py` | 21 tests — schema validity, violation-rate control (rate=0 vs rate=1), enum safety, per-vector inspectability |

---

## Running It

```bash
# Default: steady stream, ~15-20% exposure violations, 10% marketing leaks, 20% retention violations
python -m ingestion.run

# Happy path — no violations at all
python -m ingestion.run --violation-rate 0

# Force every event to be a violation (useful for demo cues / testing)
python -m ingestion.run --violation-rate 1

# Control pacing for demo rehearsal
python -m ingestion.run --log-interval 0.5 --api-interval 1.0

# Finite run for quick inspection
python -m ingestion.run --max-events 20

# Reproducible run
python -m ingestion.run --seed 42 --max-events 10
```

Each event prints as formatted JSON matching the Phase 0 `Event` schema exactly.

---

## The Three Violation Vectors

| Vector | Rule it feeds | `source_system` | How it's modeled |
|---|---|---|---|
| **Exposure** | `EXPOSURE_001` | `support-ticketing`, `order-service` | Debug log line dumps a full customer record (name, phone, address) — framed as structurally inevitable at ship pace, not carelessness |
| **Purpose-limitation** | `PURPOSE_001` | `marketing-analytics` | Marketing event schema leaks a raw `phone` field instead of staying hashed-only — framed as schema reuse from an internal-only type |
| **Retention** | `RETENTION_001` | `delivery-partner-service` | References the exact same stale delivery partner (`DP-4471` / "Suresh K." / Aadhaar `5521 8890 3347`, 240 days old) seeded in Phase 1's registry — same identity, so Phase 4 resolves both to one ground-truth record |

All three rates are independently configurable via `IngestionConfig` — see `config.py`.

---

## Stretch (not implemented, stubbed only)

Two additional breach vectors are stubbed in `api_generator.py`, clearly marked `# Phase 2 stretch, not MVP-blocking`:
- Cache/queue over-retention (stale Redis-cached user object)
- Stale staging data tagged `env: staging` carrying production-shaped PII

Both raise `NotImplementedError` if called — pick up in Phase 9 if pursued.
