"""Prove tool_use_chat parses Anthropic tool-use responses + maps SDK errors.

The SDK's Message / ContentBlock / Usage objects are awkward to fake at the
``httpx`` transport layer (nested Pydantic models, version-sensitive shape),
so we stub the SDK client directly the same way ``test_anthropic_client``
already does for :func:`anthropic_client.complete`.
"""

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


class _TextBlock:
    """Mimics the SDK's TextBlock: ``type='text'`` + ``text``."""

    type = "text"

    def __init__(self, text: str) -> None:
        self.text = text


class _ToolUseBlock:
    """Mimics the SDK's ToolUseBlock: ``type='tool_use'`` + id/name/input."""

    type = "tool_use"

    def __init__(self, *, id: str, name: str, input: dict[str, Any]) -> None:
        self.id = id
        self.name = name
        self.input = input


class _Usage:
    def __init__(self, input_tokens: int, output_tokens: int) -> None:
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens


class _Message:
    def __init__(
        self,
        *,
        stop_reason: str,
        content: list[Any],
        usage: _Usage | None = None,
    ) -> None:
        self.stop_reason = stop_reason
        self.content = content
        self.usage = usage if usage is not None else _Usage(0, 0)


def _request() -> httpx.Request:
    return httpx.Request("POST", "https://api.anthropic.com/v1/messages")


def _response(status: int) -> httpx.Response:
    return httpx.Response(status_code=status, request=_request())


# --- happy paths ----------------------------------------------------------


def test_end_turn_returns_concatenated_text(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_client(
        monkeypatch,
        _Message(
            stop_reason="end_turn",
            content=[_TextBlock("hello there")],
            usage=_Usage(11, 22),
        ),
    )
    out = anthropic_client.tool_use_chat(
        messages=[{"role": "user", "content": "hi"}],
        tools=[],
        system="sys prompt",
    )
    assert out.stop_reason == "end_turn"
    assert out.text == "hello there"
    assert out.tool_use_blocks == []
    assert out.usage_input_tokens == 11
    assert out.usage_output_tokens == 22
    assert out.raw is not None


def test_tool_use_block_parsed(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_client(
        monkeypatch,
        _Message(
            stop_reason="tool_use",
            content=[
                _TextBlock("I'll look that up."),
                _ToolUseBlock(
                    id="toolu_01",
                    name="classify_issue",
                    input={"title": "T", "body": "B"},
                ),
            ],
        ),
    )
    out = anthropic_client.tool_use_chat(
        messages=[{"role": "user", "content": "classify this"}],
        tools=[{"name": "classify_issue", "description": "x", "input_schema": {}}],
        system="sys prompt",
    )
    assert out.stop_reason == "tool_use"
    assert out.text == "I'll look that up."
    assert len(out.tool_use_blocks) == 1
    blk = out.tool_use_blocks[0]
    assert blk.id == "toolu_01"
    assert blk.name == "classify_issue"
    assert blk.input == {"title": "T", "body": "B"}


# --- error mapping (same six as complete()) -------------------------------


def test_auth_error_maps(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom() -> None:
        raise anthropic.AuthenticationError(
            message="bad key", response=_response(401), body=None
        )

    _install_fake_client(monkeypatch, boom)
    with pytest.raises(anthropic_client.AnthropicAuthError):
        anthropic_client.tool_use_chat(messages=[], tools=[], system="s")


def test_rate_limit_error_maps(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom() -> None:
        raise anthropic.RateLimitError(
            message="slow", response=_response(429), body=None
        )

    _install_fake_client(monkeypatch, boom)
    with pytest.raises(anthropic_client.AnthropicRateLimitError):
        anthropic_client.tool_use_chat(messages=[], tools=[], system="s")


def test_connection_error_maps(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom() -> None:
        raise anthropic.APIConnectionError(request=_request())

    _install_fake_client(monkeypatch, boom)
    with pytest.raises(anthropic_client.AnthropicUnreachableError):
        anthropic_client.tool_use_chat(messages=[], tools=[], system="s")


def test_timeout_error_maps(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom() -> None:
        raise anthropic.APITimeoutError(request=_request())

    _install_fake_client(monkeypatch, boom)
    with pytest.raises(anthropic_client.AnthropicTimeoutError):
        anthropic_client.tool_use_chat(messages=[], tools=[], system="s")


def test_bad_request_error_maps(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom() -> None:
        raise anthropic.BadRequestError(
            message="bad", response=_response(400), body=None
        )

    _install_fake_client(monkeypatch, boom)
    with pytest.raises(anthropic_client.AnthropicBadRequestError):
        anthropic_client.tool_use_chat(messages=[], tools=[], system="s")


def test_internal_server_error_maps(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom() -> None:
        raise anthropic.InternalServerError(
            message="upstream broken", response=_response(500), body=None
        )

    _install_fake_client(monkeypatch, boom)
    with pytest.raises(anthropic_client.AnthropicInternalError):
        anthropic_client.tool_use_chat(messages=[], tools=[], system="s")
