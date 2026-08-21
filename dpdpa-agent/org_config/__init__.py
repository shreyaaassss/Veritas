"""
org_config — Org Config module for Veritas DPDPA Agent (Phase 0)
================================================================
This package implements the Generalisation Contract Lock:
  - OrgConfig schema (schema.py)
  - Config validator with specific rejection errors (validator.py)
  - Config store: upload_org_config / get_org_config (store.py)

Any organisation integrating Veritas supplies ONE config file (YAML) and
calls upload_org_config once. From that point on, every core module looks
up configuration by org_id — no application code ever branches on org_id.
"""
