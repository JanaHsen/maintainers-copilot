"""Tool primitives — composable building blocks the chatbot agent loop calls.

Each module here exposes one tool primitive as a typed-outcome function
(Rule 11): no exceptions escape, every failure mode is a discriminated
variant of the return type. Per Rule 9, every tool wrapper is named for
what it contains (``classify_issue_tool.py``, not ``utils.py``).

This module exposes two registry tables consumed by the chatbot agent
loop (Part 2, ``app/services/chatbot_service.py``):

* :data:`TOOLS` — the list of Anthropic tool definitions, passed to
  ``anthropic_client.tool_use_chat`` as the ``tools`` argument.
* :data:`TOOLS_DISPATCH` — a name → callable map. The callable signature
  is uniform: ``(input: dict, actor: Actor, conversation_id: UUID) -> dict``.
  The four stateless wrappers (classify, ner, summarize, retrieve) ignore
  ``actor`` and ``conversation_id``; the two memory wrappers use both
  (``write_memory`` writes provenance, ``recall_memory`` scopes by user).

The memory tools have a richer signature than the stateless four (they
need ``conversation_id`` for audit-log + memory-provenance, and they
take ``actor`` because the widget refusal happens at the tool-primitive
layer per Part 1). Rather than carry two dispatch signatures, every
wrapper accepts ``conversation_id`` even when it does not consume it.
Adapter shims below project the dispatch signature onto the existing
Part 1 ``write_memory`` / ``recall_memory`` keyword-only signatures
without modifying them — those modules ship a stable API surface.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any

from app.domain.conversation import Actor
from app.services.tools import (
    classify_issue_tool,
    extract_entities_tool,
    recall_memory_tool,
    retrieve_context_tool,
    summarize_issue_tool,
    write_memory_tool,
)

ToolDispatch = Callable[[dict[str, Any], Actor, uuid.UUID], dict[str, Any]]


def _write_memory_execute(
    input: dict[str, Any],  # noqa: A002
    actor: Actor,
    conversation_id: uuid.UUID,
) -> dict[str, Any]:
    """Adapt the uniform dispatch signature onto ``write_memory_tool.write_memory``.

    Returns the JSON-serializable dispatch dict per
    ``contracts/agent-tools.md``: on ``WriteMemoryOk`` ``{"memory_id": ...}``,
    on ``WriteMemoryError`` ``{"error": {"kind", "detail"}}``.
    """
    content = str(input.get("content", ""))
    outcome = write_memory_tool.write_memory(
        content=content,
        actor=actor,
        conversation_id=conversation_id,
    )
    if isinstance(outcome, write_memory_tool.WriteMemoryOk):
        return {"memory_id": str(outcome.memory_id)}
    return {"error": {"kind": outcome.kind, "detail": outcome.detail}}


def _recall_memory_execute(
    input: dict[str, Any],  # noqa: A002
    actor: Actor,
    conversation_id: uuid.UUID,  # noqa: ARG001
) -> dict[str, Any]:
    """Adapt the uniform dispatch signature onto ``recall_memory_tool.recall_memory``.

    ``conversation_id`` is unused (recall is scoped by ``actor.user_id`` at
    the SQL boundary). Returns ``{"hits": [...]}`` on ``RecallMemoryOk`` and
    the standard ``{"error": {...}}`` envelope on ``RecallMemoryError``.
    """
    query = str(input.get("query", ""))
    k = int(input.get("k", 5))
    outcome = recall_memory_tool.recall_memory(
        query=query,
        actor=actor,
        k=k,
    )
    if isinstance(outcome, recall_memory_tool.RecallMemoryOk):
        return {
            "hits": [
                {
                    "memory_id": str(hit.memory_id),
                    "content": hit.content,
                    "similarity": hit.similarity,
                }
                for hit in outcome.hits
            ]
        }
    return {"error": {"kind": outcome.kind, "detail": outcome.detail}}


# Anthropic tool definitions list. Order matches the contract in
# ``specs/003-chatbot-part2-brain/contracts/agent-tools.md`` for readability;
# Anthropic does not care about list order.
TOOLS: list[dict[str, Any]] = [
    classify_issue_tool.TOOL_DEF,
    extract_entities_tool.TOOL_DEF,
    summarize_issue_tool.TOOL_DEF,
    retrieve_context_tool.TOOL_DEF,
    write_memory_tool.TOOL_DEF,
    recall_memory_tool.TOOL_DEF,
]


# Name → execute callable. The chatbot loop looks up by the model-emitted
# ``tool_use.name`` and invokes the callable with the parsed input + actor +
# current conversation id. The callable returns the JSON-serializable dict
# the loop wraps into a ``tool_result`` content block.
TOOLS_DISPATCH: dict[str, ToolDispatch] = {
    "classify_issue": classify_issue_tool.execute,
    "extract_entities": extract_entities_tool.execute,
    "summarize_issue": summarize_issue_tool.execute,
    "retrieve_context": retrieve_context_tool.execute,
    "write_memory": _write_memory_execute,
    "recall_memory": _recall_memory_execute,
}


__all__ = [
    "TOOLS",
    "TOOLS_DISPATCH",
    "ToolDispatch",
    "classify_issue_tool",
    "extract_entities_tool",
    "recall_memory_tool",
    "retrieve_context_tool",
    "summarize_issue_tool",
    "write_memory_tool",
]
