# DPDPA Agent — Architecture

---

## System Flow

```
┌─────────────────────────────────────────────────────────────────────┐
│                     TELEMETRY SOURCES (Blinkit)                     │
│                                                                     │
│   order-service  │  delivery-partner-service  │  support-ticketing  │
│                        marketing-analytics                          │
│                                                                     │
│   Source types: application logs  │  API request/response payloads  │
└──────────────────────────┬──────────────────────────────────────────┘
                           │  raw log lines / API payloads
                           ▼
┌─────────────────────────────────────────────────────────────────────┐
│                   INGESTION PIPELINE  (Phase 2)                     │
│                                                                     │
│  • Log simulator (Python / Faker) — generates realistic Blinkit     │
│    telemetry with embedded Indian PII for demo purposes             │
│  • WebSocket streamer — pushes Event objects to downstream          │
│  • Produces: Event  (schema: /schemas/event_schema.json)            │
└──────────────────────────┬──────────────────────────────────────────┘
                           │  Event objects
                           ▼
┌─────────────────────────────────────────────────────────────────────┐
│                    PII DETECTION  (Phase 3)                         │
│                                                                     │
│  • Presidio Analyzer — NER-based entity recognition                 │
│  • Custom regex recognizers for Indian identifiers:                 │
│      Aadhaar  \d{4}\s\d{4}\s\d{4}                                   │
│      PAN      [A-Z]{5}[0-9]{4}[A-Z]                                 │
│      UPI      [\w.-]+@[\w]+                                         │
│      Phone    [6-9]\d{9}                                            │
│  • Annotates Event.fields with detected PII types                   │
│  • Locked PII types: name, email, phone, aadhaar, pan               │
└──────────────────────────┬──────────────────────────────────────────┘
                           │  PII-annotated Event objects
                           ▼
┌─────────────────────────────────────────────────────────────────────┐
│              RULE ENGINE / POLICY DECISION POINT  (Phase 4)         │
│                                                                     │
│  Evaluates each annotated Event against three rule categories:      │
│                                                                     │
│  EXPOSURE_001   — PII present in log/API where it must not be       │
│  PURPOSE_001    — Field present but not in endpoint's consent scope  │
│  RETENTION_001  — Data record exceeds consent expiry window         │
│                                                                     │
│  Checks Event against Consent Registry (Phase 1) per source_system  │
│  and endpoint. Produces a Verdict for each rule violation.          │
│                                                                     │
│  Produces: Verdict  (schema: /schemas/verdict_schema.json)          │
└──────────┬───────────────────────────────────────────┬─────────────┘
           │  Verdict objects                          │  Verdict objects
           ▼                                          ▼
┌─────────────────────────┐              ┌─────────────────────────────┐
│   LLM EXPLAINER (Ph 5)  │              │   EVIDENCE STORE  (Phase 6) │
│                         │              │                             │
│  Claude API (Sonnet)    │              │  SQLite database            │
│  Per-verdict:           │              │  Append-only writes         │
│  • Plain-English reason │              │  SHA-256 hash chain over    │
│  • DPDPA section ref    │              │  immutable Verdict fields   │
│  • Remediation advice   │              │  Only remediation_status    │
│                         │              │  and remediation_updated_at │
│  Attaches explanation   │              │  may be updated post-write  │
│  to Verdict before      │              │                             │
│  dashboard broadcast    │              │  Audit export: point-in-    │
│                         │              │  time snapshot for Priya    │
└─────────┬───────────────┘              └──────────────┬──────────────┘
          │  Verdict + explanation                      │  stored verdicts
          └─────────────────────┬───────────────────────┘
                                ▼
┌─────────────────────────────────────────────────────────────────────┐
│                    LIVE DASHBOARD  (Phase 7)                        │
│                                                                     │
│  React frontend — two views:                                        │
│                                                                     │
│  LIVE FEED VIEW                  EVIDENCE / AUDIT VIEW              │
│  ├─ Real-time violation stream   ├─ Filterable by source_system     │
│  ├─ Severity counters            ├─ Filterable by rule_id           │
│  │   (HIGH / MEDIUM / LOW)       ├─ Filterable by severity          │
│  ├─ Source system breakdown      ├─ Remediation status tracker      │
│  └─ Per-verdict:                 └─ One-click PDF audit export      │
│      rule, field, explanation,       (for auditor Priya)            │
│      remediation suggestion                                         │
└─────────────────────────────────────────────────────────────────────┘
```

---

## Reference Stack

| Component | Library / Pattern | Usage type | Phase |
|---|---|---|---|
| Schema validation | Pydantic v2 | Direct dependency | 0 |
| API framework | FastAPI + Uvicorn | Direct dependency | 0, 2, 4, 6 |
| PII detection | Microsoft Presidio (`presidio-analyzer`, `presidio-anonymizer`) | Direct dependency | 3 |
| Indian PII patterns | Custom regex (Aadhaar, PAN, UPI, Phone) | Built in-house | 3 |
| Log simulation | Python Faker with Indian locale | Direct dependency | 2 |
| LLM explainer | Anthropic Claude API (`claude-sonnet-4-6`) | Direct dependency | 5 |
| Evidence store | SQLite via `aiosqlite` | Direct dependency | 6 |
| Hash chain | Python `hashlib` (stdlib) | Direct dependency | 6 |
| Real-time transport | FastAPI WebSocket | Direct dependency | 2, 7 |
| Frontend | React + Recharts | Direct dependency | 7 |
| DPDPA rule taxonomy | `amjadali-110/DPDPA-Checklist` (GitHub) | **Reference only** — not a code dependency | 0 |
| Go PII scanners | Various open-source Go implementations | **Conceptual reference only** — pattern inspiration for regex | 3 |
| pii-masker / pII-guard | Open source masking libraries | **Stretch goal** — evaluate in Phase 3 | 3 |

---

## Tech Stack — Locked after Phase 0

| Layer | Technology |
|---|---|
| Backend language | Python 3.11+ |
| API framework | FastAPI |
| Data validation | Pydantic v2 |
| Database | SQLite (file-based, no server setup) |
| LLM | Claude API (Anthropic) |
| PII detection | Presidio + custom Indian regex |
| Frontend | React (Vite) |
| Real-time | WebSocket (FastAPI native) |
| Testing | pytest |

Do not substitute another framework without team agreement — all module owners have assumed this stack.

---

## Key Design Decisions

**Why SQLite and not PostgreSQL?**
Hackathon MVP — SQLite is zero-config, fully self-contained, and sufficient for the demo volume. The schema is migration-ready for PostgreSQL in production.

**Why WebSocket and not Kafka?**
Kafka adds operational overhead (broker, zookeeper/KRaft) that isn't justified for a demo. WebSocket via FastAPI gives real-time push with a single process. The ingestion interface (Event schema) is Kafka-compatible if needed later.

**Why Claude API for the LLM explainer?**
Claude's structured output is reliable for classification tasks (severity, rule citation). The system prompt can be locked to DPDPA-specific reasoning without general-purpose drift.
