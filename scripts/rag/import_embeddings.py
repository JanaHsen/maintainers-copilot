"""Drain Colab-generated parquets into rag_chunks.

Operator-run companion to ``notebooks/embed_corpus_on_colab.md``. Reads
``rag/embeddings/{corpus_run_id}/parents.parquet`` and
``children.parquet`` from MinIO, then bulk-INSERTs into ``rag_chunks``
with the same ``ON CONFLICT (id) DO NOTHING`` semantics
``embed_and_upsert.py`` uses — so a partial local run (e.g. the
~1k parents + ~2.7k children that landed before the OOM kill) is
absorbed as no-op skips.

Usage::

    uv run python scripts/rag/import_embeddings.py \\
        --corpus-run-id v1-full-20260521T2327Z

Refuses to start if either parquet is missing in MinIO. Streams the
children parquet in slabs so the importer itself doesn't OOM on
hosts where the build_corpus orchestrator OOM'd.
"""

from __future__ import annotations

import argparse
import io
import logging
import os
import sys
from typing import Any

import pandas as pd

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from app.infra.database import get_engine  # noqa: E402
from app.infra.minio_client import DATA_BUCKET, get_client  # noqa: E402
from scripts.rag.embed_and_upsert import (  # noqa: E402
    INSERT_CHILD_SQL,
    INSERT_PARENT_SQL,
    _vec_to_pg_str,
)

logger = logging.getLogger("rag.import_embeddings")

DEFAULT_CHILD_SLAB = 1000  # rows per INSERT round-trip


def _read_parquet_from_minio(key: str) -> pd.DataFrame:
    """Pull a single parquet object into a DataFrame (whole-file read)."""
    s3 = get_client()
    obj = s3.get_object(Bucket=DATA_BUCKET, Key=key)
    return pd.read_parquet(io.BytesIO(obj["Body"].read()))


def _parent_row(rec: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": rec["id"],
        "parent_id": rec["parent_id"],
        "content": rec["content"],
        "source_type": rec["source_type"],
        "source_id": rec["source_id"],
        "source_timestamp": pd.to_datetime(rec["source_timestamp"], utc=True).to_pydatetime(),
        "section_path": rec["section_path"],
        "child_index": int(rec["child_index"]),
        "parent_index": int(rec["parent_index"]),
        "corpus_run_id": rec["corpus_run_id"],
    }


def _child_row(rec: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": rec["id"],
        "parent_id": rec["parent_id"],
        "content": rec["content"],
        "embedding_str": _vec_to_pg_str(rec["embedding"]),
        "source_type": rec["source_type"],
        "source_id": rec["source_id"],
        "source_timestamp": pd.to_datetime(rec["source_timestamp"], utc=True).to_pydatetime(),
        "section_path": rec["section_path"],
        "child_index": int(rec["child_index"]),
        "parent_index": int(rec["parent_index"]),
        "corpus_run_id": rec["corpus_run_id"],
    }


def _upsert_parents(df: pd.DataFrame) -> tuple[int, int]:
    """INSERT all parent rows in one statement (no embeddings; cheap)."""
    rows = [_parent_row(rec) for rec in df.to_dict(orient="records")]
    engine = get_engine()
    with engine.begin() as conn:
        result = conn.execute(INSERT_PARENT_SQL, rows)
    inserted = int(result.rowcount or 0)
    return inserted, len(rows) - inserted


def _upsert_children(df: pd.DataFrame, slab_size: int) -> tuple[int, int]:
    """INSERT child rows in slabs of `slab_size` to bound peak memory."""
    engine = get_engine()
    inserted = 0
    skipped = 0
    for start in range(0, len(df), slab_size):
        slab = df.iloc[start : start + slab_size]
        rows = [_child_row(rec) for rec in slab.to_dict(orient="records")]
        with engine.begin() as conn:
            result = conn.execute(INSERT_CHILD_SQL, rows)
        slab_inserted = int(result.rowcount or 0)
        inserted += slab_inserted
        skipped += len(rows) - slab_inserted
        logger.info(
            "child slab %d-%d: inserted=%d skipped=%d (cum: ins=%d skip=%d)",
            start,
            start + len(slab),
            slab_inserted,
            len(rows) - slab_inserted,
            inserted,
            skipped,
        )
    return inserted, skipped


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--corpus-run-id", required=True)
    p.add_argument("--slab-size", type=int, default=DEFAULT_CHILD_SLAB)
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    args = parse_args(argv)
    prefix = f"rag/embeddings/{args.corpus_run_id}"

    logger.info("reading %s/{parents,children}.parquet from MinIO", prefix)
    parents_df = _read_parquet_from_minio(f"{prefix}/parents.parquet")
    children_df = _read_parquet_from_minio(f"{prefix}/children.parquet")
    logger.info(
        "loaded parents=%d children=%d (children dim=%d)",
        len(parents_df),
        len(children_df),
        len(children_df.iloc[0]["embedding"]) if len(children_df) else 0,
    )

    p_ins, p_skip = _upsert_parents(parents_df)
    logger.info("parents: inserted=%d skipped=%d", p_ins, p_skip)

    c_ins, c_skip = _upsert_children(children_df, slab_size=args.slab_size)
    logger.info(
        "done. corpus_run_id=%s parents_inserted=%d parents_skipped=%d "
        "children_inserted=%d children_skipped=%d",
        args.corpus_run_id,
        p_ins,
        p_skip,
        c_ins,
        c_skip,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
