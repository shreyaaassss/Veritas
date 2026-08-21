"""
DPDPA Compliance Agent — Linkage / Combination-Risk Detection (Phase 3)
============================================================================
The 4th rule category, alongside Exposure, Purpose, and Retention (Checks
1-3 in engine.py). Detects the case where NO single field in an event is a
smoking gun on its own, but a COMBINATION of quasi-identifiers present
together is enough to plausibly re-identify a Data Principal (the
k-anonymity / linkage-risk concept) — e.g. a location field, a birth-date
field, and a demographic field, each individually low-signal but jointly
re-identifying at population scale (the exact field names are always
whatever a given org's config declares, never fixed here).

GROUND RULE: this module contains ZERO hardcoded field names or org-specific
combinations. Every "dangerous combination" comes from the triggering org's
`linkage_rules` config (org_config/schema.py's `LinkageRule`, already
structurally validated in Phase 1 — every field named in a linkage_rules
entry is guaranteed to also exist in that org's `fields` list, so this
module does not re-validate that, only reads it).

WHY THIS DOESN'T KEY OFF PHASE 2/3 PII DETECTION:
Per-field detection (detection/engine.py) only flags values that LOOK like
PII to Presidio's regex/NER recognizers — plenty of legitimate
quasi-identifier field shapes (a postal code, a birth date, a demographic
attribute, etc.) have no dedicated recognizer and simply won't match
anything. Linkage risk is a property of which FIELD NAMES co-occur in the
raw event, entirely independent of whether any of those fields' VALUES
were individually flagged as PII. Concretely: `detected.contains_pii` can
be False for an event whose fields nonetheless trip a linkage_rules
combination — so this check must NOT be gated behind `contains_pii`, unlike
Checks 1-3. See engine.py's evaluate_event() for how this is wired
(unconditionally, after the per-field short-circuit chain, using the full
raw event.fields dict rather than matched_entities).

RULE_ID DESIGN (documented per the plan's requirement to be reproducible,
not random per run): every linkage verdict uses the single, fixed
RuleId.LINKAGE_001 — mirroring how EXPOSURE_001/PURPOSE_001/RETENTION_001
each represent one violation CATEGORY, not a per-instance ID (a field-level
Exposure verdict doesn't get its own EXPOSURE_002, EXPOSURE_003 per field
either — the specific field is carried in Verdict.field). Consistently,
which specific configured rule fired is carried in the verdict's `field`
(a deterministic, sorted-by-config-order, comma-joined field list) and in
`matched_registry_entry` (a structured dict with the full `fields_involved`
list and the rule's `risk` label) — not by minting LINKAGE_002, LINKAGE_003,
etc., which would require inventing a per-org, per-rule numbering scheme
with no natural stable ordering across config edits.

NO LLM. NO MUTATION of Exposure/Purpose/Retention check logic (untouched,
still exactly engine.py Checks 1-3). This module only reads config and
constructs Verdict objects, same contract as engine.py's checks.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List

from config_loader import OrgConfigNotFoundError, load_org_config
from detection.models import DetectedEvent
from org_config.schema import LinkageRule
from rules.sensitivity import BREACH_NOTIFICATION_ELIGIBLE_CATEGORIES
from schemas.models import RemediationStatus, RuleId, Severity, Verdict

logger = logging.getLogger("rules.linkage")

# The org config's `risk` field is currently just a label (e.g.
# "LINKAGE_RISK") with no encoded severity tier — see org_config/schema.py's
# LinkageRule.risk. Per the plan's explicit instruction, we do NOT invent a
# severity-tuning scheme unprompted; every linkage verdict defaults to this
# fixed severity until a future config enhancement adds per-rule severity.
LINKAGE_DEFAULT_SEVERITY = Severity.MEDIUM

# pii_category placeholder passed to the shared breach-notification check
# below. "linkage" is intentionally not in BREACH_NOTIFICATION_ELIGIBLE_
# CATEGORIES ({"aadhaar", "pan"}), so this always evaluates to False unless
# a future revision assigns HIGH severity to specific linkage rules AND
# deliberately opts a rule into breach-notification eligibility — neither
# of which this phase does unprompted.
_LINKAGE_PII_CATEGORY_PLACEHOLDER = "linkage"


def _is_present(value: Any) -> bool:
    """
    Shared presence check for linkage purposes: a field counts as
    "present" only if it exists AND carries a real, non-blank value.

    - None -> not present (key missing, or explicitly null).
    - "" or whitespace-only string -> not present (a blank field can't
      leak information, even though the key technically exists).
    - Any other non-empty string, or any other truthy non-string value
      (defensive — event.fields is typed Dict[str, str], but this stays
      safe if a caller ever passes a looser dict) -> present.
    """
    if value is None:
        return False
    if isinstance(value, str):
        return value.strip() != ""
    return bool(value)


def _fired_linkage_rules(fields: Dict[str, str], org_id: str) -> List[LinkageRule]:
    """
    Pure core of linkage detection: given an event's raw field dict and an
    org_id, returns every configured linkage_rules entry whose ENTIRE
    field list is present (via _is_present) in `fields`.

    ALL-OR-NOTHING per rule: if even one of a rule's fields is missing or
    blank, that rule does not fire — no partial credit. A single event can
    independently fire multiple rules; all of them are returned, not just
    the first.

    Never raises: an org with no config on record, or with no
    linkage_rules declared at all, returns [] cleanly (a config with a
    `linkage_rules: []` or omitted key parses to an empty list already —
    see org_config/schema.py's `default_factory=list` — so both cases
    collapse to the same "no rules to check" path here).
    """
    try:
        config = load_org_config(org_id)
    except OrgConfigNotFoundError:
        return []

    return [
        rule for rule in config.linkage_rules
        if all(_is_present(fields.get(field_name)) for field_name in rule.fields)
    ]


def _linkage_verdict(event, rule: LinkageRule) -> Verdict:
    """Builds one Verdict for one fired LinkageRule, matching the exact
    Verdict schema Checks 1-3 already populate — see the module docstring's
    RULE_ID DESIGN note for why `field` carries a joined field list rather
    than this module minting new schema shape."""
    severity = LINKAGE_DEFAULT_SEVERITY
    breach_notification_candidate = (
        severity == Severity.HIGH
        and _LINKAGE_PII_CATEGORY_PLACEHOLDER in BREACH_NOTIFICATION_ELIGIBLE_CATEGORIES
    )
    return Verdict(
        tenant_id=event.tenant_id,
        verdict_id=str(uuid.uuid4()),
        event_id=str(event.event_id),
        rule_id=RuleId.LINKAGE_001,
        severity=severity,
        source=event.source_type,
        source_system=event.source_system,
        field=", ".join(rule.fields),
        timestamp=datetime.now(timezone.utc),
        matched_registry_entry={
            "rule_type": "linkage",
            "risk_label": rule.risk,
            "fields_involved": list(rule.fields),
        },
        breach_notification_candidate=breach_notification_candidate,
        remediation_status=RemediationStatus.OPEN,
        remediation_updated_at=None,
    )


def check_linkage_risk(detected: DetectedEvent) -> List[Verdict]:
    """
    Phase 3 entrypoint — Check 4 in engine.py's evaluate_event().

    Takes the DetectedEvent (matching the calling convention Checks 1-3
    already use) but, unlike them, reads the RAW event.fields dict
    directly rather than matched_entities/contains_pii — see the module
    docstring for why linkage risk is independent of per-field PII
    detection. org_id is derived from event.tenant_id, same as every
    other config-driven lookup in this pipeline.

    Returns one Verdict per fired linkage_rules entry (possibly more than
    one for a single event, possibly zero). Never raises — an org with no
    config, or no linkage_rules, produces [] cleanly via
    _fired_linkage_rules.
    """
    event = detected.event
    fired_rules = _fired_linkage_rules(event.fields, event.tenant_id)
    return [_linkage_verdict(event, rule) for rule in fired_rules]
