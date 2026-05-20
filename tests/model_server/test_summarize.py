"""Smoke the /summarize router: prompt loads, anthropic errors map to HTTP."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.infra import anthropic_client
from model_server.routers import summarize as summarize_router


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    app = FastAPI()
    app.include_router(summarize_router.router)
    return TestClient(app)


def _payload(comments: str | None = None) -> dict[str, Any]:
    body: dict[str, Any] = {"title": "t", "body": "b"}
    if comments is not None:
        body["comments"] = comments
    return body


def test_returns_200_with_summary_text(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        anthropic_client,
        "complete",
        lambda *, system, user: "this is the summary",  # noqa: ARG005
    )
    resp = client.post("/summarize", json=_payload())
    assert resp.status_code == 200
    assert resp.json() == {"summary": "this is the summary"}


def test_auth_error_returns_503(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*, system: str, user: str) -> str:
        raise anthropic_client.AnthropicAuthError("no key")

    monkeypatch.setattr(anthropic_client, "complete", boom)
    resp = client.post("/summarize", json=_payload())
    assert resp.status_code == 503


def test_rate_limit_returns_429(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*, system: str, user: str) -> str:
        raise anthropic_client.AnthropicRateLimitError("slow down")

    monkeypatch.setattr(anthropic_client, "complete", boom)
    resp = client.post("/summarize", json=_payload())
    assert resp.status_code == 429


def test_timeout_returns_504(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*, system: str, user: str) -> str:
        raise anthropic_client.AnthropicTimeoutError("slow")

    monkeypatch.setattr(anthropic_client, "complete", boom)
    resp = client.post("/summarize", json=_payload())
    assert resp.status_code == 504


def test_unreachable_returns_503(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*, system: str, user: str) -> str:
        raise anthropic_client.AnthropicUnreachableError("dns down")

    monkeypatch.setattr(anthropic_client, "complete", boom)
    resp = client.post("/summarize", json=_payload())
    assert resp.status_code == 503


def test_bad_request_returns_502(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*, system: str, user: str) -> str:
        raise anthropic_client.AnthropicBadRequestError("bad input")

    monkeypatch.setattr(anthropic_client, "complete", boom)
    resp = client.post("/summarize", json=_payload())
    assert resp.status_code == 502


def test_comments_section_threaded_into_user_message(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, str] = {}

    def capture(*, system: str, user: str) -> str:
        captured["user"] = user
        return "ok"

    monkeypatch.setattr(anthropic_client, "complete", capture)
    client.post("/summarize", json=_payload(comments="the comments"))
    assert "Comments excerpt:\nthe comments" in captured["user"]


def test_no_comments_skips_comments_section(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, str] = {}

    def capture(*, system: str, user: str) -> str:
        captured["user"] = user
        return "ok"

    monkeypatch.setattr(anthropic_client, "complete", capture)
    client.post("/summarize", json=_payload())
    assert "Comments excerpt" not in captured["user"]
