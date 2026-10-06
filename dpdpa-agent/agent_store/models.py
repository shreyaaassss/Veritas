"""
Veritas Agent Store — Pydantic models (Block 2)
===============================================
Data contracts for registered Agents and one-time registration keys.
These are the in-memory representations returned by AgentStore; they are
never written to disk as Pydantic objects (SQLite rows are the durable form).
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Dict, Optional

from pydantic import BaseModel


class AgentStatus(str, Enum):
    ACTIVE = "ACTIVE"
    REVOKED = "REVOKED"


class Agent(BaseModel):
    """A registered Veritas Agent."""

    agent_id: str           # e.g. "VERITAS-AGENT-7F82A1"
    org_id: str
    status: AgentStatus
    source_label: str       # human label set at registration, e.g. "order-service"
    created_at: datetime
    last_heartbeat_at: Optional[datetime] = None
    events_received: int = 0
    # Reported by the agent in its heartbeat (None until the first report, and for
    # agents old enough not to send one).
    agent_version: Optional[str] = None
    health: Optional[Dict[str, Any]] = None
    health_updated_at: Optional[datetime] = None


class RegistrationKey(BaseModel):
    """
    A key issued to an admin for Agent bootstrap. By default it works once and expires
    after 30 minutes. A reusable key (max_uses > 1) can enrol many agents, for example
    one per Kubernetes node, until it reaches its use limit, expires or is revoked.
    """

    key_id: str             # UUID string
    org_id: str
    created_at: datetime
    expires_at: datetime
    used: bool = False      # True once uses has reached max_uses
    max_uses: int = 1
    uses: int = 0
    revoked: bool = False
    label: str = ""
    created_by: Optional[str] = None

    def status(self, now: Optional[datetime] = None) -> str:
        """REVOKED, EXPIRED, EXHAUSTED or ACTIVE."""
        from datetime import timezone
        now = now or datetime.now(timezone.utc)
        if self.revoked:
            return "REVOKED"
        if now > self.expires_at:
            return "EXPIRED"
        if self.uses >= self.max_uses:
            return "EXHAUSTED"
        return "ACTIVE"
