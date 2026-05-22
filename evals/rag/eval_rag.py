"""RAG eval harness — naive + advanced modes.

Naive mode (FR-019 baseline): query `chunk_repository.query_first_stage`
directly with alpha=1.0, k=req_k, no rerank, no HyDE, no metadata
filter. The returned children's `parent_id`s become the predictions
(order-preserving, no dedup beyond what `dict.fromkeys` provides), so
naive predictions are scored in the same parent-id space as the
golden set.

Advanced mode (T031+ wiring): POST /retrieve with the question;
extract the parent_id from each returned chunk (currently lives under
metadata["parent_id"] in Phase-4 MVP, becomes chunk_id directly once
parent aggregation lands).

Both modes write `evals/reports/{run_ts}/rag.json` with top-level keys
`retrieval: { hit_at_5, mrr_at_10, ndcg }` and `generation: {…}` (empty
in T029; populated by the frozen Claude Haiku judge in T036).
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from app.config import get_settings
from app.domain.retrieve import ChunkFilters
from app.infra import anthropic_client, embedding_client
from app.infra.anthropic_client import AnthropicError
from app.infra.minio_client import DATA_BUCKET, ensure_bucket, get_client
from app.repositories import chunk_repository
from evals.rag.score import mrr, ndcg, recall_at_k

PROMPTS_DIR = Path(__file__).resolve().parents[2] / "prompts"
ANSWER_PROMPT_PATH = PROMPTS_DIR / "rag_answer.md"
JUDGE_PROMPT_PATH = PROMPTS_DIR / "rag_judge.md"

logger = logging.getLogger("evals.rag.eval_rag")

GOLDEN_PATH = Path(__file__).parent / "golden.jsonl"
REPORTS_ROOT = Path(__file__).resolve().parents[2] / "evals" / "reports"


def _utc_run_ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _golden_set_hash(rows: list[dict[str, Any]]) -> str:
    """Stable hash of the golden set so the report records what it was scored against."""
    import hashlib

    canonical = json.dumps(rows, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def load_golden(path: Path = GOLDEN_PATH) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    return rows


def _dedup_preserve_order(items: Sequence[str]) -> list[str]:
    return list(dict.fromkeys(items))


def predict_naive(question: str, *, corpus_run_id: str, k: int = 30) -> list[str]:
    """Naive baseline predictions for `question` — parent ids of the top-k children."""
    emb = embedding_client.embed(question, request_id="eval-naive")
    filters = ChunkFilters(source_types=["docs", "issues"], from_=None, to=None)
    hits = chunk_repository.query_first_stage(
        embedding=emb,
        query_text=question,
        alpha=1.0,
        k=k,
        filters=filters,
        corpus_run_id=corpus_run_id,
    )
    return _dedup_preserve_order([h.parent_id for h in hits])


def predict_advanced(
    question: str, *, api_base: str, k: int = 20, client: httpx.Client | None = None
) -> list[str]:
    """Advanced predictions via /retrieve — parent ids of the returned chunks.

    `k` is clamped to 20 per `RetrieveRequest.k`'s validator (FR-001).
    """
    own_client = client is None
    c = client or httpx.Client(timeout=60.0)
    k = min(k, 20)
    try:
        resp = c.post(
            f"{api_base}/retrieve",
            json={"question": question, "k": k},
            headers={"Content-Type": "application/json"},
        )
        resp.raise_for_status()
        body = resp.json()
    finally:
        if own_client:
            c.close()
    # MVP shape: chunk_id is child_id, parent_id lives in metadata.
    # T031+ shape: chunk_id IS parent_id. Support both.
    parents = []
    for ch in body.get("chunks", []):
        pid = ch.get("metadata", {}).get("parent_id") or ch.get("chunk_id")
        if pid:
            parents.append(pid)
    return _dedup_preserve_order(parents)


def run_retrieval(
    mode: str,
    *,
    golden: list[dict[str, Any]],
    corpus_run_id: str,
    api_base: str,
    k_for_metrics: int = 20,
) -> list[dict[str, Any]]:
    """Per-question predictions + structured records for the report."""
    predictions: list[dict[str, Any]] = []
    for row in golden:
        if mode == "naive":
            pred = predict_naive(
                row["question"], corpus_run_id=corpus_run_id, k=k_for_metrics
            )
        elif mode == "advanced":
            pred = predict_advanced(row["question"], api_base=api_base, k=k_for_metrics)
        else:
            raise ValueError(f"unknown mode: {mode!r}")
        gt = set(row["ground_truth_chunk_ids"])
        first_rank = next(
            (i + 1 for i, p in enumerate(pred) if p in gt),
            None,
        )
        predictions.append(
            {
                "question_id": row["question_id"],
                "question": row["question"],
                "retrieved_chunk_ids": pred[:5],
                "ground_truth_chunk_ids": sorted(gt),
                "first_correct_rank": first_rank,
            }
        )
    return predictions


def _load_prompt(path: Path) -> tuple[str, str]:
    """Parse a two-section system+user markdown prompt into (system, user_template)."""
    raw = path.read_text(encoding="utf-8")
    sections: dict[str, str] = {}
    current: str | None = None
    buf: list[str] = []
    for line in raw.splitlines():
        if line.startswith("## "):
            if current is not None:
                sections[current] = "\n".join(buf).strip()
            current = line[3:].strip().lower()
            buf = []
        elif current is not None:
            buf.append(line)
    if current is not None:
        sections[current] = "\n".join(buf).strip()
    if "system" not in sections or "user" not in sections:
        raise ValueError(f"{path} missing required '## System' / '## User' sections")
    return sections["system"], sections["user"]


def _format_contexts(parents: list[dict[str, Any]]) -> str:
    """Render retrieved parent chunks into a string block the prompts expect."""
    lines: list[str] = []
    for i, p in enumerate(parents):
        header = (
            f"[{i + 1}] source_type={p.get('source_type', '?')} "
            f"source_id={p.get('source_id', '?')} "
            f"section={p.get('section_path', '')!r}"
        )
        lines.append(header)
        lines.append(p.get("content", ""))
        lines.append("---")
    return "\n".join(lines)


def generate_answer(question: str, contexts: str) -> str:
    """One Claude Haiku call against prompts/rag_answer.md."""
    system, user_template = _load_prompt(ANSWER_PROMPT_PATH)
    user = user_template.replace("{{question}}", question).replace(
        "{{contexts}}", contexts
    )
    return anthropic_client.complete(system=system, user=user, max_tokens=400)


def judge_answer(question: str, contexts: str, answer: str) -> dict[str, float]:
    """One Claude Haiku call against prompts/rag_judge.md; returns parsed scores."""
    system, user_template = _load_prompt(JUDGE_PROMPT_PATH)
    user = (
        user_template.replace("{{question}}", question)
        .replace("{{contexts}}", contexts)
        .replace("{{answer}}", answer)
    )
    raw = anthropic_client.complete(system=system, user=user, max_tokens=200)
    # The judge prompt forces JSON-only output; strip any accidental fences.
    text = raw.strip()
    if text.startswith("```"):
        # remove leading fence + optional language tag
        text = text.split("\n", 1)[1] if "\n" in text else text
        text = text.rstrip("`").strip()
        if text.endswith("```"):
            text = text[:-3].strip()
    data = json.loads(text)
    out = {
        "faithfulness": float(data["faithfulness"]),
        "answer_relevancy": float(data["answer_relevancy"]),
        "context_recall": float(data["context_recall"]),
    }
    for k, v in out.items():
        if not 0.0 <= v <= 1.0:
            raise ValueError(f"judge returned {k} = {v}; expected [0, 1]")
    return out


def fetch_parent_contexts(parent_ids: list[str]) -> list[dict[str, Any]]:
    """Return parent rows in the requested order, dropping unknown ids."""
    parents = chunk_repository.fetch_parents(parent_ids)
    out: list[dict[str, Any]] = []
    for pid in parent_ids:
        p = parents.get(pid)
        if p is None:
            continue
        out.append(
            {
                "chunk_id": p.chunk_id,
                "content": p.content,
                "source_type": p.source_type,
                "source_id": p.source_id,
                "section_path": p.section_path,
            }
        )
    return out


def run_generation_eval(
    predictions: list[dict[str, Any]],
    golden: list[dict[str, Any]],
) -> dict[str, Any]:
    """For each prediction, generate an answer + judge it. Aggregate to means."""
    per_question: list[dict[str, Any]] = []
    skipped: list[str] = []
    for pred, row in zip(predictions, golden, strict=True):
        parent_ids = pred.get("retrieved_chunk_ids", [])[:5]
        parents = fetch_parent_contexts(parent_ids)
        contexts_block = _format_contexts(parents)
        try:
            answer = generate_answer(row["question"], contexts_block)
        except AnthropicError as exc:
            logger.warning("answer generation failed for %s: %s", row["question_id"], exc)
            skipped.append(row["question_id"])
            continue
        try:
            scores = judge_answer(row["question"], contexts_block, answer)
        except (AnthropicError, ValueError, json.JSONDecodeError) as exc:
            logger.warning("judge failed for %s: %s", row["question_id"], exc)
            skipped.append(row["question_id"])
            continue
        per_question.append(
            {
                "question_id": row["question_id"],
                "answer": answer,
                **scores,
            }
        )
    if not per_question:
        return {
            "faithfulness": 0.0,
            "answer_relevancy": 0.0,
            "context_recall": 0.0,
            "n_scored": 0,
            "n_skipped": len(skipped),
            "skipped_question_ids": skipped,
        }
    return {
        "faithfulness": sum(p["faithfulness"] for p in per_question) / len(per_question),
        "answer_relevancy": sum(p["answer_relevancy"] for p in per_question) / len(per_question),
        "context_recall": sum(p["context_recall"] for p in per_question) / len(per_question),
        "n_scored": len(per_question),
        "n_skipped": len(skipped),
        "skipped_question_ids": skipped,
        "per_question_scores": per_question,
    }


def compute_metrics(
    predictions: list[dict[str, Any]],
    golden: list[dict[str, Any]],
) -> dict[str, float]:
    pred_rankings = [p["retrieved_chunk_ids"] for p in predictions]
    gt_sets = [set(r["ground_truth_chunk_ids"]) for r in golden]
    # Use full retrieved lists for MRR@10 / nDCG; truncate to 5 inside score.py.
    # Note: predictions store only top-5 in the report to keep it compact, but
    # the metric is computed over the same top-5 here (k=10 falls back to len).
    return {
        "hit_at_5": recall_at_k(pred_rankings, gt_sets, k=5),
        "mrr_at_10": mrr(pred_rankings, gt_sets, k=10),
        "ndcg": ndcg(pred_rankings, gt_sets, k=10),
    }


def build_report(
    mode: str,
    *,
    corpus_run_id: str,
    golden: list[dict[str, Any]],
    predictions: list[dict[str, Any]],
    retrieval_metrics: dict[str, float],
    generation_metrics: dict[str, float] | None = None,
    pipeline_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "run_ts": _utc_run_ts(),
        "mode": mode,
        "corpus_run_id": corpus_run_id,
        "pipeline_config": pipeline_config or {
            "embedding_model_id": "BAAI/bge-base-en-v1.5",
            "hybrid_alpha": 1.0 if mode == "naive" else None,
            "first_stage_k": 30,
            "rerank_top_k": 5,
            "hyde_enabled": False if mode == "naive" else None,
            "chunking": "naive_fixed_400" if mode == "naive" else "parent_document",
            "parent_aggregation": None if mode == "naive" else "first_seen",
        },
        "golden_set_hash": _golden_set_hash(golden),
        "n_examples": len(golden),
        "retrieval": retrieval_metrics,
        "generation": generation_metrics or {},
        "predictions": predictions,
    }


def upload_report(report: dict[str, Any], *, run_ts: str) -> str:
    ensure_bucket(DATA_BUCKET)
    s3 = get_client()
    key = f"evals/reports/{run_ts}/rag.json"
    body = json.dumps(report, indent=2, sort_keys=True).encode("utf-8")
    s3.put_object(Bucket=DATA_BUCKET, Key=key, Body=body)
    return f"s3://{DATA_BUCKET}/{key}"


def check_thresholds(report: dict[str, Any], thresholds: dict[str, Any]) -> list[str]:
    """Return a list of human-readable breach messages (empty = passes)."""
    breaches: list[str] = []
    rag_floors = (thresholds or {}).get("rag", {})
    retrieval = report.get("retrieval", {})
    if "hit_at_5_floor" in rag_floors:
        floor = float(rag_floors["hit_at_5_floor"])
        value = float(retrieval.get("hit_at_5", 0.0))
        if value < floor:
            breaches.append(
                f"retrieval.hit_at_5 = {value:.4f} below floor {floor:.4f}"
            )
    if "mrr_at_10_floor" in rag_floors:
        floor = float(rag_floors["mrr_at_10_floor"])
        value = float(retrieval.get("mrr_at_10", 0.0))
        if value < floor:
            breaches.append(
                f"retrieval.mrr_at_10 = {value:.4f} below floor {floor:.4f}"
            )
    return breaches


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the RAG eval gate.")
    parser.add_argument(
        "--mode", required=True, choices=["naive", "advanced"],
        help="Pipeline to evaluate.",
    )
    parser.add_argument(
        "--api-base", default=os.environ.get("EVAL_API_BASE", "http://localhost:8000"),
        help="API base URL for advanced mode (default: http://localhost:8000).",
    )
    parser.add_argument(
        "--corpus-run-id", default=None,
        help="Override the corpus run id (default: RAG_CORPUS_RUN_ID env / settings).",
    )
    parser.add_argument(
        "--golden", default=str(GOLDEN_PATH),
        help="Path to golden.jsonl.",
    )
    parser.add_argument(
        "--output", default=None,
        help="Write the report JSON to this path (in addition to MinIO if --upload-report).",
    )
    parser.add_argument(
        "--upload-report", action="store_true",
        help="Upload the report to MinIO at evals/reports/{run_ts}/rag.json.",
    )
    parser.add_argument(
        "--skip-upload", action="store_true",
        help="Skip MinIO upload (default when neither --upload-report nor --skip-upload is set is to print only).",
    )
    parser.add_argument(
        "--check-thresholds", action="store_true",
        help="Read eval_thresholds.yaml's rag: section and exit non-zero on any breach.",
    )
    parser.add_argument(
        "--max-questions", type=int, default=None,
        help="Cap the number of golden questions evaluated (for smoke runs).",
    )
    parser.add_argument(
        "--with-generation", action="store_true",
        help="Run the frozen-Claude-Haiku generation eval (T036). Costs Anthropic credits.",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    corpus_run_id = args.corpus_run_id or get_settings().rag_corpus_run_id
    if not corpus_run_id:
        print("RAG_CORPUS_RUN_ID is not configured.", file=sys.stderr)
        return 2

    golden = load_golden(Path(args.golden))
    if args.max_questions is not None:
        golden = golden[: args.max_questions]
    print(f"Loaded {len(golden)} golden rows from {args.golden}", file=sys.stderr)

    predictions = run_retrieval(
        args.mode,
        golden=golden,
        corpus_run_id=corpus_run_id,
        api_base=args.api_base,
        k_for_metrics=30,
    )
    retrieval_metrics = compute_metrics(predictions, golden)
    generation_block: dict[str, Any] = {}
    if args.with_generation:
        print("Running generation eval (frozen Claude Haiku judge)…", file=sys.stderr)
        generation_block = run_generation_eval(predictions, golden)
    report = build_report(
        args.mode,
        corpus_run_id=corpus_run_id,
        golden=golden,
        predictions=predictions,
        retrieval_metrics=retrieval_metrics,
        generation_metrics=generation_block,
    )

    print(json.dumps({"retrieval": report["retrieval"], "n_examples": report["n_examples"]}, indent=2))

    if args.output:
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
        print(f"Wrote report → {out_path}", file=sys.stderr)

    if args.upload_report and not args.skip_upload:
        uri = upload_report(report, run_ts=report["run_ts"])
        print(f"Uploaded → {uri}", file=sys.stderr)

    if args.check_thresholds:
        import yaml
        with open(
            Path(__file__).resolve().parents[2] / "eval_thresholds.yaml",
            encoding="utf-8",
        ) as fh:
            thresholds = yaml.safe_load(fh) or {}
        breaches = check_thresholds(report, thresholds)
        if breaches:
            for msg in breaches:
                print(f"THRESHOLD BREACH: {msg}", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
