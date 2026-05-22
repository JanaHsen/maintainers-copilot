"""fastapi-users wiring: JWT-cookie backend + UserManager + dependencies.

Implements the auth surface specified in
``specs/002-chatbot-part1-foundations/contracts/auth.openapi.yaml``:

  * the signing key for the JWT is read from Vault (key
    :data:`KEY_AUTH_JWT_SECRET`) — never from an env var (Rule 2, research R2);
  * the JWT travels in an HTTP-only ``mc_session`` cookie (research R2,
    contracts/auth.openapi.yaml);
  * fastapi-users' :class:`SQLAlchemyUserDatabase` adapter is the only consumer
    of the async engine (Rule 1, research R1).

The Vault read is lazy: the strategy factory is wrapped in
``functools.lru_cache`` so importing this module does **not** trigger a Vault
read (a unit test asserts this). The first request that needs an auth strategy
triggers exactly one read; subsequent requests reuse the cached strategy.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from functools import lru_cache

from fastapi import Depends
from fastapi_users import BaseUserManager, FastAPIUsers, UUIDIDMixin
from fastapi_users.authentication import (
    AuthenticationBackend,
    CookieTransport,
    JWTStrategy,
)
from fastapi_users_db_sqlalchemy import SQLAlchemyUserDatabase

from app.infra.vault_client import KEY_AUTH_JWT_SECRET, read_secrets
from app.repositories.user_repository import User, get_user_db

# 1-hour JWT lifetime. Matches the ``mc_session`` cookie ``Max-Age`` in
# contracts/auth.openapi.yaml.
SECRET_LIFETIME_SECONDS = 3600


@lru_cache
def get_jwt_strategy() -> JWTStrategy[User, uuid.UUID]:
    """Build the JWT strategy with the signing key from Vault.

    Cached so the Vault read happens once per process. Importing this module
    does **not** trigger the read; only calling this function does.
    """
    secret = read_secrets([KEY_AUTH_JWT_SECRET])[KEY_AUTH_JWT_SECRET]
    return JWTStrategy(secret=secret, lifetime_seconds=SECRET_LIFETIME_SECONDS)


# HTTP-only, SameSite=Lax cookie. ``cookie_secure=False`` for the dev stack
# (Phase Operations toggles this on once the deployment terminates TLS).
cookie_transport = CookieTransport(
    cookie_name="mc_session",
    cookie_max_age=SECRET_LIFETIME_SECONDS,
    cookie_httponly=True,
    cookie_samesite="lax",
    cookie_secure=False,
)

auth_backend = AuthenticationBackend(
    name="jwt-cookie",
    transport=cookie_transport,
    get_strategy=get_jwt_strategy,
)


class UserManager(UUIDIDMixin, BaseUserManager[User, uuid.UUID]):
    """fastapi-users user manager bound to the local :class:`User` ORM model.

    :class:`UUIDIDMixin` provides ``parse_id`` so the manager accepts string
    UUIDs from the JWT subject claim. Token reset/verification secrets are
    not used yet (Part 1 has no password reset flow); fastapi-users falls
    back to the JWT signing key for those operations.
    """

    reset_password_token_secret = ""
    verification_token_secret = ""


async def get_user_manager(
    user_db: SQLAlchemyUserDatabase[User, uuid.UUID] = Depends(get_user_db),  # noqa: B008 — FastAPI DI pattern
) -> AsyncIterator[UserManager]:
    """FastAPI dependency: yields a :class:`UserManager` bound to the request DB."""
    yield UserManager(user_db)


fastapi_users = FastAPIUsers[User, uuid.UUID](get_user_manager, [auth_backend])

current_active_user = fastapi_users.current_user(active=True)
current_active_superuser = fastapi_users.current_user(active=True, superuser=True)
