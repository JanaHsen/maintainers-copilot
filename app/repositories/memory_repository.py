"""Memory repository — the only place ``chatbot_memories`` SQL lives (Rule 1).

Two functions:

  * :func:`insert` — write one memory row (and only one). The caller (the
    ``write_memory`` tool) already applied ``redact_for_persistence`` to the
    content; this layer does NOT redact again.
  * :func:`query_top_k` — return the top-k memories for one user, ordered
    by cosine similarity to a query embedding. The ``WHERE user_id = ...``
    clause is the cross-account isolation boundary (FR-010, SC-003).

Mirrors :mod:`app.repositories.chunk_repository`'s style: raw SQL via
``sqlalchemy.text`` with bound parameters, vector serialized through
``_vec_to_pg_str`` and cast in SQL.
"""

from __future__ import annotations

import uuid

from sqlalchemy import text

from app.domain.memory import MemoryRecallHit
from app.infra.database import get_engine


def _vec_to_pg_str(vec: list[float]) -> str:
    """Serialize a Python float list to pgvector's textual form.

    Same shape as :func:`app.repositories.chunk_repository._vec_to_pg_str`
    — duplicated here on purpose because the chunk repository owns its SQL
    surface and we own ours (Rule 1).
    """
    return "[" + ",".join(f"{float(v):.7f}" for v in vec) + "]"


_INSERT_SQL = text(
    """
    INSERT INTO chatbot_memories
      (id, user_id, conversation_id, content, embedding, source)
    VALUES
      (:id, :user_id, :conversation_id, :content,
       CAST(:embedding_str AS vector), :source)
    """
)


# ``embedding <=> query`` is pgvector cosine *distance* in [0, 2].
# Similarity = 1 - distance ∈ [-1, 1]. Ordering by distance ASC is identical
# to ordering by similarity DESC.
_QUERY_TOP_K_SQL = text(
    """
    SELECT
      id,
      content,
      created_at,
      1 - (embedding <=> CAST(:query_str AS vector)) AS similarity
    FROM chatbot_memories
    WHERE user_id = :user_id
    ORDER BY embedding <=> CAST(:query_str AS vector)
    LIMIT :k
    """
)


def insert(
    *,
    memory_id: uuid.UUID,
    user_id: uuid.UUID,
    conversation_id: uuid.UUID,
    content: str,
    embedding: list[float],
    source: str = "episodic",
) -> None:
    """Insert one memory row.

    The CHECK on ``source`` allows only ``'episodic'`` in Part 1; passing any
    other value triggers a Postgres ``CheckViolation``.
    """
    with get_engine().begin() as conn:
        conn.execute(
            _INSERT_SQL,
            {
                "id": memory_id,
                "user_id": user_id,
                "conversation_id": conversation_id,
                "content": content,
                "embedding_str": _vec_to_pg_str(embedding),
                "source": source,
            },
        )


def query_top_k(
    *,
    user_id: uuid.UUID,
    query_embedding: list[float],
    k: int = 5,
) -> list[MemoryRecallHit]:
    """Return the top ``k`` memories for ``user_id`` by cosine similarity.

    Cross-account isolation is enforced at the SQL boundary: the
    ``WHERE user_id = :user_id`` clause is the only thing standing between
    actor A's query and actor B's memory rows (FR-010, SC-003).
    """
    if k <= 0:
        return []
    with get_engine().connect() as conn:
        rows = conn.execute(
            _QUERY_TOP_K_SQL,
            {
                "user_id": user_id,
                "query_str": _vec_to_pg_str(query_embedding),
                "k": k,
            },
        ).all()
    return [
        MemoryRecallHit(
            memory_id=r.id,
            content=r.content,
            created_at=r.created_at,
            similarity=float(r.similarity),
        )
        for r in rows
    ]
