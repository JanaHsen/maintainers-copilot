"""Deterministic regex-based code-entity extractor for /ner.

Approach picked over a pre-trained NER model: pandas issue text is
dominated by code-shaped tokens (function calls, exception names, dotted
module paths) that regular expressions catch precisely; a general-purpose
NER model trained on news/Wikipedia would mis-tokenize identifiers like
``df.groupby`` and add a multi-hundred-MB dep for noisier output. The
precision number on a hand-curated sample is recorded in DECISIONS.md;
if it drops below 0.7 the decision is revisited (slice (e) of the Day 2
plan).

Three entity types:

  * ``exception_class`` — PascalCase identifier ending in
    ``Error`` / ``Exception`` / ``Warning`` (``IndexError``,
    ``UserWarning``).
  * ``function_call`` — an identifier (optionally dotted) followed by
    ``(`` (``len``, ``df.groupby``, ``pd.DataFrame``).
  * ``module_path`` — at least two dot-joined lowercase identifiers
    *not* followed by ``(`` (``pandas.core.indexing``, ``numpy.ndarray``).

Overlapping matches are resolved by priority order
(``exception_class`` > ``function_call`` > ``module_path``) so e.g.
``IndexError(...)`` produces a single ``exception_class`` entity, not a
``function_call`` for the bare name.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

EntityType = Literal["exception_class", "function_call", "module_path"]


@dataclass(frozen=True)
class CodeEntity:
    text: str
    type: EntityType
    start: int
    end: int


# Each pattern captures the entity span in group 1. Order in the list is
# the priority order used by `extract` to break overlaps.
_PATTERNS: list[tuple[EntityType, re.Pattern[str]]] = [
    (
        "exception_class",
        re.compile(r"\b([A-Z][A-Za-z0-9]*(?:Error|Exception|Warning))\b"),
    ),
    (
        "function_call",
        # name or dotted.name immediately followed by `(`.
        re.compile(r"\b([A-Za-z_][A-Za-z_0-9]*(?:\.[A-Za-z_][A-Za-z_0-9]*)*)\("),
    ),
    (
        "module_path",
        # 2+ dot-joined lowercase identifiers, NOT followed by `(`
        # (otherwise it's a function/method call, handled above).
        re.compile(r"\b([a-z][a-z_0-9]*(?:\.[a-z][a-z_0-9]*){1,})\b(?!\()"),
    ),
]


def _overlaps(a_start: int, a_end: int, taken: list[tuple[int, int]]) -> bool:
    for t_start, t_end in taken:
        if a_start < t_end and t_start < a_end:
            return True
    return False


def extract(text: str) -> list[CodeEntity]:
    """Return code entities in ``text``, deduped and ordered by start offset."""
    taken_spans: list[tuple[int, int]] = []
    out: list[CodeEntity] = []
    for entity_type, pattern in _PATTERNS:
        for match in pattern.finditer(text):
            start, end = match.span(1)
            if _overlaps(start, end, taken_spans):
                continue
            taken_spans.append((start, end))
            out.append(
                CodeEntity(
                    text=match.group(1),
                    type=entity_type,
                    start=start,
                    end=end,
                )
            )
    out.sort(key=lambda e: e.start)
    return out
