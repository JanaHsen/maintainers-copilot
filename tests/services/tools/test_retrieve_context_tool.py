"""Unit tests for retrieve_context_tool dispatch (T007)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest

from app.domain.conversation import AuthedUser
from app.domain.retrieve import RetrievedChunk, RetrieveRequest
from app.services import retrieve_service
from app.services.tools import retrieve_context_tool


def _actor() -> AuthedUser:
    return AuthedUser(user_id=uuid.uuid4(), role="user")


def test_tool_def_shape() -> None:
    assert retrieve_context_tool.TOOL_DEF["name"] == "retrieve_context"
    schema = retrieve_context_tool.TOOL_DEF["input_schema"]
    assert schema["required"] == ["query"]
    assert "k" in schema["properties"]


def test_retrieve_ok_projects_snippet(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}
    long_content = "x" * 500

    def fake_retrieve(req: RetrieveRequest, **_kw: Any) -> Any:
        captured["question"] = req.question
        captured["k"] = req.k
        return retrieve_service.RetrieveOk(
            chunks=[
                RetrievedChunk(
                    content=long_content,
                    source_type="docs",
                    source_id="docs/install.md",
                    score=0.9,
                    metadata={},
                    chunk_id="parent-1",
                ),
            ]
        )

    monkeypatch.setattr(retrieve_service, "retrieve", fake_retrieve)
    out = retrieve_context_tool.execute({"query": "how do I install?", "k": 3}, _actor())
    assert captured == {"question": "how do I install?", "k": 3}
    assert out == {
        "chunks": [
            {
                "id": "parent-1",
                "content_snippet": "x" * 200,
                "source_type": "docs",
                "source_id": "docs/install.md",
            }
        ]
    }


def test_retrieve_ok_default_k(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, int] = {}

    def fake_retrieve(req: RetrieveRequest, **_kw: Any) -> Any:
        captured["k"] = req.k
        return retrieve_service.RetrieveOk(chunks=[])

    monkeypatch.setattr(retrieve_service, "retrieve", fake_retrieve)
    out = retrieve_context_tool.execute({"query": "hi"}, _actor())
    assert captured["k"] == 5
    assert out == {"chunks": []}


@pytest.mark.parametrize(
    "kind",
    ["unreachable", "timeout", "bad_request", "internal", "unexpected"],
)
def test_retrieve_error_returns_envelope(
    monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    def fake_retrieve(req: RetrieveRequest, **_kw: Any) -> Any:
        return retrieve_service.RetrieveError(kind=kind, detail="boom")  # type: ignore[arg-type]

    monkeypatch.setattr(retrieve_service, "retrieve", fake_retrieve)
    out = retrieve_context_tool.execute({"query": "x"}, _actor())
    assert out == {"error": {"kind": kind, "detail": "boom"}}


def test_empty_query_returns_bad_request_envelope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called = False

    def fake_retrieve(req: RetrieveRequest, **_kw: Any) -> Any:
        nonlocal called
        called = True
        return retrieve_service.RetrieveOk(chunks=[])

    monkeypatch.setattr(retrieve_service, "retrieve", fake_retrieve)
    out = retrieve_context_tool.execute({"query": ""}, _actor())
    assert out == {
        "error": {"kind": "bad_request", "detail": "query must be a non-empty string"}
    }
    assert called is False


def test_invalid_k_returns_bad_request_envelope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    out = retrieve_context_tool.execute({"query": "x", "k": 999}, _actor())
    assert out["error"]["kind"] == "bad_request"


def _ts() -> datetime:
    return datetime.now(UTC)
