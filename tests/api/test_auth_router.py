"""Integration test for the auth round-trip (US1, SC-001, SC-010).

Covers the seven contract behaviours specified in
``contracts/auth.openapi.yaml`` + the role-gating contract enforced by
:func:`app.services.auth_service.require_admin`:

  1. POST /auth/register → 201, UserRead body with ``role='user'``.
  2. POST /auth/login   → 204, ``Set-Cookie: mc_session=…; HttpOnly; SameSite=Lax``.
  3. GET  /users/me     with cookie → 200.
  4. GET  /users/me     without cookie → 401.
  5. Direct DB promotion of the user to ``role='admin'`` → /users/me reflects it.
  6. A ``require_admin``-gated route returns 403 for ``role='user'`` and 200
     for ``role='admin'``.
  7. POST /auth/logout → 204, then GET /users/me → 401.

Skips cleanly if Postgres / Vault are not reachable in the current env —
same pattern the Phase B repository tests use. The async sessionmaker + the
migration must already be applied; otherwise the test would fail confusingly
(the existing repository tests would also fail in the same case).
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from app.api.routers.auth import router as auth_router
from app.infra.database_async import get_async_engine, get_async_sessionmaker
from app.infra.vault_client import VaultBootstrapError
from app.repositories.user_repository import User
from app.services.auth_service import require_admin


def _ensure_async_postgres_reachable() -> None:
    """Skip the test if the async engine cannot talk to Postgres / Vault."""

    async def probe() -> None:
        engine = get_async_engine()
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))

    try:
        asyncio.run(probe())
    except (OperationalError, VaultBootstrapError, RuntimeError) as exc:
        pytest.skip(f"Async Postgres / Vault not reachable: {exc}")


def _build_app() -> FastAPI:
    """Build a minimal FastAPI app with the auth router + a guarded route."""
    app = FastAPI()
    app.include_router(auth_router)

    @app.get("/test/admin-only")
    async def admin_only(
        _user: User = Depends(require_admin),  # noqa: B008 — FastAPI DI pattern
    ) -> dict[str, str]:
        return {"ok": "admin"}

    return app


def _delete_test_users(email_prefix: str) -> None:
    """Tidy up rows from prior test runs (idempotent)."""

    async def cleanup() -> None:
        sm = get_async_sessionmaker()
        async with sm() as session:
            await session.execute(
                text("DELETE FROM users WHERE email LIKE :pat"),
                {"pat": f"{email_prefix}%"},
            )
            await session.commit()

    asyncio.run(cleanup())


def _promote_to_admin(email: str) -> None:
    async def promote() -> None:
        sm = get_async_sessionmaker()
        async with sm() as session:
            await session.execute(
                text("UPDATE users SET role='admin' WHERE email=:email"),
                {"email": email},
            )
            await session.commit()

    asyncio.run(promote())


def test_auth_round_trip() -> None:
    """Full US1 lifecycle: register → login → /users/me → admin gate → logout."""
    _ensure_async_postgres_reachable()

    email_prefix = "pytest-auth-router-"
    _delete_test_users(email_prefix)

    # Reset the cached fastapi-users JWT strategy so test runs do not bleed
    # cross-process state. The strategy reads Vault on first call — that read
    # is real, not mocked: this is an integration test against the live stack.
    from app.infra import auth_backend

    auth_backend.get_jwt_strategy.cache_clear()

    app = _build_app()
    client = TestClient(app)

    email = f"{email_prefix}{uuid.uuid4().hex[:8]}@example.com"
    password = "correct-horse-battery-staple"

    try:
        # 1. Register → 201, body matches UserRead with default role.
        resp = client.post(
            "/auth/register",
            json={"email": email, "password": password},
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["email"] == email
        assert body["role"] == "user"
        assert body["is_active"] is True
        # 4. /users/me without cookie → 401 (assert this BEFORE we set a cookie).
        no_cookie_client = TestClient(app)
        anon = no_cookie_client.get("/users/me")
        assert anon.status_code == 401

        # 2. Login → 204 + Set-Cookie ``mc_session``.
        resp = client.post(
            "/auth/login",
            data={"username": email, "password": password},
        )
        assert resp.status_code == 204, resp.text
        set_cookie = resp.headers.get("set-cookie", "")
        assert "mc_session=" in set_cookie, set_cookie
        assert "HttpOnly" in set_cookie
        # ``Lax`` casing is preserved by Starlette.
        assert "samesite=lax" in set_cookie.lower()
        assert "mc_session" in client.cookies

        # 3. /users/me with cookie → 200, body matches the registered user.
        resp = client.get("/users/me")
        assert resp.status_code == 200, resp.text
        me = resp.json()
        assert me["email"] == email
        assert me["role"] == "user"

        # 6. require_admin gate as a non-admin user → 403.
        resp = client.get("/test/admin-only")
        assert resp.status_code == 403, resp.text
        assert resp.json()["detail"] == "admin role required"

        # 5. Promote Alice to admin via direct DB update.
        _promote_to_admin(email)
        resp = client.get("/users/me")
        assert resp.status_code == 200, resp.text
        assert resp.json()["role"] == "admin"

        # 6 (continued). same route now → 200.
        resp = client.get("/test/admin-only")
        assert resp.status_code == 200, resp.text
        assert resp.json() == {"ok": "admin"}

        # 7. Logout → 204, /users/me → 401.
        resp = client.post("/auth/logout")
        assert resp.status_code == 204, resp.text
        # The cookie is cleared in the response; subsequent /users/me without
        # the cookie returns 401.
        client.cookies.clear()
        resp = client.get("/users/me")
        assert resp.status_code == 401
    finally:
        _delete_test_users(email_prefix)
