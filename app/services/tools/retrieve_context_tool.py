"""retrieve_context tool wrapper (T007).

Anthropic tool definition + dispatch ``execute`` for the chatbot agent
loop. The dispatch builds a :class:`RetrieveRequest`, calls
``retrieve_service.retrieve``, and projects the result into a compact
JSON shape per
``specs/003-chatbot-part2-brain/contracts/agent-tools.md``:

* ``RetrieveOk`` → ``{"chunks": [{"id", "content_snippet", "source_type",
  "source_id"}]}``. Full chunk content is NOT sent — the model only needs
  enough to ground a response, so we slice ``content[:200]``.
* ``RetrieveError`` → ``{"error": {"kind", "detail"}}``.

Per Rule 11 this wrapper never raises out to the caller.
"""

from __future__ import annotations

import uuid
from typing import Any

from app.domain.conversation import Actor
from app.domain.retrieve import RetrieveRequest
from app.services import retrieve_service

_SNIPPET_CHARS = 200

TOOL_DEF: dict[str, Any] = {
    "name": "retrieve_context",
    "description": (
        "Retrieve up to k documentation/issue chunks relevant to the query. "
        "Use when the user asks a question that depends on project knowledge."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "k": {"type": "integer", "minimum": 1, "maximum": 20},
        },
        "required": ["query"],
    },
}


def execute(
    input: dict[str, Any],  # noqa: A002
    actor: Actor,  # noqa: ARG001
    conversation_id: uuid.UUID,  # noqa: ARG001
) -> dict[str, Any]:
    """Run /retrieve and return the JSON-serializable dispatch dict.

    ``actor`` + ``conversation_id`` are accepted to match the uniform
    dispatch signature but are unused — retrieve_context is stateless.
    """
    query = str(input.get("query", ""))
    k = int(input.get("k", 5))
    # RetrieveRequest enforces min_length=1 + 0<=k<=20; pre-validate so the
    # tool returns a structured error instead of letting Pydantic raise.
    if not query:
        return {
            "error": {
                "kind": "bad_request",
                "detail": "query must be a non-empty string",
            }
        }
    try:
        req = RetrieveRequest(question=query, k=k)
    except ValueError as exc:
        return {"error": {"kind": "bad_request", "detail": str(exc)}}
    outcome = retrieve_service.retrieve(req)
    if isinstance(outcome, retrieve_service.RetrieveOk):
        return {
            "chunks": [
                {
                    "id": chunk.chunk_id,
                    "content_snippet": chunk.content[:_SNIPPET_CHARS],
                    "source_type": chunk.source_type,
                    "source_id": chunk.source_id,
                }
                for chunk in outcome.chunks
            ]
        }
    return {"error": {"kind": outcome.kind, "detail": outcome.detail}}


__all__ = ["TOOL_DEF", "execute"]
