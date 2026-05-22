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

# Phase-5 advanced-pipeline constants (T031 wires parent-document
# chunking + max-child-score aggregation; later Phase-5 commits flip
# the others as their evals land).
MVP_ALPHA = 1.0          # dense-only (T032 will set the post-sweep value)
MVP_FIRST_STAGE_K = 30   # the spec's stage-1 cap (FR-015)


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

    # T034 — HyDE wiring was tested and DROPPED in this environment
    # (Anthropic key is `n/a` in Vault → 100% fallback to raw
    # question, no usable delta). See DECISIONS.md
    # "## RAG HyDE (T034) — DROPPED pending Anthropic key".
    # `app/services/hyde_service.py` stays in the repo for future use.

    try:
        query_embedding = embedding_client.embed(req.question, request_id=request_id)
    except ModelServerError as exc:
        kind = _EXCEPTION_TO_KIND.get(type(exc), "unexpected")
        logger.warning("embed failed: %s (kind=%s)", exc, kind)
        return RetrieveError(kind=kind, detail=redact(str(exc)))

    filters = _filters_from_request(req)
    # Always over-fetch at stage 1 so the parent-aggregation step has a
    # population to draw the top-k unique parents from (FR-015 / R2).
    stage_k = MVP_FIRST_STAGE_K
    hits = chunk_repository.query_first_stage(
        embedding=query_embedding,
        query_text=req.question,
        alpha=MVP_ALPHA,
        k=stage_k,
        filters=filters,
        corpus_run_id=corpus_run_id,
    )

    # T033 — Cross-encoder rerank tested twice (ms-marco-MiniLM-L-6-v2
    # then BAAI/bge-reranker-base) and DROPPED both times. See DECISIONS.md
    # "## RAG cross-encoder rerank (T033) — DROPPED (two attempts)" for the
    # numbers. reranker_client.py + the /rerank endpoint stay in the repo
    # for future re-evaluation; this service skips them.

    # T031 — Advanced choice 1: parent-document chunking. Aggregate the
    # 30 child hits by parent_id using the MAX child score (R2), then
    # fetch and return the top-k unique PARENT chunks.
    parent_scores: dict[str, float] = {}
    parent_meta: dict[str, dict[str, str]] = {}
    for h in hits:
        if h.parent_id not in parent_scores or h.score > parent_scores[h.parent_id]:
            parent_scores[h.parent_id] = h.score
            parent_meta[h.parent_id] = {
                "source_timestamp": h.source_timestamp.isoformat(),
                "section_path": h.section_path,
                "corpus_run_id": corpus_run_id,
            }

    ranked_parent_ids = sorted(
        parent_scores, key=lambda pid: parent_scores[pid], reverse=True
    )[: req.k]
    parents = chunk_repository.fetch_parents(ranked_parent_ids)
    chunks: list[RetrievedChunk] = []
    for pid in ranked_parent_ids:
        parent = parents.get(pid)
        if parent is None:
            # The parent row should always exist for any child row, but
            # defensive in case of a partial corpus build.
            continue
        chunks.append(
            RetrievedChunk(
                content=parent.content,
                source_type=parent.source_type,
                source_id=parent.source_id,
                score=parent_scores[pid],
                metadata={**parent_meta[pid], "parent_id": pid},
                chunk_id=pid,
            )
        )
    logger.info(
        "retrieve: question_len=%d k=%d stage1=%d parents=%d trace_id=%s",
        len(req.question),
        req.k,
        len(hits),
        len(chunks),
        trace_id,
    )
    return RetrieveOk(chunks=chunks)
