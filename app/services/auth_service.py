"""Service-layer auth helpers: role-gating dependencies.

fastapi-users handles authentication (who you are) at the infra layer
(:mod:`app.infra.auth_backend`). Authorization (what role you have) lives at
the service layer because the ``role`` column is application-defined, not
fastapi-users-defined (Rule 1 — routers depend on services, services depend on
infra).

This module exposes :func:`require_admin`, the FastAPI dependency every
admin-scoped router uses (US1 FR-007, SC-010). It also re-exports
:data:`current_active_user` so callers do not need to know which infra module
the dependency lives in.
"""

from __future__ import annotations

from fastapi import Depends, HTTPException, status

from app.infra.auth_backend import current_active_superuser, current_active_user
from app.repositories.user_repository import User


async def require_admin(
    user: User = Depends(current_active_user),  # noqa: B008 — FastAPI DI pattern
) -> User:
    """Allow only users whose ``role`` column is ``"admin"``.

    Raises ``HTTPException(403)`` for any other role; the underlying
    ``current_active_user`` dependency raises ``401`` if there is no active
    session, so the two failure modes are distinct from the caller's point of
    view (FR-007).
    """
    if user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="admin role required",
        )
    return user


__all__ = ["current_active_user", "current_active_superuser", "require_admin"]
