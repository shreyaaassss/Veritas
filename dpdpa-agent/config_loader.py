"""
Veritas DPDPA Agent — Config Loader Module Alias (Phase 1)
===========================================================
Top-level entrypoint providing the public interface for org config
loading, registry lookups, validation, and hot-reloads.
"""

from __future__ import annotations

from registry.loader import (
    FieldNotRegisteredError,
    OrgConfigNotFoundError,
    _reset_cache,
    get_registry_entry,
    list_registry_entries,
    load_org_config,
    load_registry,
    reload_org_config,
    validate_org_config,
)

__all__ = [
    "load_org_config",
    "get_registry_entry",
    "reload_org_config",
    "validate_org_config",
    "list_registry_entries",
    "load_registry",
    "OrgConfigNotFoundError",
    "FieldNotRegisteredError",
    "_reset_cache",
]
