"""
DPDPA Compliance Agent — Config-Driven Registry Loader (Phase 1)
=================================================================
Generic, multi-tenant registry loader and public query interface.

Public interface:
  load_org_config(org_id: str) -> OrgConfig
  get_registry_entry(org_id: str, field_name: str, source_system: str, raise_on_missing: bool = True) -> RegistryEntry
  reload_org_config(org_id: str) -> OrgConfig
  validate_org_config(raw_config: dict) -> list[str]
  list_registry_entries(org_id: str = "blinkit", source_system: Optional[str] = None) -> list[RegistryEntry]
  load_registry(org_id: str = "blinkit", force_reload: bool = False) -> RegistryStore
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Dict, List, Optional

from org_config.schema import OrgConfig, OrgField
from org_config.store import get_org_config
from org_config.validator import validate_org_config
from registry.models import DEIDENTIFIED_ONLY_SCOPE, RegistryEntry, RegistryStore
from registry.seed_registry import SEED_NOW

logger = logging.getLogger("registry.loader")

# In-memory config and store caches keyed by org_id
_config_cache: Dict[str, OrgConfig] = {}
_store_cache: Dict[str, RegistryStore] = {}


class OrgConfigNotFoundError(Exception):
    """Raised when an org_id has no registered config on record."""
    pass


class FieldNotRegisteredError(Exception):
    """Raised when a field is not declared in an org's config for a source_system."""
    pass


def load_org_config(org_id: str) -> OrgConfig:
    """
    Load and return the parsed, validated config for a given org.
    - Raises OrgConfigNotFoundError if org_id has no config on record.
    - Uses an in-memory cache keyed by org_id.
    """
    if not org_id or not isinstance(org_id, str):
        raise OrgConfigNotFoundError(f"Invalid org_id: {org_id!r}")

    if org_id in _config_cache:
        return _config_cache[org_id]

    config = get_org_config(org_id)
    if config is None:
        raise OrgConfigNotFoundError(f"Org config not found for org_id: {org_id!r}")

    _config_cache[org_id] = config
    return config


def reload_org_config(org_id: str) -> OrgConfig:
    """
    Force a re-read of the org's config from source of truth, bypassing cache.
    Powers hot-reload when config files on disk are updated.
    """
    _config_cache.pop(org_id, None)
    _store_cache.pop(org_id, None)
    return load_org_config(org_id)


def _org_field_to_registry_entry(org_id: str, field: OrgField) -> RegistryEntry:
    """
    Convert an OrgField to a RegistryEntry.
    Preserves seeded violation metadata for the reference regression config (blinkit).
    """
    is_seeded = False
    violation_note = None
    created_at = SEED_NOW - timedelta(days=45)

    if org_id == "blinkit":
        if field.field_name == "aadhaar" and field.source_system == "delivery-partner-service":
            # Seeded retention violation: 240 days old > 180 retention_days
            created_at = SEED_NOW - timedelta(days=240)
            is_seeded = True
            violation_note = (
                "Inactive delivery partner's aadhaar record is 240 days old, "
                "60 days past the 180-day KYC retention window. Should have "
                "been purged. RETENTION_001 candidate."
            )
        elif field.field_name == "phone" and field.source_system == "marketing-analytics":
            # Seeded invariant-carrier: marketing_events structurally forbids raw PII
            created_at = SEED_NOW - timedelta(days=30)
            is_seeded = True
            violation_note = (
                f"marketing_events structurally forbids raw PII (consent_scope='{DEIDENTIFIED_ONLY_SCOPE}'). "
                "If raw phone is observed, flags PURPOSE_001."
            )
        elif field.source_system == "order-service":
            created_at = SEED_NOW - timedelta(days=120)
        elif field.source_system == "support-ticketing":
            created_at = SEED_NOW - timedelta(days=10)
        elif field.source_system == "marketing-analytics":
            created_at = SEED_NOW - timedelta(days=30)

    # Determine table_name if applicable for backward compatibility
    table_name = None
    if org_id == "blinkit":
        mapping = {
            "order-service": "customers",
            "delivery-partner-service": "delivery_partners",
            "support-ticketing": "support_tickets",
            "marketing-analytics": "marketing_events",
        }
        table_name = mapping.get(field.source_system)

    return RegistryEntry(
        field_name=field.field_name,
        pii_category=field.pii_category,
        declared_purpose=field.declared_purpose,
        consent_scope=field.consent_scope,
        retention_days=field.retention_days,
        source_system=field.source_system,
        table_name=table_name,
        created_at=created_at,
        is_seeded_violation=is_seeded,
        violation_note=violation_note,
    )


def get_registry_entry(
    org_id: str,
    field_name: str,
    source_system: str,
    raise_on_missing: bool = True,
) -> Optional[RegistryEntry]:
    """
    Synchronous, in-memory lookup for (org_id, field_name, source_system).
    
    If field is found, returns RegistryEntry.
    If field is not found:
      - if raise_on_missing=True (default), raises FieldNotRegisteredError.
      - if raise_on_missing=False, returns None.
    If org_id is not found, raises OrgConfigNotFoundError.
    """
    config = load_org_config(org_id)

    for f in config.fields:
        if f.field_name == field_name and f.source_system == source_system:
            return _org_field_to_registry_entry(org_id, f)

    if raise_on_missing:
        raise FieldNotRegisteredError(
            f"Field {field_name!r} under source_system {source_system!r} is not "
            f"declared in config for org {org_id!r}."
        )
    return None


def list_registry_entries(
    org_id: str = "blinkit",
    source_system: Optional[str] = None,
) -> List[RegistryEntry]:
    """
    Return all registry entries for an org, optionally filtered by source_system.
    """
    config = load_org_config(org_id)
    entries = [_org_field_to_registry_entry(org_id, f) for f in config.fields]
    if source_system is None:
        return entries
    return [e for e in entries if e.source_system == source_system]


def load_registry(org_id: str = "blinkit", force_reload: bool = False) -> RegistryStore:
    """
    Load (or return cached) RegistryStore for org_id.
    """
    if force_reload:
        reload_org_config(org_id)
        _store_cache.pop(org_id, None)

    if org_id in _store_cache and not force_reload:
        return _store_cache[org_id]

    entries = list_registry_entries(org_id)
    store = RegistryStore(entries=entries)
    _store_cache[org_id] = store
    return store


def _reset_cache() -> None:
    """Test helper to reset in-memory caches."""
    _config_cache.clear()
    _store_cache.clear()
