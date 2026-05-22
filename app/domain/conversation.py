"""Pydantic domain models for conversations + messages.

The actor types (``AuthedUser``, ``WidgetSession``, ``Actor``) land in T013
along with the full set of conversation/message types — this stub exists so
the conversation repository can return typed rows without circular imports.

Aligned with ``specs/002-chatbot-part1-foundations/contracts/memory-tools.md``
and ``data-model.md`` sections 3 (conversations) + 4 (messages).
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
