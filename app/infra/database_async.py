"""Async SQLAlchemy engine scoped to fastapi-users (research R1).

The rest of ``app/`` uses the sync engine in :mod:`app.infra.database`.
fastapi-users-db-sqlalchemy requires an :class:`AsyncSession`, so we keep
two engines side by side rather than migrating every existing repository
to async (see plan §Complexity Tracking).

Same database, same password, different driver: ``psycopg`` (sync) for the
existing repositories, ``psycopg_async`` for fastapi-users. The DB password
still resolves from Vault (Rule 2).
"""

from collections.abc import AsyncIterator
from functools import lru_cache

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import get_settings
from app.infra.vault_client import KEY_DATABASE_PASSWORD, read_secrets


def _async_database_url() -> str:
    settings = get_settings()
    db_secret = read_secrets([KEY_DATABASE_PASSWORD])[KEY_DATABASE_PASSWORD]
    return (
        f"postgresql+psycopg_async://{settings.postgres_user}:{db_secret}"
        f"@{settings.postgres_host}:{settings.postgres_port}/{settings.postgres_db}"
    )


@lru_cache
def get_async_engine() -> AsyncEngine:
    return create_async_engine(_async_database_url(), pool_pre_ping=True, future=True)


@lru_cache
def get_async_sessionmaker() -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(bind=get_async_engine(), expire_on_commit=False)


async def get_async_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency: yields an AsyncSession scoped to the request."""
    async with get_async_sessionmaker()() as session:
        yield session
