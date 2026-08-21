# Phase 1 — Consent Registry

**Status: Implemented.**

Ground-truth lookup for what data each Blinkit `source_system` is declared to
collect, for what purpose, under what consent scope, and for how long. Phase 4's
Rule Engine calls this registry synchronously, per event, to decide whether a
piece of PII is where it's allowed to be, for the reason it was collected, for
as long as it's allowed to be kept.

This phase is pure data + lookup — no live ingestion, no rule evaluation, no
PII detection. Those are Phases 2–4.

---

## Files

| File | Purpose |
|---|---|
| `models.py` | `RegistryEntry` model, `RegistryStore` container, `TABLE_TO_SOURCE_SYSTEM` mapping |
| `seed_registry.py` | Hand-authored seed data for all four mock tables, including deliberate violations |
| `loader.py` | Public interface: `load_registry()`, `get_registry_entry()`, `list_registry_entries()` |
| `seeded_violations.md` | Auditor-facing index of every deliberately-seeded violation and why it exists |
| `test_registry.py` | 18 tests — per-table lookup, unknown-field handling, seeded violation checks, marketing invariant |

---

## Public Interface (what Phase 4 imports)

```python
from registry.loader import load_registry, get_registry_entry, list_registry_entries

# Synchronous, in-memory, returns None cleanly on miss — never raises
entry = get_registry_entry("aadhaar", "delivery-partner-service")

# Optional filter by source_system
all_delivery_entries = list_registry_entries("delivery-partner-service")
```

---

## The Four Mock Tables

| Table | `source_system` | Declared Purpose | Retention | Notes |
|---|---|---|---|---|
| `customers` | `order-service` | `order_fulfillment` | 1095 days (3yr) | name, phone, delivery_address, order_history |
| `delivery_partners` | `delivery-partner-service` | `onboarding_kyc` | 180 days (tight) | Aadhaar, PAN, bank_details, address — contains the **seeded retention violation** |
| `support_tickets` | `support-ticketing` | `customer_support` | 730 days (2yr) | Highest exposure risk — PII rides along in free-text notes, not registry-tracked |
| `marketing_events` | `marketing-analytics` | `marketing_analytics` | 365 days | **Structurally forbids raw PII** — any raw identifier here is a purpose-limitation violation by construction |

See `seeded_violations.md` for full detail on the two deliberately-seeded cases.

---

## Known Limitation

`delivery_partners` has two `aadhaar` entries (one normal, one seeded-violation)
sharing the same lookup key. `get_registry_entry()` returns the last-indexed
match. See `seeded_violations.md` → "Known Limitation" for what this means for
Phase 4.
