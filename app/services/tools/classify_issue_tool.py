"""classify_issue tool wrapper (T004).

Anthropic tool definition + dispatch ``execute`` for the chatbot agent
loop. The dispatch converts the underlying ``classifier_service`` typed
outcome into a JSON-serializable dict per
``specs/003-chatbot-part2-brain/contracts/agent-tools.md``:

* ``ClassifyOk`` → ``{"label", "confidence", "label_scores"}``
* ``ClassifyError`` → ``{"error": {"kind", "detail"}}``

Per Rule 11 this wrapper never raises out to the caller — every service
error becomes the typed error envelope.
"""

from __future__ import annotations

from typing import Any

from app.domain.conversation import Actor
from app.services import classifier_service

TOOL_DEF: dict[str, Any] = {
    "name": "classify_issue",
    "description": (
        "Classify a GitHub issue into bug / feature / documentation / question. "
        "Use when the user provides an issue's title and body and asks for its "
        "category."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "body": {"type": "string"},
        },
        "required": ["title", "body"],
    },
}


def execute(input: dict[str, Any], actor: Actor) -> dict[str, Any]:  # noqa: A002,ARG001
    """Run the classifier and return the JSON-serializable dispatch dict."""
    title = str(input.get("title", ""))
    body = str(input.get("body", ""))
    outcome = classifier_service.classify_issue(title=title, body=body)
    if isinstance(outcome, classifier_service.ClassifyOk):
        return {
            "label": outcome.label,
            "confidence": outcome.confidence,
            "label_scores": outcome.label_scores,
        }
    return {"error": {"kind": outcome.kind, "detail": outcome.detail}}


__all__ = ["TOOL_DEF", "execute"]
