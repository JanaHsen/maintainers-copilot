"""User repository (async) — round-trip against an ephemeral Postgres.

Migration 0003 must be applied before the test runs (the docker-compose
stack handles this; locally the migration test under tests/alembic ensures
the same). If async Postgres / Vault are unreachable in the current env the
test is skipped — there is no SQLite substitute because the migration uses
pgvector (Postgres-only).
"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from fastapi_users_db_sqlalchemy import SQLAlchemyUserDatabase
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from app.infra.database_async import get_async_engine, get_async_sessionmaker
from app.infra.vault_client import VaultBootstrapError
from app.repositories.user_repository import User


@pytest_asyncio.fixture
async def session():  # type: ignore[no-untyped-def]
    """Yield an AsyncSession; skip if Postgres / Vault unreachable."""
    try:
        engine = get_async_engine()
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except (OperationalError, VaultBootstrapError) as exc:
        pytest.skip(f"Async Postgres / Vault not reachable: {exc}")

    sessionmaker = get_async_sessionmaker()
    async with sessionmaker() as session:
        # Clean up any prior test rows whose email matches our prefix.
        await session.execute(
            text("DELETE FROM users WHERE email LIKE 'pytest-user-repo-%'")
        )
        await session.commit()
        yield session
        await session.execute(
            text("DELETE FROM users WHERE email LIKE 'pytest-user-repo-%'")
        )
        await session.commit()


async def test_create_and_fetch_user(session) -> None:  # type: ignore[no-untyped-def]
    """fastapi-users adapter writes a user, fetches by id and by email."""
    db = SQLAlchemyUserDatabase(session, User)

    email = f"pytest-user-repo-{uuid.uuid4().hex[:8]}@example.com"
    created = await db.create(
        {
            "id": uuid.uuid4(),
            "email": email,
            "hashed_password": "not-a-real-hash-just-a-placeholder",
            "is_active": True,
            "is_superuser": False,
            "is_verified": False,
            "role": "user",
        }
    )
    assert created.email == email
    assert created.role == "user"

    by_id = await db.get(created.id)
    assert by_id is not None
    assert by_id.email == email

    by_email = await db.get_by_email(email)
    assert by_email is not None
    assert by_email.id == created.id

    missing = await db.get_by_email("pytest-user-repo-not-present@example.com")
    assert missing is None
