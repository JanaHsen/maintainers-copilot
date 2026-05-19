"""Anthropic API client — STUB (Day 2+).

The layer's final shape is visible from Day 1 (Rule 1) so later days import
it without restructuring ``app/infra/``. Nothing calls this today; every
entry point raises so an accidental Day 1 use fails loud (Rule 9).
"""

from dataclasses import dataclass

_NOT_YET = "anthropic_client is a Day 2+ stub and is not wired on Day 1"


@dataclass(frozen=True)
class CompletionRequest:
    prompt: str
    max_tokens: int = 1024
    model: str = "claude-opus-4-7"


@dataclass(frozen=True)
class CompletionResponse:
    text: str


def complete(request: CompletionRequest) -> CompletionResponse:
    """Send a completion request to the Anthropic API (Day 2+)."""
    raise NotImplementedError(_NOT_YET)
