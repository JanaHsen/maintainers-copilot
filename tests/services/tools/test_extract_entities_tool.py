"""Unit tests for extract_entities_tool dispatch (T005)."""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from app.domain.conversation import AuthedUser
from app.domain.ner import EntityBuckets
from app.services import ner_service
from app.services.tools import extract_entities_tool


def _actor() -> AuthedUser:
    return AuthedUser(user_id=uuid.uuid4(), role="user")


def test_tool_def_shape() -> None:
    assert extract_entities_tool.TOOL_DEF["name"] == "extract_entities"
    schema = extract_entities_tool.TOOL_DEF["input_schema"]
    assert schema["required"] == ["text"]


def test_ner_ok_returns_four_buckets(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_extract(text: str, *, request_id: str = "") -> Any:
        assert text == "some issue text"
        return ner_service.NerOk(
            entities=EntityBuckets(
                repo_names=["openai/whisper"],
                file_paths=["src/foo.py"],
                error_types=["ValueError"],
                package_names=["numpy"],
            )
        )

    monkeypatch.setattr(ner_service, "extract", fake_extract)
    out = extract_entities_tool.execute({"text": "some issue text"}, _actor())
    assert out == {
        "entities": {
            "repo_names": ["openai/whisper"],
            "file_paths": ["src/foo.py"],
            "error_types": ["ValueError"],
            "package_names": ["numpy"],
        }
    }


@pytest.mark.parametrize(
    "kind",
    ["bad_request", "bad_format", "internal", "unreachable", "timeout", "unexpected"],
)
def test_ner_error_returns_envelope(
    monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    def fake_extract(text: str, *, request_id: str = "") -> Any:
        return ner_service.NerError(kind=kind, detail="boom")  # type: ignore[arg-type]

    monkeypatch.setattr(ner_service, "extract", fake_extract)
    out = extract_entities_tool.execute({"text": "x"}, _actor())
    assert out == {"error": {"kind": kind, "detail": "boom"}}
