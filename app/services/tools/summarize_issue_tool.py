"""summarize_issue tool wrapper (T006).

Anthropic tool definition + dispatch ``execute`` for the chatbot agent
loop. The dispatch converts ``summarize_service.summarize``'s typed
outcome into a JSON-serializable dict per
``specs/003-chatbot-part2-brain/contracts/agent-tools.md``:

* ``SummarizeOk`` → ``{"summary": ...}``
* ``SummarizeError`` → ``{"error": {"kind", "detail"}}``

Per Rule 11 this wrapper never raises out to the caller.
"""

from __future__ import annotations

import uuid
from typing import Any

from app.domain.conversation import Actor
from app.services import summarize_service

TOOL_DEF: dict[str, Any] = {
    "name": "summarize_issue",
    "description": (
        "Produce a 2-3 sentence summary of an issue body. Use when the user "
        "pastes a long issue and asks for the gist."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "text": {"type": "string"},
            "max_sentences": {"type": "integer", "minimum": 1, "maximum": 5},
        },
        "required": ["text"],
    },
}


def execute(
    input: dict[str, Any],  # noqa: A002
    actor: Actor,  # noqa: ARG001
    conversation_id: uuid.UUID,  # noqa: ARG001
) -> dict[str, Any]:
    """Run the summarize service and return the JSON-serializable dispatch dict.

    ``actor`` + ``conversation_id`` are accepted to match the uniform
    dispatch signature but are unused — summarize_issue is stateless.
    """
    text = str(input.get("text", ""))
    max_sentences = int(input.get("max_sentences", 3))
    outcome = summarize_service.summarize(text, max_sentences=max_sentences)
    if isinstance(outcome, summarize_service.SummarizeOk):
        return {"summary": outcome.summary}
    return {"error": {"kind": outcome.kind, "detail": outcome.detail}}


__all__ = ["TOOL_DEF", "execute"]
