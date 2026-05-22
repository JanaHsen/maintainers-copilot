"""Orchestrate the RAG corpus build: fetch -> chunk -> embed -> upsert -> report.

Operator-run, offline. Refuses to start if the classifier splits are
missing (FR-009 — the held-out issue slice must not overlap with the
classifier train/val/test). Picks the chunker explicitly per
``--strategy``; both ``parent_document`` and ``naive`` write into the
same ``rag_chunks`` table, under the same ``corpus_run_id``, with
deterministic IDs so re-runs against the same source state are no-ops.

After all chunks land, writes a ``corpus_report.json`` plus three
auxiliary index files under
``rag/corpus/{corpus_run_id}/`` in MinIO so downstream evals and
audits have a single source of truth on what landed.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from app.infra.minio_client import DATA_BUCKET, ensure_bucket, get_client  # noqa: E402
from scripts.rag import chunk_naive, chunk_parent_document  # noqa: E402
from scripts.rag.chunk_parent_document import ParentChunk  # noqa: E402
from scripts.rag.embed_and_upsert import (  # noqa: E402
    EMBEDDING_DIM,
    EMBEDDING_MODEL_ID,
    Embedder,
    upsert,
)
from scripts.rag.fetch_docs import DocSource  # noqa: E402
from scripts.rag.fetch_docs import fetch as fetch_docs
from scripts.rag.fetch_issues_held_out import (  # noqa: E402
    IssueSource,
)
from scripts.rag.fetch_issues_held_out import (
    fetch as fetch_issues,
)

logger = logging.getLogger("rag.build_corpus")


def _utc_now() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%MZ")


def _doc_to_text(d: DocSource) -> str:
    return d.raw_text


def _issue_to_text(i: IssueSource) -> str:
    """Flatten an issue + its maintainer comments into a single document."""
    parts = [f"# {i.title}", "", i.body]
    if i.comments:
        parts.append("")
        parts.append("## Comments")
        for c in i.comments:
            parts.append("")
            parts.append(f"### {c.author_association} @ {c.created_at.isoformat()}")
            parts.append("")
            parts.append(c.body)
    return "\n".join(parts).strip()


def _chunker_for(strategy: str):  # type: ignore[no-untyped-def]
    if strategy == "parent_document":
        return chunk_parent_document.chunk_source
    if strategy == "naive":
        return chunk_naive.chunk_source
    raise ValueError(f"unknown --strategy={strategy!r}")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--dataset-run-id", required=True)
    p.add_argument("--corpus-run-id", default=None, help="defaults to a fresh UTC stamp")
    p.add_argument(
        "--strategy",
        required=True,
        choices=["parent_document", "naive"],
        help="REQUIRED — no default. Chunking strategy. Operator MUST choose "
        "explicitly until the chunking decision settles in Phase 5.",
    )
    p.add_argument(
        "--fixture",
        type=Path,
        default=None,
        help="run against a smoke fixture instead of the live pandas repo + GitHub",
    )
    p.add_argument(
        "--pandas-repo-ref",
        default=os.environ.get("PANDAS_REPO_REF", "main"),
        help="git ref to sparse-checkout the pandas repo at (real mode only)",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    args = parse_args(argv)
    started_at = datetime.now(UTC).isoformat()
    corpus_run_id = args.corpus_run_id or _utc_now()
    logger.info(
        "corpus_run_id=%s dataset_run_id=%s strategy=%s fixture=%s",
        corpus_run_id,
        args.dataset_run_id,
        args.strategy,
        args.fixture,
    )

    # 1. Fetch
    docs_result = fetch_docs(
        ref=args.pandas_repo_ref,
        fixture_dir=args.fixture,
    )
    issues_result = fetch_issues(
        dataset_run_id=args.dataset_run_id,
        fixture_dir=args.fixture,
        corpus_run_id=corpus_run_id,
    )
    logger.info(
        "fetched %d docs (skipped=%d) and %d issues "
        "(excluded_overlap=%d, dropped_no_maintainer=%d)",
        len(docs_result.sources),
        docs_result.skipped_files,
        len(issues_result.sources),
        len(issues_result.excluded_issue_numbers),
        len(issues_result.dropped_no_maintainer),
    )

    # 2. Chunk
    chunker = _chunker_for(args.strategy)
    all_parents: list[ParentChunk] = []
    for d in docs_result.sources:
        all_parents.extend(
            chunker(
                corpus_run_id=corpus_run_id,
                source_type="docs",
                source_id=d.source_id,
                source_timestamp=d.source_timestamp,
                raw_text=_doc_to_text(d),
            )
        )
    for i in issues_result.sources:
        all_parents.extend(
            chunker(
                corpus_run_id=corpus_run_id,
                source_type="issues",
                source_id=i.source_id,
                source_timestamp=i.source_timestamp,
                raw_text=_issue_to_text(i),
            )
        )
    n_parents = len(all_parents)
    n_children = sum(len(p.children) for p in all_parents)
    logger.info("chunked: %d parents, %d children", n_parents, n_children)

    # 3. Embed + upsert
    embedder = Embedder()
    counts = upsert(all_parents, embedder=embedder)
    logger.info("upserted: %s", counts)

    # 4. Report
    finished_at = datetime.now(UTC).isoformat()
    report: dict[str, Any] = {
        "corpus_run_id": corpus_run_id,
        "dataset_run_id": args.dataset_run_id,
        "strategy": args.strategy,
        "embedding_model_id": EMBEDDING_MODEL_ID,
        "embedding_dim": EMBEDDING_DIM,
        "counts": {
            "docs": {
                "sources": len(docs_result.sources),
                "skipped_files": docs_result.skipped_files,
                "parents": sum(1 for p in all_parents if p.source_type == "docs"),
                "children": sum(
                    len(p.children) for p in all_parents if p.source_type == "docs"
                ),
            },
            "issues": {
                "sources": len(issues_result.sources),
                "excluded_overlap": len(issues_result.excluded_issue_numbers),
                "dropped_no_maintainer": len(issues_result.dropped_no_maintainer),
                "parents": sum(1 for p in all_parents if p.source_type == "issues"),
                "children": sum(
                    len(p.children) for p in all_parents if p.source_type == "issues"
                ),
            },
        },
        "upsert": counts,
        "chunking": {
            "child_chars": chunk_parent_document.CHILD_CHARS,
            "parent_chars": chunk_parent_document.PARENT_CHARS,
        },
        "started_at": started_at,
        "finished_at": finished_at,
    }

    # 5. Upload report + index JSONLs + excluded_issue_numbers.txt
    ensure_bucket(DATA_BUCKET)
    s3 = get_client()
    prefix = f"rag/corpus/{corpus_run_id}"

    def _put(key_suffix: str, body: bytes) -> str:
        key = f"{prefix}/{key_suffix}"
        s3.put_object(Bucket=DATA_BUCKET, Key=key, Body=body)
        return f"s3://{DATA_BUCKET}/{key}"

    docs_index_jsonl = "\n".join(
        json.dumps({"source_id": d.source_id, "chars": len(d.raw_text)})
        for d in docs_result.sources
    ).encode()
    issues_index_jsonl = "\n".join(
        json.dumps(
            {
                "issue_number": int(i.source_id),
                "comment_count": len(i.comments),
                "chars": len(i.body),
            }
        )
        for i in issues_result.sources
    ).encode()
    excluded_text = "\n".join(str(n) for n in issues_result.excluded_issue_numbers).encode()

    _put("docs_index.jsonl", docs_index_jsonl)
    _put("issues_index.jsonl", issues_index_jsonl)
    _put("excluded_issue_numbers.txt", excluded_text)
    location = _put(
        "corpus_report.json", json.dumps(report, indent=2, default=str).encode()
    )
    logger.info("uploaded report to %s", location)

    # Pretty-print to stdout so the smoke test can grep counts.
    print(json.dumps(report, indent=2, default=str))
    return 0


if __name__ == "__main__":
    # Embedder warm-up dominates wall time; explicitly silence the
    # sentence-transformers progress bars so the smoke output is parseable.
    raise SystemExit(main())
