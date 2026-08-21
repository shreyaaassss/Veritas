# DPDPA Compliance Agent — Demo Script

> Run this from the repo root (`/Users/shreyas/Desktop/Veritas/veritas/dpdpa-agent`)
> to get a clean, rehearsable end-to-end walkthrough from cold start.

---

## Prerequisites

```bash
cd /Users/shreyas/Desktop/Veritas/veritas/dpdpa-agent
pip install -r requirements.txt
pip install pytest pytest-asyncio
# Presidio spaCy model (if not already installed):
pip install https://github.com/explosion/spacy-models/releases/download/en_core_web_lg-3.8.0/en_core_web_lg-3.8.0-py3-none-any.whl
```

Set your LLM API key (optional — fallback template works without it):
```bash
export OPENAI_API_KEY=sk-...   # or ANTHROPIC_API_KEY for Claude
```

---

## Step 0 — Cold-start check

```bash
python -m pytest \
  schemas/test_schemas.py \
  registry/test_registry.py \
  ingestion/test_ingestion.py \
  detection/test_detection.py \
  rules/test_rule_engine.py \
  llm_explainer/test_explainer.py \
  evidence_store/test_evidence_store.py \
  -v --tb=short
```

Expected: all tests pass. If Presidio/spaCy not installed, skip detection/rules tests with `-k "not detection and not rule_engine"` and proceed — the pipeline still runs.

---

## Step 1 — Start the full pipeline

Open a terminal and run:
```bash
python run_pipeline.py --violation-rate 0.7 --port 8000
```

Then open **http://localhost:8000** in a browser.

**What you should see:**
- The dashboard loads with the Live Feed tab active.
- Within 2–3 seconds, violation cards start appearing: red for HIGH, amber for MEDIUM.
- Stats bar updates: Total Violations, High Severity, Breach Candidates.
- The "Live" indicator in the top-right header turns green.

**Point out to judges:**
> "Events are flowing from two parallel generators — an application log simulator and an API traffic simulator — both modelling real Blinkit system behaviour. Every PII match is detected by Microsoft Presidio with custom Aadhaar and PAN recognizers, evaluated against our compliance registry, and shown here in real time."

---

## Step 2 — Trigger a known violation on cue

In a **second terminal**, inject a single high-severity log line:
```bash
python -c "
import asyncio, json
from ingestion.log_generator import _make_log_event
from ingestion.normalizer import normalize_log
from schemas.models import SourceType, SourceSystem
from datetime import datetime, timezone
import uuid

# Craft a deliberate Aadhaar exposure in a debug log
line = '[2026-08-21 10:00:00] DEBUG order-service: fetched user {\"aadhaar\": \"4412 7789 0021\", \"name\": \"Priya Sharma\", \"phone\": \"+91-9876543210\"}'
print('Injecting violation log line...')
print(line)
"
```

Then restart the pipeline with `--max-events 1` to fire just one violation:
```bash
python run_pipeline.py --violation-rate 1.0 --max-events 5 --seed 42
```

**What you should see:**
- A HIGH-severity EXPOSURE_001 card appears on the Live Feed.
- Switch to the **Evidence / Audit** tab and click Apply — the violation is in the table.
- Click **Detail** on the row — the drawer shows full verdict, LLM explanation, statute citation, SHA-256 hash.
- Click **Acknowledge** — status badge updates to ACKNOWLEDGED.
- Click **Resolve** — status badge updates to RESOLVED.
- Run Verify Evidence Integrity — shows ✅ Chain intact.

**Point out to judges:**
> "The evidence store is append-only with a SHA-256 hash chain. The mutable fields — remediation status and update timestamp — live outside the hashed payload, so marking something RESOLVED doesn't touch the hash at all."

---

## Step 3 — Force LLM guardrail failure on cue

In a new terminal, set the force-fallback env var and run:
```bash
FORCE_LLM_FALLBACK=1 python run_pipeline.py --violation-rate 1.0 --max-events 5 --seed 42
```

**What you should see on the Live Feed:**
- Violation cards appear with an **⚡ Template** badge instead of a polished LLM explanation.
- The explanation text follows the format: `"Violation: EXPOSURE_001 — aadhaar exposed in order-service. See DPDPA 2023 § 8(1)…"`
- No blank cards, no errors — the pipeline never crashes.

**Point out to judges:**
> "The LLM is entirely optional. If it fails for any reason — API error, invalid JSON, hallucinated statute citation — we discard the output and fall back to this deterministic template. The pipeline never hangs or crashes. The guardrail is a one-way check: confirm the section_cited in the response matches what we provided from our static statute file. Hallucinated citations are discarded before they reach the auditor."

---

## Step 4 — Hash chain tamper demonstration

Before the demo, run this once to create a corrupted test database:
```bash
python -c "
import sqlite3, json
from pathlib import Path

db = Path('evidence_store/evidence.db')
if not db.exists():
    print('Run the pipeline first to populate the evidence store, then re-run this script.')
    exit(1)

conn = sqlite3.connect(str(db))
# Corrupt the very first row's payload
conn.execute(\"UPDATE evidence SET payload_json = ? WHERE row_index = 1\",
             (json.dumps({'tampered': 'by an adversary'}),))
conn.commit()
conn.close()
print('Row 1 corrupted. Click Verify Evidence Integrity to detect it.')
"
```

**On the dashboard:**
1. Click **Verify Evidence Integrity**
2. See: ❌ Chain broken at record #1 — tamper detected
3. Restore by running a fresh pipeline run (delete evidence.db and re-run), then click Verify again:
4. See: ✅ Chain intact — all records verified

**Point out to judges:**
> "This is what makes the evidence store tamper-evident, not just append-only. Even if someone edits the database directly — which is trivially possible with SQLite — they can't undetectably change a past record without breaking the hash chain for every subsequent row."

---

## Step 5 — Scope boundary line (for Q&A)

If judges ask about what's NOT covered, say verbatim (or refer to `docs/pitch.md`):

> "We cover exposure, purpose limitation, and retention — the subset of DPDPA observable in real-time telemetry. Consent validity, breach workflows, DPO/DPIA governance, and cross-border transfer are organizational processes outside what a monitoring agent can detect from logs and payloads alone."

---

## Quick command reference

| Action | Command |
|---|---|
| Run full pipeline | `python run_pipeline.py` |
| Force all violations | `--violation-rate 1.0` |
| Force LLM fallback | `FORCE_LLM_FALLBACK=1 python run_pipeline.py` |
| Reproducible run | `--seed 42` |
| Finite run (N events) | `--max-events 20` |
| Custom port | `--port 8080` |
| Run all tests | `python -m pytest -v` |
| Corrupt DB (pre-demo) | `python docs/corrupt_demo_entry.py` |
