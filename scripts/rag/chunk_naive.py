"""Naive baseline chunker: fixed-≈400-char chunks, no parent/child hierarchy.

Used by FR-019's naive baseline. Each emitted ``ParentChunk`` carries
exactly one ``ChildChunk`` whose content equals the parent's content
— so the downstream upsert and the rerank path don't need a separate
code path for naive mode (just treat naive parents as "trivially
their own children"). The naive baseline retrieval mode reads child
rows by cosine similarity and returns the matching child content
directly (no parent-aggregation, no rerank).

IDs use the same deterministic SHA-256-prefix recipe as
``chunk_parent_document`` so re-runs of the build under the same
``corpus_run_id`` produce byte-identical IDs.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from scripts.rag.chunk_parent_document import (
    CHILD_CHARS,
    ChildChunk,
    ParentChunk,
    _chunk_id,
    _split_at_size,
)

SourceType = Literal["docs", "issues"]


def chunk_source(
    *,
    corpus_run_id: str,
    source_type: SourceType,
    source_id: str,
    source_timestamp: datetime,
    raw_text: str,
) -> list[ParentChunk]:
    """Cut `raw_text` into a flat list of ≈400-char chunks (no hierarchy)."""
    parts = _split_at_size(raw_text.strip(), CHILD_CHARS)
    parents: list[ParentChunk] = []
    for parent_index, chunk_text in enumerate(parts):
        parent_id = _chunk_id(
            corpus_run_id, source_type, source_id, "naive", parent_index, chunk_text
        )
        # Single self-child per parent — same content; the eval --mode naive
        # pipeline returns this content directly via dense-only retrieval.
        child = ChildChunk(
            id=_chunk_id(
                corpus_run_id, source_type, source_id, "naive", parent_index, 0, chunk_text
            ),
            parent_id=parent_id,
            content=chunk_text,
            section_path="",
            child_index=0,
        )
        parents.append(
            ParentChunk(
                id=parent_id,
                content=chunk_text,
                section_path="",
                parent_index=parent_index,
                source_type=source_type,
                source_id=source_id,
                source_timestamp=source_timestamp,
                corpus_run_id=corpus_run_id,
                children=[child],
            )
        )
    return parents
