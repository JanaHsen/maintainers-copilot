"""HyDE query transformation with raw-question fallback (FR-017).

Generates a hypothetical answer to the maintainer's question via the
Anthropic API; the embedding model then sees document-shaped vocabulary
(code identifiers, API names, error class names) rather than the
question's natural-English form. Research has it as the default
transformation when query/document vocabularies differ.

Wired into retrieve_service in Phase 5 (T034) behind the same
must-beat-baseline gate as the other advanced choices. The Phase-4
MVP service calls embedding_client directly with the raw question.

Fallback contract (FR-017): if the HyDE generation produces text below
``HYDE_MIN_LENGTH`` or raises any ``AnthropicError``, the caller embeds
the raw question instead and the fallback is logged with the trace id
so the eval gate can count fallback frequency.
"""

from __future__ import annotations

import logging
from pathlib import Path

from app.infra import anthropic_client
from app.infra.anthropic_client import AnthropicError

logger = logging.getLogger("app.services.hyde")

# Generation shorter than this is treated as a refusal / unusable
# (Anthropic occasionally returns "I cannot answer that"-shaped output
# for ambiguous questions; embedding that vector would poison retrieval).
HYDE_MIN_LENGTH = 30

# The committed system+user prompt lives at the repo root so the model
# server's prompt-loading helper can read it the same way it reads
# prompts/summarizer.md.
HYDE_PROMPT_PATH = Path(__file__).resolve().parents[2] / "prompts" / "hyde.md"


def transform(question: str) -> tuple[str, bool]:
    """Return (text_to_embed, hyde_applied).

    `hyde_applied=True` if a Claude generation succeeded and met the
    length floor; `hyde_applied=False` if we fell back to the raw
    question (either by generation failure or by length-below-floor).
    """
    if not HYDE_PROMPT_PATH.exists():
        logger.warning(
            "HyDE prompt missing at %s; falling back to raw question",
            HYDE_PROMPT_PATH,
        )
        return question, False
    try:
        system, user_template = _load_prompt()
    except Exception as exc:  # noqa: BLE001 — never let prompt parsing kill retrieve
        logger.warning("HyDE prompt load failed: %s; falling back", exc)
        return question, False

    user_message = user_template.replace("{{question}}", question)
    try:
        text = anthropic_client.complete(system=system, user=user_message)
    except AnthropicError as exc:
        logger.info("HyDE generation failed (%s); falling back to raw question", exc)
        return question, False

    if len(text.strip()) < HYDE_MIN_LENGTH:
        logger.info(
            "HyDE generation below length floor (%d < %d); falling back",
            len(text.strip()),
            HYDE_MIN_LENGTH,
        )
        return question, False

    return text.strip(), True


def _load_prompt() -> tuple[str, str]:
    """Parse prompts/hyde.md into (system, user_template) — mirror of model_server.prompts."""
    raw = HYDE_PROMPT_PATH.read_text(encoding="utf-8")
    # Same two-section convention as prompts/summarizer.md.
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
        raise ValueError(
            f"{HYDE_PROMPT_PATH} missing required '## System' / '## User' sections"
        )
    return sections["system"], sections["user"]
