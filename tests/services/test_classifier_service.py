"""Prove each typed model_server_client exception becomes a typed outcome (Rule 11)."""

from __future__ import annotations

import pytest

from app.infra import model_server_client
from app.services.classifier_service import (
    ClassifyError,
    ClassifyOk,
    classify_issue,
)


def test_happy_path_returns_classify_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        model_server_client,
        "classify",
        lambda _req, *, request_id="": model_server_client.ClassificationResponse(
            label="bug",
            confidence=0.9,
            label_scores={"bug": 0.9, "docs": 0.05, "feature": 0.03, "question": 0.02},
        ),
    )
    outcome = classify_issue("title", "body")
    assert isinstance(outcome, ClassifyOk)
    assert outcome.label == "bug"


@pytest.mark.parametrize(
    "exc, expected_kind",
    [
        (model_server_client.ModelServerUnreachableError("down"), "unreachable"),
        (model_server_client.ModelServerTimeoutError("slow"), "timeout"),
        (model_server_client.ModelServerInvalidInputError("bad"), "bad_request"),
        (model_server_client.ModelServerInternalError("oops"), "internal"),
    ],
)
def test_typed_exceptions_map_to_typed_outcome(
    monkeypatch: pytest.MonkeyPatch,
    exc: Exception,
    expected_kind: str,
) -> None:
    def boom(_req: object, *, request_id: str = "") -> object:
        raise exc

    monkeypatch.setattr(model_server_client, "classify", boom)
    outcome = classify_issue("title", "body")
    assert isinstance(outcome, ClassifyError)
    assert outcome.kind == expected_kind


def test_unexpected_model_server_error_maps_to_unexpected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom(_req: object, *, request_id: str = "") -> object:
        raise model_server_client.ModelServerError("malformed response")

    monkeypatch.setattr(model_server_client, "classify", boom)
    outcome = classify_issue("title", "body")
    assert isinstance(outcome, ClassifyError)
    assert outcome.kind == "unexpected"
