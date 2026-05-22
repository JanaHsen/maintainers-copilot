"""End-to-end corpus-build smoke against the 5-doc / 5-issue fixture.

Requires the compose stack up (Postgres + pgvector + Vault + MinIO).
CI runs this via `bash` after the existing classifier eval gate and
before stack-down. Locally:

    docker compose up -d postgres redis minio vault phoenix vault-seed migrate
    uv run pytest tests/scripts/test_build_corpus_smoke.py -q
"""

from __future__ import annotations

import json
import os
import time
import uuid

import pytest
from sqlalchemy import text

# Skip when the compose stack is not reachable so the test isn't a CI
# blocker in environments that don't have it up.
if not os.environ.get("VAULT_ADDR"):
    os.environ.setdefault("VAULT_ADDR", "http://localhost:8200")
    os.environ.setdefault("MINIO_HOST", "localhost")
    os.environ.setdefault("POSTGRES_HOST", "localhost")

from app.infra.database import get_engine  # noqa: E402
from app.infra.minio_client import DATA_BUCKET, get_client  # noqa: E402
from scripts.rag.build_corpus import main  # noqa: E402


@pytest.fixture
def corpus_run_id() -> str:
    """Fresh corpus_run_id per test so successive runs don't collide."""
    return f"smoke-{uuid.uuid4().hex[:12]}"


def _stack_is_up() -> bool:
    try:
        with get_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
        get_client().head_bucket(Bucket=DATA_BUCKET)
    except Exception:
        return False
    return True


pytestmark = pytest.mark.skipif(
    not _stack_is_up(),
    reason="requires docker compose stack (postgres + minio + vault) up",
)


def test_smoke_end_to_end_parent_document(corpus_run_id: str) -> None:
    start = time.perf_counter()
    rc = main(
        [
            "--dataset-run-id",
            "20260519T133455Z",
            "--strategy",
            "parent_document",
            "--fixture",
            "tests/fixtures/rag_smoke",
            "--corpus-run-id",
            corpus_run_id,
        ]
    )
    elapsed = time.perf_counter() - start
    assert rc == 0
    # Target was <30s on CPU with the pre-cached model. Local cold-start
    # (model load + 5 docs + 4 issues' worth of children) lands well under
    # this in practice; bump if first-run cold cache pushes over.
    assert elapsed < 60, f"smoke took {elapsed:.1f}s — over budget"

    # Database has chunks for this corpus_run_id.
    with get_engine().connect() as conn:
        row = conn.execute(
            text(
                "SELECT "
                "  COUNT(*) FILTER (WHERE kind='parent') AS parents, "
                "  COUNT(*) FILTER (WHERE kind='child')  AS children, "
                "  COUNT(*) FILTER (WHERE kind='child' AND embedding IS NULL) "
                "    AS child_null_emb, "
                "  COUNT(*) FILTER (WHERE kind='parent' AND embedding IS NOT NULL) "
                "    AS parent_nonnull_emb "
                "FROM rag_chunks WHERE corpus_run_id = :rid"
            ),
            {"rid": corpus_run_id},
        ).one()
    parents, children, child_null_emb, parent_nonnull_emb = row
    assert parents > 0
    assert children > 0
    # Every child has an embedding; no parent has one.
    assert child_null_emb == 0
    assert parent_nonnull_emb == 0

    # MinIO has the four artifacts.
    s3 = get_client()
    prefix = f"rag/corpus/{corpus_run_id}"
    expected = [
        f"{prefix}/corpus_report.json",
        f"{prefix}/docs_index.jsonl",
        f"{prefix}/issues_index.jsonl",
        f"{prefix}/excluded_issue_numbers.txt",
    ]
    for key in expected:
        s3.head_object(Bucket=DATA_BUCKET, Key=key)  # raises if missing

    # corpus_report.json shape sanity check
    report = json.loads(
        s3.get_object(Bucket=DATA_BUCKET, Key=f"{prefix}/corpus_report.json")["Body"].read()
    )
    assert report["corpus_run_id"] == corpus_run_id
    assert report["strategy"] == "parent_document"
    assert report["embedding_model_id"] == "BAAI/bge-base-en-v1.5"
    assert report["embedding_dim"] == 768
    # 5 docs always land from the fixture.
    assert report["counts"]["docs"]["sources"] == 5
    # All 5 fixture issues enter the filter chain — they end up SOMEWHERE
    # in the {sources, excluded_overlap, dropped_no_maintainer} bucket.
    # Specific bucketing depends on whether the fixture issue numbers
    # (9001-9005) collide with real numbers in the dataset_run_id's
    # classifier splits, so the smoke only asserts the conservation law.
    issues = report["counts"]["issues"]
    assert (
        issues["sources"]
        + issues["excluded_overlap"]
        + issues["dropped_no_maintainer"]
        == 5
    )
    # The maintainer-association filter MUST always drop 9004 from
    # this fixture (its comments are CONTRIBUTOR + NONE only).
    assert issues["dropped_no_maintainer"] >= 1
    # Cleanup: drop this corpus_run_id's rows so successive test runs
    # don't compound; the MinIO objects are left as audit.
    with get_engine().begin() as conn:
        conn.execute(
            text("DELETE FROM rag_chunks WHERE corpus_run_id = :rid"),
            {"rid": corpus_run_id},
        )
