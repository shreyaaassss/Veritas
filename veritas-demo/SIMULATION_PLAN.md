# Veritas Enterprise Simulation: Build Spec

**Audience: the engineer or coding agent implementing the simulation.** Read this whole file first. It is written to be followed in order: facts about the system under test, then phases 0 to 6, each with tasks, commands and acceptance criteria. Do not skip a phase's acceptance criteria; the next phase assumes they pass.

Status: spec only. Nothing in this spec has been built yet.

## 0. Goal, scope, rules

**Goal.** Build a simulated enterprise that continuously sends realistic telemetry to a real Veritas server, and a checker that proves Veritas detected the right things. It stands in for a customer's production environment so we can test the product the way a customer would run it.

**Decisions (fixed, do not revisit):**

| Topic | Decision |
|---|---|
| Scale target | About 100 events/s total across about 10 agents (first version) |
| Organisations | One generic simulated org with id `sim_enterprise`, created through the normal onboarding API like any customer |
| Ground truth | Required. The simulator records what it injected and a checker compares it with what Veritas stored |
| Where it runs | AWS. `sim-host` EC2 (simulator + agents, Docker Compose) and `veritas-server` EC2 (Ubuntu 22.04, the released `.deb`). Never on a developer laptop |

**Rules:**
1. **Do not edit product code** under `dpdpa-agent/`, `veritas-agent/` or `veritas-launcher/`. If you find a product bug, write it to `veritas-demo/FINDINGS.md` (what happened, request/response, expected) and work around it. The product maintainers fix it.
2. **No customer names anywhere.** The org is `sim_enterprise`. Use generic company and service names.
3. **No secrets in git.** Credentials and tokens come from environment variables or an untracked `.env` (add `.env` and `sim/state/` to `.gitignore`).
4. **All code goes under `veritas-demo/sim/`.** The old four-service demo in `veritas-demo/services/` may be reused or deleted, but nothing else in the repo changes.
5. **Every phase ends with passing acceptance checks and a short entry in `veritas-demo/sim/PROGRESS.md`.**

## 1. System under test: what you need to know

Veritas receives events over HTTP, detects personal data (PII), applies rules from the org's uploaded policy, and stores verdicts (violations) in a tamper-evident store.

### 1.1 Roles and accounts
- First run: `POST /api/auth/setup` creates the first SUPER_ADMIN, only when no users exist.
- Roles: `SUPER_ADMIN`, `COMPLIANCE_ADMIN`, `AUDITOR`, `VIEWER`. Org config upload and agent key issuing need `COMPLIANCE_ADMIN` or higher.
- **Rate limits (per IP, in memory):** login 10 per 5 min, setup 3 per 10 min, agent registration and key issuing a few dozen per hour. Log in once, keep the cookie, and reuse it. Never log in per request.

### 1.2 Endpoints you will call

All HTTPS on port 8000. The server certificate is self-signed: use `verify=False` (or trust the cert from `GET /api/tls/cert`).

| Purpose | Call | Auth | Notes |
|---|---|---|---|
| Health | `GET /health` | none | `{"status":"ok"}` |
| Create first admin | `POST /api/auth/setup` JSON `{username,email,password}` | none (only when no users) | Password must pass the strength check |
| Login | `POST /api/auth/login` form fields `username`, `password` | none | Sets an httpOnly session cookie. Use a `requests.Session` / cookie jar |
| Create user | `POST /api/auth/users` (see `CreateUserRequest` in `dpdpa-agent/api/auth.py`) | SUPER_ADMIN cookie | Used to create the checker's AUDITOR account |
| Upload org config | `POST /v1/orgs/{org}/config` JSON body = the config | COMPLIANCE_ADMIN cookie | Returns `{"status":"ok"}` or `{"status":"error","errors":[...]}`. Takes effect immediately |
| Issue agent key | `POST /agents/issue-key` JSON `{"org_id":"sim_enterprise"}` | COMPLIANCE_ADMIN cookie | One-time key, **expires in 1800 s** |
| Register agent | `POST /agent/register` JSON `{"registration_key":"...","source_label":"host-03"}` | none | Returns `agent_id`, `auth_token` (shown once), `org_id`, `event_endpoint` |
| Heartbeat | `POST /agent/heartbeat` JSON `{"agent_id":"..."}` | `Authorization: Bearer <agent token>` | |
| **Send an event** | `POST /v1/{org}/events` | `Authorization: Bearer <agent token>` | See 1.3 |
| List verdicts | `GET /api/{org}/verdicts` (optional `source_system`, `severity`, `remediation_status`, `date_start`, `date_end`) | cookie | Returns `{"verdicts":[...],"count":N}`. Rows include `event_id`, `rule_id`, `severity`, `source_system`, `field`, `timestamp`; they do **not** include raw text |
| One verdict | `GET /api/{org}/verdicts/{verdict_id}` | cookie | |
| Verify evidence chain | `GET /api/{org}/verify-chain` | cookie | Run at the end of every test |
| Agents list | `GET /agents` | cookie | Shows status and events received |
| Revoke agent | `POST /agents/{agent_id}/revoke` | COMPLIANCE_ADMIN cookie | |
| Scan | `POST /v1/{org}/scan` JSON `{"text":"..."}` or `{"fields":{...},"source_system":"..."}` | cookie | Use to debug detection |

### 1.3 The event request (the core contract)
```json
POST /v1/sim_enterprise/events
Authorization: Bearer <agent token>

{
  "source_type": "log",                 // "log" or "api" (lower-case; upper-case accepted only on servers newer than v1.0.16, see 1.5 issue 1)
  "source_system": "order-service",     // must match a source_system in the org config
  "timestamp": "2026-10-05T09:14:02Z",  // optional, defaults to server time; used for retention math
  "raw_snippet": "free text of the log line or payload",
  "fields": {"phone": "9876543210"}     // optional structured key/values, max 50 fields
}
```
Response (HTTP 200), synchronous, **contains the verdicts**:
```json
{
  "org_id": "sim_enterprise",
  "event_id": "<uuid>",
  "contains_pii": true,
  "entities": [{"field":"...","entity_type":"IN_PHONE","confidence":0.75,"validation_status":"..."}],
  "verdicts": [{"verdict_id":"...","violation_id":12,"rule_id":"EXPOSURE_001","severity":"HIGH",
                "field":"...","source_system":"order-service","stored_in_evidence_store":true}]
}
```
This synchronous response is what makes exact per-event checking possible in Mode A (section 4).

### 1.4 What the rule engine does (this defines expected results)

For each PII match, checks run in order and **the first to fire wins; one rule per field**. Linkage runs separately per event.

| Order | Rule | Fires when | Severity |
|---|---|---|---|
| 1 | `EXPOSURE_001` | `source_type` is `log` and any PII is detected, **or** the matched field is registered with consent scope `deidentified_or_hashed_only` for that source_system | HIGH |
| 2 | `PURPOSE_001` | PII in a field that is **not declared** for that `source_system` (unregistered field), or the declared `pii_category` differs from the detected category | HIGH for aadhaar/pan, MEDIUM for phone/email, LOW for name; unregistered default MEDIUM (HIGH for aadhaar/pan) |
| 3 | `RETENTION_001` | field is registered and `event timestamp - data_since` is greater than `retention_days` | by category, same mapping |
| separate | `LINKAGE_001` | **all** fields of a configured `linkage_rules` entry are present (non-empty) in `fields`, whether or not any PII is detected | MEDIUM |

Practical consequences you must encode in the generators:
- **Every log line (`source_type: "log"`) that contains PII produces `EXPOSURE_001` HIGH.** A clean log line produces nothing. This is what the real agent always sends (it sends `fields: {}`), so agent traffic can only produce `EXPOSURE_001`.
- `PURPOSE_001`, `RETENTION_001` and `LINKAGE_001` can only be produced with `source_type: "api"` events that carry structured `fields`. Use Mode A for these.
- Severity: aadhaar and pan are HIGH, phone and email MEDIUM, name and address LOW. `breach_notification_candidate` is true only for HIGH aadhaar/pan.
- PII types detected: person name, email, phone (Indian mobile), Aadhaar, PAN.
- **Aadhaar must be Verhoeff-valid and PAN must match `AAAAA9999A` with a valid holder letter**, otherwise detection discards or downgrades it. Implement a standard Verhoeff check-digit generator in your generator; do not use random 12 digits. Spaced form `1234 5678 9012` scores higher than unspaced.
- Names are the weakest detector (spaCy). Use common Indian first and last names; any detected "name" containing a digit is filtered out on purpose.
- **One log line can produce several verdicts**: one `EXPOSURE_001` per distinct PII value found (field name is `raw_snippet` for log events). A line with a PAN and an email gives 2 verdicts; a line with a name, phone and address gave 3 in testing (the building name "Green Meadows" was tagged as a person). Count verdicts, do not assume 1 per line.
- Names and addresses are non-deterministic (spaCy). **Use aadhaar, PAN, phone and email for anything you count exactly.** Names are fine where you only need "at least one verdict".
- A registered field whose value is detected as a different category than declared also fires `PURPOSE_001` (LOW). Example seen in testing: `delivery_address` containing "Green Meadows" produced `PURPOSE_001` LOW next to `LINKAGE_001`. Prefer addresses without proper-noun building names, and tolerate this extra where you cannot avoid it (see `allowed_extra` in section 5).
- The expectations in this section were checked against the product's rule and detection code with the org config in section 3 (results in section 5). **Phase 2 verifies every one of them against the live server** before they are used as ground truth.

### 1.5 Known defects and gotchas (verified in code)

| # | Issue | What to do |
|---|---|---|
| 1 | **Agent/server `source_type` mismatch was fixed on `main` after release v1.0.16.** The v1.0.16 agent sent `"LOG"`, the v1.0.16 server rejected it with HTTP 422, and the agent retried the same event forever. | For Mode B build the **agent image from this repo** (`veritas-agent/Dockerfile`), and run a server built from `main` or newer than v1.0.16. A v1.0.16 server plus the v1.0.16 agent will not work. Check in Phase 4. For Mode A send lower-case `"log"`/`"api"`, which works everywhere |
| 2 | `/events` runs detection inside the request; no queue, one lock on the evidence store. | Expect the server to saturate somewhere below 100 events/s. Record where. This is a finding, not a simulator bug |
| 3 | The agent sends one HTTP POST per line, one worker per agent. Its throughput is `1 / server latency`. | Measure per-agent rate in Phase 4 before choosing how many agents to run |
| 4 | Verdict rows do **not** contain the raw text, and for agent traffic the server generates `event_id`. A `sim=<id>` token inside the log line does not come back. | Mode B is compared **in aggregate** (counts per source_system and rule per time window), not per event |
| 5 | Registration keys are one-time and expire after 30 minutes. | Issue the key immediately before starting each agent |
| 6 | Agent state (identity) is stored in a volume; if you delete it the agent needs a new key. | One named volume per agent |
| 7 | A `source_system` or field that is not in the org config produces `PURPOSE_001` for PII, not an error. | Declare every source_system and field the generators use |
| 8 | Service memory is capped at 800 MB by the systemd unit. | For load runs on `veritas-server`, raise it: `sudo systemctl edit veritas` then add `[Service]` and `MemoryMax=3G` |
| 9 | The live feed is a per-org WebSocket broadcast. Do not watch the dashboard during load tests. | Use the checker's report |
| 10 | Unspaced 12-digit numbers resemble Aadhaar (known detector weakness). | Decoys of that shape are recorded as `known_weak`, not failures |
| 11 | The server needs a license or it will not start. | Ask the maintainers for a `.vlic` bound to the `veritas-server` fingerprint (`sudo veritas fingerprint`) |

## 2. Repository layout to create

```
veritas-demo/
  SIMULATION_PLAN.md          this file
  FINDINGS.md                 product bugs found (create when first needed)
  sim/
    PROGRESS.md               one entry per finished phase
    README.md                 how to run everything (keep current)
    pyproject.toml            Python 3.11+, deps: httpx, pyyaml, rich, pytest, typer
    simlib/
      client.py               Veritas API client (login once, cookie jar, TLS off, retries, timing)
      config.py               loads env + scenario YAML
      verhoeff.py             Verhoeff check digit, valid Aadhaar and PAN generators
      corpus.py               event templates, each with its expected result (section 5)
      driver.py               rate-controlled sender (Mode A)
      groundtruth.py          writes/reads the manifest JSONL
      checker.py              compares expected vs actual
      report.py               prints and writes the report
    cli.py                    commands: bootstrap, send, ramp, report, agents-up, agents-down, scenario
    orgs/
      sim_enterprise.yaml     the org config (section 3)
    scenarios/                scenario YAML files (section 6)
    compose/
      docker-compose.agents.yml   N hosts, each with services + its own agent
      agent-config.template.yaml
    tests/                    unit tests for simlib (no network)
    state/                    (untracked) agent tokens, manifests, reports
```

Environment variables (document in `sim/README.md`; none committed):
`VERITAS_URL` (e.g. `https://10.0.1.20:8000`), `VERITAS_ADMIN_USER`, `VERITAS_ADMIN_PASSWORD`, `VERITAS_ORG` (default `sim_enterprise`), `SIM_STATE_DIR` (default `sim/state`).

## 3. The org config to upload (`sim/orgs/sim_enterprise.yaml`)

Use this as the starting point. It is written to be valid against the product's schema.

```yaml
org_id: sim_enterprise

identifiers:                    # identifier patterns + validators used by detection
  - {name: aadhaar, pattern: indian_aadhaar, validator: aadhaar}
  - {name: pan,     pattern: indian_pan,     validator: pan}
  - {name: phone,   pattern: indian_phone,   validator: none}

fields:
  # order-service: order fulfilment data
  - {field_name: name,             pii_category: name,    declared_purpose: order_fulfillment, consent_scope: order_fulfillment, retention_days: 1095, source_system: order-service}
  - {field_name: phone,            pii_category: phone,   declared_purpose: order_fulfillment, consent_scope: order_fulfillment, retention_days: 1095, source_system: order-service}
  - {field_name: delivery_address, pii_category: address, declared_purpose: order_fulfillment, consent_scope: order_fulfillment, retention_days: 1095, source_system: order-service}

  # payments-service
  - {field_name: pan,   pii_category: pan,   declared_purpose: payment_verification, consent_scope: payment_verification, retention_days: 365, source_system: payments-service}
  - {field_name: email, pii_category: email, declared_purpose: payment_receipts,     consent_scope: payment_receipts,     retention_days: 365, source_system: payments-service}

  # kyc-service: holds old data, so aadhaar is past its 180-day window
  - {field_name: aadhaar, pii_category: aadhaar, declared_purpose: onboarding_kyc, consent_scope: onboarding_kyc, retention_days: 180, source_system: kyc-service, data_since: 2025-01-01}
  - {field_name: pan,     pii_category: pan,     declared_purpose: onboarding_kyc, consent_scope: onboarding_kyc, retention_days: 180, source_system: kyc-service, data_since: 2026-09-01}

  # support-ticketing
  - {field_name: name,            pii_category: name,            declared_purpose: customer_support, consent_scope: customer_support, retention_days: 730, source_system: support-ticketing}
  - {field_name: phone,           pii_category: phone,           declared_purpose: customer_support, consent_scope: customer_support, retention_days: 730, source_system: support-ticketing}
  - {field_name: order_reference, pii_category: order_reference, declared_purpose: customer_support, consent_scope: customer_support, retention_days: 730, source_system: support-ticketing}

  # marketing-analytics: de-identified data only; any raw PII here is a violation
  - {field_name: hashed_customer_id, pii_category: hashed_identifier, declared_purpose: marketing_analytics, consent_scope: deidentified_or_hashed_only, retention_days: 365, source_system: marketing-analytics}
  - {field_name: campaign_segment,   pii_category: hashed_identifier, declared_purpose: marketing_analytics, consent_scope: deidentified_or_hashed_only, retention_days: 365, source_system: marketing-analytics}
  - {field_name: phone,              pii_category: phone,             declared_purpose: marketing_analytics, consent_scope: deidentified_or_hashed_only, retention_days: 365, source_system: marketing-analytics}

  # auth-service
  - {field_name: email, pii_category: email, declared_purpose: authentication, consent_scope: authentication, retention_days: 1095, source_system: auth-service}
  - {field_name: phone, pii_category: phone, declared_purpose: authentication, consent_scope: authentication, retention_days: 1095, source_system: auth-service}

linkage_rules:
  - fields: [name, phone, delivery_address]
  - fields: [name, email]
```
If the upload returns `status: error`, read `errors` and fix the YAML; do not change the product. Keep `sim/orgs/sim_enterprise.yaml` as the single source and have `sim bootstrap` upload it. Note `data_since` dates are fixed; if the run happens long after them, the retention outcome for `kyc-service` aadhaar stays "violation" (older), and for `kyc-service` pan it eventually becomes one too after 180 days. Pick the dates so the expected results in section 5 hold on the day you run, or compute them in `bootstrap` from today's date.

## 4. Two modes (build Mode A first)

| | **Mode A: direct driver** | **Mode B: real agents** |
|---|---|---|
| What sends events | Your Python driver POSTs straight to `/v1/{org}/events` using one agent's token | The real `veritas-agent` containers tail log files your services write |
| Rules you can test | all four (uses `source_type: "api"` with `fields` for purpose/retention/linkage) | `EXPOSURE_001` only (the agent sends `log` with no fields) |
| How correctness is checked | **Exact, per event**, from the synchronous response (and cross-checked against `GET /verdicts` by `event_id`) | **Aggregate**: expected verdict count per (source_system, rule) vs stored count over the run window, using only deterministic templates (section 5) |
| Max rate | limited by the server, not the agent | limited by the server and the per-agent worker (issue 3) |
| Purpose | accuracy, load ramp, rules coverage | end-to-end realism, agent behavior, failure scenarios |

## 5. Event corpus (`simlib/corpus.py`)

Every template returns `(event_payload, expected)`. `expected` is a list of `{rule_id, severity, field?}` entries, plus `count` (exact number of verdicts, or `min`/`max` where detection is non-deterministic) and an optional `allowed_extra` list (verdicts that may appear without failing the check). Clean events expect no verdicts. Build at least these, with randomized values:

| Kind | Mode | Payload sketch | Expected (checked in-process against the section 3 config) |
|---|---|---|---|
| `clean_log` | A, B | order status, zone/ETA, payment amount lines with order ids like `ORD-123456` | no PII, no verdict |
| `log_pii_name_phone` | A, B | debug dump of `{name, phone, address}` | `EXPOSURE_001` HIGH, `min` 1 (observed 3: name, phone and a building name tagged as a person) |
| `log_pii_aadhaar` | A, B | "customer verified with Aadhaar <valid spaced>" | exactly 1 `EXPOSURE_001` HIGH, `breach_notification_candidate: true` |
| `log_pii_phone` | A, B | line containing one valid Indian mobile number only | exactly 1 `EXPOSURE_001` (confirm in Phase 2); this is the deterministic phone case for Mode B counts |
| `log_pii_pan_email` | A, B | PAN and email in a payment log | exactly 2 `EXPOSURE_001` HIGH (PAN one has breach candidate true) |
| `api_clean_marketing` | A | marketing event with only `hashed_customer_id` and `campaign_segment` | no verdict |
| `api_marketing_raw_phone` | A | marketing event with raw `phone` | exactly 1 `EXPOSURE_001` HIGH on field `phone` (de-identified scope forbids raw PII) |
| `api_unregistered_field` | A | order-service event with an undeclared field `mother_maiden_name` whose value is an email | exactly 1 `PURPOSE_001` MEDIUM on that field |
| `api_retention_aadhaar` | A | kyc-service `aadhaar` field with a valid Aadhaar | exactly 1 `RETENTION_001` HIGH, breach candidate true (data older than 180 days) |
| `api_registered_ok` | A | order-service `name` and `phone` fields with valid values | contains PII but no verdict |
| `api_linkage_triple` | A | `name`, `phone`, `delivery_address` present together | `LINKAGE_001` MEDIUM; `allowed_extra`: `PURPOSE_001` LOW on `delivery_address` (observed when the address has a building name) |
| `decoy_order_id_12digit` | A, B | unspaced 12-digit number in an order log | observed: no PII detected. Tag `known_weak`; record the actual outcome, do not fail |
| `decoy_masked` | A, B | `XXXXXXXX4521`, `[REDACTED]` | no verdict |

**Mode B aggregate counts** (section 4) use only the deterministic log kinds: `clean_log`, `log_pii_aadhaar` (1 verdict per line), `log_pii_phone` (1), `log_pii_pan_email` (2), `decoy_masked`. Keep name and address lines out of the Mode B mix, or give them a wide tolerance, because their verdict count per line varies.

**Verify each template against the live server in Phase 2 before relying on its expectation.** If the server disagrees, first check the generator (valid Aadhaar and PAN, field names declared in the org config). If the generator is right and the server is wrong, log it in `FINDINGS.md` and mark the template `expected_unverified` so it does not count as a failure.

## 6. Scenario file format (`sim/scenarios/*.yaml`)

```yaml
name: steady-100
mode: A                      # A or B
duration_seconds: 300
target_eps: 100              # events per second, total
workers: 20                  # concurrent senders
violation_ratio: 0.15        # share of events that should produce a verdict (informational; the mix decides)
mix:                         # weights per corpus kind, normalised
  clean_log: 45
  log_pii_name_phone: 8
  log_pii_aadhaar: 2
  log_pii_phone: 4
  log_pii_pan_email: 2
  api_registered_ok: 15
  api_marketing_raw_phone: 3
  api_unregistered_field: 3
  api_retention_aadhaar: 3
  api_linkage_triple: 3
  decoy_masked: 4
  decoy_order_id_12digit: 4
profile:                     # optional shape over time
  type: constant             # constant | ramp | burst | diurnal
```
Profiles: `constant`; `ramp` (steps of eps, e.g. 10, 25, 50, 100, each held 60 s); `burst` (base eps with spikes of N times for S seconds); `diurnal` (sinusoid compressed into the run length).

## 7. Phases

Each phase lists tasks, then **Acceptance** (checkable) and **Stop and report** conditions. Work phase by phase. Commit after each phase.

### Phase 0: Environment and baseline
Goal: two machines up, the product reachable, one event sent by hand.

Tasks:
1. Create `veritas-server` (Ubuntu 22.04, 4 GB RAM or more, 20 GB disk) and `sim-host` (any Linux with Docker, 2 vCPU, 4 GB). Same VPC. Security group on the server: TCP 8000 from `sim-host` only, SSH from your IP. Document the private IPs.
2. On the server install a release `.deb` newer than v1.0.16 if available, otherwise v1.0.16 (see issue 1 for the consequence): `sudo apt install ./veritas_<ver>_amd64.deb`. Get its fingerprint with `sudo veritas fingerprint`. Obtain a license from the maintainers and install it with `sudo veritas license <file>`. Confirm `curl -k https://localhost:8000/health`.
3. Raise the memory cap for load work (issue 8).
4. `POST /api/auth/setup` to create the admin (store the credentials in `.env` on `sim-host` only).
5. Upload `sim_enterprise.yaml` (section 3) via `POST /v1/orgs/sim_enterprise/config`.
6. Issue an agent key, register one agent with `source_label: baseline`, and send one hand-written event from `sim-host` with `curl` (use `"source_type": "log"`). Confirm the response shape (section 1.3) and that `GET /api/sim_enterprise/verdicts` shows the verdict.
7. Send a `log` event containing a valid Aadhaar (for example `2345 6789 0124`) and check the response.

Acceptance:
- `curl -k https://<server>:8000/health` returns ok from `sim-host`.
- The hand-sent log event with a valid Aadhaar returns one `EXPOSURE_001` HIGH verdict with `breach_notification_candidate: true`.
- `GET /api/sim_enterprise/verify-chain` reports a valid chain.
- Server private IP, instance sizes and the product version are written in `sim/PROGRESS.md`.

Stop and report if: the service will not start (read `sudo veritas logs`), registration returns 429 (wait out the rate limit), or an event returns 422 (record the body in `FINDINGS.md`).

### Phase 1: Bootstrap automation
Goal: one command that makes a fresh server ready.

Tasks:
1. Implement `simlib/client.py`: login once, cookie jar, TLS verification off, per-call timing, retry on connection errors only (never retry a 4xx).
2. `cli.py bootstrap`: idempotent. If setup is already done it logs in; creates an AUDITOR user `sim_checker` (password from env); uploads the org config; issues N agent keys and registers N agents (default 10); writes `state/agents.json` (`agent_id`, `token`, `label`; file mode 600).
3. `cli.py agents --status` prints `GET /agents`.
4. Unit tests (no network) for the client's request building and for the state file handling.

Acceptance:
- Running `bootstrap` twice in a row on the same server succeeds and does not create duplicate admin or agents unless `--new-agents` is passed.
- `state/agents.json` has 10 entries and `GET /agents` shows 10 ACTIVE agents.
- `pytest sim/tests` passes.

### Phase 2: Corpus, Mode A driver and ground truth
Goal: exact correctness measurement at low rate.

Tasks:
1. Implement `verhoeff.py` and tests (known valid and invalid Aadhaar, valid PAN generation).
2. Implement `corpus.py` with all templates in section 5. Add `cli.py verify-corpus`: sends each template 20 times (through `/events` with the baseline token) and reports whether the observed verdicts equal `expected`. Fix generators until all non-decoy templates agree, or log product disagreements in `FINDINGS.md`.
3. Implement `driver.py` (async, `httpx.AsyncClient`, N workers, token bucket rate limit) and `cli.py send --scenario scenarios/smoke.yaml`.
4. Implement `groundtruth.py`: for every event append a JSONL line `{seq, sent_at, kind, source_system, expected:[...], http_status, latency_ms, event_id, observed:[...]}`. In Mode A `observed` comes from the response `verdicts`.
5. Implement `checker.py` + `cli.py report`: per kind and per rule: expected, observed, true positive, false negative, false positive, plus `known_weak` shown separately; latency p50/p95/p99; HTTP error counts. Then pull `GET /verdicts` and confirm each `event_id` from the manifest is present in the store with the same rule ids (proves the response matched what was persisted). Finish with `verify-chain`.
6. Scenario `smoke.yaml`: 5 events/s for 60 s, all kinds.

Acceptance:
- `cli.py verify-corpus` shows every non-decoy template at 100% agreement (or a documented `expected_unverified` with a `FINDINGS.md` entry).
- A `smoke` run produces 0 HTTP errors, recall and precision of 100% for non-decoy kinds, and the store contains every `event_id` returned.
- The report is written to `state/reports/<timestamp>.md` and `.json`.

### Phase 3: Load ramp
Goal: find the server's limit and record it.

Tasks:
1. Add the `ramp` profile. Scenario `ramp.yaml`: 10, 25, 50, 75, 100 events/s, 60 s each, Mode A.
2. During the run, sample the server's CPU, memory and request latency on `veritas-server` every 5 s (a tiny sampler script over SSH, saved to `state/`). Record the memory limit in effect.
3. In the report add a table per step: offered eps, achieved eps, error rate, p50/p95/p99 latency, server CPU and memory, and the first step where errors or latency growth appears.
4. Run the `steady-100` scenario for 30 minutes at the highest sustainable rate and run `verify-chain` afterwards.

Acceptance:
- `state/reports/` contains the ramp report with one row per step and a one-paragraph conclusion: "limit is about X events/s, limited by Y (CPU, lock or memory)".
- `verify-chain` is valid after the 30-minute run, and every `event_id` the server acknowledged (HTTP 200) is in the store.
- Findings that point at product limits go into `FINDINGS.md` (expected: detection inside the request, single writer lock).

### Phase 4: Mode B, real agents
Goal: end-to-end realism with real log files and real agents.

Tasks:
1. Build the agent image from this repo: `docker build -t veritas-agent:local veritas-agent/` (do not use the released v1.0.16 agent; issue 1).
2. Create `compose/docker-compose.agents.yml` for N hosts (default 5, up to 10). Each host has: a log volume, 2 to 3 log-writing containers (reuse or extend `veritas-demo/services/`, each writing realistic lines for its `source_system`, with a template mix from the corpus limited to log kinds), and its own `veritas-agent:local` container with its own state volume and its own `agent-config.yaml`.
3. The `agent-config.yaml` format: `veritas_address`, `registration_key`, `source_label`, `sources:` list of `{type: file, path: /logs/<service>.log, source_system: <name from the org config>}`. Generate one per host from the template. Issue the key right before each agent starts (issue 5).
4. Every log-writing container also records, per `(source_system, kind)`, how many lines it wrote (a counters file on a shared volume), so the checker knows what was written.
5. `cli.py agents-up --hosts 5` and `agents-down`.
6. Measure per-agent throughput: raise the services' emit rate until an agent's queue stops draining (watch `docker logs` of the agent for dropped-line warnings) and record the per-agent limit.
7. Aggregate check in `checker.py`: for the run window, compare the expected `EXPOSURE_001` verdict counts (sum of each written line's `count`) per `source_system` with `GET /verdicts?source_system=...&date_start=...&date_end=...` counts. Pass if within the tolerance recorded in `PROGRESS.md` (suggest 2% plus lines still in agent queues; wait for the queues to drain before measuring).

Acceptance:
- `GET /agents` shows all agents ACTIVE with rising `events_received` and fresh heartbeats.
- For a 10-minute run, the aggregate check passes for every source_system (no source_system has 0 verdicts while expected is above 0; totals inside tolerance).
- No agent log contains repeated HTTP 422 retries.

Stop and report if: events return 422 (issue 1: you are probably using a v1.0.16 agent or server), 401/403 (token or revoked agent), or an agent queue grows without bound.

### Phase 5: Scenarios and failure cases
Goal: behaviors an enterprise would actually see.

Implement each as a scenario or a `cli.py scenario <name>` that scripts the steps and asserts the outcome:

| Scenario | Steps | Expected |
|---|---|---|
| `burst` | base 20 eps, 5x spikes for 10 s every 60 s, Mode A | no lost acknowledged events; latency recovers after each spike |
| `incident` | violation ratio jumps from 2% to 40% for 5 minutes | verdict counts follow; chain stays valid |
| `server-restart` | `sudo systemctl restart veritas` on the server during Mode B traffic | agents back off and retry; after recovery the stored count matches the written count within tolerance; **no events lost beyond the agent's 1000-line buffer** |
| `server-outage` | stop the service for 3 minutes during Mode B | agents buffer, then deliver; record how many lines were dropped when the buffer overflowed |
| `revoke-agent` | revoke one agent mid-stream | that agent's events get 403 and stop being stored; others unaffected; the dashboard shows REVOKED |
| `bad-token` | send with a wrong token in Mode A | 401, nothing stored |
| `unknown-source` | send events for an undeclared source_system | behavior recorded (expect `PURPOSE_001` for PII) |

Acceptance: each scenario has a report section with the assertion result, and `verify-chain` is valid at the end of every scenario. Anything unexpected goes to `FINDINGS.md`.

### Phase 6: Reporting and hand-over
Tasks:
1. `cli.py report --all` builds one markdown summary with: environment, versions, corpus accuracy table, load ramp table and conclusion, Mode B result, scenario results, list of findings.
2. Complete `sim/README.md`: prerequisites, the exact command sequence from a fresh pair of machines to a full report, expected runtimes, how to tear down.
3. Add `run-all.sh` (or a Makefile): `bootstrap`, `verify-corpus`, `smoke`, `ramp`, `agents-up`, a Mode B run, scenarios, `report`.
4. Tear-down instructions for the AWS resources.

Acceptance:
- A person who has never seen the project can reproduce the full report by following `sim/README.md` only.
- `veritas-demo/SIMULATION_PLAN.md` is updated: tick off the phases, record the final numbers.

## 8. Definition of done (whole project)

- [ ] `sim bootstrap` prepares a fresh server in one command, repeatably.
- [ ] Mode A reports 100% precision and recall for all non-decoy kinds at 5 events/s, and the limit under load is documented with a table.
- [ ] Mode B with at least 5 real agents passes the aggregate check on a 10-minute run.
- [ ] All Phase 5 scenarios have recorded results.
- [ ] The evidence chain verifies after every run.
- [ ] `FINDINGS.md` lists every product issue found, each with request, response and expectation.
- [ ] No secrets, customer names or generated state are committed.

## 9. Glossary

| Term | Meaning |
|---|---|
| Org / tenant | One customer. All data is separated by org id (`sim_enterprise`) |
| Agent | Log forwarder installed on application hosts; has its own token |
| Event | One log line or API payload sent to `/v1/{org}/events` |
| Verdict | A recorded violation: rule, severity, field, source system, status |
| Evidence chain | Hash-linked store of verdicts; `verify-chain` proves nothing was altered |
| source_system | The name of an application or service, declared in the org config |
| Mode A / Mode B | Direct driver / real agents (section 4) |
| Ground truth | What the simulator knows it injected, used to score the product |
