"""Cover the retry-and-error surface of model_server_client.

These tests exercise the bounded-retry behavior and the typed-error
mapping using ``httpx.MockTransport`` so the production code path is
what's under test, not the I/O wiring. ``time.sleep`` is monkey-patched
to keep retry-driven tests fast.
"""

from __future__ import annotations

from collections.abc import Callable

import httpx
import pytest

from app.infra import model_server_client
from app.infra.model_server_client import (
    ClassificationRequest,
    ClassificationResponse,
    ModelServerError,
    ModelServerInternalError,
    ModelServerInvalidInputError,
    ModelServerTimeoutError,
    ModelServerUnreachableError,
    classify,
)

HandlerType = Callable[[httpx.Request], httpx.Response]


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(model_server_client.time, "sleep", lambda _s: None)


def _install_transport(monkeypatch: pytest.MonkeyPatch, handler: HandlerType) -> None:
    transport = httpx.MockTransport(handler)
    original = httpx.Client

    def make_client(**kwargs: object) -> httpx.Client:
        kwargs["transport"] = transport
        return original(**kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(model_server_client.httpx, "Client", make_client)


def _ok_payload() -> dict[str, object]:
    return {
        "label": "bug",
        "confidence": 0.91,
        "label_scores": {
            "bug": 0.91,
            "docs": 0.05,
            "feature": 0.03,
            "question": 0.01,
        },
    }


def test_classify_returns_response_on_2xx(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json=_ok_payload())

    _install_transport(monkeypatch, handler)

    resp = classify(
        ClassificationRequest(title="bug in groupby", body="see traceback"),
        request_id="req-1",
    )
    assert isinstance(resp, ClassificationResponse)
    assert resp.label == "bug"
    assert resp.confidence == pytest.approx(0.91)
    assert set(resp.label_scores) == {"bug", "docs", "feature", "question"}
    assert len(calls) == 1
    assert calls[0].headers["x-request-id"] == "req-1"


def test_5xx_retries_then_eventually_raises_internal_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[int] = []

    def handler(_request: httpx.Request) -> httpx.Response:
        calls.append(503)
        return httpx.Response(503, text="upstream down")

    _install_transport(monkeypatch, handler)

    with pytest.raises(ModelServerInternalError):
        classify(ClassificationRequest(title="x", body="y"))
    assert len(calls) == model_server_client.MAX_ATTEMPTS


def test_5xx_then_2xx_recovers_via_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    responses = iter(
        [
            httpx.Response(503, text="warm up"),
            httpx.Response(200, json=_ok_payload()),
        ]
    )

    def handler(_request: httpx.Request) -> httpx.Response:
        return next(responses)

    _install_transport(monkeypatch, handler)

    resp = classify(ClassificationRequest(title="x", body="y"))
    assert resp.label == "bug"


def test_4xx_is_not_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[int] = []

    def handler(_request: httpx.Request) -> httpx.Response:
        calls.append(422)
        return httpx.Response(422, json={"detail": "validation"})

    _install_transport(monkeypatch, handler)

    with pytest.raises(ModelServerInvalidInputError):
        classify(ClassificationRequest(title="x", body="y"))
    assert len(calls) == 1


def test_connect_error_retries_then_raises_unreachable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[int] = []

    def handler(_request: httpx.Request) -> httpx.Response:
        calls.append(1)
        raise httpx.ConnectError("no route to host")

    _install_transport(monkeypatch, handler)

    with pytest.raises(ModelServerUnreachableError):
        classify(ClassificationRequest(title="x", body="y"))
    assert len(calls) == model_server_client.MAX_ATTEMPTS


def test_timeout_retries_then_raises_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[int] = []

    def handler(_request: httpx.Request) -> httpx.Response:
        calls.append(1)
        raise httpx.ReadTimeout("read timed out")

    _install_transport(monkeypatch, handler)

    with pytest.raises(ModelServerTimeoutError):
        classify(ClassificationRequest(title="x", body="y"))
    assert len(calls) == model_server_client.MAX_ATTEMPTS


def test_malformed_2xx_response_raises_model_server_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"label": "bug"})  # missing fields

    _install_transport(monkeypatch, handler)

    with pytest.raises(ModelServerError):
        classify(ClassificationRequest(title="x", body="y"))
