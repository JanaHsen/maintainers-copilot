"""Prove each AnthropicError variant maps to a typed SummarizeOutcome (Rule 11)."""

from __future__ import annotations

import pytest

from app.infra import anthropic_client
from app.services.summarize_service import (
    SummarizeError,
    SummarizeOk,
    summarize,
)


def _stub(payload: str) -> object:
    def fake_complete(
        *, system: str, user: str, model: str = "", max_tokens: int = 0
    ) -> str:
        return payload

    return fake_complete


def test_happy_path_returns_summary(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        anthropic_client,
        "complete",
        _stub("The user reports a regression in pd.read_csv on 2.0."),
    )
    outcome = summarize("some issue text")
    assert isinstance(outcome, SummarizeOk)
    assert outcome.summary.startswith("The user reports")


def test_whitespace_response_is_unexpected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(anthropic_client, "complete", _stub("   \n  "))
    outcome = summarize("some issue text")
    assert isinstance(outcome, SummarizeError)
    assert outcome.kind == "unexpected"
    assert "empty summary" in outcome.detail


def test_empty_response_is_unexpected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(anthropic_client, "complete", _stub(""))
    outcome = summarize("some issue text")
    assert isinstance(outcome, SummarizeError)
    assert outcome.kind == "unexpected"


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
    outcome = summarize("some issue text")
    assert isinstance(outcome, SummarizeError)
    assert outcome.kind == expected_kind
