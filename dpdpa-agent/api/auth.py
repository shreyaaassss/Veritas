"""
Veritas — Human Authentication API
=====================================
Endpoints for login, logout, session info, and first-boot admin setup.

Routes:
  POST /api/auth/login    — authenticate with username + password → set httpOnly cookie
  POST /api/auth/logout   — clear session cookie
  GET  /api/auth/me       — return current user info (requires auth)
  POST /api/auth/setup    — create first administrator (only works if 0 users exist)

Security notes:
  - Passwords hashed with bcrypt (passlib), never stored or logged as plaintext
  - Session token is an httpOnly, SameSite=lax cookie (not localStorage)
  - Setup endpoint is disabled once any user exists (idempotent guard)
  - Error messages are deliberately vague to prevent username enumeration
"""

from __future__ import annotations

import logging
import re
from typing import Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Request, Response
from pydantic import BaseModel, Field

from api.auth_deps import (
    cookie_kwargs,
    create_access_token,
    get_current_user,
    require_roles,
)
from rate_limit import login_rate_limit, setup_rate_limit, clear_login_limit
from user_store.models import User, UserRole
from user_store.store import get_user_store

# User management models (SUPER_ADMIN only)
from typing import List

logger = logging.getLogger("api.auth")

router = APIRouter(prefix="/api/auth", tags=["authentication"])

# ---------------------------------------------------------------------------
# Password helpers
# ---------------------------------------------------------------------------

def _hash_password(plaintext: str) -> str:
    from passlib.context import CryptContext
    ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")
    return ctx.hash(plaintext)


def _verify_password(plaintext: str, hashed: str) -> bool:
    from passlib.context import CryptContext
    ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")
    try:
        return ctx.verify(plaintext, hashed)
    except Exception:
        return False


def _validate_password_strength(password: str) -> Optional[str]:
    """Returns an error message if the password is too weak, else None."""
    if len(password) < 8:
        return "Password must be at least 8 characters."
    return None


def _validate_username(username: str) -> Optional[str]:
    if not username or len(username.strip()) < 3:
        return "Username must be at least 3 characters."
    if not re.match(r"^[a-zA-Z0-9_\-\.]+$", username.strip()):
        return "Username may only contain letters, digits, underscores, hyphens, and dots."
    return None


# ---------------------------------------------------------------------------
# Request/Response models
# ---------------------------------------------------------------------------

class MeResponse(BaseModel):
    user_id:    str
    username:   str
    email:      str
    role:       str
    is_active:  bool


class SetupRequest(BaseModel):
    username: str  = Field(..., min_length=3, max_length=64)
    email:    str  = Field(..., min_length=5, max_length=254)
    password: str  = Field(..., min_length=8, max_length=256)


class SetupResponse(BaseModel):
    message:  str
    username: str
    role:     str


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/login")
async def login(
    request:   Request,
    response:  Response,
    username:  str = Form(...),
    password:  str = Form(...),
    _rl: None = Depends(login_rate_limit),
):
    """
    Authenticate with username + password.
    On success: sets httpOnly session cookie and returns user info.
    On failure: always returns 401 with a generic message (no username enumeration).
    """
    _AUTH_FAIL = HTTPException(
        status_code=401,
        detail="Invalid username or password.",
    )

    if not username or not password:
        raise _AUTH_FAIL

    store = get_user_store()
    user  = store.get_by_username(username.strip())

    if not user:
        # Constant-time: run hash to avoid timing attack revealing valid usernames
        _verify_password(password, "$2b$12$dummy.hash.to.prevent.timing.attacks.abc")
        raise _AUTH_FAIL

    if not user.is_active:
        raise _AUTH_FAIL  # Generic — do not reveal account exists but is disabled

    if not _verify_password(password, user.password_hash):
        try:
            from audit_log.store import AuditAction, get_audit_store
            get_audit_store().log(AuditAction.LOGIN_FAILED, actor_name=username.strip(), result="FAILURE")
        except Exception:
            pass
        raise _AUTH_FAIL

    # Issue session cookie
    token = create_access_token(user)
    kwargs = cookie_kwargs()
    response.set_cookie(value=token, **kwargs)

    # Update last login (best-effort)
    try:
        store.update_last_login(user.user_id)
    except Exception:
        pass

    # Clear rate limit on successful login
    try:
        from rate_limit import _client_ip, _limiter
        clear_login_limit(_client_ip(request))
    except Exception:
        pass

    logger.info("User %r logged in (role=%s)", user.username, user.role.value)
    try:
        from audit_log.store import AuditAction, get_audit_store
        get_audit_store().log(AuditAction.LOGIN, actor_id=user.user_id, actor_name=user.username)
    except Exception:
        pass
    return {
        "ok":       True,
        "user_id":  user.user_id,
        "username": user.username,
        "role":     user.role.value,
    }


@router.post("/logout")
async def logout(response: Response, _user: User = Depends(get_current_user)):
    """Clear the session cookie. Always succeeds if authenticated."""
    kwargs = cookie_kwargs()
    response.delete_cookie(
        key=kwargs["key"],
        httponly=kwargs["httponly"],
        samesite=kwargs["samesite"],
        secure=kwargs["secure"],
    )
    logger.info("User %r logged out.", _user.username)
    try:
        from audit_log.store import AuditAction, get_audit_store
        get_audit_store().log(AuditAction.LOGOUT, actor_id=_user.user_id, actor_name=_user.username)
    except Exception:
        pass
    return {"ok": True}


@router.get("/me", response_model=MeResponse)
async def me(user: User = Depends(get_current_user)) -> MeResponse:
    """Return the currently authenticated user's info. 401 if not logged in."""
    return MeResponse(
        user_id=user.user_id,
        username=user.username,
        email=user.email,
        role=user.role.value,
        is_active=user.is_active,
    )


@router.post("/setup", response_model=SetupResponse)
async def setup_first_admin(req: SetupRequest, _rl: None = Depends(setup_rate_limit)) -> SetupResponse:
    """
    Create the first administrator account.
    This endpoint is ONLY available when no users exist.
    Once any user is created, this endpoint returns 403.

    The first user is always assigned the SUPER_ADMIN role.
    """
    store = get_user_store()

    if store.count_users() > 0:
        raise HTTPException(
            status_code=403,
            detail="Setup already completed. Log in as an existing administrator.",
        )

    # Validate inputs
    username_err = _validate_username(req.username)
    if username_err:
        raise HTTPException(status_code=422, detail=username_err)

    if "@" not in req.email or "." not in req.email.split("@")[-1]:
        raise HTTPException(status_code=422, detail="Invalid email address.")

    pw_err = _validate_password_strength(req.password)
    if pw_err:
        raise HTTPException(status_code=422, detail=pw_err)

    # Create first admin
    try:
        user = store.create_user(
            username=req.username.strip(),
            email=req.email.strip().lower(),
            password_hash=_hash_password(req.password),
            role=UserRole.SUPER_ADMIN,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    logger.info("First administrator created: %r", user.username)
    return SetupResponse(
        message=f"Administrator account created. You can now log in.",
        username=user.username,
        role=user.role.value,
    )


# ---------------------------------------------------------------------------
# User management (SUPER_ADMIN only)
# ---------------------------------------------------------------------------

class CreateUserRequest(BaseModel):
    username: str  = Field(..., min_length=3, max_length=64)
    email:    str  = Field(..., min_length=5, max_length=254)
    password: str  = Field(..., min_length=8, max_length=256)
    role:     str  = Field(..., description="SUPER_ADMIN|COMPLIANCE_ADMIN|AUDITOR|VIEWER")


class UpdateUserRequest(BaseModel):
    role:      Optional[str]  = None
    is_active: Optional[bool] = None


class UserListItem(BaseModel):
    user_id:       str
    username:      str
    email:         str
    role:          str
    is_active:     bool
    created_at:    str
    last_login:    Optional[str]


@router.get("/my-orgs")
async def my_orgs(user: User = Depends(get_current_user)) -> dict:
    """
    Return the list of org_ids the current user can access.
    SUPER_ADMIN receives all registered orgs.
    Other roles receive only their explicitly granted orgs.
    """
    from org_config.store import list_registered_orgs
    if user.role == UserRole.SUPER_ADMIN:
        return {"org_ids": list_registered_orgs()}
    return {"org_ids": get_user_store().get_user_orgs(user.user_id)}


@router.post("/users/{user_id}/orgs/{org_id}", status_code=201)
async def grant_org(
    user_id: str, org_id: str,
    _admin: User = Depends(require_roles(UserRole.SUPER_ADMIN)),
) -> dict:
    """Grant a user access to an org. SUPER_ADMIN only."""
    target = get_user_store().get_by_id(user_id)
    if not target:
        raise HTTPException(status_code=404, detail=f"User {user_id!r} not found.")
    get_user_store().grant_org_access(user_id, org_id, granted_by=_admin.user_id)
    logger.info("Admin %r granted %r access to org %r", _admin.username, target.username, org_id)
    try:
        from audit_log.store import AuditAction, get_audit_store
        get_audit_store().log(AuditAction.ORG_ACCESS_GRANTED, actor_id=_admin.user_id, actor_name=_admin.username,
                              org_id=org_id, resource=f"user:{user_id}")
    except Exception:
        pass
    return {"ok": True, "user_id": user_id, "org_id": org_id}


@router.delete("/users/{user_id}/orgs/{org_id}")
async def revoke_org(
    user_id: str, org_id: str,
    _admin: User = Depends(require_roles(UserRole.SUPER_ADMIN)),
) -> dict:
    """Revoke a user's access to an org. SUPER_ADMIN only."""
    removed = get_user_store().revoke_org_access(user_id, org_id)
    if not removed:
        raise HTTPException(status_code=404, detail=f"No membership found for user {user_id!r} in org {org_id!r}.")
    return {"ok": True, "user_id": user_id, "org_id": org_id}


@router.get("/users/{user_id}/orgs")
async def list_user_orgs(
    user_id: str,
    _admin: User = Depends(require_roles(UserRole.SUPER_ADMIN)),
) -> dict:
    """List orgs a specific user can access. SUPER_ADMIN only."""
    if not get_user_store().get_by_id(user_id):
        raise HTTPException(status_code=404, detail=f"User {user_id!r} not found.")
    return {"org_ids": get_user_store().get_user_orgs(user_id)}


@router.get("/users", response_model=List[UserListItem])
async def list_users(_user: User = Depends(require_roles(UserRole.SUPER_ADMIN))) -> List[UserListItem]:
    """List all user accounts. SUPER_ADMIN only."""
    users = get_user_store().list_users()
    return [
        UserListItem(
            user_id=u.user_id, username=u.username, email=u.email,
            role=u.role.value, is_active=u.is_active,
            created_at=u.created_at.isoformat(),
            last_login=u.last_login.isoformat() if u.last_login else None,
        )
        for u in users
    ]


@router.post("/users", response_model=UserListItem, status_code=201)
async def create_user(req: CreateUserRequest, _admin: User = Depends(require_roles(UserRole.SUPER_ADMIN))) -> UserListItem:
    """Create a new user account. SUPER_ADMIN only."""
    # Validate
    username_err = _validate_username(req.username)
    if username_err:
        raise HTTPException(status_code=422, detail=username_err)
    if "@" not in req.email:
        raise HTTPException(status_code=422, detail="Invalid email address.")
    pw_err = _validate_password_strength(req.password)
    if pw_err:
        raise HTTPException(status_code=422, detail=pw_err)

    try:
        role = UserRole(req.role)
    except ValueError:
        raise HTTPException(status_code=422, detail=f"Invalid role: {req.role!r}. Valid: {[r.value for r in UserRole]}")

    try:
        u = get_user_store().create_user(req.username.strip(), req.email.strip().lower(), _hash_password(req.password), role)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    logger.info("Admin %r created user %r (role=%s)", _admin.username, u.username, u.role.value)
    try:
        from audit_log.store import AuditAction, get_audit_store
        get_audit_store().log(AuditAction.USER_CREATED, actor_id=_admin.user_id, actor_name=_admin.username,
                              resource=f"user:{u.user_id}", detail={"username": u.username, "role": u.role.value})
    except Exception:
        pass
    return UserListItem(
        user_id=u.user_id, username=u.username, email=u.email,
        role=u.role.value, is_active=u.is_active,
        created_at=u.created_at.isoformat(), last_login=None,
    )


@router.patch("/users/{user_id}", response_model=UserListItem)
async def update_user(
    user_id: str, req: UpdateUserRequest,
    _admin: User = Depends(require_roles(UserRole.SUPER_ADMIN)),
) -> UserListItem:
    """Update a user's role or active status. SUPER_ADMIN only. Cannot deactivate yourself."""
    store = get_user_store()
    target = store.get_by_id(user_id)
    if not target:
        raise HTTPException(status_code=404, detail=f"User {user_id!r} not found.")

    # Prevent self-lockout
    if target.user_id == _admin.user_id:
        if req.is_active is False:
            raise HTTPException(status_code=400, detail="You cannot deactivate your own account.")
        if req.role is not None and UserRole(req.role) != UserRole.SUPER_ADMIN:
            raise HTTPException(status_code=400, detail="You cannot remove your own SUPER_ADMIN role.")

    if req.role is not None:
        try:
            new_role = UserRole(req.role)
        except ValueError:
            raise HTTPException(status_code=422, detail=f"Invalid role: {req.role!r}")
        store.update_role(user_id, new_role)

    if req.is_active is not None:
        store.set_active(user_id, req.is_active)

    updated = store.get_by_id(user_id)
    return UserListItem(
        user_id=updated.user_id, username=updated.username, email=updated.email,
        role=updated.role.value, is_active=updated.is_active,
        created_at=updated.created_at.isoformat(),
        last_login=updated.last_login.isoformat() if updated.last_login else None,
    )


# ---------------------------------------------------------------------------
# Public helper used by server.py to check setup state
# ---------------------------------------------------------------------------

def needs_setup() -> bool:
    """Returns True if no users exist (first-boot setup required)."""
    try:
        return get_user_store().count_users() == 0
    except Exception:
        return True
