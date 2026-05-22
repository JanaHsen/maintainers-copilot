"""Unit tests for summarize_issue_tool dispatch (T006)."""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from app.domain.conversation import AuthedUser
from app.services import summarize_service
from app.services.tools import summarize_issue_tool


def _actor() -> AuthedUser:
    return AuthedUser(user_id=uuid.uuid4(), role="user")


def test_tool_def_shape() -> None:
    assert summarize_issue_tool.TOOL_DEF["name"] == "summarize_issue"
    schema = summarize_issue_tool.TOOL_DEF["input_schema"]
    assert schema["required"] == ["text"]
    assert "max_sentences" in schema["properties"]


def test_summarize_ok_returns_summary(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def fake_summarize(
        text: str, *, max_sentences: int = 3, request_id: str = ""
    ) -> Any:
        captured["text"] = text
        captured["max_sentences"] = max_sentences
        return summarize_service.SummarizeOk(summary="Two sentences. Done.")

    monkeypatch.setattr(summarize_service, "summarize", fake_summarize)
    out = summarize_issue_tool.execute(
        {"text": "long issue body", "max_sentences": 2}, _actor(), uuid.uuid4()
    )
    assert out == {"summary": "Two sentences. Done."}
    assert captured == {"text": "long issue body", "max_sentences": 2}


def test_summarize_ok_default_max_sentences(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def fake_summarize(
        text: str, *, max_sentences: int = 3, request_id: str = ""
    ) -> Any:
        captured["max_sentences"] = max_sentences
        return summarize_service.SummarizeOk(summary="x")

    monkeypatch.setattr(summarize_service, "summarize", fake_summarize)
    summarize_issue_tool.execute({"text": "body"}, _actor(), uuid.uuid4())
    assert captured["max_sentences"] == 3


@pytest.mark.parametrize(
    "kind",
    ["bad_request", "internal", "unreachable", "timeout", "unexpected"],
)
def test_summarize_error_returns_envelope(
    monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    def fake_summarize(
        text: str, *, max_sentences: int = 3, request_id: str = ""
    ) -> Any:
        return summarize_service.SummarizeError(kind=kind, detail="boom")  # type: ignore[arg-type]

    monkeypatch.setattr(summarize_service, "summarize", fake_summarize)
    out = summarize_issue_tool.execute({"text": "x"}, _actor(), uuid.uuid4())
    assert out == {"error": {"kind": kind, "detail": "boom"}}
