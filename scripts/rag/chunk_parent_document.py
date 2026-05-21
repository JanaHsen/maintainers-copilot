"""Parent-document chunking for the RAG corpus.

Each source is cut into:
  - **Parent chunks** (≈2000 chars) at section/paragraph boundaries —
    these are what `/retrieve` returns to the caller.
  - **Child chunks** (≈400 chars) inside each parent at sentence/
    paragraph boundaries — these are what get embedded and matched
    against the query.

Children carry a reference to their parent via ``parent_id``. The
two-tier design is what makes the parent-document retrieval mode
work: stage 1 matches on small precise child chunks (high recall on
the exact-passage), stage 2 surfaces the larger parent (better
context for the answering layer).

IDs are deterministic — see ``research.md`` R7. Same source content
under the same ``corpus_run_id`` produces the same parent and child
IDs across re-runs of the build, which the golden set's
``ground_truth_chunk_ids`` rely on.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

# ≈400-char children, ≈2000-char parents. Soft targets — the splitter
# tries to land near these by paragraph boundaries before falling back
# to mid-paragraph splits.
CHILD_CHARS = 400
PARENT_CHARS = 2000

SourceType = Literal["docs", "issues"]

# RST section underline characters; the most common are = - ~ ^ "
# (https://devguide.python.org/documentation/markup/#sections). Match a
# header line followed by a line of underline chars at least as long.
RST_SECTION = re.compile(
    r"^(?P<title>.+?)\n(?P<underline>[=\-~\^\"\#]{3,})\s*$",
    flags=re.MULTILINE,
)

# Markdown ATX heading (used in README/CONTRIBUTING and issue bodies).
MARKDOWN_HEADING = re.compile(r"^(?P<hashes>#{1,6})\s+(?P<title>.+?)\s*$", flags=re.MULTILINE)


@dataclass(frozen=True)
class ChildChunk:
    id: str
    parent_id: str
    content: str
    section_path: str
    child_index: int


@dataclass(frozen=True)
class ParentChunk:
    id: str
    content: str
    section_path: str
    parent_index: int
    source_type: SourceType
    source_id: str
    source_timestamp: datetime
    corpus_run_id: str
    children: list[ChildChunk] = field(default_factory=list)


# ---------------------------------------------------------------------------
# ID generation — deterministic 26-char SHA-256 prefix (research.md R7)
# ---------------------------------------------------------------------------


def _chunk_id(*parts: object) -> str:
    h = hashlib.sha256()
    for p in parts:
        h.update(str(p).encode("utf-8"))
        h.update(b"\x1f")  # unit-separator; keeps fields unambiguously joined
    return h.hexdigest()[:26]


# ---------------------------------------------------------------------------
# Section detection — produces (section_path, body) tuples
# ---------------------------------------------------------------------------


def _detect_sections(text: str) -> list[tuple[str, str]]:
    """Return a list of (section_path, body) covering the full text.

    Splits on the first heading style that appears in the text. Falls
    back to a single ('', text) section when no headings are present.
    The section_path is the heading title; nesting isn't tracked (a
    flat list is enough for the docs we ingest).
    """
    rst_hits = list(RST_SECTION.finditer(text))
    md_hits = list(MARKDOWN_HEADING.finditer(text))
    hits = rst_hits if len(rst_hits) >= len(md_hits) else md_hits
    if not hits:
        return [("", text.strip())]

    sections: list[tuple[str, str]] = []
    # Prefix before the first heading (preamble) — drop if empty.
    preamble = text[: hits[0].start()].strip()
    if preamble:
        sections.append(("", preamble))
    for i, m in enumerate(hits):
        title = m.group("title").strip()
        body_start = m.end()
        body_end = hits[i + 1].start() if i + 1 < len(hits) else len(text)
        body = text[body_start:body_end].strip()
        if body:
            sections.append((title, body))
    return sections


# ---------------------------------------------------------------------------
# Chunk splitting — paragraph-boundary aware
# ---------------------------------------------------------------------------


def _split_at_size(text: str, target: int) -> list[str]:
    """Split `text` into chunks of roughly `target` chars, preferring blank-line breaks."""
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    out: list[str] = []
    buf = ""
    for para in paragraphs:
        if buf and len(buf) + len(para) + 2 > target:
            out.append(buf)
            buf = para
        else:
            buf = f"{buf}\n\n{para}" if buf else para
    if buf:
        out.append(buf)
    # If any single paragraph is bigger than target, hard-split on sentences.
    final: list[str] = []
    for chunk in out:
        if len(chunk) <= target * 1.5:
            final.append(chunk)
            continue
        final.extend(_hard_split(chunk, target))
    return final


def _hard_split(text: str, target: int) -> list[str]:
    """Split very long text on sentence-ish boundaries when paragraph split isn't enough."""
    parts = re.split(r"(?<=[.!?])\s+", text)
    out: list[str] = []
    buf = ""
    for part in parts:
        if buf and len(buf) + len(part) + 1 > target:
            out.append(buf)
            buf = part
        else:
            buf = f"{buf} {part}" if buf else part
    if buf:
        out.append(buf)
    return out


# ---------------------------------------------------------------------------
# Public entry — chunk one source into a list of parents (with children)
# ---------------------------------------------------------------------------


def chunk_source(
    *,
    corpus_run_id: str,
    source_type: SourceType,
    source_id: str,
    source_timestamp: datetime,
    raw_text: str,
) -> list[ParentChunk]:
    """Cut `raw_text` into parent + child chunks with deterministic IDs."""
    parents: list[ParentChunk] = []
    parent_index = 0
    sections = _detect_sections(raw_text)
    for section_path, body in sections:
        for parent_text in _split_at_size(body, PARENT_CHARS):
            parent_id = _chunk_id(
                corpus_run_id, source_type, source_id, section_path, parent_index, parent_text
            )
            children: list[ChildChunk] = []
            for child_index, child_text in enumerate(_split_at_size(parent_text, CHILD_CHARS)):
                children.append(
                    ChildChunk(
                        id=_chunk_id(
                            corpus_run_id,
                            source_type,
                            source_id,
                            section_path,
                            parent_index,
                            child_index,
                            child_text,
                        ),
                        parent_id=parent_id,
                        content=child_text,
                        section_path=section_path,
                        child_index=child_index,
                    )
                )
            parents.append(
                ParentChunk(
                    id=parent_id,
                    content=parent_text,
                    section_path=section_path,
                    parent_index=parent_index,
                    source_type=source_type,
                    source_id=source_id,
                    source_timestamp=source_timestamp,
                    corpus_run_id=corpus_run_id,
                    children=children,
                )
            )
            parent_index += 1
    return parents
