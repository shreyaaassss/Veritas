# Implementation Order

Status (2026-10-06):

| Milestone | State |
|---|---|
| 1. Linux works | **Done.** Released as v1.0.16; v1.0.17 adds the agent `source_type` fix. Linux install test passes in CI. A real Ubuntu machine ran the dashboard. Remaining from that test: restart and crash-recovery checks reported back by the tester |
| 2. License portal | Not started. Needs the new Supabase key |
| 3. Architecture changes | Not started. Waits for the simulation's load numbers |
| **4. Enterprise agent and telemetry alignment** | **Planned (below). Starts next.** Brings the agent and deployments in line with the product spec "Enterprise Telemetry, Agent & Compliance Flow" |
| Simulation | A teammate builds it from `veritas-demo/SIMULATION_PLAN.md` |

Goal of the first milestone: **a Linux `.deb` that installs, activates with a license, and runs reliably**, verified by an automated install test. Everything else comes after.

## Milestone 1: Linux works (do first, in this order)

| # | Change | Why this order | Size |
|---|---|---|---|
| 0 | **Commit hygiene.** Review and commit the customer-code cleanup. Normalize line endings (`.gitattributes`), fix the broken portal submodule link. | 88 files show as modified from line-ending churn; real changes are hidden in it. Do this before piling on more diffs. Needs your OK to commit. | S |
| 1 | **Fingerprint v2.** `v2:` + hash of `/etc/machine-id` (fallback: disk serial). No MAC, no hostname. One implementation (the runtime, `--fingerprint`); the Go launcher and the `veritas fingerprint` CLI both use it. | Root cause of the Linux test failure. Blocks every license test, and the portal's fingerprint validation depends on this format. | M |
| 2 | **No-license behaviour.** Exit with a distinct code and make systemd stop retrying (`RestartPreventExitStatus`), so a missing or invalid license shows one clear message instead of a crash loop. | Seen in the Linux test; confusing for a first install. | S |
| 3 | **Linux packaging polish.** `veritas` CLI no longer needs host `python3`/`lsblk`. Consistent http/https messages. Update `INSTALL.md` version, `control` email and deps. Print the fingerprint during install. | Removes the other install-time surprises from the test. | S |
| 4 | **Linux install CI test** (`test-linux-install.yml`): install the `.deb` in a clean Ubuntu runner, check CLI fingerprint == runtime fingerprint, activate a bound and an unbound license, start, `/health`, restart, kill/auto-restart, missing-license message. | Gives the same safety net macOS and Windows already have. Without it each Linux fix is untested. | M |
| 5 | **Release v1.0.16 and re-run the Ubuntu test** from the checklist, on your hardware and one cloud VM. | The milestone gate. | S |

## Milestone 2: License portal (starts after step 1, can run in parallel with 2 to 5)

| # | Change | Notes |
|---|---|---|
| 6 | New Supabase project, new key you provide, and a fresh `licenses` table (you said the old database must be deleted). **Row-level security on.** | Needs the new keys from you. Old data discarded. |
| 7 | Fingerprint format validation (`v2:` + hex), reject bad input before signing. | Needs step 1's final format. |
| 8 | Save failures reported to the admin instead of swallowed; the license is returned but flagged "not recorded", or issuing fails. | Small. |
| 9 | Remove the portal from the parent repo as a gitlink (or make it a real submodule). | Done as part of step 0 if you choose. |

Left aside for now (your call): revocation, renewal/reissue, multi-user login, license id, tier limits. All are listed in `LICENSE_PORTAL_REVIEW.md`.

## Milestone 3: Architecture changes (after Milestone 1 passes)

These change runtime behaviour, so they come after the install path is proven and a baseline exists.

| # | Change | Why after Milestone 1 |
|---|---|---|
| 10 | **Baseline load measurement** using the simulation driver (Mode A): 10, 25, 50, 100 events/s against the unchanged server. | Needed to prove the queue is worth it and to measure the gain. Needs your teammate's driver. |
| 11 | **Ingest queue + worker pool**; `/events` returns 202 quickly. SQLite WAL mode; evidence chain written by one worker. | Biggest scalability gain; touches the hot path, so it needs the baseline and the install test first. |
| 12 | **Offline hardening:** bundle fonts and jsPDF locally; re-check license expiry while running. | Required for air-gapped customers; independent of 11. |
| 13 | **Metrics:** queue depth, events/s, detection latency, agent last-seen. | Most useful once the queue exists. |
| 14 | **Structure and docs:** split `server.py` into routers and `index.html` into modules; rewrite the technical audit and `architecture.md`. | Pure refactor and docs; do last so it doesn't block anything. |

## Milestone 4: Enterprise agent and telemetry alignment (next)

Source: the product spec "Enterprise Telemetry, Agent & Compliance Flow", stored at `docs/ENTERPRISE_TELEMETRY_SPEC.md` (agent is a thin telemetry bridge, all compliance logic stays in Core, Kubernetes via DaemonSet, plain servers as OS services, sanitization optional and later).

### Where we stand against that spec (assessed 2026-10-06)

| Spec area | State |
|---|---|
| Thin agent, intelligence in Core | Aligned |
| Blank dashboard, then create org, policy, key, agent | Aligned in code (no bundled orgs). The empty-dashboard screen itself still needs a visual check |
| One-time-key registration | Aligned |
| Core pipeline (ingest, PII, four rules, violations, evidence, dashboard, cases, audit) | Aligned |
| Modes A and C (Veritas does not store raw logs; customer's own logging is untouched) | Effectively in place. Core stores verdicts and metadata only, not raw text |
| Security and reliability basics (TLS, token, revoke, heartbeat, retry, bounded buffer) | Mostly aligned. Token saved as plain JSON; buffer in memory only; no Core ingest queue yet |
| Plain Linux/Windows/Docker agent | Partly. The Linux `.deb` very likely cannot save its identity or read logs; no log-rotation handling |
| Kubernetes DaemonSet | **Not aligned.** Shipped manifests are broken (see Phase B) |
| Mode B (sanitize and forward downstream) | Not built |

Findings behind the "not aligned" rows come from reading the manifests and agent code; **nothing has been run on a cluster**. Phase B starts by reproducing them.

### Phase A: Agent reliability and the Linux server path (do first; spec priorities 2, 3, 5)

| # | Task | Notes |
|---|---|---|
| A1 | Fix the agent `.deb`: writable state location (identity file), read access to application logs, correct post-install text (`veritas_address`, not `server_url`), identity file mode 0600 | `ProtectSystem=strict` makes `/opt/veritas-agent` read-only, so `.veritas_state.json` cannot be written, and the one-time key is consumed anyway. Log files are usually `root:adm 0640` |
| A2 | Log rotation: detect rotation/truncation and reopen the file | Today the agent holds the old file open and silently misses new lines |
| A3 | Do not let one failing event block the queue: bounded retries for non-auth errors, then drop and count | Today any non-401/403 error (including a persistent 422) retries the same event forever |
| A4 | Heartbeat carries basic health: sources being read, queue depth, lines dropped, agent version. Show it in the dashboard Agents tab | Spec: "report basic health/status" |
| A5 | Agent Docker image and `.deb` report their real version | Part of the version-reporting backlog item |
| A6 | CI test for the agent: install the agent `.deb` on a runner next to a started server, register with a key, tail a file, append PII lines, rotate the file, assert violations in the ledger | Same safety net as the server install test |
| A7 | Release v1.0.18 and retest on the Ubuntu machine | Gate for Phase A |

**Acceptance:** on a clean Ubuntu machine the agent `.deb` registers, survives a restart and a log rotation, and violations appear in the dashboard; the CI agent test is green.

### Phase B: Kubernetes agent (spec priority 4)

Start by reproducing the defects on a local `kind` cluster, then fix them.

| # | Task | Notes |
|---|---|---|
| B1 | **Reusable enrollment keys** on the server (maximum uses, expiry, org-scoped, revocable) plus the dashboard control to issue one | A DaemonSet registers one agent per node; today one key is shared by every node and the first node uses it up |
| B2 | **Wildcard log paths** in sources (for example `/var/log/containers/order-*.log`) and parsing of the container runtime's line format | Pod log file names change on every redeploy; stdout logs are the main source on containerd clusters, which have no Docker socket |
| B3 | **`source_system` naming rules** for node logs (map namespace/pod/container name patterns to a `source_system` in the agent config) | Needed so the org policy applies to the right system. Decision pending, see below |
| B4 | Rewrite the manifests: state at its own mount path (not over `/app`), config and key in a Secret, key delivered through an environment variable or init step (today the `${VERITAS_REGISTRATION_KEY}` placeholder is never substituted), one agent per node | Shipped `agent-deployment.yaml` and `agent-daemonset.yaml` mount state over `/app`, hiding `agent.py` |
| B5 | Publish the agent image to a registry (GHCR) in the release workflow | Today only a local `veritas-agent:latest` exists |
| B6 | CI test on `kind` with 2 nodes: DaemonSet registers both agents with one reusable key; a pod writing PII to stdout produces a violation mapped to the right `source_system` | Proves the spec's main Kubernetes claim |
| B7 | Rewrite `veritas-agent/k8s/README.md` to match | |

**Acceptance:** one command deploys the DaemonSet; both nodes appear ACTIVE; stdout PII from a test pod shows up as a violation under the expected source system; the `kind` CI test is green.

### Phase C: Complete the customer flow (spec priorities 1, 6)

| # | Task | Notes |
|---|---|---|
| C1 | Add `data_since` to the Add Organization form | Existing systems with old data cannot be described through the UI today |
| C2 | User and role management screen (create users, assign role and organizations) | Today only the API; a customer admin should not need curl |
| C3 | Visual check of the blank first-run dashboard and fix anything half-empty | Spec priority 1 |
| C4 | Version reporting in `/health` and agent registration | Backlog item |
| C5 | Ingest queue and worker pool (Milestone 3, step 11), once the simulation baseline exists | Spec section 13: only when measured traffic needs it |
| C6 | Optional on-disk spool in the agent for long outages and agent restarts | Spec: bounded local buffering; today it is memory-only (1000 lines) |
| C7 | Full end-to-end check: file log, agent, Core, violation, evidence, dashboard, on Linux and in Kubernetes | Spec priority 6; the simulation covers most of this |

### Phase D: Sanitization and downstream forwarding, Mode B (spec priority 7, deliberately last)

1. Write a short design first: output actions `raw`, `sanitized`, `compliance-metadata-only`; per-organization setting; connectors (syslog, HTTP, object storage) and where redaction runs (Core).
2. Build only after Phases A to C are stable. Not a prerequisite for the Agent.

### Phase E: Housekeeping

- Remove Raspberry Pi wording from docs and install scripts (spec: enterprise software, not an appliance requirement).
- Update `PROJECT_OVERVIEW.md` and `INSTALL.md` to reflect the agent changes.
- ~~Store the enterprise telemetry spec in the repo~~ Done: `docs/ENTERPRISE_TELEMETRY_SPEC.md`.

### Decisions needed before Phase B

| Decision | Recommendation |
|---|---|
| How a node log line gets its `source_system` | Mapping rules in the agent config, for example "namespace `shop`, pod `order-*` becomes `order-service`", with a clear warning for unmapped logs |
| Reusable enrollment keys | Yes: maximum uses plus expiry, shown once, revocable from the dashboard |
| Agent buffer in the first release | Keep in memory for Phase A; add the disk spool in Phase C (C6) |
| Agent token storage | File mode 0600 on servers; Kubernetes Secret plus a writable state volume in the cluster |

### Order and dependencies

1. Phase A, then release v1.0.18 and the Ubuntu retest.
2. Phase B (needs A2's rotation and A3's queue behavior; B1 touches the server).
3. Phase C in parallel with B where it does not touch the same files (C1 to C4 are independent).
4. Phase D after A to C are stable.
5. The simulation and Milestones 2 and 3 run alongside; the simulation's Mode B should use the v1.0.18 agent once it exists.

## In parallel from now (teammate)

Simulation: Phase 0 baseline, then Mode A driver and ground-truth checker, then Mode B with real agents. It depends only on the finished customer-code cleanup, not on the steps above. Its load results feed step 10 and 11.

## What I need from you

1. OK to commit the cleanup (step 0) and how you want it split (one commit, or cleanup / fixtures / docs).
2. The new Supabase URL and service key when you are ready (step 6). Don't paste them here: put them in the portal's `.env.local` and Vercel, and I'll work from the variable names.
3. Confirm Milestone 1 as the first thing to build.

## Backlog: changes to make later

| Item | Notes |
|---|---|
| Support email | No support mailbox exists yet. The product says `support@veritas.io` (license errors in `license.py`, `license.go`, `tools/fingerprint.py`) and the `.deb` control file says `support@veritas.app`. Create the real address, then replace all of them with one value (ideally one constant). |
| CI signing key | CI tests sign licenses with the production private key stored in the `VERITAS_PRIVATE_KEY_B64` secret. Move to a separate test keypair and a test build that embeds its public key. |
| macOS and Windows fingerprint | Still the old scheme (disk serial + MAC + hostname). Move to `IOPlatformUUID` / `MachineGuid` anchors, same `v2:` format, once the Linux path is proven. |
| Reissue of old licenses | Test licenses from before v2 are invalid on Linux by design. No customer licenses exist, so nothing to migrate. |
| Portal: revocation, renewal/reissue, license id, multi-user login, tier limits | Deferred by decision; see `LICENSE_PORTAL_REVIEW.md`. |
| Go launcher gofmt | `license.go` and `main_linux.go` already fail `gofmt -l` (comment formatting). Cosmetic. |
| Version reporting | `/health` and agent registration report version `1.0.0` regardless of the release (found while testing v1.0.17 from source). The runtime does not know its own version; embed a VERSION file at build time (release workflow) and read it. |
| User management screen | No dashboard screen for creating users or assigning roles/orgs; only the API. Needed before a customer admin can onboard analysts without curl. |
| macOS install test trigger | `test-macos-install.yml` runs at tag push before release assets exist, so it fails every release and passes on re-run. Make it wait for the release or trigger after it. |
