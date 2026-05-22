"""extract_entities tool wrapper (T005).

Anthropic tool definition + dispatch ``execute`` for the chatbot agent
loop. The dispatch converts ``ner_service.extract``'s typed outcome into
a JSON-serializable dict per
``specs/003-chatbot-part2-brain/contracts/agent-tools.md``:

* ``NerOk`` → ``{"entities": {repo_names, file_paths, error_types, package_names}}``
* ``NerError`` → ``{"error": {"kind", "detail"}}``

The four-bucket Pydantic model (``EntityBuckets``) is flattened to a plain
dict so the loop's ``json.dumps(..., default=str)`` produces a clean
``tool_result.content`` string.
"""

from __future__ import annotations

import uuid
from typing import Any

from app.domain.conversation import Actor
from app.services import ner_service

TOOL_DEF: dict[str, Any] = {
    "name": "extract_entities",
    "description": (
        "Extract repo names, file paths, error types, and package names from "
        "issue text. Use when the user gives you a chunk of text and you need "
        "its named entities."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "text": {"type": "string"},
        },
        "required": ["text"],
    },
}


def execute(
    input: dict[str, Any],  # noqa: A002
    actor: Actor,  # noqa: ARG001
    conversation_id: uuid.UUID,  # noqa: ARG001
) -> dict[str, Any]:
    """Run the NER service and return the JSON-serializable dispatch dict.

    ``actor`` + ``conversation_id`` are accepted to match the uniform
    dispatch signature but are unused — extract_entities is stateless.
    """
    text = str(input.get("text", ""))
    outcome = ner_service.extract(text)
    if isinstance(outcome, ner_service.NerOk):
        buckets = outcome.entities
        return {
            "entities": {
                "repo_names": list(buckets.repo_names),
                "file_paths": list(buckets.file_paths),
                "error_types": list(buckets.error_types),
                "package_names": list(buckets.package_names),
            }
        }
    return {"error": {"kind": outcome.kind, "detail": outcome.detail}}


__all__ = ["TOOL_DEF", "execute"]
