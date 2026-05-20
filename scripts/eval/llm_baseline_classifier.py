"""Benchmark Claude Haiku as a classifier on the canonical test split.

Reads ``processed/pandas/{dataset_run_id}/test.parquet`` from MinIO,
classifies each row with Claude Haiku using the committed system/user
prompt at ``prompts/llm_baseline_classifier.md``, and writes a single
JSON report to ``artifacts/llm_baseline/{run_id}/report.json``.

The report captures accuracy, macro-F1, per-class F1, latency p50/p95,
and total + per-1k cost so the slice-(h) DECISIONS.md table can compare
DistilBERT and Haiku apples-to-apples. The script runs the test set
through with bounded concurrency (4 workers by default) so a real run
on ~2.5k examples finishes in roughly 10 minutes and stays well under
Anthropic's rate limits.

Run (after `vault_seed.sh` has stored a real `anthropic_api_key`):

    uv run python scripts/eval/llm_baseline_classifier.py \\
        --dataset-run-id 20260519T133455Z

Override price-per-token defaults if Anthropic pricing has shifted:

    --input-price-per-m 1.0  --output-price-per-m 5.0 \\
        --cache-read-discount 0.1  --cache-write-multiplier 1.25

`--max-rows N` runs a small dry-run sample for cost/sanity checking.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import io
import json
import logging
import sys
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import anthropic
import pandas as pd

from app.infra.minio_client import DATA_BUCKET, get_client
from app.infra.vault_client import KEY_ANTHROPIC_API_KEY, read_secrets
from model_server.prompts import load_system_user, render

logger = logging.getLogger("llm_baseline")

PROMPT_PATH = Path(__file__).resolve().parents[2] / "prompts" / "llm_baseline_classifier.md"
DEFAULT_MODEL = "claude-haiku-4-5-20251001"
CLASSES = ("bug", "docs", "feature", "question")


@dataclass
class Prediction:
    issue_number: int
    true_label: str
    pred_label: str | None  # None when the LLM emitted an unparseable response
    raw_text: str
    latency_ms: float
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0


@dataclass
class CostBreakdown:
    input: float = 0.0
    output: float = 0.0
    cache_read: float = 0.0
    cache_creation: float = 0.0

    @property
    def total(self) -> float:
        return self.input + self.output + self.cache_read + self.cache_creation


@dataclass
class TokenTotals:
    input_total: int = 0
    output_total: int = 0
    cache_read_total: int = 0
    cache_creation_total: int = 0

    def add(self, p: Prediction) -> None:
        self.input_total += p.input_tokens
        self.output_total += p.output_tokens
        self.cache_read_total += p.cache_read_tokens
        self.cache_creation_total += p.cache_creation_tokens


@dataclass
class PriceConfig:
    input_per_million: float = 1.00
    output_per_million: float = 5.00
    cache_read_discount: float = 0.10
    cache_write_multiplier: float = 1.25


@dataclass
class Report:
    run_id: str
    dataset_run_id: str
    model: str
    prompt_path: str
    test_n: int
    evaluated_n: int
    unparseable_n: int
    metrics: dict[str, Any]
    latency_ms: dict[str, float]
    cost_usd: dict[str, float]
    tokens: dict[str, int]
    concurrency: int
    price_config: dict[str, float]
    started_at: str
    finished_at: str
    sample_predictions: list[dict[str, Any]] = field(default_factory=list)


def _utc_now_compact() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def _read_test_split(dataset_run_id: str) -> pd.DataFrame:
    key = f"processed/pandas/{dataset_run_id}/test.parquet"
    logger.info("reading s3://%s/%s", DATA_BUCKET, key)
    body = get_client().get_object(Bucket=DATA_BUCKET, Key=key)["Body"].read()
    return pd.read_parquet(io.BytesIO(body))


def _make_anthropic_client() -> anthropic.Anthropic:
    key = read_secrets([KEY_ANTHROPIC_API_KEY])[KEY_ANTHROPIC_API_KEY]
    if not key:
        raise RuntimeError(
            "anthropic_api_key not seeded in Vault; run "
            "ANTHROPIC_API_KEY=sk-ant-... bash scripts/vault_seed.sh"
        )
    return anthropic.Anthropic(api_key=key)


def _parse_label(raw: str) -> str | None:
    cleaned = raw.strip().lower().split()[0] if raw.strip() else ""
    cleaned = cleaned.strip(".,:;'\"`*")
    return cleaned if cleaned in CLASSES else None


def classify_one(
    client: anthropic.Anthropic,
    *,
    system_prompt: str,
    user_template: str,
    title: str,
    body: str,
    issue_number: int,
    true_label: str,
    model: str,
) -> Prediction:
    user_message = render(user_template, title=title, body=body)
    t0 = time.perf_counter()
    resp = client.messages.create(
        model=model,
        max_tokens=8,
        temperature=0,
        system=[
            {
                "type": "text",
                "text": system_prompt,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[{"role": "user", "content": user_message}],
    )
    latency_ms = (time.perf_counter() - t0) * 1000.0

    raw_text = resp.content[0].text if resp.content else ""
    pred = _parse_label(raw_text)
    usage = resp.usage
    return Prediction(
        issue_number=issue_number,
        true_label=true_label,
        pred_label=pred,
        raw_text=raw_text,
        latency_ms=latency_ms,
        input_tokens=getattr(usage, "input_tokens", 0) or 0,
        output_tokens=getattr(usage, "output_tokens", 0) or 0,
        cache_read_tokens=getattr(usage, "cache_read_input_tokens", 0) or 0,
        cache_creation_tokens=getattr(usage, "cache_creation_input_tokens", 0) or 0,
    )


def _accuracy(preds: Iterable[Prediction]) -> float:
    matched = total = 0
    for p in preds:
        if p.pred_label is None:
            continue
        total += 1
        if p.pred_label == p.true_label:
            matched += 1
    return matched / total if total else 0.0


def _per_class_f1(preds: list[Prediction]) -> dict[str, float]:
    out: dict[str, float] = {}
    for c in CLASSES:
        tp = fp = fn = 0
        for p in preds:
            if p.pred_label is None:
                continue
            if p.true_label == c and p.pred_label == c:
                tp += 1
            elif p.true_label != c and p.pred_label == c:
                fp += 1
            elif p.true_label == c and p.pred_label != c:
                fn += 1
        precision = tp / (tp + fp) if (tp + fp) else 0.0
        recall = tp / (tp + fn) if (tp + fn) else 0.0
        out[c] = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    return out


def _percentile(sorted_values: list[float], q: float) -> float:
    if not sorted_values:
        return 0.0
    k = max(0, min(len(sorted_values) - 1, int(round(q * (len(sorted_values) - 1)))))
    return sorted_values[k]


def compute_metrics(preds: list[Prediction]) -> dict[str, Any]:
    per_class = _per_class_f1(preds)
    macro = sum(per_class.values()) / len(CLASSES)
    return {
        "accuracy": _accuracy(preds),
        "macro_f1": macro,
        "per_class_f1": per_class,
    }


def compute_latency(preds: list[Prediction]) -> dict[str, float]:
    sorted_lat = sorted(p.latency_ms for p in preds)
    return {
        "p50": _percentile(sorted_lat, 0.50),
        "p95": _percentile(sorted_lat, 0.95),
        "mean": sum(sorted_lat) / len(sorted_lat) if sorted_lat else 0.0,
    }


def compute_cost(preds: list[Prediction], price: PriceConfig) -> tuple[CostBreakdown, TokenTotals]:
    totals = TokenTotals()
    for p in preds:
        totals.add(p)
    cost = CostBreakdown(
        input=totals.input_total * price.input_per_million / 1_000_000.0,
        output=totals.output_total * price.output_per_million / 1_000_000.0,
        cache_read=(
            totals.cache_read_total
            * price.input_per_million
            * price.cache_read_discount
            / 1_000_000.0
        ),
        cache_creation=(
            totals.cache_creation_total
            * price.input_per_million
            * price.cache_write_multiplier
            / 1_000_000.0
        ),
    )
    return cost, totals


def build_report(
    *,
    run_id: str,
    dataset_run_id: str,
    model: str,
    preds: list[Prediction],
    test_n: int,
    concurrency: int,
    price: PriceConfig,
    started_at: str,
    finished_at: str,
) -> Report:
    cost, tokens = compute_cost(preds, price)
    per_1k = (cost.total / len(preds) * 1000.0) if preds else 0.0
    evaluated_n = sum(1 for p in preds if p.pred_label is not None)
    unparseable_n = len(preds) - evaluated_n
    return Report(
        run_id=run_id,
        dataset_run_id=dataset_run_id,
        model=model,
        prompt_path=str(PROMPT_PATH.relative_to(PROMPT_PATH.parents[1])),
        test_n=test_n,
        evaluated_n=evaluated_n,
        unparseable_n=unparseable_n,
        metrics=compute_metrics(preds),
        latency_ms=compute_latency(preds),
        cost_usd={
            "total": cost.total,
            "input": cost.input,
            "output": cost.output,
            "cache_read": cost.cache_read,
            "cache_creation": cost.cache_creation,
            "per_1k_predictions": per_1k,
        },
        tokens={
            "input_total": tokens.input_total,
            "output_total": tokens.output_total,
            "cache_read_total": tokens.cache_read_total,
            "cache_creation_total": tokens.cache_creation_total,
        },
        concurrency=concurrency,
        price_config={
            "input_per_million_usd": price.input_per_million,
            "output_per_million_usd": price.output_per_million,
            "cache_read_discount": price.cache_read_discount,
            "cache_write_multiplier": price.cache_write_multiplier,
        },
        started_at=started_at,
        finished_at=finished_at,
        sample_predictions=[
            {
                "issue_number": p.issue_number,
                "true_label": p.true_label,
                "pred_label": p.pred_label,
                "raw_text": p.raw_text,
            }
            for p in preds[:10]
        ],
    )


def _upload_report(report: Report) -> str:
    key = f"artifacts/llm_baseline/{report.run_id}/report.json"
    body = json.dumps(report.__dict__, indent=2, default=str).encode("utf-8")
    get_client().put_object(Bucket=DATA_BUCKET, Key=key, Body=body)
    return f"s3://{DATA_BUCKET}/{key}"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--dataset-run-id", required=True)
    p.add_argument("--run-id", default=None, help="defaults to a UTC timestamp")
    p.add_argument("--model", default=DEFAULT_MODEL)
    p.add_argument("--concurrency", type=int, default=4)
    p.add_argument(
        "--max-rows",
        type=int,
        default=None,
        help="dry-run: classify only this many rows (no MinIO upload)",
    )
    p.add_argument("--input-price-per-m", type=float, default=1.00)
    p.add_argument("--output-price-per-m", type=float, default=5.00)
    p.add_argument("--cache-read-discount", type=float, default=0.10)
    p.add_argument("--cache-write-multiplier", type=float, default=1.25)
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    args = parse_args(argv)
    started_at = _utc_now_compact()
    run_id = args.run_id or started_at
    price = PriceConfig(
        input_per_million=args.input_price_per_m,
        output_per_million=args.output_price_per_m,
        cache_read_discount=args.cache_read_discount,
        cache_write_multiplier=args.cache_write_multiplier,
    )

    df = _read_test_split(args.dataset_run_id)
    if args.max_rows is not None:
        df = df.head(args.max_rows)
    test_n = len(df)
    logger.info(
        "classifying %d examples (concurrency=%d, model=%s)",
        test_n,
        args.concurrency,
        args.model,
    )

    system_prompt, user_template = load_system_user(PROMPT_PATH)
    client = _make_anthropic_client()

    preds: list[Prediction] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        futures = [
            pool.submit(
                classify_one,
                client,
                system_prompt=system_prompt,
                user_template=user_template,
                title=str(row.title or ""),
                body=str(row.body or ""),
                issue_number=int(row.issue_number),
                true_label=str(row.target_class),
                model=args.model,
            )
            for row in df.itertuples(index=False)
        ]
        for i, fut in enumerate(concurrent.futures.as_completed(futures), start=1):
            preds.append(fut.result())
            if i % 100 == 0:
                logger.info("progress: %d / %d", i, test_n)

    finished_at = _utc_now_compact()
    report = build_report(
        run_id=run_id,
        dataset_run_id=args.dataset_run_id,
        model=args.model,
        preds=preds,
        test_n=test_n,
        concurrency=args.concurrency,
        price=price,
        started_at=started_at,
        finished_at=finished_at,
    )

    if args.max_rows is None:
        location = _upload_report(report)
        logger.info("uploaded report -> %s", location)
    else:
        logger.info("dry-run (max_rows=%d); not uploading", args.max_rows)

    print(json.dumps(report.__dict__, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
