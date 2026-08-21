# Seeded Violations Index

> Single source of truth for every deliberately-seeded violation in the registry.
> Phase 2's event generator and Phase 4's rule engine should both reference this
> list rather than re-deriving it independently — if you add a new seeded
> violation to `seed_registry.py`, add it here too.

---

## 1. `delivery_partners` — Retention Violation (`RETENTION_001`)

**Where:** `registry/seed_registry.py` → `DELIVERY_PARTNERS_ENTRIES` → the entry with `is_seeded_violation=True`

**What it tests:** An inactive delivery partner's `aadhaar` record has `created_at` 240 days in the past, but `delivery_partners` has a `retention_days` of 180. The record is **60 days past its legal retention window**.

**Why it matters:** This is the required Phase 1 retention-violation seed. Phase 4's rule engine must compute `age_days = now - created_at` and compare against `retention_days` to catch this — it's the canonical `RETENTION_001` test case.

**Lookup:** `get_registry_entry("aadhaar", "delivery-partner-service")` — note this returns the *first* matching entry per the current index design (keyed by `field_name` + `source_system`); the seeded-violation row and the "normal" `aadhaar` row share a key. See **Known Limitation** below.

---

## 2. `marketing_events` — Structural Purpose-Limitation Invariant (`PURPOSE_001`)

**Where:** `registry/seed_registry.py` → `MARKETING_EVENTS_ENTRIES` → the `phone` field entry with `is_seeded_violation=True`

**What it tests:** Every `marketing_events` registry entry carries `consent_scope == "deidentified_or_hashed_only"`. This is not a per-row exception — it's structural. Any raw (unhashed) PII observed in a `marketing-analytics` event is automatically a purpose-limitation violation **by construction**, because no field governed by this scope permits raw identifiers.

**Why it matters:** When Phase 2's mock event generator occasionally injects a raw phone/Aadhaar into a marketing event (per Phase 2's spec), Phase 4 needs a registry-backed reason to flag it — not just "field not found." This seeded `phone` entry ensures the lookup **succeeds** (rather than returning `None`), and `entry.forbids_raw_pii()` returns `True`, giving Phase 4 a clean, deterministic signal.

**Lookup:** `get_registry_entry("phone", "marketing-analytics")` → returns the entry → call `.forbids_raw_pii()` → `True`.

**Programmatic check:** `RegistryEntry.forbids_raw_pii()` — checks `declared_purpose == "marketing_analytics" and consent_scope == MARKETING_EVENTS_ALLOWED_SCOPE`. Phase 4 should call this method rather than re-deriving the string comparison itself.

---

## Known Limitation — Duplicate Keys in `delivery_partners`

`RegistryStore` is indexed by `(source_system, field_name)`. The `delivery_partners` table has **two** `aadhaar` entries: one normal (created 45 days ago) and one seeded-violation (created 240 days ago). Since both share the same `(delivery-partner-service, aadhaar)` key, the index in `registry/models.py::RegistryStore._index()` will resolve to **whichever entry is later in the `entries` list** (Python dict construction keeps the last value on key collision).

In `seed_registry.py`, the seeded-violation row is appended **after** the normal row, so `get_registry_entry("aadhaar", "delivery-partner-service")` currently returns the **violation** row.

This is acceptable for Phase 1 (a single-org-record registry doesn't really have "two customers' worth" of the same field under MVP scope), but Phase 4 should be aware: if it needs to reason about *all* Aadhaar records for delivery partners (e.g. to catch every retention violation, not just look up "the" Aadhaar policy), it must call `list_registry_entries("delivery-partner-service")` and filter by `field_name == "aadhaar"` — not rely on `get_registry_entry` returning every row.

This is documented here rather than "fixed" because Phase 4 hasn't been scoped yet and changing the indexing strategy (e.g. per-row IDs, list-valued lookups) is a decision that affects Phase 4's design — flagging it now so it isn't a silent surprise.
