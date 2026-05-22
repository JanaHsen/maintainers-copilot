"""Pydantic schemas + role literal for authenticated maintainers.

Aligned with ``contracts/auth.openapi.yaml`` plus the dedicated admin-only
``PATCH /users/{user_id}/role`` endpoint.

Notes on ``role``:

  * :class:`UserRead` exposes ``role`` so callers know who they are talking to.
  * :class:`UserCreate` does **not** carry ``role``: clients calling
    ``POST /auth/register`` must NOT be able to self-promote. The server
    always inserts new users with the column default ``'user'`` (see
    migration 0003).
  * :class:`UserUpdate` does **not** carry ``role`` either: ``PATCH /users/me``
    (and the fastapi-users admin-scoped routes) cannot change role. The only
    role-mutation surface is ``PATCH /users/{user_id}/role`` gated on
    :func:`app.services.auth_service.require_admin`, which writes an
    ``audit_log`` entry.
"""

from __future__ import annotations

import uuid
from typing import Literal

from fastapi_users import schemas
from pydantic import BaseModel

Role = Literal["user", "admin"]


class UserRead(schemas.BaseUser[uuid.UUID]):
    role: Role = "user"


class UserCreate(schemas.BaseUserCreate):
    """Registration payload. Server forces ``role='user'`` regardless of body."""


class UserUpdate(schemas.BaseUserUpdate):
    """Profile-update payload. Excludes ``role`` — use the dedicated endpoint."""


class RoleUpdate(BaseModel):
    """Request body for ``PATCH /users/{user_id}/role`` (admin-only)."""

    role: Role
