"""Prove typed exception mapping for anthropic_client.complete."""

from __future__ import annotations

from typing import Any

import anthropic
import httpx
import pytest

from app.infra import anthropic_client


@pytest.fixture(autouse=True)
def _api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(anthropic_client, "_read_api_key", lambda: "sk-ant-test")


class _FakeMessages:
    def __init__(self, behavior: Any) -> None:
        self._behavior = behavior

    def create(self, **_kwargs: Any) -> Any:
        if callable(self._behavior):
            return self._behavior()
        return self._behavior


class _FakeClient:
    def __init__(self, behavior: Any) -> None:
        self.messages = _FakeMessages(behavior)


def _install_fake_client(monkeypatch: pytest.MonkeyPatch, behavior: Any) -> None:
    monkeypatch.setattr(
        anthropic_client.anthropic, "Anthropic", lambda **_k: _FakeClient(behavior)
    )


class _Block:
    def __init__(self, text: str) -> None:
        self.text = text


class _Response:
    def __init__(self, text: str) -> None:
        self.content = [_Block(text)]


def _request() -> httpx.Request:
    return httpx.Request("POST", "https://api.anthropic.com/v1/messages")


def _response(status: int) -> httpx.Response:
    return httpx.Response(status_code=status, request=_request())


def test_complete_returns_first_block_text(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_client(monkeypatch, _Response("hello there"))
    assert anthropic_client.complete(system="s", user="u") == "hello there"


def test_auth_error_maps_to_anthropic_auth_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom() -> None:
        raise anthropic.AuthenticationError(
            message="bad key", response=_response(401), body=None
        )

    _install_fake_client(monkeypatch, boom)
    with pytest.raises(anthropic_client.AnthropicAuthError):
        anthropic_client.complete(system="s", user="u")


def test_rate_limit_error_maps(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom() -> None:
        raise anthropic.RateLimitError(
            message="slow down", response=_response(429), body=None
        )

    _install_fake_client(monkeypatch, boom)
    with pytest.raises(anthropic_client.AnthropicRateLimitError):
        anthropic_client.complete(system="s", user="u")


def test_connection_error_maps(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom() -> None:
        raise anthropic.APIConnectionError(request=_request())

    _install_fake_client(monkeypatch, boom)
    with pytest.raises(anthropic_client.AnthropicUnreachableError):
        anthropic_client.complete(system="s", user="u")


def test_timeout_error_maps(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom() -> None:
        raise anthropic.APITimeoutError(request=_request())

    _install_fake_client(monkeypatch, boom)
    with pytest.raises(anthropic_client.AnthropicTimeoutError):
        anthropic_client.complete(system="s", user="u")


def test_bad_request_error_maps(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom() -> None:
        raise anthropic.BadRequestError(
            message="bad request", response=_response(400), body=None
        )

    _install_fake_client(monkeypatch, boom)
    with pytest.raises(anthropic_client.AnthropicBadRequestError):
        anthropic_client.complete(system="s", user="u")


def test_internal_server_error_maps(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom() -> None:
        raise anthropic.InternalServerError(
            message="upstream broken", response=_response(500), body=None
        )

    _install_fake_client(monkeypatch, boom)
    with pytest.raises(anthropic_client.AnthropicInternalError):
        anthropic_client.complete(system="s", user="u")
