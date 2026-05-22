"""Fix 2: admin-bootstrap one-shot.

Three scenarios cover the contract from the brief:

  (a) empty users table → an admin user with the correct email + role='admin'
      is created.
  (b) admin already exists → no new user, exit 0, "skipping bootstrap" log
      line emitted.
  (c) Vault key missing → MissingVaultKeyError surfaces with a hint pointing
      back at .env / scripts/vault_seed.sh.

Skips cleanly if the async Postgres / Vault stack is unreachable. Tests run
against the live dev stack with VAULT_ADDR=localhost env override (matching
the pattern in tests/api/test_auth_router.py).
"""

from __future__ import annotations

import asyncio
import logging
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from app.bootstrap import admin_bootstrap
from app.infra.database_async import get_async_engine, get_async_sessionmaker
from app.infra.vault_client import (
    KEY_BOOTSTRAP_ADMIN_EMAIL,
    KEY_BOOTSTRAP_ADMIN_PASSWORD,
    MissingVaultKeyError,
    VaultBootstrapError,
)


def _ensure_async_postgres_reachable() -> None:
    async def probe() -> None:
        engine = get_async_engine()
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))

    try:
        asyncio.run(probe())
    except (OperationalError, VaultBootstrapError, RuntimeError) as exc:
        pytest.skip(f"Async Postgres / Vault not reachable: {exc}")


def _delete_test_admins(email_prefix: str) -> None:
    async def cleanup() -> None:
        sm = get_async_sessionmaker()
        async with sm() as session:
            await session.execute(
                text("DELETE FROM users WHERE email LIKE :pat"),
                {"pat": f"{email_prefix}%"},
            )
            await session.commit()

    asyncio.run(cleanup())


def _seed_user_directly(email: str, role: str) -> None:
    """Insert a fully-formed user row for fixture purposes."""

    async def insert() -> None:
        sm = get_async_sessionmaker()
        async with sm() as session:
            await session.execute(
                text(
                    "INSERT INTO users "
                    "(id, email, hashed_password, is_active, is_superuser, "
                    " is_verified, role) "
                    "VALUES "
                    "(:id, :email, 'fake-hash', TRUE, FALSE, TRUE, :role)"
                ),
                {"id": uuid.uuid4(), "email": email, "role": role},
            )
            await session.commit()

    asyncio.run(insert())


def _count_admins(email_prefix: str) -> int:
    async def q() -> int:
        sm = get_async_sessionmaker()
        async with sm() as session:
            row = (
                await session.execute(
                    text(
                        "SELECT count(*) FROM users WHERE role = 'admin' "
                        "AND email LIKE :pat"
                    ),
                    {"pat": f"{email_prefix}%"},
                )
            ).first()
            return 0 if row is None else int(row[0])

    return asyncio.run(q())


def test_bootstrap_creates_admin_on_empty_users(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """(a) Empty users table → admin user with role='admin' is created."""
    _ensure_async_postgres_reachable()

    email_prefix = "pytest-bootstrap-fresh-"
    target_email = f"{email_prefix}{uuid.uuid4().hex[:8]}@example.com"
    _delete_test_admins(email_prefix)

    # Stub Vault to return our test credentials (avoids mutating live Vault).
    def fake_read_secrets(keys: list[str]) -> dict[str, str]:
        return {
            KEY_BOOTSTRAP_ADMIN_EMAIL: target_email,
            KEY_BOOTSTRAP_ADMIN_PASSWORD: "correct-horse-battery-staple",
        }

    monkeypatch.setattr(admin_bootstrap, "read_secrets", fake_read_secrets)

    # Stub the "existing admin" check to return None so this scenario doesn't
    # depend on the live DB being admin-free.
    async def no_existing_admin() -> str | None:
        return None

    monkeypatch.setattr(admin_bootstrap, "_existing_admin_id", no_existing_admin)

    try:
        with caplog.at_level(logging.INFO, logger="app.bootstrap.admin_bootstrap"):
            exit_code = asyncio.run(admin_bootstrap.bootstrap_admin())

        assert exit_code == 0
        assert _count_admins(email_prefix) == 1
        # Verify the created row's email matches.
        async def q() -> str | None:
            sm = get_async_sessionmaker()
            async with sm() as session:
                row = (
                    await session.execute(
                        text(
                            "SELECT email FROM users WHERE role = 'admin' "
                            "AND email LIKE :pat"
                        ),
                        {"pat": f"{email_prefix}%"},
                    )
                ).first()
                return None if row is None else str(row[0])

        assert asyncio.run(q()) == target_email
        assert any(
            "Bootstrap admin created" in rec.getMessage() for rec in caplog.records
        )
    finally:
        _delete_test_admins(email_prefix)


def test_bootstrap_skips_when_admin_exists(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """(b) Admin already exists → no-op, "Admin already exists" log line."""
    _ensure_async_postgres_reachable()

    email_prefix = "pytest-bootstrap-skip-"
    pre_existing_email = f"{email_prefix}preexisting-{uuid.uuid4().hex[:8]}@example.com"
    _delete_test_admins(email_prefix)
    _seed_user_directly(pre_existing_email, "admin")

    # Stub the existence check to return the pre-existing admin id so the
    # scenario does not depend on the live DB having no other admin (live
    # dev stacks may already have one from the docker-compose bootstrap).
    async def existing_admin() -> str | None:
        return str(uuid.uuid4())

    monkeypatch.setattr(admin_bootstrap, "_existing_admin_id", existing_admin)

    # If anything calls into Vault we want to know — there should be no read.
    sentinel = {"vault_called": False}

    def boom(keys: list[str]) -> dict[str, str]:
        sentinel["vault_called"] = True
        return {}

    monkeypatch.setattr(admin_bootstrap, "read_secrets", boom)

    try:
        with caplog.at_level(logging.INFO, logger="app.bootstrap.admin_bootstrap"):
            exit_code = asyncio.run(admin_bootstrap.bootstrap_admin())

        assert exit_code == 0
        assert sentinel["vault_called"] is False
        assert any(
            "Admin already exists, skipping bootstrap" in rec.getMessage()
            for rec in caplog.records
        )
        # Verify no NEW admin row was created beyond the seeded one.
        assert _count_admins(email_prefix) == 1
    finally:
        _delete_test_admins(email_prefix)


def test_bootstrap_raises_on_missing_vault_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """(c) Vault key missing → MissingVaultKeyError with actionable hint."""
    _ensure_async_postgres_reachable()

    async def no_existing_admin() -> str | None:
        return None

    monkeypatch.setattr(admin_bootstrap, "_existing_admin_id", no_existing_admin)

    def missing(keys: list[str]) -> dict[str, str]:
        raise MissingVaultKeyError(
            f"missing required Vault key(s): {KEY_BOOTSTRAP_ADMIN_EMAIL}"
        )

    monkeypatch.setattr(admin_bootstrap, "read_secrets", missing)

    with pytest.raises(MissingVaultKeyError) as exc_info:
        asyncio.run(admin_bootstrap.bootstrap_admin())

    msg = str(exc_info.value)
    # The hint points the operator at the recovery action.
    assert "BOOTSTRAP_ADMIN_EMAIL" in msg
    assert "vault_seed.sh" in msg
