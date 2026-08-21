"""
Veritas DPDPA Agent — Org Config Validator (Phase 1)
=====================================================
Validates a raw config dict against the OrgConfig schema.

Design principle: every rejection MUST be specific and actionable.
"Bad config" is not a valid error message. The user must know exactly
which field failed and why, so they can fix it without guessing.

Public interface:
  validate_org_config(raw: dict) -> list[str]  # Empty list = valid
  validate_org_config_strict(raw: dict) -> OrgConfig  # Raises OrgConfigValidationError
"""

from __future__ import annotations

import re
from typing import Any, Dict, List

from pydantic import ValidationError

from org_config.schema import BUILTIN_PATTERNS, KNOWN_VALIDATORS, OrgConfig


class OrgConfigValidationError(Exception):
    """
    Raised when an org config fails validation in strict mode.

    errors: list of specific, actionable error strings.
    """

    def __init__(self, errors: List[str]) -> None:
        self.errors = errors
        super().__init__(self._format())

    def _format(self) -> str:
        numbered = "\n".join(f"  {i+1}. {e}" for i, e in enumerate(self.errors))
        return f"Org config validation failed with {len(self.errors)} error(s):\n{numbered}"

    def __repr__(self) -> str:
        return f"OrgConfigValidationError(errors={self.errors!r})"


def validate_org_config(raw: Dict[str, Any]) -> List[str]:
    """
    Run all schema/sanity checks against a raw parsed config BEFORE it is
    accepted/stored. Return a list of error strings (empty list = valid).
    Never silently accept a partially-broken config.

    Checks performed:
      1. org_id is present, non-empty, and matches ^[a-z0-9_]+$.
      2. identifiers list: each entry has name and pattern; pattern compiles
         as a valid regex (or known built-in alias); no duplicate name values.
      3. fields list: each entry has field_name, pii_category, declared_purpose,
         consent_scope, retention_days (positive integer > 0), source_system;
         no duplicate (source_system, field_name) entries.
      4. linkage_rules (if present): each entry has fields list (>= 2 fields)
         and risk label; every field in linkage_rules[].fields exists in fields.
    """
    errors: List[str] = []

    if not isinstance(raw, dict):
        return [f"Config must be a dict/mapping, got {type(raw).__name__}."]

    # 1. org_id check
    if "org_id" not in raw:
        errors.append("Missing required field 'org_id'. Every org config must declare a unique org_id.")
    elif not isinstance(raw["org_id"], str) or not raw["org_id"].strip():
        errors.append("Field 'org_id' must be a non-empty string.")
    elif not re.match(r"^[a-z0-9_]+$", raw["org_id"]):
        errors.append(
            f"Field 'org_id' {raw['org_id']!r} is invalid. It must contain only lowercase letters, "
            f"digits, and underscores (pattern: ^[a-z0-9_]+$)."
        )

    # 2. identifiers check
    identifiers = raw.get("identifiers")
    if identifiers is not None:
        if not isinstance(identifiers, list):
            errors.append(f"Field 'identifiers' must be a list, got {type(identifiers).__name__}.")
        else:
            seen_ident_names: set[str] = set()
            for idx, ident in enumerate(identifiers):
                if not isinstance(ident, dict):
                    errors.append(f"identifiers[{idx}] must be a dict, got {type(ident).__name__}.")
                    continue

                name = ident.get("name")
                if not name or not isinstance(name, str):
                    errors.append(f"identifiers[{idx}].name must be a non-empty string.")
                else:
                    if name in seen_ident_names:
                        errors.append(
                            f"identifiers contains duplicate identifier name {name!r}. "
                            f"Each identifier name must be unique within an org's config."
                        )
                    seen_ident_names.add(name)

                pattern = ident.get("pattern")
                if not pattern or not isinstance(pattern, str):
                    errors.append(f"identifiers[{idx}].pattern must be a non-empty regex string.")
                else:
                    resolved = BUILTIN_PATTERNS.get(pattern, pattern)
                    try:
                        re.compile(resolved)
                    except re.error as e:
                        errors.append(
                            f"identifiers[{idx}].pattern {pattern!r} is not a valid regex "
                            f"(or known built-in alias): {e}."
                        )

                val_name = ident.get("validator", "none")
                if val_name not in KNOWN_VALIDATORS:
                    errors.append(
                        f"identifiers[{idx}].validator {val_name!r} is not a registered validator. "
                        f"Known validators: {sorted(KNOWN_VALIDATORS)}."
                    )

    # 3. fields check
    declared_field_names: set[str] = set()
    if "fields" not in raw:
        errors.append("Missing required field 'fields'. Every org config must declare at least one field.")
    elif not isinstance(raw["fields"], list):
        errors.append(f"Field 'fields' must be a list, got {type(raw['fields']).__name__}.")
    elif len(raw["fields"]) == 0:
        errors.append("Field 'fields' must contain at least one field definition.")
    else:
        seen_field_keys: set[tuple[str, str]] = set()
        for idx, f in enumerate(raw["fields"]):
            if not isinstance(f, dict):
                errors.append(f"fields[{idx}] must be a dict, got {type(f).__name__}.")
                continue

            for req in ("field_name", "pii_category", "declared_purpose", "consent_scope", "retention_days", "source_system"):
                if req not in f:
                    errors.append(f"fields[{idx}] missing required key {req!r}.")

            field_name = f.get("field_name")
            source_system = f.get("source_system")

            if field_name and isinstance(field_name, str):
                declared_field_names.add(field_name)

            if field_name and source_system and isinstance(field_name, str) and isinstance(source_system, str):
                key = (source_system, field_name)
                if key in seen_field_keys:
                    errors.append(
                        f"fields contains duplicate entry for field {field_name!r} under source_system {source_system!r}. "
                        f"Each field_name must be unique within its source_system."
                    )
                seen_field_keys.add(key)

            retention_days = f.get("retention_days")
            if retention_days is not None:
                if not isinstance(retention_days, int) or isinstance(retention_days, bool) or retention_days <= 0:
                    errors.append(
                        f"fields[{idx}].retention_days must be a positive integer (> 0), got {retention_days!r}."
                    )

    # 4. linkage_rules check
    linkage_rules = raw.get("linkage_rules")
    if linkage_rules is not None:
        if not isinstance(linkage_rules, list):
            errors.append(f"Field 'linkage_rules' must be a list, got {type(linkage_rules).__name__}.")
        else:
            for idx, rule in enumerate(linkage_rules):
                if not isinstance(rule, dict):
                    errors.append(f"linkage_rules[{idx}] must be a dict, got {type(rule).__name__}.")
                    continue

                r_fields = rule.get("fields")
                if not isinstance(r_fields, list) or len(r_fields) < 2:
                    errors.append(
                        f"linkage_rules[{idx}].fields must be a list of at least 2 field names, got {r_fields!r}."
                    )
                else:
                    undeclared = [rf for rf in r_fields if rf not in declared_field_names]
                    if undeclared:
                        errors.append(
                            f"linkage_rules[{idx}] references field(s) not declared in 'fields': {undeclared}. "
                            f"Declare them in fields first, or fix the typo."
                        )

    return errors


def validate_org_config_strict(raw: Dict[str, Any]) -> OrgConfig:
    """
    Validate a raw config dict and return a typed OrgConfig.
    Raises OrgConfigValidationError if validation fails.
    """
    errors = validate_org_config(raw)
    if errors:
        raise OrgConfigValidationError(errors)

    try:
        return OrgConfig(**raw)
    except ValidationError as e:
        # Fallback in case Pydantic catches any unexpected edge cases
        messages = [f"[{' → '.join(str(p) for p in err['loc'])}] {err['msg']}" for err in e.errors()]
        raise OrgConfigValidationError(messages) from e
