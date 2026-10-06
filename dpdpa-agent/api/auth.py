"""
Veritas — Human Authentication API
=====================================
Endpoints for login, logout, session info, and first-boot admin setup.

Routes:
  POST /api/auth/login    — authenticate with username + password → set httpOnly cookie
  POST /api/auth/logout   — clear session cookie
  GET  /api/auth/me       — return current user info (requires auth)
  POST /api/auth/setup    — create first administrator (only works if 0 users exist, and needs
                            the one-time setup code shown in the server log)

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


def _validate_password_strength(password: str, username: Optional[str] = None,
                                email: Optional[str] = None) -> Optional[str]:
    """Returns an error message if the password is not acceptable, else None."""
    from api import passwords
    return passwords.check(password, username=username, email=email)


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
    must_change_password: bool = False


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(..., min_length=1, max_length=256)
    new_password:     str = Field(..., min_length=1, max_length=256)


class ResetPasswordRequest(BaseModel):
    # Leave empty to have a temporary password generated.
    new_password: Optional[str] = Field(None, max_length=256)


class SetupRequest(BaseModel):
    username: str  = Field(..., min_length=3, max_length=64)
    email:    str  = Field(..., min_length=5, max_length=254)
    password: str  = Field(..., min_length=1, max_length=256)
    # The one-time code from the server's log or `veritas setup-code`. Optional here only so
    # that a missing code gets the same helpful 403 as a wrong one.
    setup_code: str = Field("", max_length=64)


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
        "must_change_password": user.must_change_password,
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
        must_change_password=user.must_change_password,
    )


@router.post("/change-password")
async def change_password(
    req: ChangePasswordRequest, response: Response,
    user: User = Depends(get_current_user),
    _rl: None = Depends(login_rate_limit),
) -> dict:
    """
    Change your own password. Needs the current password. Every other session of this
    account ends; this one continues with a fresh cookie. Also the way out of the
    "choose a new password" state after an administrator created or reset the account.
    """
    store = get_user_store()
    if not _verify_password(req.current_password, user.password_hash):
        _audit("PASSWORD_CHANGE_FAILED", user, result="FAILURE")
        raise HTTPException(status_code=400, detail="Current password is incorrect.")
    if req.new_password == req.current_password:
        raise HTTPException(status_code=422, detail="The new password must differ from the current one.")
    err = _validate_password_strength(req.new_password, user.username, user.email)
    if err:
        raise HTTPException(status_code=422, detail=err)

    store.set_password(user.user_id, _hash_password(req.new_password), must_change=False)
    fresh = store.get_by_id(user.user_id)
    response.set_cookie(value=create_access_token(fresh), **cookie_kwargs())
    _audit("PASSWORD_CHANGED", user, resource=f"user:{user.user_id}")
    logger.info("User %r changed their password", user.username)
    return {"ok": True}


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

    # Only someone who can read the server's log or data directory (the operator) knows the
    # setup code, so a stranger who reaches this page first cannot claim the server.
    from setup_code import verify as verify_setup_code
    if not verify_setup_code(req.setup_code):
        logger.warning("First-administrator setup refused: missing or wrong setup code")
        raise HTTPException(
            status_code=403,
            detail=(
                "A valid setup code is required. Find it in the server log (look for "
                "'SETUP CODE') or run: sudo veritas setup-code"
            ),
        )

    # Validate inputs
    username_err = _validate_username(req.username)
    if username_err:
        raise HTTPException(status_code=422, detail=username_err)

    if "@" not in req.email or "." not in req.email.split("@")[-1]:
        raise HTTPException(status_code=422, detail="Invalid email address.")

    pw_err = _validate_password_strength(req.password, req.username, req.email)
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

    from setup_code import clear as clear_setup_code
    clear_setup_code()   # the code is single-use: it is gone once the first administrator exists
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
    password: str  = Field(..., min_length=1, max_length=256)
    role:     str  = Field(..., description="SUPER_ADMIN|COMPLIANCE_ADMIN|AUDITOR|VIEWER")
    # The new user must choose their own password at first login (default). Turn off only for
    # service-style accounts.
    require_password_change: bool = True


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
    must_change_password: bool = False
    org_ids:       List[str] = []


def _list_item(u: User) -> "UserListItem":
    return UserListItem(
        user_id=u.user_id, username=u.username, email=u.email,
        role=u.role.value, is_active=u.is_active,
        created_at=u.created_at.isoformat(),
        last_login=u.last_login.isoformat() if u.last_login else None,
        must_change_password=u.must_change_password,
        org_ids=get_user_store().get_user_orgs(u.user_id),
    )


def _audit(action: str, actor: Optional[User] = None, **kw) -> None:
    try:
        from audit_log.store import get_audit_store
        get_audit_store().log(action, actor_id=actor.user_id if actor else None,
                              actor_name=actor.username if actor else None, **kw)
    except Exception:
        pass


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
    return [_list_item(u) for u in get_user_store().list_users()]


@router.post("/users", response_model=UserListItem, status_code=201)
async def create_user(req: CreateUserRequest, _admin: User = Depends(require_roles(UserRole.SUPER_ADMIN))) -> UserListItem:
    """Create a new user account. SUPER_ADMIN only."""
    # Validate
    username_err = _validate_username(req.username)
    if username_err:
        raise HTTPException(status_code=422, detail=username_err)
    if "@" not in req.email:
        raise HTTPException(status_code=422, detail="Invalid email address.")
    pw_err = _validate_password_strength(req.password, req.username, req.email)
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
    if req.require_password_change:
        get_user_store().set_must_change_password(u.user_id, True)
        u = get_user_store().get_by_id(u.user_id)

    logger.info("Admin %r created user %r (role=%s)", _admin.username, u.username, u.role.value)
    try:
        from audit_log.store import AuditAction, get_audit_store
        get_audit_store().log(AuditAction.USER_CREATED, actor_id=_admin.user_id, actor_name=_admin.username,
                              resource=f"user:{u.user_id}", detail={"username": u.username, "role": u.role.value})
    except Exception:
        pass
    return _list_item(u)


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

    new_role = None
    if req.role is not None:
        try:
            new_role = UserRole(req.role)
        except ValueError:
            raise HTTPException(status_code=422, detail=f"Invalid role: {req.role!r}")

    # Prevent self-lockout
    if target.user_id == _admin.user_id:
        if req.is_active is False:
            raise HTTPException(status_code=400, detail="You cannot deactivate your own account.")
        if new_role is not None and new_role != UserRole.SUPER_ADMIN:
            raise HTTPException(status_code=400, detail="You cannot remove your own SUPER_ADMIN role.")

    # Never leave the system without an active administrator.
    loses_admin = target.role == UserRole.SUPER_ADMIN and target.is_active and (
        req.is_active is False or (new_role is not None and new_role != UserRole.SUPER_ADMIN))
    if loses_admin and store.count_active_super_admins() <= 1:
        raise HTTPException(status_code=400, detail="At least one active SUPER_ADMIN must remain.")

    changes = {}
    if new_role is not None and new_role != target.role:
        store.update_role(user_id, new_role)
        changes["role"] = new_role.value
    if req.is_active is not None and req.is_active != target.is_active:
        store.set_active(user_id, req.is_active)
        changes["is_active"] = req.is_active
    if changes:
        _audit("USER_DISABLED" if changes.get("is_active") is False else "USER_UPDATED",
               _admin, resource=f"user:{user_id}", detail={"username": target.username, **changes})

    return _list_item(store.get_by_id(user_id))


@router.post("/users/{user_id}/reset-password")
async def reset_password(
    user_id: str, req: ResetPasswordRequest,
    _admin: User = Depends(require_roles(UserRole.SUPER_ADMIN)),
) -> dict:
    """
    Reset another user's password. SUPER_ADMIN only. Without new_password a temporary one is
    generated and returned once; the user must replace it at next login. Ends all of that
    user's sessions. Use change-password for your own account.
    """
    store = get_user_store()
    target = store.get_by_id(user_id)
    if not target:
        raise HTTPException(status_code=404, detail=f"User {user_id!r} not found.")
    if target.user_id == _admin.user_id:
        raise HTTPException(status_code=400, detail="Use change-password to change your own password.")

    generated = not req.new_password
    if generated:
        from api import passwords
        password = passwords.generate_temporary_password()
    else:
        password = req.new_password
        err = _validate_password_strength(password, target.username, target.email)
        if err:
            raise HTTPException(status_code=422, detail=err)

    store.set_password(user_id, _hash_password(password), must_change=True)
    _audit("PASSWORD_RESET", _admin, resource=f"user:{user_id}", detail={"username": target.username})
    logger.info("Admin %r reset the password of %r", _admin.username, target.username)
    out = {"ok": True, "user_id": user_id, "must_change_password": True}
    if generated:
        out["temporary_password"] = password
    return out


# ---------------------------------------------------------------------------
# Public helper used by server.py to check setup state
# ---------------------------------------------------------------------------

def needs_setup() -> bool:
    """Returns True if no users exist (first-boot setup required)."""
    try:
        return get_user_store().count_users() == 0
    except Exception:
        return True
