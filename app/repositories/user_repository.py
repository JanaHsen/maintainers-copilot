"""User repository — fastapi-users SQLAlchemy adapter glue (Rule 1).

The ``users`` table created by migration 0003 is fastapi-users-managed at the
schema level (``id UUID PK``, ``email``, ``hashed_password``, the three
``is_*`` flags, ``role``, ``created_at``). This module exposes:

  * the SQLAlchemy 2.x Declarative ORM model :class:`User`, mapped to the
    ``users`` table (table name overridden from fastapi-users' default
    ``"user"`` so it matches the migration);
  * :func:`get_user_db`, the FastAPI dependency that yields the
    :class:`SQLAlchemyUserDatabase` adapter fastapi-users' BaseUserManager
    consumes.

This is the **only** repository that uses the async engine
(:mod:`app.infra.database_async`); every other repository continues to use
the sync engine (research R1, plan §Complexity Tracking).
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

from fastapi import Depends
from fastapi_users_db_sqlalchemy import SQLAlchemyBaseUserTableUUID, SQLAlchemyUserDatabase
from sqlalchemy import String
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.infra.database_async import get_async_session


class Base(DeclarativeBase):
    """Declarative base scoped to the chatbot tables fastapi-users owns.

    Distinct from any base the sync repositories use; fastapi-users' adapter
    only ever sees this metadata, so the existing RAG-slice tables are not
    visible to its mapper.
    """


class User(SQLAlchemyBaseUserTableUUID, Base):
    """SQLAlchemy model for the ``users`` table (migration 0003).

    Inherits the standard fastapi-users columns (id, email, hashed_password,
    is_active, is_superuser, is_verified) and adds the application-level
    ``role`` column whose check constraint and default live in the migration.
    """

    __tablename__ = "users"

    role: Mapped[str] = mapped_column(String(length=16), nullable=False, default="user")


async def get_user_db(
    session: AsyncSession = Depends(get_async_session),  # noqa: B008 — FastAPI DI pattern
) -> AsyncIterator[SQLAlchemyUserDatabase[User, uuid.UUID]]:
    """FastAPI dependency: yields the fastapi-users SQLAlchemy adapter."""
    yield SQLAlchemyUserDatabase(session, User)
