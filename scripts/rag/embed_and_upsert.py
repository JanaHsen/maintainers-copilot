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
# Number of parents processed per embed+upsert chunk. Keeps peak RAM
# bounded so the full pandas corpus (~46k parents, ~78k children, ~480MB
# of f32 vectors at once) doesn't OOM on a memory-constrained host.
DEFAULT_UPSERT_BATCH_PARENTS = 250

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


def _parent_row(parent: ParentChunk) -> dict[str, Any]:
    return {
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


def _child_row(
    parent: ParentChunk, child: Any, embedding: list[float]
) -> dict[str, Any]:
    return {
        "id": child.id,
        "parent_id": child.parent_id,
        "content": child.content,
        "embedding_str": _vec_to_pg_str(embedding),
        "source_type": parent.source_type,
        "source_id": parent.source_id,
        "source_timestamp": parent.source_timestamp,
        "section_path": child.section_path,
        "child_index": child.child_index,
        "parent_index": parent.parent_index,
        "corpus_run_id": parent.corpus_run_id,
    }


def upsert(
    parents: list[ParentChunk],
    embedder: Embedder | None = None,
    upsert_batch_parents: int = DEFAULT_UPSERT_BATCH_PARENTS,
) -> dict[str, int]:
    """Stream-embed + bulk-upsert parents + children, bounding peak memory.

    Materializing all ~78k child embeddings at once (480MB+ of f32 vectors
    plus the serialized embedding strings) OOMs on a 4GB-class WSL host.
    Instead we walk ``parents`` in slabs of ``upsert_batch_parents``,
    embed only that slab's children, INSERT, and drop the buffers before
    moving on. Peak RAM stays at a few tens of MB per slab.

    Returns the same counts dict as before:
    ``{"parents_inserted", "children_inserted", "parents_skipped",
    "children_skipped"}``. "skipped" counts the ``ON CONFLICT DO NOTHING``
    no-ops (idempotent re-run).
    """
    embedder = embedder or Embedder()
    n_children_total = sum(len(p.children) for p in parents)
    logger.info(
        "embedding %d child chunks across %d parents (model=%s, slab=%d parents)",
        n_children_total,
        len(parents),
        embedder.model_id,
        upsert_batch_parents,
    )

    engine = get_engine()
    parents_inserted = 0
    parents_skipped = 0
    children_inserted = 0
    children_skipped = 0

    for slab_start in range(0, len(parents), upsert_batch_parents):
        slab = parents[slab_start : slab_start + upsert_batch_parents]

        parent_rows = [_parent_row(p) for p in slab]

        child_pairs: list[tuple[ParentChunk, Any]] = []
        for p in slab:
            for c in p.children:
                child_pairs.append((p, c))

        if child_pairs:
            texts = [c.content for _, c in child_pairs]
            vectors = embedder.encode_batch(texts)
            child_rows = [
                _child_row(parent, child, vec)
                for (parent, child), vec in zip(child_pairs, vectors, strict=True)
            ]
            del texts, vectors
        else:
            child_rows = []

        with engine.begin() as conn:
            if parent_rows:
                pres = conn.execute(INSERT_PARENT_SQL, parent_rows)
                pinserted = pres.rowcount or 0
                parents_inserted += pinserted
                parents_skipped += len(parent_rows) - pinserted
            if child_rows:
                cres = conn.execute(INSERT_CHILD_SQL, child_rows)
                cinserted = cres.rowcount or 0
                children_inserted += cinserted
                children_skipped += len(child_rows) - cinserted

        logger.info(
            "slab %d-%d done: parents=%d children=%d (cum: p_ins=%d p_skip=%d c_ins=%d c_skip=%d)",
            slab_start,
            slab_start + len(slab),
            len(slab),
            len(child_pairs),
            parents_inserted,
            parents_skipped,
            children_inserted,
            children_skipped,
        )
        del parent_rows, child_rows, child_pairs

    return {
        "parents_inserted": parents_inserted,
        "children_inserted": children_inserted,
        "parents_skipped": parents_skipped,
        "children_skipped": children_skipped,
    }
