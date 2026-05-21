"""Embed child chunks via sentence-transformers, bulk-upsert into rag_chunks.

Offline counterpart to the model-server's online ``/embed`` (see
``research.md`` R3 — same weights, two paths). Loads
``BAAI/bge-base-en-v1.5`` once at the top of the run, then iterates
over a list of ``ParentChunk`` objects (output of the chunker), batching
child-chunk text through ``SentenceTransformer.encode`` with a
configurable batch size and bulk-inserting the resulting rows into the
``rag_chunks`` table.

Parents get inserted with ``NULL`` embedding; children get the dense
vector. ``ON CONFLICT (id) DO NOTHING`` makes the upsert idempotent
across re-runs (deterministic chunk IDs from ``chunk_parent_document``
mean the same source produces the same ID, so subsequent runs on the
same source state are no-ops in the database).
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from typing import Any

from sentence_transformers import SentenceTransformer  # type: ignore[import-untyped]
from sqlalchemy import text

from app.infra.database import get_engine
from scripts.rag.chunk_parent_document import ParentChunk

logger = logging.getLogger("rag.embed_and_upsert")

EMBEDDING_MODEL_ID = "BAAI/bge-base-en-v1.5"
EMBEDDING_DIM = 768
DEFAULT_BATCH_SIZE = 64

# Parents and children get separate INSERT statements: parent rows
# always have NULL embedding, child rows always have a literal vector.
# A single statement with CASE WHEN on the embedding parameter trips
# Postgres' type inference (`could not determine data type of parameter`),
# so we keep them disjoint.

_INSERT_COLUMNS = (
    "id, kind, parent_id, content, embedding, "
    "source_type, source_id, source_timestamp, "
    "section_path, child_index, parent_index, corpus_run_id"
)

INSERT_PARENT_SQL = text(
    f"""
    INSERT INTO rag_chunks ({_INSERT_COLUMNS})
    VALUES (
      :id, 'parent', :parent_id, :content, NULL,
      :source_type, :source_id, :source_timestamp,
      :section_path, :child_index, :parent_index, :corpus_run_id
    )
    ON CONFLICT (id) DO NOTHING
    """
)

INSERT_CHILD_SQL = text(
    f"""
    INSERT INTO rag_chunks ({_INSERT_COLUMNS})
    VALUES (
      :id, 'child', :parent_id, :content, CAST(:embedding_str AS vector),
      :source_type, :source_id, :source_timestamp,
      :section_path, :child_index, :parent_index, :corpus_run_id
    )
    ON CONFLICT (id) DO NOTHING
    """
)


def _vec_to_pg_str(vec: Iterable[float]) -> str:
    return "[" + ",".join(f"{float(v):.7f}" for v in vec) + "]"


class Embedder:
    """Thin wrapper over SentenceTransformer; loaded once per run."""

    def __init__(self, model_id: str = EMBEDDING_MODEL_ID) -> None:
        logger.info("loading embedding model %s", model_id)
        self._model = SentenceTransformer(model_id)
        self.model_id = model_id
        self.dim = int(self._model.get_sentence_embedding_dimension() or EMBEDDING_DIM)
        if self.dim != EMBEDDING_DIM:
            raise RuntimeError(
                f"{model_id} reported dim={self.dim}, expected {EMBEDDING_DIM} "
                f"(matches the vector(768) column in alembic 0002_rag_chunks)"
            )

    def encode_batch(
        self, texts: list[str], batch_size: int = DEFAULT_BATCH_SIZE
    ) -> list[list[float]]:
        vectors = self._model.encode(
            texts,
            batch_size=batch_size,
            convert_to_numpy=True,
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        return [list(map(float, row)) for row in vectors]


def upsert(parents: list[ParentChunk], embedder: Embedder | None = None) -> dict[str, int]:
    """Bulk-upsert a list of parent + child rows; embedding the children in batches.

    Returns a counts dict: ``{"parents_inserted", "children_inserted",
    "parents_skipped", "children_skipped"}`` — "skipped" counts the
    ``ON CONFLICT DO NOTHING`` no-ops (idempotent re-run).
    """
    embedder = embedder or Embedder()
    child_texts: list[str] = []
    child_keys: list[tuple[str, str]] = []  # (parent.id, child.id) for stable join after encode
    for parent in parents:
        for child in parent.children:
            child_texts.append(child.content)
            child_keys.append((parent.id, child.id))

    logger.info(
        "embedding %d child chunks across %d parents (model=%s)",
        len(child_texts),
        len(parents),
        embedder.model_id,
    )
    vectors = embedder.encode_batch(child_texts) if child_texts else []
    embedding_by_child: dict[str, list[float]] = dict(
        zip([c for _, c in child_keys], vectors, strict=True)
    )

    parent_rows: list[dict[str, Any]] = []
    child_rows: list[dict[str, Any]] = []
    for parent in parents:
        parent_rows.append(
            {
                "id": parent.id,
                "parent_id": parent.id,
                "content": parent.content,
                "source_type": parent.source_type,
                "source_id": parent.source_id,
                "source_timestamp": parent.source_timestamp,
                "section_path": parent.section_path,
                "child_index": 0,
                "parent_index": parent.parent_index,
                "corpus_run_id": parent.corpus_run_id,
            }
        )
        for child in parent.children:
            child_rows.append(
                {
                    "id": child.id,
                    "parent_id": child.parent_id,
                    "content": child.content,
                    "embedding_str": _vec_to_pg_str(embedding_by_child[child.id]),
                    "source_type": parent.source_type,
                    "source_id": parent.source_id,
                    "source_timestamp": parent.source_timestamp,
                    "section_path": child.section_path,
                    "child_index": child.child_index,
                    "parent_index": parent.parent_index,
                    "corpus_run_id": parent.corpus_run_id,
                }
            )

    engine = get_engine()
    with engine.begin() as conn:
        # SQLAlchemy returns rowcount as the number of rows actually touched;
        # with ON CONFLICT DO NOTHING that's the number of *new* rows inserted.
        parent_result = conn.execute(INSERT_PARENT_SQL, parent_rows) if parent_rows else None
        child_result = conn.execute(INSERT_CHILD_SQL, child_rows) if child_rows else None
    parents_inserted = parent_result.rowcount if parent_result is not None else 0
    children_inserted = child_result.rowcount if child_result is not None else 0
    return {
        "parents_inserted": parents_inserted,
        "children_inserted": children_inserted,
        "parents_skipped": len(parent_rows) - parents_inserted,
        "children_skipped": len(child_rows) - children_inserted,
    }
