"""
DPDPA Compliance Agent — Rule Engine (Policy Decision Point)
=================================================================
PROTECTED MODULE — never cut, even under time pressure. This is where a
PII match becomes an actual compliance verdict. Fully deterministic, no
LLM anywhere in this module. Decoupled from rendering/explanation (OPA-
style PDP/PEP separation): this module decides, it does not explain.

For each field-level PII match in a DetectedEvent, runs three checks IN
ORDER, with Check 1 able to short-circuit Checks 2 and 3:

  Check 1 — Exposure   (cheapest, binary, runs first)
  Check 2 — Purpose/Consent scope   (registry lookup)
  Check 3 — Retention   (registry lookup, age vs retention_days)

A field can trigger AT MOST ONE rule — the first check that fires wins
and the remaining checks are skipped for that field. See _evaluate_field
for the exact short-circuit logic.

PHASE 3 ADDITION — Check 4 — Linkage / combination-risk (rules/linkage.py):
runs once per EVENT (not per field-level match) AFTER Checks 1-3, using
the event's full raw field set against the org's config-declared
linkage_rules. Implemented in its own module, not inline here, and NOT
gated behind contains_pii — see evaluate_event()'s docstring and
rules/linkage.py's module docstring for why. This module still owns
Checks 1-3 exclusively; Check 4's detection logic lives entirely in
rules/linkage.py, evaluate_event() only wires the two together.

=====================================================================
DESIGN DECISION #1 — Exposure vs Purpose precedence for marketing-analytics
=====================================================================
The plan is explicit: "any matched_entities hit where source_type ==
'log' ... is an exposure violation" AND "if you think this precedence
rule produces a confusing verdict for the marketing-events case
specifically, implement exactly what the plan says (exposure
short-circuits) but leave a clear comment flagging the ambiguity."

We originally implemented EXACTLY that literal instruction: Check 1 fired
on source_type == "log" OR the hardcoded string source_system ==
"marketing-analytics", short-circuiting Checks 2/3 for that field. This
meant a raw phone number leaked into a marketing-analytics API event was
classified EXPOSURE_001 (HIGH severity), NOT PURPOSE_001 — even though
Phase 1 built registry.models.RegistryEntry.forbids_raw_pii()
SPECIFICALLY so this exact case could be classified as a purpose
violation.

THIS WAS FLAGGED AS A GENUINE, UNRESOLVED TENSION BETWEEN PHASE 1 AND
PHASE 4, NOT A BUG, with two resolution options documented for a future
revision:
  (a) Narrow Check 1's marketing-analytics clause to exposure-flavored
      surfaces only (e.g. logs), and let Check 2 own the
      marketing-analytics-payload case via forbids_raw_pii() as Phase 1
      intended.
  (b) Keep Check 1 as specified and treat EXPOSURE_001 as the correct
      classification precisely because "PII visible in plaintext where
      it structurally shouldn't be" IS exposure, regardless of channel.

PHASE 4 RESOLUTION: option (a), but implemented so the CLASSIFICATION
OUTCOME for Blinkit's existing seeded case is unchanged (still
EXPOSURE_001/HIGH — see test_marketing_purpose_case_classified_as_exposure_
per_design_decision_1, which still passes unmodified). What actually
changed is WHERE the signal comes from, per this phase's mandate to
remove org-specific hardcoding, not what the check decides: the literal
`== "marketing-analytics"` string comparison is gone. In its place,
Check 1 now calls _forbids_raw_pii_here(), a read-only, config-driven
probe via get_registry_entry()/RegistryEntry.forbids_raw_pii() — the same
mechanism Check 2 already used. For Blinkit's actual seeded data this
produces the IDENTICAL answer (its marketing-analytics phone field's
consent_scope is DEIDENTIFIED_ONLY_SCOPE, so forbids_raw_pii() is True,
so Check 1 still fires, still EXPOSURE_001), but it now works for ANY
org that declares a deidentified-only source_system in their own config,
not only one spelled exactly "marketing-analytics". forbids_raw_pii()
being "unreachable" in Check 2 (see that check's docstring below) is
therefore still accurate — Check 1 still intercepts every case where it
would apply, by construction, just via a generic signal instead of a
hardcoded name.

=====================================================================
DESIGN DECISION #2 — Unregistered-field handling (Check 2)
=====================================================================
If contains_pii is True for a field but get_registry_entry(field,
source_system) returns None (no declared purpose/consent/retention
exists for this field at all), we treat this as a VIOLATION, not a
warning-only pass-through. Rationale: DPDPA's purpose-limitation
principle requires a declared purpose to exist BEFORE data is
processed — a field with PII flowing through a system with literally no
registry entry is data with zero declared purpose, which is a purpose
violation by definition, arguably a *more* severe gap than "declared for
purpose X but used for Y" (Check 2's normal case), since there is no
purpose at all on record. We classify this as PURPOSE_001,
matched_registry_entry = null (nothing was found), severity = MEDIUM by
default (see UNREGISTERED_FIELD_DEFAULT_SEVERITY below) since we cannot
assess PII-category sensitivity from a registry entry that doesn't
exist — we fall back to the entity_type-derived category via
sensitivity.entity_type_to_pii_category for severity, which is the best
signal available without a registry hit.

This is a genuine judgment call the plan explicitly asked us to make and
document (not a fact derivable from the plan) — see rules/README.md for
the same rationale restated for a human audience.

=====================================================================
NO LLM. NO REGISTRY MUTATION. NO EVIDENCE STORE WRITES. This module only
constructs Verdict objects and hands them to the fan-out (see fanout.py).
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import List, Optional

from detection.models import DetectedEvent, MatchedEntity
from registry.loader import (
    FieldNotRegisteredError,
    OrgConfigNotFoundError,
    get_registry_entry,
)
from registry.models import RegistryEntry
from rules.linkage import check_linkage_risk
from rules.sensitivity import (
    BREACH_NOTIFICATION_ELIGIBLE_CATEGORIES,
    PiiSensitivity,
    entity_type_to_pii_category,
    sensitivity_for_pii_category,
)
from schemas.models import RemediationStatus, RuleId, Severity, Verdict
# NOTE: SourceSystem enum no longer imported — source_system is a free-form str (Phase 0).

logger = logging.getLogger("rules.engine")

# Severity fallback for the unregistered-field case (Design Decision #2)
# when we have no registry entry to assess sensitivity against and must
# fall back to entity_type-derived category. MEDIUM chosen as a
# deliberate middle ground: not LOW (this is a genuine gap — PII with
# zero declared purpose is not a minor issue), not automatically HIGH
# (we don't yet know if the underlying PII type is high-sensitivity;
# the category-derived override below upgrades to HIGH when the
# entity_type maps to aadhaar/pan specifically).
UNREGISTERED_FIELD_DEFAULT_SEVERITY = Severity.MEDIUM


def _severity_for_category(pii_category: str, default: Severity = Severity.LOW) -> Severity:
    """Maps a pii_category to a Verdict Severity via the shared sensitivity tiers."""
    tier = sensitivity_for_pii_category(pii_category)
    return {
        PiiSensitivity.HIGH: Severity.HIGH,
        PiiSensitivity.MEDIUM: Severity.MEDIUM,
        PiiSensitivity.LOW: default,
    }[tier]


def _breach_notification_candidate(severity: Severity, pii_category: str) -> bool:
    """
    Design point per the plan (Check 6): breach_notification_candidate
    is True ONLY when severity == HIGH AND the pii_category is
    specifically aadhaar or pan — not just any HIGH-severity verdict.
    E.g. a HIGH-severity EXPOSURE_001 on a plain 'name' field (possible
    if a future severity rule ever assigns HIGH to name exposure) would
    NOT be a breach-notification candidate under this rule, because name
    is not in BREACH_NOTIFICATION_ELIGIBLE_CATEGORIES.

    This ONLY marks what would trigger a DPDPA Section 8(6)
    breach-notification obligation for human/engineering review — it
    does not implement, queue, or trigger any actual notification
    workflow. That workflow is explicitly out of scope; see
    docs/scope.md "Out of Scope — Breach-notification workflow execution".
    """
    return severity == Severity.HIGH and pii_category in BREACH_NOTIFICATION_ELIGIBLE_CATEGORIES


def _new_verdict(
    *,
    event_id,
    tenant_id: str,
    rule_id: RuleId,
    severity: Severity,
    source,
    source_system,
    field: str,
    matched_registry_entry,
    pii_category: str,
) -> Verdict:
    """
    Shared Verdict constructor. remediation_status always initializes to
    OPEN, remediation_updated_at always initializes to None — this
    module NEVER sets these to anything else; that is Phase 6/7's job
    exclusively, per the plan.
    Phase 0: tenant_id added — inherited from the triggering Event.
    """
    return Verdict(
        tenant_id=tenant_id,
        verdict_id=str(uuid.uuid4()),
        event_id=str(event_id),
        rule_id=rule_id,
        severity=severity,
        source=source,
        source_system=source_system,
        field=field,
        timestamp=datetime.now(timezone.utc),
        matched_registry_entry=matched_registry_entry,
        breach_notification_candidate=_breach_notification_candidate(severity, pii_category),
        remediation_status=RemediationStatus.OPEN,
        remediation_updated_at=None,
    )


# ---------------------------------------------------------------------------
# Check 1 — Exposure
# ---------------------------------------------------------------------------

def _forbids_raw_pii_here(event, field_name: str) -> bool:
    """
    PHASE 4 GENERALISATION of what used to be a hardcoded
    `source_system == "marketing-analytics"` string comparison (see
    Design Decision #1's history, and rules/README.md's Phase 4 note).

    Generic, config-driven equivalent: True iff THIS org's config has a
    registry entry for (field_name, event.source_system) whose
    consent_scope structurally forbids raw PII — i.e.
    RegistryEntry.forbids_raw_pii(), which Phase 1 already built for
    exactly this purpose. No org name, no source_system name, no
    hardcoded list of "risky" systems anywhere in this function — any
    org can opt any source_system into this behavior purely via its own
    config (declare a field's consent_scope as
    registry.models.DEIDENTIFIED_ONLY_SCOPE), with zero pipeline code
    changes.

    Read-only probe, safe to call from Check 1 despite Check 1's
    "no registry lookup" convention for the VERDICT it produces (see
    _check_1_exposure — matched_registry_entry stays null on fire; this
    function's result only decides WHETHER to fire, it is never surfaced
    as evidence). An unregistered (field_name, source_system) — no
    config entry at all — returns False here, not an error: that case is
    Check 2's Design Decision #2 territory, not Check 1's.
    """
    try:
        entry: Optional[RegistryEntry] = get_registry_entry(
            org_id=event.tenant_id,
            field_name=field_name,
            source_system=event.source_system,
            raise_on_missing=True,
        )
    except (FieldNotRegisteredError, OrgConfigNotFoundError):
        return False
    return entry is not None and entry.forbids_raw_pii()


def _check_1_exposure(detected: DetectedEvent, match: MatchedEntity) -> Optional[Verdict]:
    """
    Fires when raw PII is present somewhere it structurally shouldn't be:
      - source_type == "log"   (debug logs should never carry raw PII —
        this is a Phase 0 schema-level Event field, universal across
        every org, never org-specific data, so no config lookup needed)
      - this field's registry entry (if any) structurally forbids raw
        PII for its source_system — see _forbids_raw_pii_here above.
        PHASE 4: this used to be a hardcoded `== "marketing-analytics"`
        string; it is now fully config-driven and applies identically to
        any org that declares a deidentified-only source_system, not
        just Blinkit's literal spelling of one. The classification
        OUTCOME for Blinkit's existing seeded marketing-analytics case is
        deliberately unchanged (still EXPOSURE_001, still HIGH — see
        Design Decision #1, and rules/test_rule_engine.py's
        test_marketing_purpose_case_classified_as_exposure_per_design_decision_1,
        which still passes unmodified) — only WHERE the signal comes
        from changed, not WHAT it decides for that specific fixture.

    On fire: severity = HIGH, rule_id = EXPOSURE_001, no registry lookup
    performed (matched_registry_entry = null), Checks 2/3 skipped for
    this field by the caller (_evaluate_field).
    """
    event = detected.event
    is_log_exposure = event.source_type.value == "log"
    # Short-circuits before the registry lookup when already True via
    # the log path — avoids a needless config read on the common case.
    is_structurally_forbidden_exposure = is_log_exposure or _forbids_raw_pii_here(event, match.field)

    if not is_structurally_forbidden_exposure:
        return None

    pii_category = entity_type_to_pii_category(match.entity_type)

    return _new_verdict(
        event_id=event.event_id,
        tenant_id=event.tenant_id,
        rule_id=RuleId.EXPOSURE_001,
        severity=Severity.HIGH,
        source=event.source_type,
        source_system=event.source_system,
        field=match.field,
        matched_registry_entry=None,  # no registry lookup for exposure — see plan Check 1
        pii_category=pii_category,
    )


# ---------------------------------------------------------------------------
# Check 2 — Purpose / Consent scope
# ---------------------------------------------------------------------------

def _check_2_purpose(detected: DetectedEvent, match: MatchedEntity) -> Optional[Verdict]:
    """
    Only reached for fields that did NOT trigger Check 1. Looks up the
    registry entry for (field, source_system):

      - No entry found -> UNREGISTERED FIELD case (Design Decision #2
        above): classified as PURPOSE_001, matched_registry_entry=null,
        severity derived from entity_type since no registry sensitivity
        signal exists.
      - Entry found, entry.forbids_raw_pii() is True -> purpose
        violation (Phase 1's marketing_events structural invariant).
        NOTE: as documented in Design Decision #1, this branch is
        CURRENTLY UNREACHABLE in practice — Check 1's
        _forbids_raw_pii_here() probe (Phase 4) already intercepts
        every match where this same condition (entry.forbids_raw_pii())
        is True, by construction, before Check 2 ever runs. It remains
        implemented here for correctness/forward-compatibility (e.g. if
        Design Decision #1 is ever revised toward option (b) instead),
        and this is what makes it safe to leave in place rather than
        delete it as dead code.
      - Entry found, field's actual usage falls outside declared_purpose/
        consent_scope in some OTHER way -> also a purpose violation.
        For MVP scope (3 rule categories, 4 mock tables, no
        cross-source-system field movement modeled by Phase 2), the only
        concrete purpose-violation shape actually producible by this
        pipeline is the forbids_raw_pii() case above; this function
        still checks the general condition for forward-compatibility
        but it will not fire on Phase 1/2's current seed data outside
        that case.

    Severity: HIGH for aadhaar/pan, MEDIUM for phone/email, LOW for name
    alone — via rules.sensitivity's shared mapping (see that module's
    docstring for the full rationale).
    """
    event = detected.event
    pii_category = entity_type_to_pii_category(match.entity_type)

    # Phase 1: get_registry_entry is now keyed by org_id (event.tenant_id)
    try:
        entry: Optional[RegistryEntry] = get_registry_entry(
            org_id=event.tenant_id,
            field_name=match.field,
            source_system=event.source_system,
            raise_on_missing=True,
        )
    except (FieldNotRegisteredError, OrgConfigNotFoundError):
        entry = None

    if entry is None:
        # UNREGISTERED FIELD — Design Decision #2: treated as a violation,
        # not a silent pass or crash.
        logger.info(
            "Unregistered field with PII detected: field=%r source_system=%r "
            "entity_type=%r — classifying as PURPOSE_001 per documented policy "
            "(see rules/engine.py Design Decision #2).",
            match.field, event.source_system, match.entity_type,
        )
        severity = _severity_for_category(pii_category, default=UNREGISTERED_FIELD_DEFAULT_SEVERITY)
        # Category-derived HIGH override: even with no registry entry, if
        # the entity_type itself maps to a high-sensitivity category
        # (aadhaar/pan), don't under-report severity just because the
        # registry lookup missed.
        if sensitivity_for_pii_category(pii_category) == PiiSensitivity.HIGH:
            severity = Severity.HIGH

        return _new_verdict(
            event_id=event.event_id,
            tenant_id=event.tenant_id,
            rule_id=RuleId.PURPOSE_001,
            severity=severity,
            source=event.source_type,
            source_system=event.source_system,
            field=match.field,
            matched_registry_entry=None,
            pii_category=pii_category,
        )

    violates_purpose = entry.forbids_raw_pii()
    # Forward-compatible general check: field present but registry entry's
    # declared_purpose doesn't match the pii_category we detected. Not
    # currently reachable by seed data (see docstring) but kept for
    # correctness if the registry grows.
    if not violates_purpose and entry.pii_category != pii_category:
        violates_purpose = True

    if not violates_purpose:
        return None

    severity = _severity_for_category(entry.pii_category)

    return _new_verdict(
        event_id=event.event_id,
        tenant_id=event.tenant_id,
        rule_id=RuleId.PURPOSE_001,
        severity=severity,
        source=event.source_type,
        source_system=event.source_system,
        field=match.field,
        matched_registry_entry=entry.model_dump(mode="json"),
        pii_category=entry.pii_category,
    )


# ---------------------------------------------------------------------------
# Check 3 — Retention
# ---------------------------------------------------------------------------

def _check_3_retention(detected: DetectedEvent, match: MatchedEntity) -> Optional[Verdict]:
    """
    Only reached for fields that did NOT trigger Checks 1 or 2 (which
    means a registry entry DOES exist here — Check 2 already handled the
    no-entry case and would have returned a verdict before this runs).

    Computes event.timestamp - registry_entry.created_at and compares
    against registry_entry.retention_days. Past the window -> RETENTION_001.

    Severity mapping: same sensitivity-based approach as Check 2 (HIGH
    for aadhaar/pan, MEDIUM default otherwise) — chosen for consistency
    rather than inventing a second independent severity scheme; a
    retention violation on a HIGH-sensitivity field (e.g. a stale
    Aadhaar record, exactly Phase 1's seeded case) is not inherently
    less serious than a purpose violation on the same field type, so
    there's no principled reason to score it lower.
    """
    event = detected.event

    # Phase 1: get_registry_entry is now keyed by org_id (event.tenant_id)
    try:
        entry: Optional[RegistryEntry] = get_registry_entry(
            org_id=event.tenant_id,
            field_name=match.field,
            source_system=event.source_system,
            raise_on_missing=True,
        )
    except (FieldNotRegisteredError, OrgConfigNotFoundError):
        entry = None
    if entry is None:
        # Should not happen in practice — Check 2 already returned a
        # verdict for the no-entry case before Check 3 ever runs (see
        # _evaluate_field's short-circuit ordering). Defensive guard only.
        logger.warning(
            "Check 3 reached with no registry entry for field=%r source_system=%r — "
            "this should have been caught by Check 2. Skipping retention check.",
            match.field, event.source_system,
        )
        return None

    age = event.timestamp - entry.created_at
    if age.days <= entry.retention_days:
        return None  # within window, no violation

    severity = _severity_for_category(entry.pii_category, default=Severity.MEDIUM)

    return _new_verdict(
        event_id=event.event_id,
        tenant_id=event.tenant_id,
        rule_id=RuleId.RETENTION_001,
        severity=severity,
        source=event.source_type,
        source_system=event.source_system,
        field=match.field,
        matched_registry_entry=entry.model_dump(mode="json"),
        pii_category=entry.pii_category,
    )


# ---------------------------------------------------------------------------
# Per-field match deduplication — handles a Phase 3/Phase 4 interface gap
# ---------------------------------------------------------------------------

def _dedupe_matches_by_field(matches: List[MatchedEntity]) -> List[MatchedEntity]:
    """
    INTERFACE GAP FOUND AND HANDLED HERE (documented per the plan's
    explicit instruction to stop and document rather than silently
    reshape an earlier phase's contract):

    Phase 3's detection engine can produce MULTIPLE MatchedEntity records
    for the SAME field, with DIFFERENT entity_type values, when more than
    one Presidio recognizer matches the same substring. Concretely
    verified: an Aadhaar value like "4412 7789 0021" is matched BOTH by
    our custom IN_AADHAAR recognizer (score 0.75) AND by Presidio's
    generic built-in PHONE_NUMBER recognizer (score 0.4, since a 12-digit
    spaced numeric string partially satisfies its phone-shaped pattern
    too). Phase 3's DetectedEvent.matched_entities is documented as "all
    matches found" with no per-field uniqueness guarantee — Phase 3 never
    claimed one match per field, and this phase's plan assumed one
    Verdict-triggering outcome per field without stating that assumption
    depends on one match per field, which Phase 3's actual behavior does
    not guarantee.

    Without handling this, the SAME real-world field (e.g. one Aadhaar
    value) could independently trigger a DIFFERENT rule for each
    recognizer's match (e.g. RETENTION_001 via the correct IN_AADHAAR
    typing, AND a spurious PURPOSE_001 via the incorrect PHONE_NUMBER
    typing, since "phone" != registry's "aadhaar" pii_category triggers
    Check 2's general purpose-mismatch condition) — directly violating
    the plan's "a field can only trigger exactly one rule" requirement.

    RESOLUTION: before evaluation, keep only the HIGHEST-CONFIDENCE match
    per (field, matched_text) pair. Confidence is Presidio's own score,
    which is the most defensible existing signal for "which recognizer's
    typing of this same substring should win" — our custom IN_AADHAAR/
    IN_PAN/IN_PHONE recognizers are deliberately scored higher than
    Presidio's generic built-ins specifically because they're
    India-specific and more precise (see detection/recognizers.py), so
    confidence-based tie-breaking naturally prefers the more specific
    typing. This keys on (field, matched_text) rather than just field,
    so genuinely distinct values within one raw_snippet free-text field
    (e.g. both a name AND a phone number inside one support-ticket note)
    are correctly kept as separate matches, not collapsed into one.

    This fix lives in Phase 4 (not Phase 3) because it is Phase 4's
    "one rule per field" contract that the ambiguity threatens — Phase 3's
    contract (report everything a recognizer finds) is not itself wrong,
    just under-specified for what Phase 4 needed. Flagging in
    rules/README.md for the team; NOT silently editing Phase 3's
    detection/engine.py to change its documented multi-match behavior.
    """
    best_by_key: dict = {}
    for m in matches:
        key = (m.field, m.matched_text)
        existing = best_by_key.get(key)
        if existing is None or m.confidence > existing.confidence:
            best_by_key[key] = m
    return list(best_by_key.values())


# ---------------------------------------------------------------------------
# Per-field evaluation — the short-circuit chain
# ---------------------------------------------------------------------------

def _evaluate_field(detected: DetectedEvent, match: MatchedEntity) -> Optional[Verdict]:
    """
    Runs Checks 1 -> 2 -> 3 IN ORDER for a single matched_entities hit.
    The first check that returns a Verdict wins; remaining checks are
    skipped entirely for this field (short-circuit, not "run all three
    and pick one"). A field can therefore trigger AT MOST ONE rule.
    """
    verdict = _check_1_exposure(detected, match)
    if verdict is not None:
        return verdict

    verdict = _check_2_purpose(detected, match)
    if verdict is not None:
        return verdict

    verdict = _check_3_retention(detected, match)
    if verdict is not None:
        return verdict

    return None  # field has PII but violates none of the three rules


# ---------------------------------------------------------------------------
# Top-level entrypoint
# ---------------------------------------------------------------------------

def evaluate_event(detected: DetectedEvent) -> List[Verdict]:
    """
    Top-level Rule Engine entrypoint for a single DetectedEvent.

    Runs Checks 1-3 (Exposure -> Purpose -> Retention, per-field,
    short-circuited) IF contains_pii is True — same cheap early-exit as
    before for that portion: a clean event with no PII anywhere cannot
    violate any of those three rules, so no registry calls happen for it.

    Then runs Check 4 — Linkage (Phase 3, rules/linkage.py) —
    UNCONDITIONALLY, regardless of contains_pii. This is deliberate, not
    an oversight: linkage risk is a property of which FIELD NAMES
    co-occur in the raw event (per the org's linkage_rules config), not
    of whether Presidio flagged any of their VALUES as PII — see
    rules/linkage.py's module docstring for why gating Check 4 behind
    contains_pii would silently miss real linkage-risk events (e.g. a
    quasi-identifier combination where none of the individual field
    values are independently recognized as PII).

    Returns one Verdict per violating field (Checks 1-3) plus one Verdict
    per fired linkage rule (Check 4) — a clean event with no PII and no
    fired linkage rule produces an empty list, same "absence of a
    Verdict IS the no-violation signal" contract as before.
    """
    verdicts: List[Verdict] = []

    if detected.contains_pii:
        deduped_matches = _dedupe_matches_by_field(detected.matched_entities)
        for match in deduped_matches:
            verdict = _evaluate_field(detected, match)
            if verdict is not None:
                verdicts.append(verdict)

    verdicts.extend(check_linkage_risk(detected))

    return verdicts
