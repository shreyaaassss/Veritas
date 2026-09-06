"""
Veritas Agent Management API (Block 2)
=======================================
Six endpoints split into two groups:

  Admin-facing (no auth — consistent with existing no-auth model for admin routes):
    POST /agents/issue-key          Issue a one-time registration key for an org
    GET  /agents                    List all registered agents (filterable by org)
    GET  /agents/{agent_id}         Single agent detail
    POST /agents/{agent_id}/revoke  Revoke an agent

  Agent-facing (called by the deployable Veritas Agent container):
    POST /agent/register            Bootstrap: consume key → get identity
    POST /agent/heartbeat           Update last-seen timestamp

Note: /agent/register and /agent/heartbeat use the singular /agent prefix
(not /agents) to match the plan's documented URL contract that the Agent
binary is configured with.
"""

from __future__ import annotations

import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from agent_store.models import Agent, AgentStatus
from agent_store.store import get_agent_store
from api.agent_auth import verify_agent_token
from config_loader import OrgConfigNotFoundError, load_org_config

logger = logging.getLogger("api.agents")

router = APIRouter(tags=["agent_management"])


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _require_org(org_id: str) -> None:
    """Raise 404 if org_id has no registered config."""
    try:
        load_org_config(org_id)
    except OrgConfigNotFoundError:
        raise HTTPException(
            status_code=404,
            detail=(
                f"org_id {org_id!r} has no registered config. "
                f"Register one first via POST /v1/orgs/{org_id}/config."
            ),
        )


def _agent_to_dict(agent: Agent) -> dict:
    return {
        "agent_id": agent.agent_id,
        "org_id": agent.org_id,
        "status": agent.status.value,
        "source_label": agent.source_label,
        "created_at": agent.created_at.isoformat(),
        "last_heartbeat_at": agent.last_heartbeat_at.isoformat() if agent.last_heartbeat_at else None,
        "events_received": agent.events_received,
    }


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------

class IssueKeyRequest(BaseModel):
    org_id: str = Field(..., min_length=1)


class IssueKeyResponse(BaseModel):
    key: str
    org_id: str
    expires_in_seconds: int


class RegisterAgentRequest(BaseModel):
    registration_key: str = Field(..., min_length=1, max_length=500)
    source_label: str = Field(default="", max_length=200, description="Human label for this agent, e.g. 'order-service'")


class RegisterAgentResponse(BaseModel):
    agent_id: str
    auth_token: str
    org_id: str
    event_endpoint: str


class HeartbeatRequest(BaseModel):
    agent_id: str = Field(..., min_length=1)


# ---------------------------------------------------------------------------
# Admin-facing endpoints
# ---------------------------------------------------------------------------

@router.post("/agents/issue-key", response_model=IssueKeyResponse)
async def issue_registration_key(req: IssueKeyRequest) -> IssueKeyResponse:
    """
    Issue a one-time registration key for an org.
    Called by the admin UI when the admin clicks "Register Agent".
    Returns the plaintext key — show it once, then it's gone.
    """
    _require_org(req.org_id)

    store = get_agent_store()
    key = store.issue_key(req.org_id)

    logger.info("Admin issued registration key for org %r", req.org_id)
    return IssueKeyResponse(
        key=key,
        org_id=req.org_id,
        expires_in_seconds=1800,
    )


@router.get("/agents")
async def list_agents(org_id: Optional[str] = None) -> dict:
    """
    List all registered agents, optionally filtered by org_id.
    Called by the admin UI's Agents section.
    """
    agents = get_agent_store().list_agents(org_id=org_id)
    return {"agents": [_agent_to_dict(a) for a in agents]}


@router.get("/agents/{agent_id}")
async def get_agent(agent_id: str) -> dict:
    """Single agent detail by agent_id."""
    agent = get_agent_store().get_agent(agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail=f"Agent {agent_id!r} not found.")
    return _agent_to_dict(agent)


@router.post("/agents/{agent_id}/revoke")
async def revoke_agent(agent_id: str) -> dict:
    """
    Revoke an agent. Subsequent telemetry from this agent will be rejected
    with 403. The agent record is kept for audit purposes.
    """
    found = get_agent_store().revoke_agent(agent_id)
    if not found:
        raise HTTPException(status_code=404, detail=f"Agent {agent_id!r} not found.")
    logger.info("Admin revoked agent %s", agent_id)
    return {"ok": True, "agent_id": agent_id, "status": "REVOKED"}


# ---------------------------------------------------------------------------
# Agent-facing endpoints
# ---------------------------------------------------------------------------

@router.post("/agent/register", response_model=RegisterAgentResponse)
async def register_agent(req: RegisterAgentRequest) -> RegisterAgentResponse:
    """
    Agent bootstrap registration.
    The Agent presents the one-time registration key it was configured with.
    On success, returns: agent_id, auth_token (show once), org_id, event_endpoint.
    The Agent stores these and uses auth_token for all subsequent /events calls.
    """
    store = get_agent_store()

    try:
        reg_key = store.consume_key(req.registration_key)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    agent, plaintext_token = store.create_agent(reg_key.org_id, req.source_label)

    logger.info(
        "Agent %s registered for org %r (source: %r)",
        agent.agent_id, agent.org_id, req.source_label,
    )

    return RegisterAgentResponse(
        agent_id=agent.agent_id,
        auth_token=plaintext_token,
        org_id=agent.org_id,
        event_endpoint=f"/v1/{agent.org_id}/events",
    )


@router.post("/agent/heartbeat")
async def agent_heartbeat(
    req: HeartbeatRequest,
    authorization: Optional[str] = Header(default=None),
) -> dict:
    """
    Agent heartbeat — updates last_heartbeat_at.
    Requires a valid Bearer token (same token issued at registration).
    """
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=401,
            detail="Missing or malformed Authorization header.",
        )

    token = authorization.removeprefix("Bearer ").strip()
    agent = get_agent_store().validate_token(token)

    if agent is None:
        raise HTTPException(status_code=401, detail="Unknown or invalid agent token.")

    if agent.status == AgentStatus.REVOKED:
        raise HTTPException(status_code=403, detail="Agent has been revoked.")

    # Verify the agent_id in the request matches the token owner
    if agent.agent_id != req.agent_id:
        raise HTTPException(
            status_code=403,
            detail="Token does not match the agent_id in the request.",
        )

    get_agent_store().record_heartbeat(agent.agent_id)
    return {"ok": True, "agent_id": agent.agent_id}
