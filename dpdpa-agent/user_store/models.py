"""
Veritas User Store — Models
============================
User accounts and roles for human authentication.
Completely separate from Agent authentication (which uses Bearer tokens).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Optional


class UserRole(str, Enum):
    """
    Role hierarchy (highest to lowest privilege):
      SUPER_ADMIN      — full system access: users, orgs, agents, policies, evidence
      COMPLIANCE_ADMIN — org config, violations, investigation, reports, agents (view)
      AUDITOR          — view evidence, verify chain, investigation, reports (read-only)
      VIEWER           — read-only dashboard access
    """
    SUPER_ADMIN      = "SUPER_ADMIN"
    COMPLIANCE_ADMIN = "COMPLIANCE_ADMIN"
    AUDITOR          = "AUDITOR"
    VIEWER           = "VIEWER"


@dataclass
class User:
    user_id:       str
    username:      str
    email:         str
    password_hash: str           # bcrypt hash — never the plaintext
    role:          UserRole
    is_active:     bool
    created_at:    datetime
    last_login:    Optional[datetime] = None
