"""Classifier eval gate: live DistilBERT inference over the golden set.

Calls the deployed model server's ``/classify`` endpoint once per row in
``evals/classification/golden.jsonl``, computes macro-F1 + per-class F1 +
the confusion matrix, writes the full report to MinIO at
``evals/reports/{ts}/classification.json``, and exits non-zero if macro-F1
falls below the floor in ``eval_thresholds.yaml``.

Why macro-F1 only: with 6-7 examples per class, a single misclassification
swings per-class F1 by ~15-17 points — gating on that would be gating on
noise. Per-class F1 and the confusion matrix are still **computed and
reported** (the brief requires them) so an operator can diagnose any
regression, but only macro-F1 is enforced. See
``evals/classification/README.md`` for the selection logic.

CI invokes this after the compose stack is healthy:

    uv run python evals/classification/eval_classification.py
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import yaml

# The script lives outside the app package; make repo root importable.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from app.infra.minio_client import DATA_BUCKET, get_client  # noqa: E402

logger = logging.getLogger("eval_classification")

ROOT = Path(__file__).resolve().parents[2]
THRESHOLDS_PATH = ROOT / "eval_thresholds.yaml"
GOLDEN_PATH = Path(__file__).parent / "golden.jsonl"

CLASSES = ("bug", "docs", "feature", "question")
DEFAULT_MODEL_SERVER_URL = "http://localhost:8001"
DEFAULT_CLASSIFY_TIMEOUT_S = 10.0


# --- IO -------------------------------------------------------------------


def load_thresholds() -> dict[str, Any]:
    with THRESHOLDS_PATH.open(encoding="utf-8") as fh:
        cfg: dict[str, Any] = yaml.safe_load(fh)
    return cfg


def load_golden() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with GOLDEN_PATH.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    return rows


# --- metrics --------------------------------------------------------------


def per_class_f1(pairs: list[tuple[str, str | None]]) -> dict[str, float]:
    out: dict[str, float] = {}
    for c in CLASSES:
        tp = fp = fn = 0
        for truth, pred in pairs:
            if pred is None:
                continue
            if truth == c and pred == c:
                tp += 1
            elif truth != c and pred == c:
                fp += 1
            elif truth == c and pred != c:
                fn += 1
        precision = tp / (tp + fp) if (tp + fp) else 0.0
        recall = tp / (tp + fn) if (tp + fn) else 0.0
        out[c] = (
            2 * precision * recall / (precision + recall)
            if (precision + recall)
            else 0.0
        )
    return out


def macro_f1(per_class: dict[str, float]) -> float:
    return sum(per_class.values()) / len(CLASSES)


def confusion_matrix(pairs: list[tuple[str, str | None]]) -> dict[str, dict[str, int]]:
    """Nested dict ``{true_class: {pred_class_or_None: count}}``."""
    matrix: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for truth, pred in pairs:
        key = pred if pred is not None else "_unparseable"
        matrix[truth][key] += 1
    return {t: dict(row) for t, row in matrix.items()}


# --- inference ------------------------------------------------------------


def classify_one(
    client: httpx.Client,
    *,
    url: str,
    title: str,
    body: str,
    issue_number: int,
) -> str | None:
    """Call ``/classify`` and return the predicted label, or None on failure."""
    try:
        resp = client.post(
            f"{url}/classify",
            json={"title": title, "body": body},
            headers={"X-Request-Id": f"eval-{issue_number}"},
        )
    except (httpx.NetworkError, httpx.TimeoutException) as exc:
        logger.error("issue %d: model server unreachable: %s", issue_number, exc)
        return None
    if resp.status_code != 200:
        logger.error(
            "issue %d: /classify returned %d: %s",
            issue_number,
            resp.status_code,
            resp.text[:200],
        )
        return None
    try:
        label = str(resp.json()["label"])
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        logger.error("issue %d: malformed /classify response: %s", issue_number, exc)
        return None
    return label if label in CLASSES else None


# --- report ---------------------------------------------------------------


def _utc_ts_compact() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def build_report(
    *,
    run_ts: str,
    macro: float,
    per_class: dict[str, float],
    matrix: dict[str, dict[str, int]],
    predictions: list[dict[str, Any]],
    macro_floor: float,
    failed_n: int,
) -> dict[str, Any]:
    return {
        "run_ts": run_ts,
        "golden_set": str(GOLDEN_PATH.relative_to(ROOT)),
        "n_examples": len(predictions),
        "n_failed_calls": failed_n,
        "macro_f1": macro,
        "macro_f1_floor": macro_floor,
        "macro_f1_passes": macro >= macro_floor,
        "per_class_f1": per_class,
        "per_class_counts": dict(
            Counter(p["true_class"] for p in predictions)
        ),
        "confusion_matrix": matrix,
        "predictions": predictions,
    }


def upload_report(report: dict[str, Any]) -> str:
    key = f"evals/reports/{report['run_ts']}/classification.json"
    body = json.dumps(report, indent=2, ensure_ascii=False).encode("utf-8")
    get_client().put_object(Bucket=DATA_BUCKET, Key=key, Body=body)
    return f"s3://{DATA_BUCKET}/{key}"


# --- main -----------------------------------------------------------------


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument(
        "--model-server-url",
        default=os.environ.get("MODEL_SERVER_URL", DEFAULT_MODEL_SERVER_URL),
        help="base URL for the model server (default: %(default)s)",
    )
    p.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_CLASSIFY_TIMEOUT_S,
        help="per-request timeout in seconds (default: %(default)s)",
    )
    p.add_argument(
        "--skip-upload",
        action="store_true",
        help="compute the gate but don't upload to MinIO (for local dry runs)",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    args = parse_args(argv)
    cfg = load_thresholds()
    if not cfg.get("enforced", False):
        logger.warning("eval_thresholds.yaml has enforced=false; skipping gate")
        return 0

    macro_floor = float(cfg["classifier"]["macro_f1_floor"])
    golden = load_golden()
    if not golden:
        raise SystemExit(f"{GOLDEN_PATH} is empty")
    logger.info(
        "running %d-example classifier gate against %s (macro_f1 floor %.2f)",
        len(golden),
        args.model_server_url,
        macro_floor,
    )

    pairs: list[tuple[str, str | None]] = []
    predictions: list[dict[str, Any]] = []
    failed_n = 0
    with httpx.Client(timeout=args.timeout) as client:
        for row in golden:
            true_class = str(row["true_class"])
            pred = classify_one(
                client,
                url=args.model_server_url,
                title=str(row.get("title") or ""),
                body=str(row.get("body") or ""),
                issue_number=int(row["issue_number"]),
            )
            if pred is None:
                failed_n += 1
            pairs.append((true_class, pred))
            predictions.append(
                {
                    "issue_number": int(row["issue_number"]),
                    "true_class": true_class,
                    "pred_class": pred,
                    "correct": pred == true_class,
                }
            )

    per_class = per_class_f1(pairs)
    macro = macro_f1(per_class)
    matrix = confusion_matrix(pairs)
    report = build_report(
        run_ts=_utc_ts_compact(),
        macro=macro,
        per_class=per_class,
        matrix=matrix,
        predictions=predictions,
        macro_floor=macro_floor,
        failed_n=failed_n,
    )

    if not args.skip_upload:
        location = upload_report(report)
        logger.info("uploaded report -> %s", location)
    else:
        logger.info("skipping MinIO upload (--skip-upload)")

    # Console summary so CI logs carry the headline numbers without parsing JSON.
    logger.info(
        "macro_f1=%.4f (floor %.2f) per_class_f1=%s failed_calls=%d",
        macro,
        macro_floor,
        {c: round(per_class[c], 4) for c in CLASSES},
        failed_n,
    )

    if failed_n > 0:
        logger.error("%d model-server calls failed — treating as gate failure", failed_n)
        return 1
    if macro < macro_floor:
        logger.error(
            "FAIL: macro_f1 %.4f < floor %.2f", macro, macro_floor
        )
        return 1
    print(f"PASS: macro_f1={macro:.4f} (floor {macro_floor:.2f})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
