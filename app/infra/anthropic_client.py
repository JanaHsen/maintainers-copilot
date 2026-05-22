"""Anthropic API client with a small typed-error surface (Rule 11).

The API key is read from Vault (Rule 2) at call time, not at import, so a
process that doesn't use the client doesn't blow up on missing creds.
The system prompt is marked cacheable so a single hot prompt amortizes
its tokens across the whole request stream (Anthropic prompt caching;
useful for /summarize and the chatbot).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import anthropic

from app.infra.vault_client import KEY_ANTHROPIC_API_KEY, read_secrets

DEFAULT_MODEL = "claude-haiku-4-5-20251001"
DEFAULT_MAX_TOKENS = 400
TOOL_USE_DEFAULT_MODEL = "claude-sonnet-4-5-20250929"
TOOL_USE_DEFAULT_MAX_TOKENS = 1024


class AnthropicError(RuntimeError):
    """Base for any Anthropic call failure."""


class AnthropicAuthError(AnthropicError):
    """Missing or invalid API key; surface as 503 (config error)."""


class AnthropicRateLimitError(AnthropicError):
    """Rate-limited by Anthropic; surface as 429."""


class AnthropicUnreachableError(AnthropicError):
    """Network error reaching api.anthropic.com."""


class AnthropicTimeoutError(AnthropicError):
    """Request timed out."""


class AnthropicBadRequestError(AnthropicError):
    """4xx from the API (bad model, bad messages); not retryable."""


class AnthropicInternalError(AnthropicError):
    """5xx from the API after the SDK's internal retries."""


def _read_api_key() -> str:
    key = read_secrets([KEY_ANTHROPIC_API_KEY])[KEY_ANTHROPIC_API_KEY]
    if not key:
        raise AnthropicAuthError(
            "anthropic_api_key is empty in Vault; /summarize will not work"
        )
    return key


def complete(
    *,
    system: str,
    user: str,
    model: str = DEFAULT_MODEL,
    max_tokens: int = DEFAULT_MAX_TOKENS,
) -> str:
    """Send a single user turn with a cached system prompt; return the text."""
    api_key = _read_api_key()
    client = anthropic.Anthropic(api_key=api_key)
    system_blocks: list[dict[str, Any]] = [
        {
            "type": "text",
            "text": system,
            "cache_control": {"type": "ephemeral"},
        }
    ]
    try:
        resp = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system_blocks,  # type: ignore[arg-type]
            messages=[{"role": "user", "content": user}],
        )
    except anthropic.AuthenticationError as exc:
        raise AnthropicAuthError(f"anthropic auth failed: {exc}") from exc
    except anthropic.RateLimitError as exc:
        raise AnthropicRateLimitError(f"anthropic rate limited: {exc}") from exc
    except anthropic.APITimeoutError as exc:
        raise AnthropicTimeoutError(f"anthropic timeout: {exc}") from exc
    except anthropic.APIConnectionError as exc:
        raise AnthropicUnreachableError(f"anthropic unreachable: {exc}") from exc
    except anthropic.BadRequestError as exc:
        raise AnthropicBadRequestError(f"anthropic rejected request: {exc}") from exc
    except anthropic.InternalServerError as exc:
        raise AnthropicInternalError(f"anthropic 5xx: {exc}") from exc
    except anthropic.APIStatusError as exc:
        raise AnthropicError(f"anthropic api error: {exc}") from exc

    if not resp.content:
        raise AnthropicError("anthropic returned empty content")
    block = resp.content[0]
    text = getattr(block, "text", None)
    if not isinstance(text, str):
        raise AnthropicError("anthropic first content block has no text")
    return text


@dataclass(frozen=True)
class ToolUseBlock:
    """One ``tool_use`` content block parsed out of an Anthropic response.

    The SDK returns these as opaque objects; we project them to a plain
    dataclass so the chatbot service (Part 2) can introspect the model's
    tool calls without depending on the SDK's internal types.
    """

    id: str
    name: str
    input: dict[str, Any]


@dataclass(frozen=True)
class ToolUseResponse:
    """Typed wrapper around an Anthropic tool-use response.

    Fields:

    * ``stop_reason`` — one of ``"end_turn"``, ``"tool_use"``,
      ``"max_tokens"``, ``"stop_sequence"`` (SDK union).
    * ``text`` — concatenation of any text blocks in ``content`` (empty
      string if the response is all tool_use blocks).
    * ``tool_use_blocks`` — every tool_use block the model emitted, in order.
    * ``usage_input_tokens`` / ``usage_output_tokens`` — raw token counts
      from ``resp.usage`` for cost accounting + span attributes.
    * ``raw`` — the SDK Message object, exposed for tests/inspect. Typed
      ``Any`` deliberately so callers cannot couple to SDK internals.
    """

    stop_reason: str
    text: str
    tool_use_blocks: list[ToolUseBlock] = field(default_factory=list)
    usage_input_tokens: int = 0
    usage_output_tokens: int = 0
    raw: Any = None


def tool_use_chat(
    *,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    system: str,
    model: str = TOOL_USE_DEFAULT_MODEL,
    max_tokens: int = TOOL_USE_DEFAULT_MAX_TOKENS,
) -> ToolUseResponse:
    """Drive the Anthropic tool-use API and return a typed wrapper.

    Mirrors :func:`complete` for error handling (six SDK errors mapped to the
    ``AnthropicError`` family). The system prompt is cached the same way so
    a chat session amortizes the system-prompt tokens across every turn.

    The caller (chatbot service) inspects ``stop_reason`` + ``tool_use_blocks``
    to decide whether to dispatch tools and re-enter the loop, or end the
    turn and persist the assistant message.
    """
    api_key = _read_api_key()
    client = anthropic.Anthropic(api_key=api_key)
    system_blocks: list[dict[str, Any]] = [
        {
            "type": "text",
            "text": system,
            "cache_control": {"type": "ephemeral"},
        }
    ]
    try:
        resp = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system_blocks,  # type: ignore[arg-type]
            tools=tools,  # type: ignore[arg-type]
            messages=messages,  # type: ignore[arg-type]
        )
    except anthropic.AuthenticationError as exc:
        raise AnthropicAuthError(f"anthropic auth failed: {exc}") from exc
    except anthropic.RateLimitError as exc:
        raise AnthropicRateLimitError(f"anthropic rate limited: {exc}") from exc
    except anthropic.APITimeoutError as exc:
        raise AnthropicTimeoutError(f"anthropic timeout: {exc}") from exc
    except anthropic.APIConnectionError as exc:
        raise AnthropicUnreachableError(f"anthropic unreachable: {exc}") from exc
    except anthropic.BadRequestError as exc:
        raise AnthropicBadRequestError(f"anthropic rejected request: {exc}") from exc
    except anthropic.InternalServerError as exc:
        raise AnthropicInternalError(f"anthropic 5xx: {exc}") from exc
    except anthropic.APIStatusError as exc:
        raise AnthropicError(f"anthropic api error: {exc}") from exc

    stop_reason = getattr(resp, "stop_reason", None)
    if not isinstance(stop_reason, str):
        raise AnthropicError("anthropic response missing stop_reason")

    text_parts: list[str] = []
    tool_use_blocks: list[ToolUseBlock] = []
    for block in resp.content or []:
        block_type = getattr(block, "type", None)
        if block_type == "text":
            block_text = getattr(block, "text", "")
            if isinstance(block_text, str):
                text_parts.append(block_text)
        elif block_type == "tool_use":
            block_id = getattr(block, "id", None)
            block_name = getattr(block, "name", None)
            block_input = getattr(block, "input", None)
            if (
                not isinstance(block_id, str)
                or not isinstance(block_name, str)
                or not isinstance(block_input, dict)
            ):
                raise AnthropicError(
                    "anthropic tool_use block missing id/name/input"
                )
            tool_use_blocks.append(
                ToolUseBlock(
                    id=block_id,
                    name=block_name,
                    input=dict(block_input),
                )
            )

    usage = getattr(resp, "usage", None)
    usage_input = int(getattr(usage, "input_tokens", 0) or 0)
    usage_output = int(getattr(usage, "output_tokens", 0) or 0)

    return ToolUseResponse(
        stop_reason=stop_reason,
        text="".join(text_parts),
        tool_use_blocks=tool_use_blocks,
        usage_input_tokens=usage_input,
        usage_output_tokens=usage_output,
        raw=resp,
    )
