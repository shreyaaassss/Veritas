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
from typing import Optional

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


class RegistrationKey(BaseModel):
    """A one-time key issued to an admin for Agent bootstrap."""

    key_id: str             # UUID string
    org_id: str
    created_at: datetime
    expires_at: datetime    # created_at + 30 minutes
    used: bool = False
