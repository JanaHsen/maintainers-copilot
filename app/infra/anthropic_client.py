"""Anthropic API client with a small typed-error surface (Rule 11).

The API key is read from Vault (Rule 2) at call time, not at import, so a
process that doesn't use the client doesn't blow up on missing creds.
The system prompt is marked cacheable so a single hot prompt amortizes
its tokens across the whole request stream (Anthropic prompt caching;
useful for /summarize and the chatbot).
"""

from __future__ import annotations

from typing import Any

import anthropic

from app.infra.vault_client import KEY_ANTHROPIC_API_KEY, read_secrets

DEFAULT_MODEL = "claude-haiku-4-5-20251001"
DEFAULT_MAX_TOKENS = 400


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
