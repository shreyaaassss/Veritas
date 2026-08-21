"""
Veritas DPDPA Agent — Org Config Validator (Phase 0)
=====================================================
Validates a raw config dict against the OrgConfig schema.
Returns an OrgConfig on success, raises OrgConfigValidationError on failure.

Design principle: every rejection MUST be specific and actionable.
"Bad config" is not a valid error message. The user must know exactly
which field failed and why, so they can fix it without guessing.
"""

from __future__ import annotations

from typing import Any, Dict, List

from pydantic import ValidationError

from org_config.schema import OrgConfig


# ---------------------------------------------------------------------------
# Custom exception — carries a structured list of failure messages
# ---------------------------------------------------------------------------

class OrgConfigValidationError(Exception):
    """
    Raised when an org config fails validation.

    errors: list of specific, actionable error strings. Each entry describes
    exactly which field/rule failed and why.
    """

    def __init__(self, errors: List[str]) -> None:
        self.errors = errors
        super().__init__(self._format())

    def _format(self) -> str:
        numbered = "\n".join(f"  {i+1}. {e}" for i, e in enumerate(self.errors))
        return f"Org config validation failed with {len(self.errors)} error(s):\n{numbered}"

    def __repr__(self) -> str:
        return f"OrgConfigValidationError(errors={self.errors!r})"


# ---------------------------------------------------------------------------
# Pre-validation checks (before Pydantic)
# ---------------------------------------------------------------------------

def _pre_validate(raw: Dict[str, Any]) -> List[str]:
    """
    Fast structural checks before Pydantic validation. Returns a list of
    specific error messages. If non-empty, validation is aborted immediately.

    These catch missing top-level keys with clearer messages than Pydantic's
    default "field required" (which can be cryptic for nested structures).
    """
    errors: List[str] = []

    if not isinstance(raw, dict):
        return [f"Config must be a dict/mapping, got {type(raw).__name__}."]

    if "org_id" not in raw:
        errors.append(
            "Missing required field 'org_id'. Every org config must declare a unique org_id."
        )
    elif not isinstance(raw["org_id"], str) or not raw["org_id"].strip():
        errors.append(
            "Field 'org_id' must be a non-empty string."
        )

    if "fields" not in raw:
        errors.append(
            "Missing required field 'fields'. "
            "Every org config must declare at least the PII fields the org collects."
        )
    elif not isinstance(raw["fields"], list):
        errors.append(
            f"Field 'fields' must be a list of field definitions, got {type(raw['fields']).__name__}."
        )
    elif len(raw["fields"]) == 0:
        errors.append(
            "Field 'fields' must contain at least one field definition. "
            "An org config with no fields is not useful."
        )

    return errors


def _pydantic_errors_to_messages(e: ValidationError, org_id: str) -> List[str]:
    """
    Convert Pydantic's ValidationError into specific, human-readable messages.
    Pydantic's raw errors are technically complete but often cryptic — this
    function rewrites them into the format the spec requires.
    """
    messages: List[str] = []
    for err in e.errors():
        loc = " → ".join(str(p) for p in err["loc"]) if err["loc"] else "(root)"
        msg = err["msg"]
        err_type = err.get("type", "")

        # Produce a specific, contextual message for common error shapes
        if "duplicate field_name" in msg:
            messages.append(f"[{loc}] {msg}")
        elif "linkage_rule references field" in msg:
            messages.append(f"[linkage_rules] {msg}")
        elif "pattern" in loc and "valid regex" in msg:
            messages.append(
                f"[{loc}] Invalid regex pattern — {msg}. "
                f"Fix the pattern or use a built-in alias."
            )
        elif "validator" in loc and "not a registered" in msg:
            messages.append(
                f"[{loc}] {msg}"
            )
        elif err_type in ("missing", "value_error.missing"):
            messages.append(
                f"[{loc}] Required field is missing. "
                f"Every field definition must include: field_name, pii_category, "
                f"declared_purpose, consent_scope, retention_days, source_system."
            )
        elif "at_least_two" in msg or "min_length" in err_type:
            messages.append(f"[{loc}] {msg}")
        else:
            messages.append(f"[{loc}] {msg}")

    return messages


# ---------------------------------------------------------------------------
# Public validator function
# ---------------------------------------------------------------------------

def validate_org_config(raw: Dict[str, Any]) -> OrgConfig:
    """
    Validate a raw config dict against the OrgConfig schema.

    Returns:
        OrgConfig: a validated, fully-typed config object.

    Raises:
        OrgConfigValidationError: if the config is invalid. The exception's
        `errors` attribute contains a list of specific, actionable messages —
        one per validation failure.

    This is the ONLY function callers should use to validate configs.
    Do not instantiate OrgConfig directly from untrusted input.
    """
    # Step 1: fast structural pre-checks
    pre_errors = _pre_validate(raw)
    if pre_errors:
        raise OrgConfigValidationError(pre_errors)

    org_id = raw.get("org_id", "<unknown>")

    # Step 2: Pydantic schema validation
    try:
        config = OrgConfig(**raw)
    except ValidationError as e:
        messages = _pydantic_errors_to_messages(e, org_id)
        raise OrgConfigValidationError(messages) from e

    return config
