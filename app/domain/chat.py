"""Pydantic domain models for the chatbot endpoints (Rule 1, Rule 9).

Aligned with ``specs/003-chatbot-part2-brain/spec.md`` §3 (chatbot service
contract) and ``contracts/agent-tools.md``. Two request shapes — one per
endpoint — plus the shared response shape and a per-tool trace entry.

``ToolTraceEntry`` is a Pydantic model (not a dataclass) so the
``ChatResponse.tool_trace`` list serializes cleanly through FastAPI's
default JSON encoder without ``arbitrary_types_allowed``. The chatbot
service constructs these inside the agent loop and the router emits them
verbatim — the operator-visible trace of which tools fired in what order.
"""

from __future__ import annotations

import uuid
from typing import Any

from pydantic import BaseModel, Field


class ToolTraceEntry(BaseModel):
    """One row in the per-turn tool-execution trace.

    Mirrors the dispatch contract in ``contracts/agent-tools.md``: every
    tool the model calls during one ``/chat`` invocation produces exactly
    one entry, in the order Anthropic emitted the tool_use blocks. ``output``
    carries either the dispatch's success dict (e.g. ``{"label": ...}``) or
    the error envelope (``{"error": {...}}``); ``is_error`` mirrors the
    ``is_error`` flag the loop sets on the corresponding ``tool_result``.
    """

    tool_name: str
    input: dict[str, Any]
    output: dict[str, Any]
    latency_ms: int
    is_error: bool


class ChatRequestAuthed(BaseModel):
    """Authenticated maintainer's ``POST /chat`` request body.

    ``conversation_id`` is ``None`` on the first turn (the service creates
    a fresh conversation row) and the same UUID on every subsequent turn
    so short-term memory carries forward (FR-001 / spec §3).
    """

    conversation_id: uuid.UUID | None = None
    message: str = Field(min_length=1, max_length=8000)


class ChatRequestWidget(BaseModel):
    """Anonymous widget visitor's ``POST /widget/chat`` request body.

    The router validates ``widget_id`` + ``X-Widget-Token`` against
    ``widget_repository.get_by_token_hash`` (FR-002 / FR-003); this shape
    only carries the in-body fields. ``session_id`` is the per-visitor
    identifier (HMAC-signed cookie in Part 3); the chatbot service uses
    it to scope the conversation row.
    """

    widget_id: uuid.UUID
    session_id: str = Field(min_length=1, max_length=128)
    message: str = Field(min_length=1, max_length=8000)


class ChatResponse(BaseModel):
    """Shared response shape for both ``/chat`` and ``/widget/chat``.

    ``tool_trace`` is the operator-visible per-turn tool log; one entry
    per tool the model called. ``request_id`` + ``trace_id`` mirror the
    pattern used by every other router in the codebase (Rule 7) so the
    caller can grep Phoenix by either identifier.
    """

    assistant_message: str
    conversation_id: uuid.UUID
    tool_trace: list[ToolTraceEntry]
    request_id: str
    trace_id: str


__all__ = [
    "ChatRequestAuthed",
    "ChatRequestWidget",
    "ChatResponse",
    "ToolTraceEntry",
]
