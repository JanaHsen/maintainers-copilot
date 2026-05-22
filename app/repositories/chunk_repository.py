"""Chunk repository — the ONLY place pgvector + tsvector SQL lives (Rule 1).

Exposes:

  * ``query_first_stage`` — one SQL statement combining dense cosine
    similarity (pgvector ``<=>``) and sparse text search
    (``ts_rank_cd``) with a tunable weight ``alpha``. Returns the top
    ``k`` child rows for the configured ``corpus_run_id``, optionally
    filtered by source_type / time window inside the same query
    (FR-018 — filters apply DURING stage 1, not after).
  * ``fetch_parents`` — bulk parent lookup by parent_id, used after
    the rerank-and-aggregate step.
  * ``is_empty`` — boot-check predicate (Rule 4: refuse to boot if the
    rag_chunks table is empty for the configured run_id).
"""

from __future__ import annotations

from sqlalchemy import bindparam, text

from app.domain.retrieve import ChildHit, ChunkFilters, Parent, SourceType
from app.infra.database import get_engine


def _vec_to_pg_str(vec: list[float]) -> str:
    """Same serialization as scripts/rag/embed_and_upsert._vec_to_pg_str."""
    return "[" + ",".join(f"{float(v):.7f}" for v in vec) + "]"


# One SQL statement: hybrid α-weighted score, metadata filter inside the
# stage-1 query (so the rerank candidate pool is drawn from the filtered
# subset). ``embedding <=> :v`` is pgvector cosine *distance* (0=identical,
# 2=opposite); subtracting from 1 yields similarity in [-1, 1].
_FIRST_STAGE_SQL = text(
    """
    SELECT
      id,
      parent_id,
      content,
      source_type,
      source_id,
      source_timestamp,
      section_path,
      (
        :alpha * (1 - (embedding <=> CAST(:embedding_str AS vector)))
        + (1 - :alpha) * ts_rank_cd(content_tsv, plainto_tsquery('english', :query_text))
      ) AS score
    FROM rag_chunks
    WHERE kind = 'child'
      AND corpus_run_id = :corpus_run_id
      AND source_type = ANY(:source_types)
      AND (
        CAST(:ts_from AS timestamptz) IS NULL
        OR source_timestamp >= CAST(:ts_from AS timestamptz)
      )
      AND (
        CAST(:ts_to AS timestamptz) IS NULL
        OR source_timestamp <= CAST(:ts_to AS timestamptz)
      )
    ORDER BY score DESC
    LIMIT :k
    """
).bindparams(
    # Help Postgres infer the array's element type.
    bindparam("source_types", expanding=False),
)


_FETCH_PARENTS_SQL = text(
    """
    SELECT
      id,
      content,
      source_type,
      source_id,
      source_timestamp,
      section_path
    FROM rag_chunks
    WHERE kind = 'parent'
      AND id = ANY(:parent_ids)
    """
)


_IS_EMPTY_SQL = text(
    "SELECT 1 FROM rag_chunks WHERE corpus_run_id = :corpus_run_id LIMIT 1"
)


def query_first_stage(
    *,
    embedding: list[float],
    query_text: str,
    alpha: float,
    k: int,
    filters: ChunkFilters,
    corpus_run_id: str,
) -> list[ChildHit]:
    """Run the hybrid (dense + sparse) stage-1 query and return ChildHits."""
    if not 0.0 <= alpha <= 1.0:
        raise ValueError(f"alpha must be in [0,1]; got {alpha}")
    params = {
        "alpha": alpha,
        "embedding_str": _vec_to_pg_str(embedding),
        "query_text": query_text,
        "corpus_run_id": corpus_run_id,
        "source_types": list(filters.source_types),
        "ts_from": filters.from_,
        "ts_to": filters.to,
        "k": k,
    }
    with get_engine().connect() as conn:
        rows = conn.execute(_FIRST_STAGE_SQL, params).all()
    return [
        ChildHit(
            chunk_id=r.id,
            parent_id=r.parent_id,
            content=r.content,
            source_type=r.source_type,
            source_id=r.source_id,
            source_timestamp=r.source_timestamp,
            section_path=r.section_path,
            score=float(r.score),
        )
        for r in rows
    ]


def fetch_parents(parent_ids: list[str]) -> dict[str, Parent]:
    """Bulk-fetch parent rows by id; returns a {parent_id: Parent} dict."""
    if not parent_ids:
        return {}
    with get_engine().connect() as conn:
        rows = conn.execute(
            _FETCH_PARENTS_SQL, {"parent_ids": list(parent_ids)}
        ).all()
    out: dict[str, Parent] = {}
    for r in rows:
        source_type: SourceType = r.source_type
        out[r.id] = Parent(
            chunk_id=r.id,
            content=r.content,
            source_type=source_type,
            source_id=r.source_id,
            source_timestamp=r.source_timestamp,
            section_path=r.section_path,
            metadata={
                "source_timestamp": r.source_timestamp.isoformat(),
                "section_path": r.section_path,
            },
        )
    return out


def is_empty(corpus_run_id: str) -> bool:
    """True if no rows exist for `corpus_run_id` — used by the boot check."""
    with get_engine().connect() as conn:
        row = conn.execute(_IS_EMPTY_SQL, {"corpus_run_id": corpus_run_id}).first()
    return row is None
