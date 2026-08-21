# Phase 4 — Rule Engine (Policy Decision Point)

**Status: Implemented. PROTECTED MODULE — never cut, even under time pressure.**

Fully deterministic. No LLM anywhere in this module. Decoupled from
rendering/explanation (OPA-style PDP/PEP separation): this module decides,
it does not explain. This is where a PII match becomes an actual
compliance verdict — everything downstream (LLM Explainer, Evidence Store,
Dashboard) depends on this being correct.

---

## Files

| File | Purpose |
|---|---|
| `sensitivity.py` | Shared PII sensitivity tiers (HIGH/MEDIUM/LOW), entity_type→pii_category bridge, breach-notification eligibility |
| `engine.py` | The three checks in order, per-field short-circuit chain, `evaluate_event()` top-level entrypoint |
| `fanout.py` | `VerdictFanout` — async fan-out to two downstream queues (Phase 5, Phase 6), stub consumer interfaces |
| `test_rule_engine.py` | 27 tests across the plan's six exit-criteria categories plus interface-gap regression tests |

---

## The Three Checks (in order, short-circuit)

```
contains_pii == False?  →  [] immediately, no registry calls, no checks

contains_pii == True  →  for each matched_entities hit (deduplicated first — see below):

  Check 1 — Exposure          →  fires? → EXPOSURE_001, HIGH, stop.
  Check 2 — Purpose/Consent   →  fires? → PURPOSE_001, stop.
  Check 3 — Retention         →  fires? → RETENTION_001, stop.

  none fire → no verdict for this field
```

A field can trigger **at most one** rule.

---

## Design Decision #1 — Exposure vs Purpose Precedence for `marketing-analytics`

**The tension:** Phase 1 built `RegistryEntry.forbids_raw_pii()` specifically so a raw PII leak into `marketing-analytics` would be classified `PURPOSE_001`. The Phase 4 plan explicitly instructs Check 1 to fire on `source_system == "marketing-analytics"` and short-circuit before Check 2 ever runs.

**What we did:** Implemented exactly what the plan says. A raw phone number leaked into a marketing-analytics event is classified **`EXPOSURE_001`** (HIGH), not `PURPOSE_001` — even though it reads, in plain English, like a textbook purpose violation.

**This is a genuine, unresolved tension, not a bug.** `forbids_raw_pii()` is still called in Check 2 and is still correct — it's just currently **unreachable** for real marketing-analytics traffic, because Check 1 intercepts every such match first. Verified directly: `get_registry_entry("phone", "marketing-analytics").forbids_raw_pii()` returns `True`, and this fact is independently tested (`test_registry_forbids_raw_pii_invariant_still_true_even_though_unreached`), while the actual verdict produced is `EXPOSURE_001`.

**Two resolution options for the team, not decided here:**
- (a) Narrow Check 1's marketing-analytics clause to exposure-flavored surfaces only (e.g. logs), letting Check 2 own the marketing-payload case via `forbids_raw_pii()` as Phase 1 intended.
- (b) Keep Check 1 as specified — "PII visible in plaintext where it structurally shouldn't be" genuinely *is* exposure regardless of channel — and note in `registry/README.md` that `forbids_raw_pii()` is a structural invariant Check 4 doesn't currently need.

---

## Design Decision #2 — Unregistered-Field Handling (Check 2)

**The question:** If a field contains PII but `get_registry_entry(field, source_system)` returns `None` — no declared purpose, consent, or retention exists for it at all — is that a violation, a warning, or a crash?

**What we did:** Treated as a **violation**: `PURPOSE_001`, `matched_registry_entry: null`.

**Rationale:** DPDPA's purpose-limitation principle requires a declared purpose to exist *before* data is processed. A field with PII flowing through a system with zero registry entry is data with no declared purpose at all — arguably *more* severe than "declared for X, used for Y" (Check 2's normal case), since there's no purpose on record whatsoever.

**Severity for this case:** Since there's no registry entry to assess sensitivity against, we fall back to the entity_type-derived category (`sensitivity.entity_type_to_pii_category`). Default is `MEDIUM` — deliberately not `LOW` (this is a genuine compliance gap, not minor) and not automatically `HIGH` (we don't yet know if the underlying PII type is high-sensitivity). A category-derived override upgrades to `HIGH` when the entity type maps to `aadhaar`/`pan` specifically, so a stray Aadhaar number in an unregistered field is never under-reported.

Verified never crashes: `get_registry_entry()` returns `None` cleanly (per Phase 1's own contract), and Check 2 handles `None` explicitly rather than propagating an exception.

---

## Severity Mapping Rationale (Checks 2 & 3)

Three-tier sensitivity classification, shared across Purpose and Retention checks (see `sensitivity.py` for the full inline rationale):

| Tier | Categories | Reasoning |
|---|---|---|
| **HIGH** | `aadhaar`, `pan` | Government-issued identity/financial identifiers — highest individual harm ceiling (identity theft, cross-database linkage, financial fraud) |
| **MEDIUM** | `phone`, `email` | Standard contact identifiers — exploitable (phishing, spam, social engineering) but lower harm ceiling than a government ID |
| **LOW** | `name`, `address`, `order_history`, `order_reference`, `financial_account`, `hashed_identifier` | See note on `address` below |

**Why `address` is LOW despite being physically sensitive in principle:** Phase 3's detector has a documented false-positive risk on address fields (building names read as `PERSON` — see `detection/README.md` "Known Limitations #1"). We deliberately don't let a less-reliable detection surface drive a HIGH severity determination.

Retention (Check 3) reuses the *exact same* mapping rather than inventing a second scheme — a stale Aadhaar record isn't inherently less serious than a purpose-violating one of the same sensitivity, so there's no principled reason to score retention lower.

---

## Breach-Notification Tagging

`breach_notification_candidate = True` **only when** `severity == HIGH` **AND** `pii_category in {aadhaar, pan}` — not just any HIGH-severity verdict. A HIGH-severity `EXPOSURE_001` on a `phone` or `name` field (exposure is *always* HIGH per Check 1) does **not** get tagged, verified by dedicated tests.

This **only marks** what would trigger a DPDPA Section 8(6) breach-notification obligation for human/engineering review. It does not implement, queue, or trigger any actual notification workflow — that's explicitly out of scope per `docs/scope.md`.

---

## Interface Gap Found — Phase 3 Can Multi-Match One Field

**Found during implementation, not anticipated by the plan.** Phase 3's `DetectedEvent.matched_entities` can contain **multiple `MatchedEntity` records for the same field**, with **different `entity_type` values**, when more than one Presidio recognizer matches the same substring.

**Concretely verified:** An Aadhaar value like `"4412 7789 0021"` is matched by both:
- our custom `IN_AADHAAR` recognizer (confidence `0.75`)
- Presidio's generic built-in `PHONE_NUMBER` recognizer (confidence `0.4`, since a 12-digit spaced numeric string partially satisfies its phone pattern too)

Without handling this, the same real-world Aadhaar field independently triggered **two verdicts** — a correct `RETENTION_001` (via the `IN_AADHAAR` typing) and a spurious `PURPOSE_001` (via the `PHONE_NUMBER`-derived `"phone"` category mismatching the registry's `"aadhaar"` category) — directly violating the plan's "a field can only trigger exactly one rule" requirement.

**Resolution (implemented in Phase 4, not Phase 3):** `engine.py`'s `_dedupe_matches_by_field()` keeps only the **highest-confidence match per `(field, matched_text)` pair** before evaluation. Our custom Indian recognizers are deliberately scored higher than Presidio's generic built-ins (more precise, India-specific), so confidence-based tie-breaking naturally prefers the correct typing. Keys on `(field, matched_text)` rather than just `field`, so genuinely distinct values sharing Phase 3's `raw_snippet` free-text label (e.g. a name *and* a phone number in one support-ticket note) are correctly kept as separate matches.

**Why this lives in Phase 4, not Phase 3:** it's Phase 4's "one rule per field" contract that the ambiguity threatens. Phase 3's contract (report everything a recognizer finds) isn't wrong — just under-specified for what Phase 4 needed. This was **not** fixed by silently editing Phase 3's detection engine; flagged here instead, per the plan's explicit instruction.

Regression-tested: `test_aadhaar_field_double_matched_by_two_recognizers_produces_one_verdict`.

---

## Downstream Fan-Out

`VerdictFanout` pushes every verdict to two independent `asyncio.Queue`s — `llm_explainer_queue` (Phase 5) and `evidence_store_queue` (Phase 6). Both puts happen atomically per verdict so neither consumer can silently fall behind relative to the other. Stub consumer functions (`stub_llm_explainer_consumer`, `stub_evidence_store_consumer`) demonstrate the expected drain pattern — Phase 5 and Phase 6 replace these, they don't have to invent the wiring.

---

## Running the Tests

```bash
python -m pytest rules/test_rule_engine.py -v
```

27 tests total (24 sync + 3 async `VerdictFanout` tests via `pytest-asyncio`).
