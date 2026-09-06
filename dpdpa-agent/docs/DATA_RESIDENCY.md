# Veritas — Data Residency & Architecture Security Document

**Version:** 1.0.0  
**Audience:** CTOs, Security Architects, IT Teams  
**Classification:** Share freely with prospective clients

---

## 1. Executive Summary

Veritas is deployed entirely within your own infrastructure. Your production data — including all personally identifiable information (PII), telemetry, compliance verdicts, and audit evidence — **never leaves your network**.

Veritas Technologies (the vendor) ships software. We do not operate infrastructure on your behalf, receive copies of your data, or have any network access to your Veritas installation.

---

## 2. Complete Data Flow Diagram

```
YOUR ORGANISATION'S NETWORK
════════════════════════════════════════════════════════════════════════

  Your Application Services
  (order-service, support-ticketing, marketing-analytics, etc.)
         │
         │  Raw application logs / telemetry
         │  (may contain PII)
         ▼
  ┌─────────────────────────────────┐
  │       VERITAS AGENT             │  ← Deployed by you, runs in
  │  (Docker container / K8s pod)   │    your cluster / server
  │                                 │
  │  • Reads log files or container │
  │    stdout — never modifies them │
  │  • Tags each line with Agent ID │
  │  • Forwards via authenticated   │
  │    HTTP (Bearer token)          │
  └──────────────────┬──────────────┘
                     │  POST /v1/{org}/events
                     │  Authorization: Bearer <token>
                     │  (stays inside your network)
                     ▼
  ┌─────────────────────────────────────────────────────────────────┐
  │                  VERITAS RUNTIME                                 │
  │         (installed on your VM / server / Pi)                    │
  │                                                                 │
  │  ┌─────────────┐    ┌──────────────┐    ┌───────────────────┐  │
  │  │ PII         │    │ Rule Engine  │    │ Evidence Store    │  │
  │  │ Detection   │───▶│ EXPOSURE_001 │───▶│ (SQLite, SHA-256  │  │
  │  │ (spaCy +    │    │ PURPOSE_001  │    │  hash-chained,    │  │
  │  │  Presidio)  │    │ RETENTION_001│    │  append-only)     │  │
  │  └─────────────┘    └──────────────┘    └───────────────────┘  │
  │                                                   │             │
  │                                                   ▼             │
  │                                          ┌─────────────────┐   │
  │                                          │ Dashboard       │   │
  │                                          │ (port 8000)     │   │
  │                                          └────────┬────────┘   │
  └───────────────────────────────────────────────────┼────────────┘
                                                      │
                                                      │  http://your-server:8000
                                                      │  (browser on your LAN)
                                                      ▼
                                             Your Admin's Browser

════════════════════════════════════════════════════════════════════
NETWORK BOUNDARY — nothing crosses this line (see Section 4)
════════════════════════════════════════════════════════════════════

  EXTERNAL (internet)
  └── OpenAI API (OPTIONAL — see Section 5)
```

---

## 3. What Each Component Does (and Does Not Do)

### 3.1 Veritas Agent
| Does | Does Not |
|------|----------|
| Read log lines from configured sources | Store any data persistently |
| Tag each event with an Agent ID | Perform PII detection |
| Forward events to Veritas Runtime over your LAN | Make compliance decisions |
| Send periodic heartbeats | Communicate outside your network |

The Agent is stateless between restarts (except for its registration identity stored in a local `.veritas_state.json` on the machine where it runs).

### 3.2 Veritas Runtime
| Does | Does Not |
|------|----------|
| Detect PII using local models (spaCy, Presidio) | Send your data to any cloud |
| Evaluate DPDPA rules locally | Phone home to Veritas Technologies |
| Store evidence in a local SQLite database | Require internet access to function |
| Serve the dashboard on a local port | Accept connections from outside your network |

### 3.3 Veritas Technologies (the vendor)
| Does | Does Not |
|------|----------|
| Ship the software (installer + license file) | Operate any infrastructure on your behalf |
| Issue license keys | Receive copies of your telemetry or PII |
| Provide support | Have network access to your installation |
| Release software updates | Store any of your organisation's data |

---

## 4. Network Traffic Analysis

### Traffic that stays INSIDE your network (always)
| Source | Destination | Data | Protocol |
|--------|-------------|------|----------|
| Your application services | Veritas Agent | Log lines / telemetry | Local file read or Docker socket |
| Veritas Agent | Veritas Runtime | Tagged log events | HTTPS/HTTP on your LAN |
| Admin browser | Veritas Runtime dashboard | Dashboard UI, verdicts, reports | HTTP on your LAN |
| Veritas Runtime | SQLite evidence store | Compliance verdicts | Local disk write |

### Traffic that crosses the internet (one optional call)
| Source | Destination | Data sent | When |
|--------|-------------|-----------|------|
| Veritas Runtime | OpenAI API | Rule type, field name, DPDPA statute text | ONLY when auditor asks `@N What happened?` and `OPENAI_API_KEY` is configured |

**Critical note on the OpenAI call:** The LLM investigation feature sends a structured prompt containing:
- The rule that fired (e.g., `EXPOSURE_001`)
- The field name (e.g., `phone`)
- Relevant DPDPA statute text (pre-loaded from a local file)

It does **NOT** send:
- Raw log lines
- Actual PII values
- Customer names, phone numbers, Aadhaar numbers, or any other personal data

If you do not configure `OPENAI_API_KEY`, this feature is disabled entirely. All other Veritas functionality — detection, rule evaluation, evidence storage, dashboard, PDF export — operates with **no internet access whatsoever**.

---

## 5. Data Storage Inventory

All storage is local to your infrastructure.

| Data | Location | Format | Access |
|------|----------|--------|--------|
| Compliance violations + audit trail | `{install_dir}/evidence.db` | SQLite, SHA-256 hash-chained | Veritas Runtime only |
| Registered agents | `{install_dir}/agents.db` | SQLite | Veritas Runtime only |
| Organisation configuration (PII fields, purposes, retention policies) | `{install_dir}/org_config/configs/{org_id}/` | YAML files, versioned | Veritas Runtime only |
| License file | `{install_dir}/veritas.vlic` | RSA-signed binary | Veritas Runtime (read only) |
| Agent registration state | `{agent_machine}/.veritas_state.json` | JSON (Agent ID + token) | Veritas Agent only |
| OpenAI API key (optional) | `{install_dir}/.env` | Plain text | Veritas Runtime only |

**Veritas Technologies has no access to any of these files.** They exist solely on machines you control.

---

## 6. Authentication & Security Controls

### Agent-to-Runtime Authentication
- Every Veritas Agent is issued a unique Bearer token at registration time
- Tokens are stored as SHA-256 hashes (never plaintext) in `agents.db`
- Revoked agents are rejected with HTTP 403 immediately
- Cross-organisation token use is rejected (an agent registered to Org A cannot submit to Org B)

### License Enforcement
- License files are RSA-2048 signed by Veritas Technologies' private key
- The private key never leaves Veritas Technologies' systems
- The Runtime contains only the public key — sufficient to verify but not to forge
- Machine-bound licenses include a hardware fingerprint (SHA-256 of disk serial + MAC + hostname) — they cannot be copied to another server

### Evidence Integrity
- The audit evidence store is append-only
- Every record is SHA-256 chained to the previous record (per organisation)
- Chain verification is available from the dashboard at any time
- The `VERIFIED ✓` result proves no records have been tampered with or deleted

---

## 7. DPDPA Compliance Posture of Veritas Itself

Veritas, as a data processor operating within your infrastructure, adheres to the following DPDPA principles:

| Principle | How Veritas applies it |
|-----------|----------------------|
| **Purpose limitation** | Veritas processes your telemetry solely for the purpose of DPDPA compliance monitoring. No other processing occurs. |
| **Data minimisation** | The Agent forwards only log lines you configure. The Runtime stores only compliance metadata (rule, field name, severity) — not the raw PII values themselves. |
| **Storage limitation** | Veritas does not impose a retention policy on its own evidence store (you control this). Raw log lines are never stored by Veritas. |
| **Data localisation** | All processing and storage occurs within your infrastructure. No cross-border transfer takes place (the optional LLM call sends no personal data — see Section 4). |
| **Security** | RSA license signing, Bearer token authentication, SHA-256 hash-chained audit records, input validation, request size limits. |

---

## 8. Deployment Verification Checklist

Use this checklist to independently verify Veritas's data residency claims:

- [ ] Run `netstat -an` on the Veritas Runtime machine — confirm no outbound connections except to OpenAI (if configured)
- [ ] Inspect `evidence.db` with any SQLite browser — confirm it contains only metadata (rule IDs, severity, field names) and no raw PII
- [ ] Run the chain verification from the dashboard — confirm `VERIFIED ✓`
- [ ] Revoke an agent from the dashboard — confirm its subsequent requests return HTTP 403
- [ ] Disable `OPENAI_API_KEY` — confirm all features still work (investigation falls back to template explanations)
- [ ] Review firewall logs — confirm no outbound traffic from the Runtime host (with OpenAI key removed)
