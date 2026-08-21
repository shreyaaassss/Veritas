# Phase 6 — Evidence Store

Not yet implemented.

This module implements:
- Append-only SQLite storage for Verdict objects
- SHA-256 hash chain over all immutable Verdict fields
- Runtime enforcement of the immutability contract
  (only remediation_status and remediation_updated_at may be updated post-write)
- Point-in-time audit snapshot export for auditor Priya

Read /docs/scope.md Immutability Contract section before implementing.
