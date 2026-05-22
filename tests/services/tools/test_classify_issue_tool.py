"""Unit tests for classify_issue_tool dispatch (T004).

Stubs ``classifier_service.classify_issue`` to return each typed outcome
and asserts the dispatch dict shape — the chatbot loop turns these dicts
into ``tool_result`` content, so the wire shape is load-bearing.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from app.domain.conversation import AuthedUser
from app.services import classifier_service
from app.services.tools import classify_issue_tool


def _actor() -> AuthedUser:
    return AuthedUser(user_id=uuid.uuid4(), role="user")


def test_tool_def_shape() -> None:
    """The Anthropic tool definition has name + description + input_schema."""
    assert classify_issue_tool.TOOL_DEF["name"] == "classify_issue"
    assert isinstance(classify_issue_tool.TOOL_DEF["description"], str)
    schema = classify_issue_tool.TOOL_DEF["input_schema"]
    assert schema["type"] == "object"
    assert schema["required"] == ["title", "body"]


def test_classify_ok_returns_label_confidence_scores(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_classify(*, title: str, body: str, request_id: str = "") -> Any:
        assert title == "T"
        assert body == "B"
        return classifier_service.ClassifyOk(
            label="bug", confidence=0.91, label_scores={"bug": 0.91, "feature": 0.05}
        )

    monkeypatch.setattr(classifier_service, "classify_issue", fake_classify)
    out = classify_issue_tool.execute({"title": "T", "body": "B"}, _actor(), uuid.uuid4())
    assert out == {
        "label": "bug",
        "confidence": 0.91,
        "label_scores": {"bug": 0.91, "feature": 0.05},
    }
    assert "error" not in out


@pytest.mark.parametrize(
    "kind",
    ["unreachable", "timeout", "bad_request", "internal", "unexpected"],
)
def test_classify_error_returns_envelope(
    monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    def fake_classify(*, title: str, body: str, request_id: str = "") -> Any:
        return classifier_service.ClassifyError(kind=kind, detail="boom")  # type: ignore[arg-type]

    monkeypatch.setattr(classifier_service, "classify_issue", fake_classify)
    out = classify_issue_tool.execute({"title": "T", "body": "B"}, _actor(), uuid.uuid4())
    assert out == {"error": {"kind": kind, "detail": "boom"}}


def test_missing_fields_defaults_to_empty_strings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, str] = {}

    def fake_classify(*, title: str, body: str, request_id: str = "") -> Any:
        captured["title"] = title
        captured["body"] = body
        return classifier_service.ClassifyOk(label="bug", confidence=0.1, label_scores={})

    monkeypatch.setattr(classifier_service, "classify_issue", fake_classify)
    classify_issue_tool.execute({}, _actor(), uuid.uuid4())
    assert captured == {"title": "", "body": ""}
