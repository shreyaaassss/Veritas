# Veritas — DPDPA Compliance Agent: Pitch Q&A Reference

Three paragraphs to use verbatim during Q&A. These are not talking points to paraphrase — they are precise scope statements that address the most likely judge questions.

---

## Scope Boundary

> "We cover exposure, purpose limitation, and retention — the subset of DPDPA observable in real-time telemetry. Consent validity, breach workflows, DPO/DPIA governance, and cross-border transfer are organizational processes outside what a monitoring agent can detect from logs and payloads alone."

Use when asked: *"What about consent management?"* / *"Does this handle breach notification?"* / *"What about DPIA obligations?"*

---

## Why Engineers Still Leak PII

> "This isn't about developer carelessness — debug logging, greedy object serialization, and cross-team payload drift make certain classes of leak structurally inevitable at the pace modern teams ship. That's a detection problem, not a discipline problem — which is exactly why Section 8's 'reasonable security safeguards' is framed as an organizational obligation."

Use when asked: *"Shouldn't engineers just not log PII?"* / *"Is this a training problem?"*

---

## Auditor Value

> "The live feed proves detection works in real time. The Evidence Store with remediation tracking is what turns that into something an auditor can actually use a month later — a tamper-proof record of what broke and what's been fixed."

Use when asked: *"Who actually uses this?"* / *"Is the live feed the main feature?"* / *"Why does tamper-evidence matter?"*

---

## What's Mocked vs What's Real

| Component | Status |
|---|---|
| Blinkit DB schema / log format | Simulated (realistic Blinkit-flavored generators) |
| Network traffic / Kafka | Simulated (in-process asyncio queue, same topology) |
| PII detection (Presidio + custom recognizers) | **Real logic** |
| DPDPA rule evaluation (3-check engine) | **Real logic** |
| LLM explanation with statute grounding | **Real logic** (falls back gracefully if no API key) |
| Evidence Store hash chain | **Real logic** (SHA-256, verifiable in live demo) |
| Remediation tracking | **Real logic** |
| Auto-remediation | Out of scope by design |

---

## Hash Chain — If a Judge Probes

The hash chain works as follows:
1. Every verdict's immutable fields are serialized as deterministic JSON.
2. `hash = sha256(json + previous_row_hash)` — the genesis hash is a fixed known string.
3. `remediation_status` and `remediation_updated_at` live in **separate columns**, outside the hashed payload — so marking a verdict RESOLVED never touches the hash.
4. `verify_chain()` recomputes every hash from scratch and compares — any tampered row shows up immediately, and every subsequent row's hash is also wrong (the chain propagates the break).

> "If you edit the SQLite database directly right now, click Verify, and it will tell you exactly which record was tampered with."
