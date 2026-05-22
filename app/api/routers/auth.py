"""HTTP surface for authentication + user-profile endpoints.

Wires fastapi-users' built-in routers under the paths specified in
``contracts/auth.openapi.yaml`` plus the dedicated admin-only role-change
endpoint:

  * ``POST /auth/register``       — register router (201 + UserRead body)
  * ``POST /auth/login``          — auth router  (204 + Set-Cookie ``mc_session``)
  * ``POST /auth/logout``         — auth router  (204)
  * ``GET  /users/me``            — users router (200 + UserRead body, 401 unauth)
  * ``PATCH /users/me``           — users router (update self; ``role`` is
    excluded from :class:`UserUpdate` so this cannot self-promote).
  * ``PATCH /users/{user_id}/role`` — admin-only (Rule 7 audit-logged); rejects
    self-role-change with 400. Mounted **before** the fastapi-users users
    router so the explicit path takes precedence.

Cookie semantics: HttpOnly, SameSite=Lax, Max-Age=3600 — see
:mod:`app.infra.auth_backend`.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status

from app.domain.auth import RoleUpdate, UserCreate, UserRead, UserUpdate
from app.infra.auth_backend import auth_backend, fastapi_users
from app.repositories.user_repository import User
from app.services.auth_service import (
    SelfRoleChangeError,
    UserNotFoundError,
    change_user_role,
    require_admin,
)

router = APIRouter()

# /auth/register (201) — UserRead body.
router.include_router(
    fastapi_users.get_register_router(UserRead, UserCreate),
    prefix="/auth",
    tags=["auth"],
)

# /auth/login (204 + Set-Cookie) + /auth/logout (204).
router.include_router(
    fastapi_users.get_auth_router(auth_backend),
    prefix="/auth",
    tags=["auth"],
)


# Explicit admin-only role-change handler — mounted BEFORE fastapi-users'
# users router so PATCH /users/{user_id}/role takes precedence over any
# generic /users/{id} catchall.
@router.patch(
    "/users/{user_id}/role",
    tags=["users"],
    status_code=status.HTTP_200_OK,
)
async def patch_user_role(
    user_id: uuid.UUID,
    body: RoleUpdate,
    actor: User = Depends(require_admin),  # noqa: B008 — FastAPI DI pattern
) -> dict[str, str]:
    """Set the target user's role. Admin-only, audit-logged, no-self-change."""
    try:
        old_role = await change_user_role(
            target_user_id=user_id,
            new_role=body.role,
            actor=actor,
        )
    except SelfRoleChangeError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc
    except UserNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc
    return {
        "user_id": str(user_id),
        "old_role": old_role,
        "new_role": body.role,
    }


# /users/me (GET 200 + PATCH) and admin-scoped /users/{id} routes.
router.include_router(
    fastapi_users.get_users_router(UserRead, UserUpdate),
    prefix="/users",
    tags=["users"],
)
