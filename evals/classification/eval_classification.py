"""Classification eval suite — two modes:

* ``--measure``  Load the deployed DistilBERT from MinIO, classify every row
  in ``evals/classification/golden.jsonl``, and write fresh metrics to
  ``evals/classification/last_eval.json``. Operator-run, after any model
  retraining or golden-set edit.

* ``--check``  (CI default) Read ``last_eval.json``, compare to
  ``eval_thresholds.yaml``, exit non-zero if any metric is below its floor.
  This is the gate that fires on every push (Rule 5 / Rule 10).

Splitting measure/check this way keeps the gate runnable in CI without
shipping the ~500MB DistilBERT artifact into the CI runner: the operator
commits a fresh ``last_eval.json`` after each model run, CI verifies that
JSON against the thresholds. Drift is bounded by an integrity check —
``--check`` fails if the metrics file's golden-set hash doesn't match the
current committed golden set (a forcing function to re-measure).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

# The script lives outside the app package; make repo root importable.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

logger = logging.getLogger("eval_classification")

ROOT = Path(__file__).resolve().parents[2]
THRESHOLDS_PATH = ROOT / "eval_thresholds.yaml"
GOLDEN_PATH = Path(__file__).parent / "golden.jsonl"
LAST_EVAL_PATH = Path(__file__).parent / "last_eval.json"

CLASSES = ("bug", "docs", "feature", "question")


@dataclass(frozen=True)
class GoldenExample:
    id: str
    target_class: str
    title: str
    body: str


def load_thresholds() -> dict[str, Any]:
    with THRESHOLDS_PATH.open(encoding="utf-8") as fh:
        cfg: dict[str, Any] = yaml.safe_load(fh)
    return cfg


def load_golden() -> list[GoldenExample]:
    out: list[GoldenExample] = []
    with GOLDEN_PATH.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            out.append(
                GoldenExample(
                    id=str(row["id"]),
                    target_class=str(row["target_class"]),
                    title=str(row.get("title", "")),
                    body=str(row.get("body", "")),
                )
            )
    return out


def golden_set_hash(examples: list[GoldenExample]) -> str:
    """SHA-256 over the canonical (id-sorted) golden set content.

    Used by --check to invalidate a stale last_eval.json when the golden
    set has been edited but metrics weren't regenerated.
    """
    canonical = json.dumps(
        [
            {
                "id": e.id,
                "target_class": e.target_class,
                "title": e.title,
                "body": e.body,
            }
            for e in sorted(examples, key=lambda e: e.id)
        ],
        sort_keys=True,
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def load_last_eval() -> dict[str, Any]:
    if not LAST_EVAL_PATH.exists():
        raise SystemExit(
            f"missing {LAST_EVAL_PATH}\n"
            f"Run `python evals/classification/eval_classification.py --measure` "
            f"against the deployed classifier and commit the result."
        )
    with LAST_EVAL_PATH.open(encoding="utf-8") as fh:
        data: dict[str, Any] = json.load(fh)
    return data


# --- metrics (mirrors scripts/eval/llm_baseline_classifier.py) --------------


def _accuracy(pairs: list[tuple[str, str | None]]) -> float:
    matched = total = 0
    for truth, pred in pairs:
        if pred is None:
            continue
        total += 1
        if pred == truth:
            matched += 1
    return matched / total if total else 0.0


def _per_class_f1(pairs: list[tuple[str, str | None]]) -> dict[str, float]:
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


def compute_metrics(
    pairs: list[tuple[str, str | None]],
) -> dict[str, Any]:
    per_class = _per_class_f1(pairs)
    return {
        "accuracy": _accuracy(pairs),
        "macro_f1": sum(per_class.values()) / len(CLASSES),
        "per_class_f1": per_class,
        "per_class_counts": dict(Counter(t for t, _ in pairs)),
    }


# --- measure mode ----------------------------------------------------------


def _utc_now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def measure() -> int:
    """Live-measure DistilBERT on the golden set.

    Imports torch/transformers lazily so --check stays installable without
    the ml dependency group.
    """
    # Lazy imports so CI's --check doesn't pull torch.
    from app.infra.minio_client import DATA_BUCKET, get_client  # noqa: PLC0415
    from model_server.boot_check import verify_artifacts  # noqa: PLC0415
    from model_server.inference import load_model, predict  # noqa: PLC0415
    from model_server.storage import MinioArtifactStorage  # noqa: PLC0415

    model_run_id = os.environ.get("MODEL_RUN_ID")
    dataset_run_id = os.environ.get("DATASET_RUN_ID")
    if not model_run_id or not dataset_run_id:
        raise SystemExit(
            "MODEL_RUN_ID and DATASET_RUN_ID must be set; "
            "the same values the model server boots with."
        )

    storage = MinioArtifactStorage(
        client=get_client(),
        bucket=DATA_BUCKET,
        model_run_id=model_run_id,
        dataset_run_id=dataset_run_id,
    )
    verified = verify_artifacts(storage)
    loaded = load_model(verified)

    examples = load_golden()
    logger.info("classifying %d golden examples", len(examples))
    pairs: list[tuple[str, str | None]] = []
    for ex in examples:
        result = predict(loaded, ex.title, ex.body)
        pairs.append((ex.target_class, result.label))

    metrics = compute_metrics(pairs)
    report = {
        "model_run_id": model_run_id,
        "dataset_run_id": dataset_run_id,
        "weights_sha256": verified.model_card["weights"]["weights_sha256"],
        "golden_set_hash": golden_set_hash(examples),
        "n_examples": len(examples),
        "measured_at": _utc_now(),
        "accuracy": metrics["accuracy"],
        "macro_f1": metrics["macro_f1"],
        "per_class_f1": metrics["per_class_f1"],
        "per_class_counts": metrics["per_class_counts"],
    }
    LAST_EVAL_PATH.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    logger.info("wrote %s", LAST_EVAL_PATH)
    print(json.dumps(report, indent=2))
    return 0


# --- check mode ------------------------------------------------------------


def check() -> int:
    cfg = load_thresholds()
    if not cfg.get("enforced", False):
        logger.warning("eval_thresholds.yaml has enforced=false; skipping gate")
        return 0

    classifier_cfg = cfg.get("classifier") or {}
    macro_floor = float(classifier_cfg.get("macro_f1_floor", 0.0))
    per_class_floors: dict[str, float] = {
        k: float(v) for k, v in (classifier_cfg.get("per_class_f1_floor") or {}).items()
    }

    metrics = load_last_eval()
    examples = load_golden()

    errors: list[str] = []

    expected_hash = golden_set_hash(examples)
    actual_hash = metrics.get("golden_set_hash")
    if actual_hash != expected_hash:
        errors.append(
            "golden_set_hash mismatch — last_eval.json was measured against a "
            "different golden set than the one currently committed. Re-run "
            "`--measure` and commit the fresh last_eval.json."
        )

    macro_f1 = float(metrics.get("macro_f1", 0.0))
    if macro_f1 < macro_floor:
        errors.append(
            f"macro_f1 {macro_f1:.4f} < floor {macro_floor:.4f}"
        )

    per_class = metrics.get("per_class_f1") or {}
    for cls, floor in per_class_floors.items():
        f1 = float(per_class.get(cls, 0.0))
        if f1 < floor:
            errors.append(
                f"per_class_f1[{cls}] {f1:.4f} < floor {floor:.4f}"
            )

    if errors:
        for e in errors:
            print(f"FAIL: {e}", file=sys.stderr)
        return 1

    print(
        f"PASS: classifier macro_f1={macro_f1:.4f} (floor {macro_floor:.4f}); "
        f"per-class F1 above floors"
    )
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--check",
        action="store_true",
        help="CI mode: read last_eval.json and gate on thresholds",
    )
    mode.add_argument(
        "--measure",
        action="store_true",
        help="operator mode: run DistilBERT on the golden set and write last_eval.json",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    args = parse_args(argv)
    if args.measure:
        return measure()
    return check()


# Legacy entry point retained for backwards compatibility with any caller
# that imported the old stub.
def run_eval() -> dict[str, object]:
    return {
        "suite": "classification",
        "enforced": bool(load_thresholds().get("enforced", False)),
        "status": "use --check or --measure",
    }


if __name__ == "__main__":
    sys.exit(main())
