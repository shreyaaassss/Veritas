# Integrate Veritas in 4 Steps

Veritas is a config-driven DPDPA compliance engine. The detection logic, validators, linkage-risk analysis, tamper-evident evidence chain, and dashboard are all **built once and shared by every organisation** — what makes it *yours* is a single YAML config. Everything below is real, working behavior on the current codebase (Blinkit and EdTech Co both run on it today, with zero code differences between them).

---

## Step 1 — Write your config

An Org Config has three sections. Minimal example:

```yaml
org_id: acme_corp

identifiers:
  - name: employee_id
    pattern: "^EMP-[0-9]{6}$"
    validator: none

fields:
  - field_name: employee_id
    pii_category: identity
    declared_purpose: hr_records
    consent_scope: hr_records
    retention_days: 365
    source_system: hris
  - field_name: home_address
    pii_category: address
    declared_purpose: hr_records
    consent_scope: hr_records
    retention_days: 365
    source_system: hris

linkage_rules:
  - fields: [employee_id, home_address]
    risk: LINKAGE_RISK
```

(This validates cleanly — see Step 2's error example for what happens if you forget to declare one of the fields a `linkage_rules` entry references.)

- **`identifiers`** — custom PII shapes your org cares about beyond the built-ins (name, email, generic phone). Each has a regex `pattern` (or a built-in alias like `indian_phone`) and an optional `validator` (`none`, `pan`, or `aadhaar` today — see Step 1a below).
- **`fields`** — every PII field you collect: what it *is* (`pii_category`), *why* you collect it (`declared_purpose`/`consent_scope`), how long you may keep it (`retention_days`), and which system it flows through (`source_system`). This is the ground truth Veritas checks every event against.
- **`linkage_rules`** — combinations of individually-harmless fields that jointly re-identify someone (e.g. name + school + date of birth). No single field here needs to look like PII on its own.

**Honest limitation, not a bug:** a custom identifier with `validator: none` is still detected (via its pattern) and tagged `confidence: "pattern_match"` — it just isn't checksum-confirmed. Only `pan` and `aadhaar` have real validators today (structural check and Verhoeff checksum respectively). If your identifier has no public checksum spec, `none` is the correct, honest choice — don't invent one.

---

## Step 2 — Register it

```bash
curl -X POST http://localhost:8000/v1/orgs/acme_corp/config \
  -H "Content-Type: application/json" \
  -d @acme_corp_config.json
```

**Success:**
```json
{"status": "ok", "org_id": "acme_corp", "version": "2026-08-21T19-39-55Z.yaml"}
```

**Validation error** (real example — a `linkage_rules` entry referencing a field never declared in `fields`):
```json
{
  "status": "error",
  "org_id": "acme_corp",
  "errors": [
    "linkage_rules[0] references field(s) not declared in 'fields': ['home_address']. Declare them in fields first, or fix the typo."
  ]
}
```
Nothing is stored on a validation error — your previous version (if any) stays live. Every error is specific and actionable; "bad config" is never a valid message here.

A config is usable **immediately** on success — no restart, no deploy.

---

## Step 3 — Integrate

Three modes, one detection+rule engine underneath — pick based on how your data reaches Veritas:

| Mode | Endpoint | Use it for |
|---|---|---|
| **Stream** | `POST /v1/{org_id}/events` | Continuous monitoring — point your log/event pipeline at this for every request/event you want watched. |
| **Scan** | `POST /v1/{org_id}/scan` | One-off, ad-hoc checks from anywhere in your stack — pass `text` (a raw string) and/or `fields` (structured, matching your declared field names). Returns detected entities, any Exposure/Purpose/Retention/Linkage verdicts, and a masked/redacted copy of your input (`[AADHAAR_REDACTED]`, `[PHONE_REDACTED]`, etc.) — never the generic form-losing `[REDACTED]`. |
| **CLI** | `veritas_scan_cli.py` | Pipe debug output through a scan from a terminal — `kubectl logs <pod> \| python veritas_scan_cli.py --org acme_corp`. **Roadmap-stage proof-of-concept, not production-ready**: no retries, no streaming, no CI/CD tooling — it's a thin wrapper that calls `/scan` over real HTTP, nothing more. |

---

## Step 4 — Review

Every violation Veritas detects — whether from Stream or Scan — lands in **your org's own** append-only, SHA-256 hash-chained Evidence Store. Each org's chain is independent: verifying yours never touches, and can never be affected by, another org's data. This is what the monthly compliance auditor actually opens day to day — the dashboard's **Audit view**, filtered strictly to your `org_id`, with one click to run `verify_chain()` and confirm nothing has been retroactively altered.

---

## What's generic vs. what's yours

| Built-in (shared, reusable) | Supplied by you (config) |
|---|---|
| Detection engine (Presidio + custom Indian-identifier recognizers) | Which fields exist and what they mean |
| Validators (PAN structural check, Aadhaar Verhoeff checksum) | Retention periods |
| Linkage-risk / combination detection | Which identifiers matter, and their patterns |
| Tamper-evident evidence chain | Declared purpose / consent scope labels |
| Dashboard (Live Feed + Audit, per-org scoped) | Which combinations count as linkage risk |

For exhaustive API reference (full request/response schemas, every field), see the code directly — `api/integration.py` for endpoints, `org_config/schema.py` for the config format. This doc is a quickstart, not a spec.
