"""Hybrid α sweep (T032).

Runs the advanced pipeline shape (parent-doc max-aggregation, k=5)
against the golden set at α ∈ {0.0, 0.1, ..., 1.0}. Picks the α that
maximizes `hit_at_5` (recall@5 macro-average). Writes a per-α metrics
table to `evals/rag/alpha_sweep.json` and prints the chosen α + the
delta vs naive baseline.

The sweep calls `chunk_repository.query_first_stage` directly so it
can vary α without touching `/retrieve`'s configuration. The
aggregation step mirrors `retrieve_service.retrieve` so the sweep's
numbers are comparable to the live advanced pipeline once the chosen
α is wired in.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from app.config import get_settings
from app.domain.retrieve import ChunkFilters
from app.infra import embedding_client
from app.repositories import chunk_repository
from evals.rag.eval_rag import _dedup_preserve_order  # noqa: F401  (kept for compat)
from evals.rag.eval_rag import (
    GOLDEN_PATH,
    compute_metrics,
    load_golden,
)
from evals.rag.score import mrr, ndcg, recall_at_k  # noqa: F401


def _predict_at_alpha(
    question: str, *, corpus_run_id: str, alpha: float, stage_k: int, top_k: int
) -> list[str]:
    emb = embedding_client.embed(question, request_id=f"sweep-a{alpha:.1f}")
    hits = chunk_repository.query_first_stage(
        embedding=emb,
        query_text=question,
        alpha=alpha,
        k=stage_k,
        filters=ChunkFilters(source_types=["docs", "issues"], from_=None, to=None),
        corpus_run_id=corpus_run_id,
    )
    # max-aggregation by parent_id, mirroring retrieve_service.retrieve.
    best: dict[str, float] = {}
    for h in hits:
        if h.parent_id not in best or h.score > best[h.parent_id]:
            best[h.parent_id] = h.score
    ranked = sorted(best, key=lambda pid: best[pid], reverse=True)[:top_k]
    return ranked


def run_sweep(
    alphas: list[float],
    *,
    corpus_run_id: str,
    golden: list[dict[str, Any]],
    stage_k: int = 30,
    top_k: int = 5,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for alpha in alphas:
        predictions = []
        for r in golden:
            pred = _predict_at_alpha(
                r["question"],
                corpus_run_id=corpus_run_id,
                alpha=alpha,
                stage_k=stage_k,
                top_k=top_k,
            )
            predictions.append({"retrieved_chunk_ids": pred})
        metrics = compute_metrics(predictions, golden)
        rows.append({"alpha": round(alpha, 3), **metrics})
        print(
            f"  α={alpha:.2f}  hit_at_5={metrics['hit_at_5']:.4f}  "
            f"mrr_at_10={metrics['mrr_at_10']:.4f}  ndcg={metrics['ndcg']:.4f}",
            file=sys.stderr,
        )
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Sweep hybrid α on the golden set (T032).")
    parser.add_argument(
        "--corpus-run-id", default=None,
        help="Default: get_settings().rag_corpus_run_id.",
    )
    parser.add_argument(
        "--golden", default=str(GOLDEN_PATH),
        help="Path to golden.jsonl.",
    )
    parser.add_argument(
        "--output", default=None,
        help="Write the sweep table to this path (default: evals/rag/alpha_sweep.json).",
    )
    parser.add_argument(
        "--alphas", nargs="+", type=float, default=[i / 10.0 for i in range(11)],
        help="α values to sweep (default 0.0..1.0 by 0.1).",
    )
    args = parser.parse_args(argv)

    corpus_run_id = args.corpus_run_id or get_settings().rag_corpus_run_id
    if not corpus_run_id:
        print("RAG_CORPUS_RUN_ID is not configured.", file=sys.stderr)
        return 2

    golden = load_golden(Path(args.golden))
    print(f"Sweeping α over {len(args.alphas)} values on {len(golden)} questions", file=sys.stderr)
    rows = run_sweep(args.alphas, corpus_run_id=corpus_run_id, golden=golden)

    # Pick best α by hit_at_5 (tie-break: higher mrr_at_10, then ndcg).
    best = max(rows, key=lambda r: (r["hit_at_5"], r["mrr_at_10"], r["ndcg"]))
    out = {
        "corpus_run_id": corpus_run_id,
        "n_examples": len(golden),
        "rows": rows,
        "best": best,
    }

    out_path = Path(args.output) if args.output else (
        Path(__file__).parent / "alpha_sweep.json"
    )
    out_path.write_text(json.dumps(out, indent=2, sort_keys=True), encoding="utf-8")
    print(f"\nbest α = {best['alpha']:.2f}", file=sys.stderr)
    print(f"  hit_at_5  = {best['hit_at_5']:.4f}", file=sys.stderr)
    print(f"  mrr_at_10 = {best['mrr_at_10']:.4f}", file=sys.stderr)
    print(f"  ndcg      = {best['ndcg']:.4f}", file=sys.stderr)
    print(f"  → {out_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
