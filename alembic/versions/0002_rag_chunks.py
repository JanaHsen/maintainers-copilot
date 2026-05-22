"""rag chunks: parent+child rows with dense + sparse indices

Revision ID: 0002_rag_chunks
Revises: 0001_baseline
Create Date: 2026-05-21

Backs `/retrieve` and the corpus pipeline. One table holds both
parent-document parent rows (≈2000 chars, NULL embedding, content
surfaced to the caller) and child rows (≈400 chars, vector(768)
embedding, used for matching). Distinguished by `kind`.

Indices:
  * GIN over the generated `content_tsv` for sparse text search
    (Postgres tsvector — the hybrid α's sparse component).
  * Partial IVFFlat on `embedding` for `kind='child'` only — the
    rerank candidate pool comes from child rows; parent rows aren't
    embedded so they shouldn't take up index pages.
  * B-tree on `parent_id` (parent-lookup during aggregation),
    `(source_type, source_timestamp)` (stage-1 metadata filter,
    FR-018), `corpus_run_id` (the api pins one run at boot — every
    query carries this WHERE clause).

Rules: 3 (storage), 4 (refuse-to-boot reads `is_empty(corpus_run_id)`
from this table).
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0002_rag_chunks"
down_revision: str | None = "0001_baseline"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 768-dim vector to match BAAI/bge-base-en-v1.5 (see
    # specs/rag/research.md R12). An embedding-model swap to a
    # different dim is a new migration.
    op.execute(
        """
        CREATE TABLE rag_chunks (
            id                 TEXT        PRIMARY KEY,
            kind               TEXT        NOT NULL CHECK (kind IN ('parent', 'child')),
            parent_id          TEXT        NOT NULL,
            content            TEXT        NOT NULL,
            embedding          vector(768),
            source_type        TEXT        NOT NULL CHECK (source_type IN ('docs', 'issues')),
            source_id          TEXT        NOT NULL,
            source_timestamp   TIMESTAMPTZ NOT NULL,
            section_path       TEXT        NOT NULL DEFAULT '',
            child_index        INTEGER     NOT NULL,
            parent_index       INTEGER     NOT NULL,
            corpus_run_id      TEXT        NOT NULL,
            content_tsv        TSVECTOR
                GENERATED ALWAYS AS (to_tsvector('english', content)) STORED
        )
        """
    )

    # Parent-lookup during max-child-score aggregation.
    op.execute(
        "CREATE INDEX ix_rag_chunks_parent_id ON rag_chunks (parent_id)"
    )
    # Metadata filter inside stage-1 SQL (FR-018).
    op.execute(
        "CREATE INDEX ix_rag_chunks_source_type_timestamp "
        "ON rag_chunks (source_type, source_timestamp)"
    )
    # Fast filter for the corpus_run_id the api pins at boot.
    op.execute(
        "CREATE INDEX ix_rag_chunks_corpus_run_id "
        "ON rag_chunks (corpus_run_id)"
    )
    # Sparse-text-search index (the hybrid α's sparse component).
    op.execute(
        "CREATE INDEX gin_rag_chunks_content_tsv "
        "ON rag_chunks USING GIN (content_tsv)"
    )
    # Partial IVFFlat on child rows only — parent rows have NULL
    # embedding. lists=100 is a reasonable default for tens of
    # thousands of chunks; tune if the corpus grows past ~1M rows.
    op.execute(
        "CREATE INDEX ivfflat_rag_chunks_embedding "
        "ON rag_chunks USING ivfflat (embedding vector_cosine_ops) "
        "WITH (lists = 100) WHERE kind = 'child'"
    )

    # Unique invariants from specs/rag/data-model.md:
    # - within a corpus_run_id, parents are unique by (source_type, source_id, parent_index)
    # - children are unique by (parent_id, child_index)
    op.execute(
        "CREATE UNIQUE INDEX uq_rag_chunks_parent "
        "ON rag_chunks (corpus_run_id, source_type, source_id, parent_index) "
        "WHERE kind = 'parent'"
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_rag_chunks_child "
        "ON rag_chunks (parent_id, child_index) "
        "WHERE kind = 'child'"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_rag_chunks_child")
    op.execute("DROP INDEX IF EXISTS uq_rag_chunks_parent")
    op.execute("DROP INDEX IF EXISTS ivfflat_rag_chunks_embedding")
    op.execute("DROP INDEX IF EXISTS gin_rag_chunks_content_tsv")
    op.execute("DROP INDEX IF EXISTS ix_rag_chunks_corpus_run_id")
    op.execute("DROP INDEX IF EXISTS ix_rag_chunks_source_type_timestamp")
    op.execute("DROP INDEX IF EXISTS ix_rag_chunks_parent_id")
    op.execute("DROP TABLE IF EXISTS rag_chunks")
