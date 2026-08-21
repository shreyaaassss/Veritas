"""
DPDPA Compliance Agent — Registry Loader & Public Query Interface
==================================================================
This is the ONLY module Phase 4 (Rule Engine) should import from.

Public interface (stable — do not change signatures without team review):
    load_registry() -> RegistryStore
    get_registry_entry(field_name: str, source_system: str) -> RegistryEntry | None
    list_registry_entries(source_system: str | None = None) -> list[RegistryEntry]

get_registry_entry is synchronous and in-memory — safe to call per-event on
a live stream with zero network/DB round-trip latency. It returns None
(never raises) on a miss, so Phase 4 can treat "field not in registry" as
a distinct, handleable case rather than a crash.

MIGRATION NOTE: This module currently loads from a Python seed file into an
in-memory RegistryStore. Per the locked tech stack (SQLite), this can be
swapped for a SQLite-backed load without changing load_registry(),
get_registry_entry(), or list_registry_entries()'s signatures — callers
in Phase 4 never need to know which backing store is in use.
"""

from __future__ import annotations

from typing import List, Optional

from registry.models import RegistryEntry, RegistryStore
from registry.seed_registry import ALL_SEED_ENTRIES

# Module-level cache so repeated calls don't re-instantiate the store.
# Fine for MVP scale (a few dozen entries); reset via _reset_cache() in tests.
_registry_store: Optional[RegistryStore] = None


def load_registry(force_reload: bool = False) -> RegistryStore:
    """
    Load (or return the cached) RegistryStore built from the seed data in
    registry/seed_registry.py.

    force_reload=True bypasses the cache — primarily useful for tests that
    need a fresh store instance.
    """
    global _registry_store
    if _registry_store is None or force_reload:
        _registry_store = RegistryStore(entries=list(ALL_SEED_ENTRIES))
    return _registry_store


def get_registry_entry(field_name: str, source_system: str) -> Optional[RegistryEntry]:
    """
    Synchronous, in-memory lookup for a single (field_name, source_system) pair.

    Returns None cleanly when no matching entry exists — callers (Phase 4)
    must treat this as "field not declared for this source_system", not
    as an error condition.

    Example:
        >>> get_registry_entry("aadhaar", "delivery-partner-service")
        RegistryEntry(field_name='aadhaar', ...)

        >>> get_registry_entry("credit_score", "order-service")
        None
    """
    store = load_registry()
    return store.get(field_name=field_name, source_system=source_system)


def list_registry_entries(source_system: Optional[str] = None) -> List[RegistryEntry]:
    """
    Return all registry entries, optionally filtered to a single source_system.
    Useful for Phase 4's batch reasoning and for reporting/audit views.
    """
    store = load_registry()
    return store.list(source_system=source_system)


def _reset_cache() -> None:
    """Test-only helper to force a clean RegistryStore on the next load_registry() call."""
    global _registry_store
    _registry_store = None
