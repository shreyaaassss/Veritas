# DPDPA Compliance Monitoring Agent

An autonomous, real-time compliance monitoring agent for India's **Digital Personal Data Protection Act (DPDPA)**. Built around **Blinkit** as the example organization, the system ingests streaming application logs and API payloads, evaluates them against core DPDPA rules, and surfaces violations on a live dashboard with AI-generated explanations and remediation guidance.

**Primary user:** Priya — a compliance auditor running monthly DPDPA audits.

---

## Quick Links

- [Scope — what this agent does and does not do](docs/scope.md)
- [Architecture — system flow and reference stack](docs/architecture.md)
- [Module Ownership](docs/ownership.md)

---

## Project Structure

```
/schemas/           — Frozen event + verdict contracts (Phase 0)
/registry/          — Consent registry (Phase 1)
/ingestion/         — Log simulator + WebSocket streamer (Phase 2)
/detection/         — PII detection engine — Presidio + Indian regex (Phase 3)
/rules/             — Rule engine / Policy Decision Point (Phase 4)
/llm_explainer/     — Claude API explainer + guardrails (Phase 5)
/evidence_store/    — Append-only SQLite + hash chain (Phase 6)
/dashboard/         — React live feed + audit view (Phase 7)
/docs/              — Scope, architecture, ownership docs
```

---

## Running Schema Tests (Phase 0)

```bash
python -m venv venv
source venv/bin/activate       # Windows: venv\Scripts\activate
pip install pydantic pytest
python -m pytest schemas/test_schemas.py -v
```

---

## Three Rules This Agent Enforces

| Rule | What It Catches |
|---|---|
| `EXPOSURE_001` | PII (Aadhaar, PAN, phone, email, name) appearing in logs or API responses where it shouldn't |
| `PURPOSE_001` | Data fields flowing through an endpoint that declared it wouldn't use them |
| `RETENTION_001` | Data records present in a system past their consent expiry window |

Penalties under DPDPA: up to **₹250 crore per violation**.
