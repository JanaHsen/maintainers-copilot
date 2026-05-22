"""HTTP surface for authentication + user-profile endpoints.

Wires fastapi-users' built-in routers under the paths specified in
``contracts/auth.openapi.yaml``:

  * ``POST /auth/register``  — register router (201 + UserRead body)
  * ``POST /auth/login``     — auth router  (204 + Set-Cookie ``mc_session``)
  * ``POST /auth/logout``    — auth router  (204)
  * ``GET  /users/me``       — users router (200 + UserRead body, 401 unauth)
  * ``PATCH /users/me``      — users router (update self, plus admin-scoped
    routes ``GET /users/{id}``, ``DELETE /users/{id}`` etc. — fastapi-users
    gates those on ``is_superuser`` and the application-defined ``role``
    column is enforced separately by :func:`require_admin`).

Cookie semantics: HttpOnly, SameSite=Lax, Max-Age=3600 — see
:mod:`app.infra.auth_backend`.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.domain.auth import UserCreate, UserRead, UserUpdate
from app.infra.auth_backend import auth_backend, fastapi_users

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

# /users/me (GET 200 + PATCH) and admin-scoped /users/{id} routes.
router.include_router(
    fastapi_users.get_users_router(UserRead, UserUpdate),
    prefix="/users",
    tags=["users"],
)
