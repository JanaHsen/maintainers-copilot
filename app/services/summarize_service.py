"""Summarize orchestration: Anthropic Haiku → plain-text summary → typed outcome.

Per Rule 11 the service never raises out to the router — Anthropic
failures are translated into a typed :class:`SummarizeError`. Per Rule
1 the router consumes this outcome and chooses the HTTP status; the
service owns the Anthropic call.

The summarizer prompt at ``prompts/summarizer.md`` is the existing
two-section markdown the model server already uses. It contains
``{{title}}``, ``{{body}}``, ``{{comments_section}}`` placeholders;
this service substitutes the input ``text`` into ``{{body}}`` and
leaves the other two empty so a single-string call still produces a
coherent summary.

Model pin: ``claude-haiku-4-5-20251001`` (anthropic_client default).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from app.infra import anthropic_client
from app.infra.anthropic_client import (
    AnthropicAuthError,
    AnthropicBadRequestError,
    AnthropicError,
    AnthropicInternalError,
    AnthropicTimeoutError,
    AnthropicUnreachableError,
)
from app.infra.log_redaction import redact

logger = logging.getLogger("app.services.summarize")

SUMMARIZE_MODEL = "claude-haiku-4-5-20251001"
SUMMARIZE_MAX_TOKENS = 400

_PROMPT_PATH = Path(__file__).resolve().parents[2] / "prompts" / "summarizer.md"


SummarizeErrorKind = Literal[
    "bad_request",
    "internal",
    "unreachable",
    "timeout",
    "unexpected",
]


@dataclass(frozen=True)
class SummarizeOk:
    summary: str


@dataclass(frozen=True)
class SummarizeError:
    kind: SummarizeErrorKind
    detail: str


SummarizeOutcome = SummarizeOk | SummarizeError


def _split_prompt(raw: str) -> tuple[str, str]:
    """Parse the two-section prompt into (system, user_template).

    The file's structure is the same as ``prompts/rag_answer.md`` etc:
    ``## System`` / ``## User`` headers each followed by a body block.
    """
    sections: dict[str, str] = {}
    current: str | None = None
    buf: list[str] = []
    for line in raw.splitlines():
        if line.startswith("## "):
            if current is not None:
                sections[current] = "\n".join(buf).strip()
            current = line[3:].strip().lower()
            buf = []
        elif current is not None:
            buf.append(line)
    if current is not None:
        sections[current] = "\n".join(buf).strip()
    if "system" not in sections or "user" not in sections:
        raise RuntimeError(
            "prompts/summarizer.md missing '## System' / '## User' sections"
        )
    return sections["system"], sections["user"]


def _load_prompt() -> tuple[str, str]:
    return _split_prompt(_PROMPT_PATH.read_text(encoding="utf-8"))


def summarize(
    text: str, *, max_sentences: int = 3, request_id: str = ""
) -> SummarizeOutcome:
    """Call Anthropic Haiku with the summarizer prompt; return a typed outcome."""
    del request_id  # reserved for structured-log fields (Rule 7).
    del max_sentences  # the existing prompt covers 1-3 sentences; honoring the
    # request-level hint is a future-extension hook and intentionally a no-op
    # in Part 1 so the prompt stays the source of truth.

    system, user_template = _load_prompt()
    user = (
        user_template.replace("{{title}}", "")
        .replace("{{body}}", text)
        .replace("{{comments_section}}", "")
    )

    try:
        raw = anthropic_client.complete(
            system=system,
            user=user,
            model=SUMMARIZE_MODEL,
            max_tokens=SUMMARIZE_MAX_TOKENS,
        )
    except AnthropicTimeoutError as exc:
        return SummarizeError(kind="timeout", detail=redact(str(exc)))
    except AnthropicUnreachableError as exc:
        return SummarizeError(kind="unreachable", detail=redact(str(exc)))
    except AnthropicAuthError as exc:
        return SummarizeError(kind="unreachable", detail=redact(str(exc)))
    except AnthropicBadRequestError as exc:
        return SummarizeError(kind="bad_request", detail=redact(str(exc)))
    except AnthropicInternalError as exc:
        return SummarizeError(kind="internal", detail=redact(str(exc)))
    except AnthropicError as exc:
        return SummarizeError(kind="unexpected", detail=redact(str(exc)))

    summary = raw.strip()
    if not summary:
        return SummarizeError(kind="unexpected", detail="empty summary returned")
    return SummarizeOk(summary=summary)
