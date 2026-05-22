"""Service-layer auth helpers: role-gating dependencies + role mutation.

fastapi-users handles authentication (who you are) at the infra layer
(:mod:`app.infra.auth_backend`). Authorization (what role you have) lives at
the service layer because the ``role`` column is application-defined, not
fastapi-users-defined (Rule 1 — routers depend on services, services depend on
infra).

This module exposes:

  * :func:`require_admin` — FastAPI dependency every admin-scoped router uses
    (US1 FR-006, SC-010).
  * :func:`change_user_role` — the only allowed role mutation path. Writes one
    ``audit_log`` row per call (FR-023). Rejects self-role-change.
"""

from __future__ import annotations

import uuid

from fastapi import Depends, HTTPException, status
from sqlalchemy import text

from app.domain.auth import Role
from app.infra.auth_backend import current_active_superuser, current_active_user
from app.infra.database_async import get_async_sessionmaker
from app.repositories import audit_repository
from app.repositories.user_repository import User


async def require_admin(
    user: User = Depends(current_active_user),  # noqa: B008 — FastAPI DI pattern
) -> User:
    """Allow only users whose ``role`` column is ``"admin"``.

    Raises ``HTTPException(403)`` for any other role; the underlying
    ``current_active_user`` dependency raises ``401`` if there is no active
    session, so the two failure modes are distinct from the caller's point of
    view (FR-006).
    """
    if user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="admin role required",
        )
    return user


class UserNotFoundError(RuntimeError):
    """Target user id does not exist; surface as 404."""


class SelfRoleChangeError(RuntimeError):
    """Admins cannot change their own role; surface as 400."""


async def change_user_role(
    *,
    target_user_id: uuid.UUID,
    new_role: Role,
    actor: User,
) -> Role:
    """Set the target user's role to ``new_role`` and write an audit entry.

    Returns the previous role so the caller can shape its response. Raises:

      * :class:`SelfRoleChangeError` if the actor is also the target (400).
      * :class:`UserNotFoundError` if the target id does not exist (404).

    Writes one ``audit_log`` row with ``action='user.role_changed'``,
    ``target_type='user'``, ``target_id=str(target_user_id)``, and a payload
    capturing ``old_role`` / ``new_role`` / ``changed_by`` (FR-023, Rule 7).
    """
    if target_user_id == actor.id:
        raise SelfRoleChangeError(
            "admin cannot change their own role; ask a different admin"
        )

    sm = get_async_sessionmaker()
    async with sm() as session:
        # SELECT … FOR UPDATE captures the prior role and locks the row so a
        # concurrent role change cannot race between read and write.
        sel = await session.execute(
            text("SELECT role FROM users WHERE id = :id FOR UPDATE"),
            {"id": target_user_id},
        )
        old_row = sel.first()
        if old_row is None:
            await session.rollback()
            raise UserNotFoundError(f"user {target_user_id} not found")
        old_role: Role = old_row[0]
        await session.execute(
            text("UPDATE users SET role = :new_role WHERE id = :id"),
            {"new_role": new_role, "id": target_user_id},
        )
        await session.commit()

    # Audit through the sync repo (research R3) — the sync engine opens its
    # own connection; bridging async→sync here is fine for an admin-only,
    # low-volume mutation.
    audit_repository.record(
        action="user.role_changed",
        target_type="user",
        target_id=str(target_user_id),
        payload={
            "old_role": old_role,
            "new_role": new_role,
            "changed_by": str(actor.id),
        },
        actor_user_id=actor.id,
    )

    return old_role


__all__ = [
    "SelfRoleChangeError",
    "UserNotFoundError",
    "change_user_role",
    "current_active_superuser",
    "current_active_user",
    "require_admin",
]
