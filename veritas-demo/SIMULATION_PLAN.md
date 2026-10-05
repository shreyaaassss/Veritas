# Enterprise Simulation Plan

Status: **plan only, nothing built yet.** Supersedes the scope of `BLOCK5_SIMULATION_PLAN.md` (the 4-service demo).

## Decisions (agreed)

| Topic | Decision |
|---|---|
| Scale | Up to ~100 events/s total, ~10 agents. |
| Orgs | One generic simulated org (`sim_enterprise`), created through normal onboarding like any customer. No vendor-specific org ships with the product. Multi-org later. |
| Ground truth | Yes. The simulator records every injected violation, and a checker compares that to what Veritas stored. |
| Where it runs | AWS. Simulator + agents on one EC2 instance, Veritas server on a separate EC2 instance (Ubuntu, `.deb`). Not on the developer's Mac. |

## Prerequisite: remove customer-specific code (product must be org-agnostic) — DONE in `dpdpa-agent/`

Status: completed (see summary at the end of this section). Remaining customer references live only in `veritas-demo/` (to be rewritten by the simulation work) and historical planning docs.

Veritas is onboarded fresh for each enterprise. Nothing named after a customer may ship or influence behavior. Found in the current code:

- `registry/loader.py`: defaults `org_id="blinkit"`, plus `if org_id == "blinkit"` branches (seeded violation ages, hardcoded table mapping per source system).
- `registry/seed_registry.py`, `ingestion/` (dead code), `demo_fixtures/`.
- `detection/analyzer_engine.py`: a filter for "Blinkit's own ID convention" (`BLK-`, `DP-`) that suppresses matches. That is customer-specific behavior inside the detector and should become org-configurable (per-org ignore patterns in the org YAML).
- `dashboard/index.html`: `if (orgId === 'blinkit') return 'E-Commerce'` and `org: 'blinkit'` default.
- `veritas_scan_cli.py` examples, `schemas/models.py` examples, docs, and `veritas-demo/` service names/IDs.
- `org_config/configs/blinkit`, `edtech_co`, `acme_bank` are bundled in the PyInstaller build (`veritas.spec`), so a fresh install contains other companies' configs. Ship none; keep them as test fixtures only.
- `tools/generate_license.py` examples.

Result of the cleanup: no org-specific code or bundled configs remain. Retention is now driven by an optional `data_since` field in the org config (default: start of the retention clock = config upload time). The detector's ID filter was already generic (digit-bearing PERSON matches), so no per-org setting was needed. Fixture orgs for tests live in `dpdpa-agent/tests/fixtures/org_configs/` and are loaded by `conftest.py` into a temp data dir. Suite: 399 passed, 1 skipped.

The simulation must not depend on any of the old behavior. It creates `sim_enterprise` through the onboarding flow and uploads its config like a real customer. Tests that rely on the blinkit seed move to fixtures under the tests directory.

## Target topology

```
EC2 "sim-host" (Docker Compose)                      EC2 "veritas-server" (Ubuntu 22.04)
┌───────────────────────────────────────┐            ┌──────────────────────────────┐
│ ~12 simulated services (containers)   │            │ veritas .deb, systemd        │
│   grouped into ~10 "hosts"            │            │ TLS :8000 (self-signed/proxy)│
│ each host: own log volume + own agent │──HTTPS────►│ license: unbound dev license │
│   (separate registration → own        │  :8000     │   or machine-id bound (v2)   │
│    agent ID/token, revocable)         │  outbound  └──────────────────────────────┘
│ traffic driver (scenario YAML)        │
│ ground-truth manifest (JSONL)         │◄──── checker pulls verdicts via REST (login)
└───────────────────────────────────────┘
```

Security group: server allows TCP 8000 (or 443 via proxy) only from the sim-host's IP, plus SSH and a dashboard IP of the developer.

## Components to build

1. **Service library** (`services/`): reuse `common.py` and the four current services. Add about eight more (payments, auth, search, inventory, notifications, api-gateway/nginx, KYC, support-chat). Each has several log formats: plain text, JSON structured, nginx access log (PII in query string), stack trace containing PII, multi-line.
2. **Traffic driver** replaces per-service env vars. A scenario YAML sets the target total events/s and a violation mix per service. The profile has a base rate, a day and night curve, bursts, and an "incident" (e.g. debug logging turned on, so the violation rate jumps).
3. **Hosts and agents:** compose generates N host groups. Each has a log volume, its own `agent-config.yaml`, and its own registration key. A provisioning script (`sim provision`) logs in to Veritas, issues N one-time keys, and writes the configs.
4. **Rule coverage:** EXPOSURE_001, PURPOSE_001, RETENTION_001, LINKAGE_001, and PII types Aadhaar, PAN, phone, email, DOB. Decoys that must not fire: 12-digit order IDs (known issue H1), hashed IDs, masked values.
5. **Failure scenarios:** server stop and start (agent buffer + retry), revoke an agent mid-stream, wrong token, slow server.
6. **Ground truth and checker:**
   - Every emitted line carries a non-PII correlation token (`sim=<id>`).
   - The driver appends `{sim_id, host, service, expected_rule|none, injected_at}` to a manifest.
   - `sim report` logs in to Veritas, pulls verdicts, and joins on `sim_id`. It reports missed violations, false positives, per-rule precision and recall, and ingest latency percentiles.
7. **CLI:** `sim up --scenario <file>`, `sim provision`, `sim report`, `sim down`.

## Risks / things to verify first

- **Correlation token survival.** Veritas masks PII in API responses. Confirm that `sim=<id>` in `raw_snippet` is stored and returned unmasked by the verdict or evidence export API. If not, use the event timestamp + source + rule as a weaker join key.
- **100 events/s on today's server.** Detection runs inside the `/events` request, so this may overload it. This is the reason for the planned ingest queue. Run the simulator at 10, 50 and 100 events/s against the current server first to get a baseline, then compare after the queue lands.
- **Agent throughput.** Agent queue is 1000 and it forwards one HTTP POST per line. Measure it per host. The per-host rate is ~10 events/s.
- **License on the AWS server.** EC2 `machine-id` is stable, but the fingerprint fix (v2) is not built yet. For the first run use an unbound dev license.
- **Org policy fit.** All sources must use `source_system` values that exist in the `sim_enterprise` registry, or events are rejected. New services need registry entries.
- **Auth for the checker and provisioner.** They need a service user with the auditor or admin role. Confirm the login flow and whether it needs a CSRF or cookie handling.

## Phases

0. **Baseline (small):** run the current demo on AWS against the server. Verify the correlation-token question and measure the baseline rate.
1. **Driver + manifest:** scenario YAML, rate control, ground-truth output, no new services yet.
2. **Hosts + agents:** N agents, provisioning script, more services and formats.
3. **Checker:** `sim report`, precision and recall table.
4. **Scenarios:** bursts, incident, failure cases.
5. **Load run:** 100 events/s for 30 minutes. Capture results in this repo.

## Open questions

- AWS: instance sizes, region, and whether the account exists. Suggest t3.large for the server (spaCy needs ~2 GB) and t3.medium for the sim-host.
- Is the 4-service demo still needed as a short demo, separate from this simulation?

---

# Handoff notes for the implementer

Verified against the code (not guesses). Read this before building.

## Known issues you will hit

1. **The agent forwards one line at a time.** `veritas-agent/agent.py` has a single forwarding worker doing one blocking `requests.post` per line. Its throughput is `1 / server latency per event`. The server runs PII detection (spaCy) inside the request, so expect roughly a handful of events per second per agent, not 10. Measure this in Phase 0 before sizing the number of hosts. Total = agents x per-agent rate.
2. **`/v1/{org}/events` is synchronous.** It runs detect, rules, evidence write and live-feed publish before it answers. There is no queue. 100 events/s total will probably saturate one server process. Run a ramp (10, 25, 50, 100 events/s) and record where latency or errors start. A failure here is the expected finding that motivates the ingest queue, not a simulator bug.
3. **The evidence store uses one lock.** Writes are serialized, so more agents do not scale linearly.
4. **Rate limits on provisioning and login (in-memory, per IP).** Login: 10 per 5 min. First-admin setup: 3 per 10 min. Registration and issue-key: 20 and 30 per hour. Provision about 10 agents once, log in once and reuse the session cookie, and do not log in per request or you will get HTTP 429.
5. **Registration keys are one-time.** Each agent needs its own key (issued via the dashboard API as an admin). If an agent's state volume is wiped, it needs a new key. Keep the state volumes per agent.
6. **`source_system` and field names must exist in the org config.** The registry is loaded from `org_config/configs/<org>/*.yaml`. A new simulated service only works after its `source_system` and fields are declared in the `sim_enterprise` org YAML (upload via `POST /v1/orgs/sim_enterprise/config`, admin only). `_require_org_config` rejects unknown orgs. Unknown source systems or fields will not produce the verdicts you expect.
7. **Some blinkit behavior is hardcoded in `registry/loader.py` (to be removed, see prerequisite below)** (seeded retention ages, the table mapping for the four existing source systems). The RETENTION and PURPOSE results for the four existing services come from that seeded registry data, not only from the log text. For new services, check how each rule actually triggers (`rules/engine.py`) before writing a "this line should violate RETENTION" expectation, or the ground truth will be wrong.
8. **Detection quirks cause expected false positives.** Unspaced 12-digit Aadhaar matches order-ID-like numbers (known issue H1), so decoys of that shape will fire. Record decoys as known-fail cases rather than treating them as simulator failures.
9. **Self-signed TLS.** The server auto-generates a cert. The agent has its own handling (`_tls_verify`, fetches the server cert). A custom driver or checker must pass `verify=False` or trust the cert, and `http://` may not work if TLS is on.
10. **The license.** Use an unbound dev license on the AWS server until the v2 fingerprint fix lands, or you will hit the fingerprint mismatch documented in `LINUX_TEST_FINDINGS.md`.
11. **Memory.** The systemd unit caps the server at `MemoryMax=800M`. Under load, spaCy plus the queue may be OOM-killed. Check `journalctl -u veritas` and `dmesg` when a run dies, and raise the limit for load tests.
12. **The live feed is per-org WebSocket broadcast.** 100 events/s of violations will flood a browser tab. Do not judge results by watching the dashboard.

## Easier way (recommended for v1)

Split the work into two modes. The first one is much easier and covers most of the value.

**Mode A: direct driver (build this first).**
- A small async Python script POSTs events straight to `/v1/sim_enterprise/events` using one registered agent's ID and token (the same `Authorization` header an agent uses).
- Rate is a single number, so 10 to 100 events/s is trivial and needs no agent containers.
- **No correlation token or verdict-join is needed.** The `/events` response already returns `event_id`, `contains_pii`, and the list of `verdicts` synchronously. Compare that response to the expected rule at send time and you get precision, recall and latency immediately.
- Run it from the sim-host EC2 instance against the server EC2 instance.

**Mode B: real agent path (small, realistic demo).**
- Keep the existing compose stack, but scale it: `docker compose up --scale order-service=3`, one agent per group, 3 to 5 agents total.
- Agents discard the server response, so here you do need the `sim=<id>` token to check results. Treat it as a smoke test and a demo, not the load test.
- Only pursue extra log formats (JSON, nginx, stack traces) and failure scenarios here.

**Suggested order:** Phase 0 baseline -> Mode A with ground truth and a ramp -> Mode B scaling -> failure scenarios. This drops the need for a manifest-to-verdict join for most results and avoids the agent throughput limit.
