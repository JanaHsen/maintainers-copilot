"""Pydantic domain models for conversations + messages + actor identity.

Aligned with ``contracts/memory-tools.md`` and ``data-model.md`` sections 3
(conversations) + 4 (messages).

Two layers of types:

  * ``Conversation`` / ``Message`` / ``MessageRole`` — the row shapes
    returned by the conversation repository. Mirrors the SQL columns.
  * ``AuthedUser`` / ``WidgetSession`` / ``Actor`` — the identity types
    the chatbot service (Part 2) will pattern-match against. The contract
    in ``contracts/memory-tools.md`` specifies ``Actor`` as the union of
    these two; the memory tools branch on the kind to enforce the
    widget-actor refusal rule (FR-011, SC-004).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel

MessageRole = Literal["user", "assistant", "tool"]


class Conversation(BaseModel):
    """One conversation row.

    Exactly one of ``user_id`` (authenticated maintainer) OR
    (``widget_id`` + ``session_id``) is set — the DB CHECK enforces this
    invariant (FR-019).
    """

    id: uuid.UUID
    user_id: uuid.UUID | None
    widget_id: uuid.UUID | None
    session_id: str | None
    created_at: datetime
    last_message_at: datetime


class Message(BaseModel):
    """One message row.

    ``tool_name`` / ``tool_input`` / ``tool_output`` are populated iff
    ``role == 'tool'`` — the DB CHECK ``chk_messages_tool_consistency``
    enforces this.
    """

    id: uuid.UUID
    conversation_id: uuid.UUID
    role: MessageRole
    content: str
    tool_name: str | None
    tool_input: dict[str, Any] | None
    tool_output: dict[str, Any] | None
    created_at: datetime


class MemoryWindowMessage(BaseModel):
    """A single message in the Redis short-term window.

    Distinct from :class:`Message` (the Postgres row): the in-Redis record has
    no DB-assigned ``id`` and the ``created_at`` is recorded by the appender,
    not by Postgres' ``default now()``. Same tool-consistency rule applies
    (``tool_*`` are populated iff ``role == 'tool'``) but enforced at the
    service layer, not the DB layer (FR-015..FR-018).
    """

    role: MessageRole
    content: str
    tool_name: str | None = None
    tool_input: dict[str, Any] | None = None
    tool_output: dict[str, Any] | None = None
    created_at: datetime | None = None


# --- actor identity -------------------------------------------------------


class AuthedUser(BaseModel):
    """An authenticated maintainer acting on their own behalf.

    Carries the ``role`` so the memory tools can short-circuit
    ``require_admin``-style checks without re-reading the DB. ``role`` is
    sourced from the JWT cookie / fastapi-users current-user dependency
    (Part 1 phase C).
    """

    model_config = {"frozen": True}

    user_id: uuid.UUID
    role: Literal["user", "admin"]


class WidgetSession(BaseModel):
    """An anonymous widget visitor identified by the embedded widget id +
    a per-session id (HMAC-signed cookie in Part 3).

    Memory tools refuse to operate on this actor kind (FR-011 / SC-004) —
    a widget session never reads or writes long-term memory.
    """

    model_config = {"frozen": True}

    widget_id: uuid.UUID
    session_id: str


Actor = AuthedUser | WidgetSession
"""Union of the two actor kinds. The chatbot service in Part 2 pattern-matches
this union and the memory tools branch on ``isinstance`` to refuse widget
sessions before any DB work."""
