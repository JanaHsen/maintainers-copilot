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


def _query_role(email: str) -> str | None:
    async def q() -> str | None:
        sm = get_async_sessionmaker()
        async with sm() as session:
            row = (
                await session.execute(
                    text("SELECT role FROM users WHERE email = :email"),
                    {"email": email},
                )
            ).first()
            return None if row is None else str(row[0])

    return asyncio.run(q())


def _query_audit_count(target_id: str) -> int:
    """Count audit_log rows with action='user.role_changed' for a target."""
    async def q() -> int:
        sm = get_async_sessionmaker()
        async with sm() as session:
            row = (
                await session.execute(
                    text(
                        "SELECT count(*) FROM audit_log WHERE "
                        "action = 'user.role_changed' AND target_id = :tid"
                    ),
                    {"tid": target_id},
                )
            ).first()
            return 0 if row is None else int(row[0])

    return asyncio.run(q())


def _user_id_for(email: str) -> str | None:
    async def q() -> str | None:
        sm = get_async_sessionmaker()
        async with sm() as session:
            row = (
                await session.execute(
                    text("SELECT id FROM users WHERE email = :email"),
                    {"email": email},
                )
            ).first()
            return None if row is None else str(row[0])

    return asyncio.run(q())


def test_register_ignores_body_role_field() -> None:
    """Fix 1 (a): client-supplied role='admin' in registration body has no effect."""
    _ensure_async_postgres_reachable()

    email_prefix = "pytest-register-role-"
    _delete_test_users(email_prefix)

    from app.infra import auth_backend
    auth_backend.get_jwt_strategy.cache_clear()

    app = _build_app()
    client = TestClient(app)

    email = f"{email_prefix}{uuid.uuid4().hex[:8]}@example.com"
    password = "correct-horse-battery-staple"

    try:
        # Body explicitly tries to set role='admin'.
        resp = client.post(
            "/auth/register",
            json={"email": email, "password": password, "role": "admin"},
        )
        # Either UserCreate rejects the extra field (422) OR pydantic drops
        # it (201). Either is a defense; the persisted row MUST have
        # role='user' regardless.
        assert resp.status_code in (201, 422), resp.text
        if resp.status_code == 201:
            assert resp.json()["role"] == "user"
            assert _query_role(email) == "user"
        else:
            # If pydantic forbids the extra field, no user was created;
            # register without role and confirm the persisted role is 'user'.
            resp = client.post(
                "/auth/register",
                json={"email": email, "password": password},
            )
            assert resp.status_code == 201, resp.text
            assert _query_role(email) == "user"
    finally:
        _delete_test_users(email_prefix)


def test_role_change_endpoint() -> None:
    """Fix 1 (b,c,d): non-admin 403; admin PATCH 200 + audit; self-PATCH 400."""
    _ensure_async_postgres_reachable()

    prefix = "pytest-role-change-"
    _delete_test_users(prefix)

    from app.infra import auth_backend
    auth_backend.get_jwt_strategy.cache_clear()

    app = _build_app()
    client = TestClient(app)

    # Two users: Alice will become admin; Bob is the target.
    alice_email = f"{prefix}alice-{uuid.uuid4().hex[:8]}@example.com"
    bob_email = f"{prefix}bob-{uuid.uuid4().hex[:8]}@example.com"
    password = "correct-horse-battery-staple"

    try:
        # Register both.
        for e in (alice_email, bob_email):
            resp = client.post(
                "/auth/register",
                json={"email": e, "password": password},
            )
            assert resp.status_code == 201, resp.text

        bob_id = _user_id_for(bob_email)
        alice_id = _user_id_for(alice_email)
        assert bob_id is not None and alice_id is not None

        # (b) Login as Alice (still role='user'). PATCH → 403.
        resp = client.post(
            "/auth/login",
            data={"username": alice_email, "password": password},
        )
        assert resp.status_code == 204, resp.text
        resp = client.patch(
            f"/users/{bob_id}/role",
            json={"role": "admin"},
        )
        assert resp.status_code == 403, resp.text

        # (c) Promote Alice to admin directly, retry PATCH → 200 + audit row.
        _promote_to_admin(alice_email)
        baseline_audit = _query_audit_count(bob_id)
        resp = client.patch(
            f"/users/{bob_id}/role",
            json={"role": "admin"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["user_id"] == bob_id
        assert body["old_role"] == "user"
        assert body["new_role"] == "admin"
        assert _query_role(bob_email) == "admin"
        assert _query_audit_count(bob_id) == baseline_audit + 1

        # (d) Alice tries to PATCH her own role → 400.
        resp = client.patch(
            f"/users/{alice_id}/role",
            json={"role": "user"},
        )
        assert resp.status_code == 400, resp.text
        assert "own role" in resp.json()["detail"]
        # Sanity: Alice's role is unchanged.
        assert _query_role(alice_email) == "admin"
    finally:
        _delete_test_users(prefix)


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
