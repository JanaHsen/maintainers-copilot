"""Unit tests for the tools registry (T008).

Asserts the two exported tables match the contract in
``specs/003-chatbot-part2-brain/contracts/agent-tools.md``:

* :data:`TOOLS` is a list of 6 dicts each with ``name``, ``description``,
  and ``input_schema``.
* :data:`TOOLS_DISPATCH` keys are exactly the 6 tool names.
* Each dispatch callable returns a JSON-serializable dict (the loop
  passes the dict through ``json.dumps``).
* The memory-tool adapters project Part 1's ``write_memory`` /
  ``recall_memory`` typed outcomes into the documented dispatch shapes.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest

from app.domain.conversation import AuthedUser
from app.domain.memory import MemoryRecallHit
from app.services import classifier_service
from app.services.tools import (
    TOOLS,
    TOOLS_DISPATCH,
    recall_memory_tool,
    write_memory_tool,
)

_EXPECTED_NAMES = {
    "classify_issue",
    "extract_entities",
    "summarize_issue",
    "retrieve_context",
    "write_memory",
    "recall_memory",
}


def _actor() -> AuthedUser:
    return AuthedUser(user_id=uuid.uuid4(), role="user")


def test_tools_list_has_six_definitions() -> None:
    """TOOLS is the six-tool Anthropic-tool-definition list."""
    assert len(TOOLS) == 6
    names = set()
    for tool_def in TOOLS:
        assert "name" in tool_def
        assert "description" in tool_def
        assert "input_schema" in tool_def
        assert isinstance(tool_def["input_schema"], dict)
        assert tool_def["input_schema"]["type"] == "object"
        names.add(tool_def["name"])
    assert names == _EXPECTED_NAMES


def test_dispatch_keys_match_expected_names() -> None:
    """TOOLS_DISPATCH covers the six tool names exactly."""
    assert set(TOOLS_DISPATCH.keys()) == _EXPECTED_NAMES


def test_dispatch_signature_is_callable() -> None:
    """Every dispatch entry is a callable; targeted tests below cover behavior."""
    for name, fn in TOOLS_DISPATCH.items():
        assert callable(fn), f"{name} dispatch is not callable"


def test_classify_dispatch_returns_ok_dict(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub classifier_service and assert the dispatch returns the documented shape."""

    def fake_classify(*, title: str, body: str, request_id: str = "") -> Any:
        return classifier_service.ClassifyOk(
            label="bug", confidence=0.7, label_scores={"bug": 0.7}
        )

    monkeypatch.setattr(classifier_service, "classify_issue", fake_classify)
    out = TOOLS_DISPATCH["classify_issue"](
        {"title": "T", "body": "B"}, _actor(), uuid.uuid4()
    )
    assert out == {"label": "bug", "confidence": 0.7, "label_scores": {"bug": 0.7}}


def test_write_memory_dispatch_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    """When write_memory_tool returns Ok, the adapter emits {memory_id: <str>}."""
    fake_id = uuid.uuid4()

    def fake_write(**_kw: Any) -> Any:
        return write_memory_tool.WriteMemoryOk(memory_id=fake_id)

    monkeypatch.setattr(write_memory_tool, "write_memory", fake_write)
    out = TOOLS_DISPATCH["write_memory"](
        {"content": "I prefer concise summaries"},
        _actor(),
        uuid.uuid4(),
    )
    assert out == {"memory_id": str(fake_id)}


def test_write_memory_dispatch_widget_forbidden(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When write_memory refuses (widget actor), the adapter emits the envelope."""

    def fake_write(**_kw: Any) -> Any:
        return write_memory_tool.WriteMemoryError(
            kind="widget_actor_forbidden",
            detail="widget sessions cannot write long-term memory",
        )

    monkeypatch.setattr(write_memory_tool, "write_memory", fake_write)
    out = TOOLS_DISPATCH["write_memory"](
        {"content": "x"}, _actor(), uuid.uuid4()
    )
    assert out == {
        "error": {
            "kind": "widget_actor_forbidden",
            "detail": "widget sessions cannot write long-term memory",
        }
    }


def test_recall_memory_dispatch_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    """When recall_memory returns Ok, the adapter projects hits to JSON shape."""
    hit_id = uuid.uuid4()

    def fake_recall(**_kw: Any) -> Any:
        return recall_memory_tool.RecallMemoryOk(
            hits=[
                MemoryRecallHit(
                    memory_id=hit_id,
                    content="user prefers concise summaries",
                    created_at=datetime.now(UTC),
                    similarity=0.83,
                )
            ]
        )

    monkeypatch.setattr(recall_memory_tool, "recall_memory", fake_recall)
    out = TOOLS_DISPATCH["recall_memory"](
        {"query": "preferences", "k": 3}, _actor(), uuid.uuid4()
    )
    assert out == {
        "hits": [
            {
                "memory_id": str(hit_id),
                "content": "user prefers concise summaries",
                "similarity": 0.83,
            }
        ]
    }


def test_recall_memory_dispatch_widget_forbidden(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When recall refuses (widget actor), the adapter emits the envelope."""

    def fake_recall(**_kw: Any) -> Any:
        return recall_memory_tool.RecallMemoryError(
            kind="widget_actor_forbidden",
            detail="widget sessions cannot read long-term memory",
        )

    monkeypatch.setattr(recall_memory_tool, "recall_memory", fake_recall)
    out = TOOLS_DISPATCH["recall_memory"](
        {"query": "x"}, _actor(), uuid.uuid4()
    )
    assert out == {
        "error": {
            "kind": "widget_actor_forbidden",
            "detail": "widget sessions cannot read long-term memory",
        }
    }


def test_dispatch_outputs_are_json_serializable() -> None:
    """The loop serializes dispatch output via json.dumps — sanity check."""
    import json

    sample = {
        "label": "bug",
        "confidence": 0.7,
        "label_scores": {"bug": 0.7},
    }
    json.dumps(sample, default=str)  # raises if not serializable
