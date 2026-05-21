"""Orchestrate /retrieve: HyDE -> embed -> first-stage -> rerank -> parent-aggregate.

**Phase-4 (MVP) shape**: dense-only stage 1, no HyDE, no rerank,
child chunks returned directly to the caller. This is the naive
configuration described in FR-019.

The advanced shape (HyDE -> hybrid α -> cross-encoder rerank -> parent
aggregation) lands piece-by-piece in Phase 5 (T031-T034) behind this
same service entry point. The router never changes; the wiring inside
this module does.

Errors from the model server's /embed are caught here and converted
into RetrieveError outcomes (Rule 11 — the router maps these to
503/504/502, never a 500).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal

from app.config import get_settings
from app.domain.retrieve import (
    ChunkFilters,
    RetrievedChunk,
    RetrieveFilters,
    RetrieveRequest,
    SourceType,
)
from app.infra import embedding_client
from app.infra.log_redaction import redact
from app.infra.model_server_client import (
    ModelServerError,
    ModelServerInternalError,
    ModelServerInvalidInputError,
    ModelServerTimeoutError,
    ModelServerUnreachableError,
)
from app.repositories import chunk_repository

logger = logging.getLogger("app.services.retrieve")

# Phase-4 MVP constants. Phase-5 commits replace these with config-driven
# values; the names match so the router doesn't need to change.
MVP_ALPHA = 1.0          # dense-only (FR-019 naive baseline shape)
MVP_FIRST_STAGE_K = 30   # the spec's stage-1 cap; capped further by req.k


RetrieveErrorKind = Literal[
    "unreachable",
    "timeout",
    "bad_request",
    "internal",
    "unexpected",
]


@dataclass(frozen=True)
class RetrieveOk:
    chunks: list[RetrievedChunk]


@dataclass(frozen=True)
class RetrieveError:
    kind: RetrieveErrorKind
    detail: str


RetrieveOutcome = RetrieveOk | RetrieveError


_EXCEPTION_TO_KIND: dict[type[ModelServerError], RetrieveErrorKind] = {
    ModelServerUnreachableError: "unreachable",
    ModelServerTimeoutError: "timeout",
    ModelServerInvalidInputError: "bad_request",
    ModelServerInternalError: "internal",
}


def _filters_from_request(req: RetrieveRequest) -> ChunkFilters:
    """Translate the request's optional filters into the repository's shape."""
    sources: list[SourceType] = ["docs", "issues"]
    from_: object = None
    to: object = None
    if req.filters is not None:
        f: RetrieveFilters = req.filters
        if f.source:
            sources = list(f.source)
        from_ = f.from_
        to = f.to
    return ChunkFilters(source_types=sources, from_=from_, to=to)


def retrieve(
    req: RetrieveRequest, *, request_id: str = "", trace_id: str = ""
) -> RetrieveOutcome:
    """Run the MVP retrieve pipeline; return a typed outcome (never raise upstream)."""
    if req.k == 0:
        return RetrieveOk(chunks=[])

    corpus_run_id = get_settings().rag_corpus_run_id

    try:
        query_embedding = embedding_client.embed(req.question, request_id=request_id)
    except ModelServerError as exc:
        kind = _EXCEPTION_TO_KIND.get(type(exc), "unexpected")
        logger.warning("embed failed: %s (kind=%s)", exc, kind)
        return RetrieveError(kind=kind, detail=redact(str(exc)))

    filters = _filters_from_request(req)
    stage_k = max(req.k, 1)
    hits = chunk_repository.query_first_stage(
        embedding=query_embedding,
        query_text=req.question,
        alpha=MVP_ALPHA,
        k=stage_k,
        filters=filters,
        corpus_run_id=corpus_run_id,
    )

    # Phase-4 MVP: child chunks returned directly (no parent aggregation,
    # no cross-encoder rerank — the same naive shape FR-019 tests against).
    chunks = [
        RetrievedChunk(
            content=h.content,
            source_type=h.source_type,
            source_id=h.source_id,
            score=h.score,
            metadata={
                "source_timestamp": h.source_timestamp.isoformat(),
                "section_path": h.section_path,
                "corpus_run_id": corpus_run_id,
                "parent_id": h.parent_id,
            },
            chunk_id=h.chunk_id,
        )
        for h in hits[: req.k]
    ]
    logger.info(
        "retrieve: question_len=%d k=%d returned=%d trace_id=%s",
        len(req.question),
        req.k,
        len(chunks),
        trace_id,
    )
    return RetrieveOk(chunks=chunks)
