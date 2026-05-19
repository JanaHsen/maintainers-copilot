"""SQLAlchemy 2.x engine and session factory.

The DB credential comes from Vault (Rule 2), never from env. Boot uses a
bounded retry so a compose start-order race is absorbed, but a genuinely
unreachable Postgres still fails loud (Rule 4). Only ``app/repositories/``
may import this; the rest of the app stays storage-agnostic (Rule 1).
"""

import time
from functools import lru_cache

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings
from app.infra.vault_client import KEY_DATABASE_PASSWORD, read_secrets


class DatabaseUnreachableError(RuntimeError):
    """Postgres could not be reached after bounded retries (refuse-to-boot)."""


def _database_url() -> str:
    settings = get_settings()
    db_secret = read_secrets([KEY_DATABASE_PASSWORD])[KEY_DATABASE_PASSWORD]
    return (
        f"postgresql+psycopg://{settings.postgres_user}:{db_secret}"
        f"@{settings.postgres_host}:{settings.postgres_port}/{settings.postgres_db}"
    )


@lru_cache
def get_engine() -> Engine:
    return create_engine(_database_url(), pool_pre_ping=True, future=True)


@lru_cache
def get_sessionmaker() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(), expire_on_commit=False)


def connect_with_retry(attempts: int = 6, base_delay: float = 0.5) -> None:
    """Block until Postgres answers ``SELECT 1`` or raise after exhaustion."""
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            with get_engine().connect() as conn:
                conn.execute(text("SELECT 1"))
            return
        except OperationalError as exc:
            last_error = exc
            time.sleep(base_delay * (2**attempt))
    raise DatabaseUnreachableError(
        f"Postgres unreachable after {attempts} attempts"
    ) from last_error


def ping() -> None:
    """Single fast probe for /health; raises on failure."""
    try:
        with get_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
    except OperationalError as exc:
        raise DatabaseUnreachableError("Postgres unreachable") from exc
