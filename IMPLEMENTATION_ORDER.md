# Implementation Order

Status: agreed order to confirm. Nothing below is built yet except Step 0's cleanup (already done in the working tree, not committed).

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
