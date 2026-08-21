# Phase 3 — PII Detection Pass

**Status: Implemented.**

Deterministic PII detection over Phase 2's Event stream using Microsoft
Presidio (regex + spaCy NLP-based recognizers). **No LLM is used anywhere
in this phase or ever will be for this decision** — this is the hard
boundary the whole pipeline depends on for explainability. Any future
ML/LLM-based secondary detector (Phase 9 stretch) is additive signal only
and never overrides what this engine decides.

---

## Files

| File | Purpose |
|---|---|
| `recognizers.py` | Custom Presidio `PatternRecognizer` subclasses: `AadhaarRecognizer`, `PANRecognizer`, `IndianPhoneRecognizer` |
| `analyzer_engine.py` | Builds the shared `AnalyzerEngine` (spaCy NLP + built-in + custom recognizers), `analyze_text()` entrypoint, `SUPPORTED_ENTITY_TYPES` allowlist |
| `models.py` | `DetectedEvent` (wraps `Event`, never mutates it), `MatchedEntity`, `FREE_TEXT_FIELD_LABEL` |
| `engine.py` | `detect_event()` — the per-event detection function; `detect_from_queue()` — async queue-to-queue consumer for Phase 2 → Phase 4 wiring |
| `fixtures.py` | Real/realistic sample events (pulled from actual Phase 2 output shapes) used in tests, including a hand-built `support_tickets`-style free-text event |
| `test_detection.py` | 31 tests across 5 categories — see below |

---

## Entity Type Names (locked — Phase 1 and Phase 4 reference these)

| Entity type | Source | What it catches |
|---|---|---|
| `PERSON` | Presidio built-in (spaCy NER) | Names |
| `EMAIL_ADDRESS` | Presidio built-in | Email addresses |
| `PHONE_NUMBER` | Presidio built-in (generic) | Phone-shaped numbers, locale-agnostic |
| `IN_AADHAAR` | Custom (`recognizers.py`) | 12-digit Aadhaar numbers, spaced (`XXXX XXXX XXXX`) or unspaced |
| `IN_PAN` | Custom (`recognizers.py`) | 10-char PAN: `[A-Z]{5}[0-9]{4}[A-Z]{1}` |
| `IN_PHONE` | Custom (`recognizers.py`) | 10-digit Indian mobile numbers (starts 6-9), with/without `+91` prefix |

Do not rename these without updating this file and checking whether Phase 4 has started keying off them.

---

## What Gets Scanned

For every `Event`:
1. **Every value in `event.fields`**, individually — so matches are attributed to a real `field_name` (what Phase 1's registry and Phase 4's rule engine key off of).
2. **`event.raw_snippet`**, for every event regardless of `source_type` — this is how PII embedded in unstructured text (e.g. a support agent's free-text resolution note) gets caught, not just structured fields.
3. Matches found in `raw_snippet` that duplicate an already-found field match are deduplicated (see `engine.py`'s `_normalize_for_dedup`) so the same real-world PII value isn't double-counted just because it appears in both a structured field and the serialized/logged text.
4. Matches with no corresponding structured field (pure free-text catches) are labeled `field: "raw_snippet"` — see `FREE_TEXT_FIELD_LABEL` in `models.py` for why no fuzzy field-name guessing is attempted.

---

## Output Shape

```json
{
  "contains_pii": true,
  "matched_entities": [
    { "field": "phone", "entity_type": "IN_PHONE", "confidence": 0.75, "matched_text": "9876543210" }
  ]
}
```

`contains_pii` and `matched_entities` are **always present**, never omitted — `false` / `[]` for clean events.

**Interface note for Phase 4:** a `DetectedEvent` with `contains_pii == False` still flows through the pipeline (pass-through, tagged but untouched) — this phase does not filter anything out. Phase 4's rule engine should short-circuit immediately on `contains_pii == False`, since a clean event cannot trigger `EXPOSURE_001`, `PURPOSE_001`, or `RETENTION_001`. That short-circuit is Phase 4's responsibility, not implemented here.

---

## Known Limitations (documented, not silently patched)

**1. Address fields can false-positive as `PERSON`.**
spaCy's NER sometimes tags building/complex names inside address strings (e.g. "Green Meadows") as `PERSON`. This is a genuine NER precision limitation on short capitalized tokens with ambiguous real-world referents (a building name and a person's name look identical to the tagger without broader context). Not filtered, since a general fix would risk suppressing real names — Phase 4 should be aware that `PERSON` matches on an `address` field carry a higher false-positive risk than `PERSON` matches on a `name` field.

**2. Blinkit's own ID shapes (`BLK-XXXXXX`, `DP-XXXX`) are filtered out of `PERSON` results.**
Presidio's spaCy-backed `PERSON` recognizer assigns a flat 0.85 confidence to any isolated capitalized alphanumeric token, regardless of whether it resembles a real name — verified directly (`BLK-431682` scored identically to `Priya Nair`). Since Phase 3 scans field values in isolation (needed for field attribution), it loses the sentence context that would normally disambiguate this. A narrow, explicit post-filter (`analyzer_engine.py`'s `_is_known_id_shape`) suppresses `PERSON` matches on tokens matching Blinkit's `[A-Z]{2,5}-\d+` ID convention. This is intentionally narrow — it does not attempt a general solution, and does not affect real two-word name matches (verified by regression test).

**3. Aadhaar false-positive trade-off.**
The unspaced 12-digit Aadhaar pattern (score 0.4, lower than the spaced pattern's 0.75) can in principle match any bare 12-digit numeric string with no surrounding context. Context-word boosting (`aadhaar`, `uid` nearby) raises confidence but Presidio's context mechanism does not suppress matches when no context word is present. Accepted for MVP since Phase 2's own generators never emit a colliding 12-digit shape (order IDs use `BLK-XXXXXX`, not bare digits). A production system would need Aadhaar's Verhoeff checksum validation to fully close this — out of MVP scope.

---

## Running the Tests

```bash
python -m pytest detection/test_detection.py -v
```

First test run is slower (~5s) since it loads the `en_core_web_lg` spaCy model into the shared `AnalyzerEngine` singleton on first use.

---

## Setup (if running fresh)

```bash
pip install presidio-analyzer presidio-anonymizer spacy --break-system-packages
pip install https://github.com/explosion/spacy-models/releases/download/en_core_web_lg-3.8.0/en_core_web_lg-3.8.0-py3-none-any.whl --break-system-packages
```
