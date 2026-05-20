"""Load a versioned markdown prompt (system + user) at request time.

The model server's /summarize uses this to keep prompt text out of code
(Rule 9 — prompts are first-class artifacts). The file format is two
``## System`` / ``## User`` sections; the loader returns the two bodies
and the caller does any ``{{placeholder}}`` substitution.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

_SECTION_HEADER = re.compile(r"^## ", flags=re.MULTILINE)


class PromptFormatError(ValueError):
    """Prompt file is missing a required section."""


@lru_cache
def load_system_user(path: Path) -> tuple[str, str]:
    """Read ``path`` and return ``(system_prompt, user_template)`` bodies."""
    content = path.read_text(encoding="utf-8")
    sections: dict[str, str] = {}
    parts = _SECTION_HEADER.split(content)
    # parts[0] is the preamble before any header; ignore it.
    for chunk in parts[1:]:
        header, _, body = chunk.partition("\n")
        sections[header.strip().lower()] = body.strip()
    if "system" not in sections or "user" not in sections:
        raise PromptFormatError(
            f"{path} missing required '## System' and/or '## User' sections"
        )
    return sections["system"], sections["user"]


def render(template: str, **values: str) -> str:
    """Replace ``{{key}}`` placeholders with the corresponding value."""
    out = template
    for key, value in values.items():
        out = out.replace(f"{{{{{key}}}}}", value)
    return out
