"""Conversation repository — the only place ``conversations`` /
``messages`` SQL lives (Rule 1).

Four functions:

  * :func:`create` — insert a new ``conversations`` row for either an
    authenticated user (user_id set) OR a widget session
    (widget_id + session_id set). The actor-exclusivity CHECK at the DB
    level (FR-019) rejects any other combination.
  * :func:`get` — fetch one conversation by id.
  * :func:`append_message` — insert a new ``messages`` row in the same
    statement that updates ``conversations.last_message_at``.
  * :func:`list_messages` — return all messages for a conversation in
    chronological order.

The tool-column consistency CHECK at the DB level (``role='tool'`` iff
``tool_name`` is set) rejects malformed message rows at insert time; the
test suite exercises this.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from sqlalchemy import text

from app.domain.conversation import Conversation, Message, MessageRole
from app.infra.database import get_engine

_INSERT_CONVERSATION_SQL = text(
    """
    INSERT INTO conversations (id, user_id, widget_id, session_id)
    VALUES (:id, :user_id, :widget_id, :session_id)
    """
)


_GET_CONVERSATION_SQL = text(
    """
    SELECT id, user_id, widget_id, session_id, created_at, last_message_at
    FROM conversations
    WHERE id = :id
    """
)


_APPEND_MESSAGE_SQL = text(
    """
    INSERT INTO messages
      (id, conversation_id, role, content, tool_name, tool_input, tool_output)
    VALUES
      (:id, :conversation_id, :role, :content, :tool_name,
       CAST(:tool_input AS JSONB), CAST(:tool_output AS JSONB))
    """
)


_TOUCH_CONVERSATION_SQL = text(
    "UPDATE conversations SET last_message_at = now() WHERE id = :id"
)


_LIST_MESSAGES_SQL = text(
    """
    SELECT id, conversation_id, role, content,
           tool_name, tool_input, tool_output, created_at
    FROM messages
    WHERE conversation_id = :conversation_id
    ORDER BY created_at ASC, id ASC
    """
)


def create(
    *,
    user_id: uuid.UUID | None,
    widget_id: uuid.UUID | None,
    session_id: str | None,
) -> uuid.UUID:
    """Create a conversation row and return its id.

    The DB CHECK rejects (user_id + widget_id), (neither), or
    (widget_id without session_id). Each of those surfaces as
    ``IntegrityError`` from psycopg.
    """
    conversation_id = uuid.uuid4()
    with get_engine().begin() as conn:
        conn.execute(
            _INSERT_CONVERSATION_SQL,
            {
                "id": conversation_id,
                "user_id": user_id,
                "widget_id": widget_id,
                "session_id": session_id,
            },
        )
    return conversation_id


def get(conversation_id: uuid.UUID) -> Conversation | None:
    """Fetch one conversation row, or ``None`` if absent."""
    with get_engine().connect() as conn:
        row = conn.execute(
            _GET_CONVERSATION_SQL, {"id": conversation_id}
        ).first()
    if row is None:
        return None
    return Conversation(
        id=row.id,
        user_id=row.user_id,
        widget_id=row.widget_id,
        session_id=row.session_id,
        created_at=row.created_at,
        last_message_at=row.last_message_at,
    )


def append_message(
    *,
    conversation_id: uuid.UUID,
    role: MessageRole,
    content: str,
    tool_name: str | None = None,
    tool_input: dict[str, Any] | None = None,
    tool_output: dict[str, Any] | None = None,
) -> uuid.UUID:
    """Insert a message row and refresh the parent's ``last_message_at``.

    Both statements run in one transaction; either both commit or both
    roll back. The tool-consistency CHECK at the DB level (role='tool' iff
    tool_name is set) rejects malformed input.
    """
    message_id = uuid.uuid4()
    with get_engine().begin() as conn:
        conn.execute(
            _APPEND_MESSAGE_SQL,
            {
                "id": message_id,
                "conversation_id": conversation_id,
                "role": role,
                "content": content,
                "tool_name": tool_name,
                "tool_input": (
                    None if tool_input is None else json.dumps(tool_input)
                ),
                "tool_output": (
                    None if tool_output is None else json.dumps(tool_output)
                ),
            },
        )
        conn.execute(_TOUCH_CONVERSATION_SQL, {"id": conversation_id})
    return message_id


def list_messages(conversation_id: uuid.UUID) -> list[Message]:
    """Return all messages for ``conversation_id`` in chronological order."""
    with get_engine().connect() as conn:
        rows = conn.execute(
            _LIST_MESSAGES_SQL, {"conversation_id": conversation_id}
        ).all()
    return [
        Message(
            id=r.id,
            conversation_id=r.conversation_id,
            role=r.role,
            content=r.content,
            tool_name=r.tool_name,
            tool_input=r.tool_input,
            tool_output=r.tool_output,
            created_at=r.created_at,
        )
        for r in rows
    ]
