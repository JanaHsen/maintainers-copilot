"""Prove each AnthropicError variant maps to a typed NerOutcome (Rule 11, R7)."""

from __future__ import annotations

import json

import pytest

from app.infra import anthropic_client
from app.services.ner_service import (
    NerError,
    NerOk,
    extract,
)

_GOOD_PAYLOAD = {
    "repo_names": ["pandas-dev/pandas"],
    "file_paths": ["src/io/parsers.py"],
    "error_types": ["ConnectionError"],
    "package_names": ["requests"],
}


def _stub(payload: str) -> object:
    def fake_complete(
        *, system: str, user: str, model: str = "", max_tokens: int = 0
    ) -> str:
        return payload

    return fake_complete


def test_happy_path_parses_strict_json(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        anthropic_client, "complete", _stub(json.dumps(_GOOD_PAYLOAD))
    )
    outcome = extract("some text")
    assert isinstance(outcome, NerOk)
    assert outcome.entities.repo_names == ["pandas-dev/pandas"]
    assert outcome.entities.file_paths == ["src/io/parsers.py"]
    assert outcome.entities.error_types == ["ConnectionError"]
    assert outcome.entities.package_names == ["requests"]


def test_happy_path_strips_code_fence(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = "```json\n" + json.dumps(_GOOD_PAYLOAD) + "\n```"
    monkeypatch.setattr(anthropic_client, "complete", _stub(payload))
    outcome = extract("some text")
    assert isinstance(outcome, NerOk)


def test_prose_response_is_bad_format(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        anthropic_client,
        "complete",
        _stub("Sure, here are the entities you asked for: ..."),
    )
    outcome = extract("some text")
    assert isinstance(outcome, NerError)
    assert outcome.kind == "bad_format"


def test_missing_required_key_is_bad_format(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = json.dumps(
        {"repo_names": [], "file_paths": [], "error_types": []}
    )  # missing package_names
    monkeypatch.setattr(anthropic_client, "complete", _stub(payload))
    outcome = extract("some text")
    assert isinstance(outcome, NerError)
    assert outcome.kind == "bad_format"


def test_non_list_value_is_bad_format(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = json.dumps(
        {
            "repo_names": "not-a-list",
            "file_paths": [],
            "error_types": [],
            "package_names": [],
        }
    )
    monkeypatch.setattr(anthropic_client, "complete", _stub(payload))
    outcome = extract("some text")
    assert isinstance(outcome, NerError)
    assert outcome.kind == "bad_format"


def test_non_string_item_is_bad_format(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = json.dumps(
        {
            "repo_names": [1, 2, 3],
            "file_paths": [],
            "error_types": [],
            "package_names": [],
        }
    )
    monkeypatch.setattr(anthropic_client, "complete", _stub(payload))
    outcome = extract("some text")
    assert isinstance(outcome, NerError)
    assert outcome.kind == "bad_format"


@pytest.mark.parametrize(
    "exc_cls, expected_kind",
    [
        (anthropic_client.AnthropicTimeoutError, "timeout"),
        (anthropic_client.AnthropicUnreachableError, "unreachable"),
        (anthropic_client.AnthropicAuthError, "unreachable"),
        (anthropic_client.AnthropicBadRequestError, "bad_request"),
        (anthropic_client.AnthropicInternalError, "internal"),
        (anthropic_client.AnthropicError, "unexpected"),
    ],
)
def test_anthropic_errors_map_to_typed_outcomes(
    monkeypatch: pytest.MonkeyPatch,
    exc_cls: type[Exception],
    expected_kind: str,
) -> None:
    def boom(
        *, system: str, user: str, model: str = "", max_tokens: int = 0
    ) -> str:
        raise exc_cls("simulated")

    monkeypatch.setattr(anthropic_client, "complete", boom)
    outcome = extract("some text")
    assert isinstance(outcome, NerError)
    assert outcome.kind == expected_kind
