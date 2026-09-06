"""
Veritas Agent Auth — FastAPI dependency (Block 2)
=================================================
verify_agent_token() is a FastAPI Depends() used on the
POST /v1/{org_id}/events endpoint. It:
  1. Extracts the Bearer token from the Authorization header
  2. Looks it up in the AgentStore
  3. Validates: token exists, agent is ACTIVE, agent belongs to the org in the URL

Hard-rejects with 401 (unknown/missing token) or 403 (revoked, wrong org).
Also increments the agent's events_received counter on every accepted call.
"""

from __future__ import annotations

from typing import Optional

from fastapi import Header, HTTPException

from agent_store.models import Agent, AgentStatus
from agent_store.store import get_agent_store


async def verify_agent_token(
    org_id: str,
    authorization: Optional[str] = Header(default=None),
) -> Agent:
    """
    FastAPI dependency: validates the Bearer token on /v1/{org_id}/events.

    org_id is injected from the URL path by FastAPI when this dependency
    is declared on an endpoint that has {org_id} in its path.
    """
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=401,
            detail="Missing or malformed Authorization header. Expected: Authorization: Bearer <token>",
        )

    token = authorization.removeprefix("Bearer ").strip()
    if not token:
        raise HTTPException(
            status_code=401,
            detail="Empty Bearer token.",
        )

    agent = get_agent_store().validate_token(token)

    if agent is None:
        raise HTTPException(
            status_code=401,
            detail="Unknown or invalid agent token.",
        )

    if agent.status == AgentStatus.REVOKED:
        raise HTTPException(
            status_code=403,
            detail=f"Agent {agent.agent_id!r} has been revoked and cannot submit telemetry.",
        )

    if agent.org_id != org_id:
        raise HTTPException(
            status_code=403,
            detail=(
                f"Agent {agent.agent_id!r} is registered to org {agent.org_id!r}, "
                f"not {org_id!r}. Cross-org telemetry is not permitted."
            ),
        )

    # Increment event counter (best-effort — failure here must not block the event)
    try:
        get_agent_store().increment_events(agent.agent_id)
    except Exception:
        pass

    return agent
