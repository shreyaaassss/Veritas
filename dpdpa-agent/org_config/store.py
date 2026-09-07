"""
Veritas DPDPA Agent — Org Config Store (Phase 0)
=================================================
Stores and retrieves validated org configs keyed by org_id.

DESIGN:
  - One directory per org: org_config/configs/{org_id}/
  - One YAML file per upload: {timestamp_iso}.yaml
  - Latest version = lexicographically greatest filename
  - get_org_config returns the latest version for an org_id
  - upload_org_config validates before storing — bad configs are
    rejected with specific errors, never silently accepted

VERSIONING:
  Config files are named by UTC timestamp (ISO8601, safe for filenames).
  Uploading a new config for an org creates a new versioned file —
  it never overwrites the previous version. This makes config changes
  themselves auditable (an org updating their retention policy leaves
  a clear paper trail).

PUBLIC INTERFACE:
  upload_org_config(org_id, config_dict) -> dict
  get_org_config(org_id) -> OrgConfig | None
  list_org_config_versions(org_id) -> list[str]
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from org_config.schema import OrgConfig
from org_config.validator import OrgConfigValidationError, validate_org_config

logger = logging.getLogger("org_config.store")

# The directory where all org config YAML files are stored.
# Relative to this file's location (org_config/), configs/ is a sibling directory.
from runtime_paths import data_root as _data_root
_CONFIGS_DIR = _data_root() / "org_config" / "configs"


def _org_dir(org_id: str) -> Path:
    """Return the per-org config directory, creating it if needed."""
    org_dir = _CONFIGS_DIR / org_id
    org_dir.mkdir(parents=True, exist_ok=True)
    return org_dir


def _timestamp_filename() -> str:
    """
    Generate a safe, sortable filename from the current UTC timestamp.
    Format: 2026-08-21T23-04-55Z.yaml (colons replaced with hyphens for FS safety).
    Lexicographic order = chronological order — latest file is always last.
    """
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")
    return f"{ts}.yaml"


def _latest_config_path(org_id: str) -> Optional[Path]:
    """
    Return the path of the latest config file for an org, or None if
    no config exists for this org_id.
    """
    org_dir = _CONFIGS_DIR / org_id
    if not org_dir.exists():
        return None
    yaml_files = sorted(org_dir.glob("*.yaml"))
    if not yaml_files:
        return None
    return yaml_files[-1]  # lexicographically greatest = most recent


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def upload_org_config(org_id: str, config_dict: Dict[str, Any]) -> Dict[str, Any]:
    """
    Validate and store an org config.

    The org_id in config_dict must match the org_id parameter — this
    prevents accidentally uploading org A's config under org B's key.

    Returns:
        On success: {"status": "ok", "org_id": org_id, "version": filename}
        On failure: {"status": "error", "org_id": org_id, "errors": [...]}

    The config is NOT stored if validation fails. Callers can check
    result["status"] or inspect result["errors"].

    Design note: this function never raises on validation errors — it
    returns a structured error dict instead. This makes it suitable for
    use as both a CLI function and an HTTP handler. Callers that want
    exception semantics can raise on result["status"] == "error" themselves.
    """
    # Inject org_id into config_dict for validation (in case caller omitted it)
    if "org_id" not in config_dict:
        config_dict = {**config_dict, "org_id": org_id}

    # Verify the org_id in the config matches the upload target
    config_org_id = config_dict.get("org_id", "")
    if config_org_id != org_id:
        return {
            "status": "error",
            "org_id": org_id,
            "errors": [
                f"org_id mismatch: config declares org_id={config_org_id!r} but "
                f"you are uploading to org_id={org_id!r}. They must match."
            ],
        }

    errors = validate_org_config(config_dict)
    if errors:
        logger.warning(
            "Rejected config upload for org_id=%r: %d error(s)", org_id, len(errors)
        )
        return {"status": "error", "org_id": org_id, "errors": errors}

    # Validation passed — persist to disk
    filename = _timestamp_filename()
    config_path = _org_dir(org_id) / filename

    with open(config_path, "w", encoding="utf-8") as f:
        yaml.dump(config_dict, f, allow_unicode=True, sort_keys=False)

    logger.info(
        "Stored org config: org_id=%r version=%r path=%s", org_id, filename, config_path
    )
    return {"status": "ok", "org_id": org_id, "version": filename}


def get_org_config(org_id: str) -> Optional[OrgConfig]:
    """
    Retrieve the latest validated config for an org_id.

    Returns:
        OrgConfig: the latest config for this org.
        None: if no config has ever been uploaded for this org_id.

    Note: configs stored on disk are assumed to be already valid (they
    were validated before storage). If a stored config fails to parse,
    a warning is logged and None is returned — this should never happen
    in normal operation but guards against manual disk edits.
    """
    path = _latest_config_path(org_id)
    if path is None:
        logger.debug("No config found for org_id=%r", org_id)
        return None

    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    if not isinstance(raw, dict):
        return None

    errors = validate_org_config(raw)
    if errors:
        logger.warning(
            "Stored config for org_id=%r at %s failed re-validation: %s. "
            "This suggests the file was manually edited after upload.",
            org_id, path, errors,
        )
        return None

    try:
        config = OrgConfig(**raw)
    except Exception as e:
        logger.warning("Stored config for org_id=%r failed parsing: %s", org_id, e)
        return None

    return config


def list_org_config_versions(org_id: str) -> List[str]:
    """
    List all stored config versions for an org, oldest first.
    Returns an empty list if no config exists for this org_id.
    """
    org_dir = _CONFIGS_DIR / org_id
    if not org_dir.exists():
        return []
    return sorted(p.name for p in org_dir.glob("*.yaml"))


def list_registered_orgs() -> List[str]:
    """
    Phase 5: returns every org_id that has at least one stored config
    version, sorted alphabetically. Powers the dashboard's org-selector
    (dashboard/index.html) and the /v1/orgs API route — deliberately a
    thin read of the filesystem, no caching, since this is a low-traffic,
    demo-scale listing, not a hot path.
    """
    if not _CONFIGS_DIR.exists():
        return []
    return sorted(
        d.name for d in _CONFIGS_DIR.iterdir()
        if d.is_dir() and any(d.glob("*.yaml"))
    )


def get_config_raw(org_id: str, version: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """
    Return the raw dict of an org's config (latest, or a specific version).
    Version is the filename e.g. "2026-08-21T00-00-00Z.yaml".
    Returns None if the org or version doesn't exist.
    """
    if version:
        config_path = _CONFIGS_DIR / org_id / version
        if not config_path.exists():
            return None
    else:
        config_path = _latest_config_path(org_id)
        if config_path is None:
            return None

    with open(config_path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    return raw if isinstance(raw, dict) else None


def get_config_version_meta(org_id: str) -> List[Dict[str, Any]]:
    """
    Return metadata for all config versions of an org, newest first.
    Each entry: {version, uploaded_at, is_current, size_bytes}
    """
    versions = list_org_config_versions(org_id)
    if not versions:
        return []
    current = versions[-1]  # newest = current
    result = []
    for v in reversed(versions):
        path = _CONFIGS_DIR / org_id / v
        # Convert filename back to ISO timestamp: "2026-08-21T00-00-00Z.yaml" → "2026-08-21T00:00:00Z"
        ts_str = v.replace(".yaml", "").replace("-", ":", 2)  # only first two hyphens in time part
        # Safer approach: just return the filename as version ID, timestamp parseable from it
        uploaded_at = v.replace(".yaml", "").replace("T", "T").replace("-", ":", 2) if "T" in v else v
        result.append({
            "version":     v,
            "uploaded_at": uploaded_at,
            "is_current":  v == current,
            "size_bytes":  path.stat().st_size if path.exists() else 0,
        })
    return result


def delete_org_configs(org_id: str) -> int:
    """
    Delete all stored configs for an org. Returns the number of files deleted.
    TEST HELPER — not intended for production use.
    """
    org_dir = _CONFIGS_DIR / org_id
    if not org_dir.exists():
        return 0
    deleted = 0
    for f in org_dir.glob("*.yaml"):
        f.unlink()
        deleted += 1
    return deleted
