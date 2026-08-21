"""
DPDPA Compliance Agent — PII Sensitivity Classification
============================================================
Shared sensitivity tiers used by Check 2 (Purpose), Check 3 (Retention),
and breach-notification tagging. Defined once here so severity mapping
and breach-notification logic never drift out of sync with each other.

SENSITIVITY MAPPING RATIONALE (explicit, since the plan says to make
this reasoning clear rather than arbitrary):

  HIGH_SENSITIVITY   = {aadhaar, pan}
    These are government-issued identity/financial identifiers. Under
    DPDPA, they carry the highest individual harm potential if exposed
    or misused — Aadhaar enables identity theft and cross-database
    linkage at national scale; PAN is tied directly to financial/tax
    identity. This is also the ONLY tier that can ever produce
    breach_notification_candidate = true (see Check 6 / verdict_builder.py).

  MEDIUM_SENSITIVITY = {phone, email}
    Standard contact identifiers. Individually exploitable (spam,
    phishing, social engineering, SIM-swap-adjacent fraud) but lower
    ceiling of harm than a government identity number — a leaked phone
    number alone doesn't let someone open a bank account in your name.

  LOW_SENSITIVITY    = {name, address, order_history, order_reference,
                         financial_account, hashed_identifier}
    'name' alone (with no other identifier) has limited standalone harm
    — many people share a name. 'address' is technically higher-risk in
    isolation (physical safety) but is kept LOW here for a stated,
    narrow reason: Phase 3's detector has a known false-positive risk on
    address fields (building names read as PERSON — see
    detection/README.md "Known Limitations #1"), so we deliberately do
    not let 'address' drive a HIGH severity determination off a
    detection layer we know is less reliable for that specific field.
    'financial_account' (bank_details) is LOW here specifically because
    Phase 1's registry never exposes the actual account number through
    Presidio-detectable entity types (it's an opaque masked string like
    'XXXXXXXX4521' — Phase 3 confirmed this does not false-positive as
    PAN/Aadhaar, and Presidio has no BANK_ACCOUNT recognizer registered
    in Phase 3's SUPPORTED_ENTITY_TYPES), so in practice this category
    is never actually the trigger for a verdict — included here only for
    completeness of the mapping.
    'hashed_identifier' is LOW because, by construction (Phase 1's
    marketing_events invariant), a hashed/de-identified value is not raw
    PII at all — it should never be what a matched_entities hit points
    to, but is included for completeness / defensive mapping.

This mapping is intentionally coarse (3 tiers) rather than per-field
scoring, to keep the rule engine's severity decision auditable and
explainable in one sentence per verdict — a requirement the LLM
Explainer (Phase 5) will lean on.
"""

from __future__ import annotations

from enum import Enum


class PiiSensitivity(str, Enum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


# pii_category values as used in registry/seed_registry.py, mapped to
# sensitivity tier. Also covers Phase 3's raw entity_type strings (see
# ENTITY_TYPE_TO_PII_CATEGORY below) via normalization before lookup.
PII_CATEGORY_SENSITIVITY: dict[str, PiiSensitivity] = {
    "aadhaar": PiiSensitivity.HIGH,
    "pan": PiiSensitivity.HIGH,
    "phone": PiiSensitivity.MEDIUM,
    "email": PiiSensitivity.MEDIUM,
    "name": PiiSensitivity.LOW,
    "address": PiiSensitivity.LOW,
    "order_history": PiiSensitivity.LOW,
    "order_reference": PiiSensitivity.LOW,
    "financial_account": PiiSensitivity.LOW,
    "hashed_identifier": PiiSensitivity.LOW,
}

# Maps Phase 3's Presidio entity_type strings to the pii_category
# vocabulary used by Phase 1's registry (registry/seed_registry.py
# pii_category values) and by PII_CATEGORY_SENSITIVITY above. Needed
# because matched_entities carries entity_type ("IN_AADHAAR"), not
# pii_category ("aadhaar") — this is the bridge between the two phases'
# vocabularies. See rules/README.md for the full interface note.
ENTITY_TYPE_TO_PII_CATEGORY: dict[str, str] = {
    "IN_AADHAAR": "aadhaar",
    "IN_PAN": "pan",
    "IN_PHONE": "phone",
    "PHONE_NUMBER": "phone",
    "EMAIL_ADDRESS": "email",
    "PERSON": "name",
}

# Only these pii_category values can ever produce a HIGH-sensitivity
# breach-notification candidate — see verdict_builder.py Check 6.
BREACH_NOTIFICATION_ELIGIBLE_CATEGORIES = frozenset({"aadhaar", "pan"})


def entity_type_to_pii_category(entity_type: str) -> str:
    """
    Maps a Phase 3 entity_type (e.g. 'IN_AADHAAR') to the pii_category
    vocabulary Phase 1's registry uses (e.g. 'aadhaar'). Falls back to
    the lowercased entity_type itself if not in the explicit map, so an
    unmapped future entity type degrades to a best-effort guess rather
    than raising — logged as a warning by the caller, not here (this
    function stays pure/side-effect-free).
    """
    return ENTITY_TYPE_TO_PII_CATEGORY.get(entity_type, entity_type.lower())


def sensitivity_for_pii_category(pii_category: str) -> PiiSensitivity:
    """
    Returns the sensitivity tier for a pii_category string. Unknown
    categories default to LOW (fail toward under-alarming rather than
    over-alarming for categories we don't have an explicit opinion on —
    the registry is the source of truth for what's actually sensitive,
    and an unrecognized category is more likely a data-shape surprise
    than a genuinely novel high-risk PII type).
    """
    return PII_CATEGORY_SENSITIVITY.get(pii_category, PiiSensitivity.LOW)
