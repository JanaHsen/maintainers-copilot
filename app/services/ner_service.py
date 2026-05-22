"""NER orchestration: Anthropic Sonnet 4 → strict 4-bucket JSON → typed outcome.

Per Rule 11 the service never raises out to the router — Anthropic
failures are translated into a typed :class:`NerError`. Per Rule 1 the
router consumes this outcome and chooses the HTTP status; the service
owns the Anthropic call AND the JSON-parsing step that the strict-JSON
prompt (research R7) requires.

Model pin: ``claude-sonnet-4-5-20250929`` (R7).
Prompt pin: ``prompts/ner.md`` (versioned header on line 1).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from app.domain.ner import EntityBuckets
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

logger = logging.getLogger("app.services.ner")

NER_MODEL = "claude-sonnet-4-5-20250929"
NER_MAX_TOKENS = 1024

_PROMPT_PATH = Path(__file__).resolve().parents[2] / "prompts" / "ner.md"
_REQUIRED_KEYS: tuple[str, ...] = (
    "repo_names",
    "file_paths",
    "error_types",
    "package_names",
)


NerErrorKind = Literal[
    "bad_request",
    "bad_format",
    "internal",
    "unreachable",
    "timeout",
    "unexpected",
]


@dataclass(frozen=True)
class NerOk:
    entities: EntityBuckets


@dataclass(frozen=True)
class NerError:
    kind: NerErrorKind
    detail: str


NerOutcome = NerOk | NerError


def _load_prompt() -> str:
    """Read prompts/ner.md verbatim — used as the system prompt."""
    return _PROMPT_PATH.read_text(encoding="utf-8")


def _parse_buckets(raw: str) -> EntityBuckets | None:
    """Return ``EntityBuckets`` if ``raw`` is the strict JSON shape, else ``None``.

    Accepts a leading code fence defensively even though the prompt
    forbids it — Anthropic occasionally wraps strict-JSON output. The
    parser strips one fence at most; anything else is a bad_format.
    """
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()
    try:
        data: Any = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    for key in _REQUIRED_KEYS:
        if key not in data:
            return None
        value = data[key]
        if not isinstance(value, list):
            return None
        for item in value:
            if not isinstance(item, str):
                return None
    return EntityBuckets(
        repo_names=list(data["repo_names"]),
        file_paths=list(data["file_paths"]),
        error_types=list(data["error_types"]),
        package_names=list(data["package_names"]),
    )


def extract(text: str, *, request_id: str = "") -> NerOutcome:
    """Call Anthropic Sonnet 4 with the NER prompt; return a typed outcome."""
    del request_id  # reserved for future structured-log fields (Rule 7).
    prompt = _load_prompt()
    try:
        raw = anthropic_client.complete(
            system=prompt,
            user=text,
            model=NER_MODEL,
            max_tokens=NER_MAX_TOKENS,
        )
    except AnthropicTimeoutError as exc:
        return NerError(kind="timeout", detail=redact(str(exc)))
    except AnthropicUnreachableError as exc:
        return NerError(kind="unreachable", detail=redact(str(exc)))
    except AnthropicAuthError as exc:
        # Auth/config issues surface as 503 (same as unreachable in our
        # contract — the operator cannot reach the API at all).
        return NerError(kind="unreachable", detail=redact(str(exc)))
    except AnthropicBadRequestError as exc:
        return NerError(kind="bad_request", detail=redact(str(exc)))
    except AnthropicInternalError as exc:
        return NerError(kind="internal", detail=redact(str(exc)))
    except AnthropicError as exc:
        return NerError(kind="unexpected", detail=redact(str(exc)))

    buckets = _parse_buckets(raw)
    if buckets is None:
        logger.warning(
            "ner_service: bad_format from anthropic (len=%d)", len(raw or "")
        )
        return NerError(
            kind="bad_format",
            detail="anthropic response did not match the 4-bucket JSON schema",
        )
    return NerOk(entities=buckets)
