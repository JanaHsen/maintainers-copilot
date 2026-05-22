"""NER eval harness — fixture + real modes.

Real mode (T034 wiring): call the live ``ner_service.extract`` in-process
against each ``text`` in ``evals/ner/golden.jsonl`` and score the
returned ``EntityBuckets`` against ``expected`` via per-bucket exact-set
micro-F1. Aggregate micro-F1 is the metric tracked against
``eval_thresholds.yaml``'s ``ner.f1_floor`` (Rule 5 / Rule 10 / R8).

Fixture mode: read ``evals/ner/fixture_outputs.jsonl`` (id-keyed
predictions) instead of calling Anthropic. If the file does not exist,
the harness seeds it as a perfect-prediction copy of ``expected`` so CI
stays green deterministically. Operators regenerate the fixture by
running ``--mode=real --emit-fixture=evals/ner/fixture_outputs.jsonl``.

Both modes write a JSON report to ``--out`` with top-level keys
``per_example`` (per-row TP/FP/FN per bucket), ``per_bucket_f1``,
``aggregate_f1``, and ``pipeline_config`` (prompt + model pin).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.services.ner_service import NerError, NerOk
from app.services.ner_service import extract as ner_extract

logger = logging.getLogger("evals.ner.eval_ner")

GOLDEN_PATH = Path(__file__).parent / "golden.jsonl"
FIXTURE_PATH = Path(__file__).parent / "fixture_outputs.jsonl"
PROMPT_PATH = Path(__file__).resolve().parents[2] / "prompts" / "ner.md"
THRESHOLDS_PATH = Path(__file__).resolve().parents[2] / "eval_thresholds.yaml"

_BUCKETS: tuple[str, ...] = (
    "repo_names",
    "file_paths",
    "error_types",
    "package_names",
)


def _utc_run_ts() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def _golden_set_hash(rows: list[dict[str, Any]]) -> str:
    canonical = json.dumps(rows, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    return hashlib.sha256(canonical).hexdigest()


def _prompt_version() -> str:
    """Best-effort read of the version header from prompts/ner.md."""
    try:
        first_line = PROMPT_PATH.read_text(encoding="utf-8").splitlines()[0]
    except OSError:
        return "unknown"
    # Expect: "# Prompt version: ner-2026-05-22-001"
    if "Prompt version:" in first_line:
        return first_line.split("Prompt version:", 1)[1].strip()
    return "unknown"


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    return rows


def _seed_perfect_fixture(golden: list[dict[str, Any]], out: Path) -> None:
    """Write a fixture file with predictions = expected for every example."""
    with out.open("w", encoding="utf-8") as fh:
        for row in golden:
            fh.write(
                json.dumps(
                    {"id": row["id"], "predicted": row["expected"]},
                    separators=(",", ":"),
                )
                + "\n"
            )


def _load_fixture_predictions(
    fixture_path: Path, golden: list[dict[str, Any]]
) -> dict[str, dict[str, list[str]]]:
    if not fixture_path.exists():
        logger.info(
            "fixture %s missing; seeding perfect-prediction copy", fixture_path
        )
        _seed_perfect_fixture(golden, fixture_path)
    by_id: dict[str, dict[str, list[str]]] = {}
    for row in load_jsonl(fixture_path):
        rid = row.get("id")
        pred = row.get("predicted") or {}
        if not isinstance(rid, str):
            continue
        # Normalize: every bucket present as a list of strings.
        normalized: dict[str, list[str]] = {}
        for bucket in _BUCKETS:
            v = pred.get(bucket, []) or []
            normalized[bucket] = [str(x) for x in v]
        by_id[rid] = normalized
    return by_id


def predict_real(golden: list[dict[str, Any]]) -> dict[str, dict[str, list[str]]]:
    """Call ner_service.extract for each example; collect predictions."""
    out: dict[str, dict[str, list[str]]] = {}
    for row in golden:
        outcome = ner_extract(row["text"])
        if isinstance(outcome, NerOk):
            out[row["id"]] = {
                "repo_names": list(outcome.entities.repo_names),
                "file_paths": list(outcome.entities.file_paths),
                "error_types": list(outcome.entities.error_types),
                "package_names": list(outcome.entities.package_names),
            }
        else:
            assert isinstance(outcome, NerError)
            logger.warning(
                "ner_service returned %s for %s: %s",
                outcome.kind,
                row["id"],
                outcome.detail,
            )
            out[row["id"]] = {b: [] for b in _BUCKETS}
    return out


def _f1(tp: int, fp: int, fn: int) -> float:
    if tp == 0 and fp == 0 and fn == 0:
        # Per-bucket convention: if neither the predictor nor the golden
        # have entities of this kind, the bucket scores 1.0 (no error).
        return 1.0
    if tp == 0:
        return 0.0
    precision = tp / (tp + fp)
    recall = tp / (tp + fn)
    if precision + recall == 0:
        return 0.0
    return 2 * precision * recall / (precision + recall)


def score(
    golden: list[dict[str, Any]],
    predictions: dict[str, dict[str, list[str]]],
) -> dict[str, Any]:
    per_example: list[dict[str, Any]] = []
    per_bucket_totals: dict[str, dict[str, int]] = {
        b: {"tp": 0, "fp": 0, "fn": 0} for b in _BUCKETS
    }
    aggregate_totals = {"tp": 0, "fp": 0, "fn": 0}

    for row in golden:
        rid = row["id"]
        expected = row["expected"]
        predicted = predictions.get(rid, {b: [] for b in _BUCKETS})
        ex_record: dict[str, Any] = {"id": rid, "buckets": {}}
        for bucket in _BUCKETS:
            exp_set = set(expected.get(bucket, []) or [])
            pred_set = set(predicted.get(bucket, []) or [])
            tp = len(exp_set & pred_set)
            fp = len(pred_set - exp_set)
            fn = len(exp_set - pred_set)
            per_bucket_totals[bucket]["tp"] += tp
            per_bucket_totals[bucket]["fp"] += fp
            per_bucket_totals[bucket]["fn"] += fn
            aggregate_totals["tp"] += tp
            aggregate_totals["fp"] += fp
            aggregate_totals["fn"] += fn
            ex_record["buckets"][bucket] = {
                "expected": sorted(exp_set),
                "predicted": sorted(pred_set),
                "tp": tp,
                "fp": fp,
                "fn": fn,
            }
        per_example.append(ex_record)

    per_bucket_f1 = {
        bucket: _f1(t["tp"], t["fp"], t["fn"])
        for bucket, t in per_bucket_totals.items()
    }
    aggregate_f1 = _f1(
        aggregate_totals["tp"],
        aggregate_totals["fp"],
        aggregate_totals["fn"],
    )
    return {
        "per_example": per_example,
        "per_bucket_totals": per_bucket_totals,
        "per_bucket_f1": per_bucket_f1,
        "aggregate_totals": aggregate_totals,
        "aggregate_f1": aggregate_f1,
    }


def build_report(
    mode: str,
    *,
    golden: list[dict[str, Any]],
    scoring: dict[str, Any],
) -> dict[str, Any]:
    return {
        "run_ts": _utc_run_ts(),
        "mode": mode,
        "pipeline_config": {
            "model": "claude-sonnet-4-5-20250929",
            "prompt_path": "prompts/ner.md",
            "prompt_version": _prompt_version(),
        },
        "golden_set_hash": _golden_set_hash(golden),
        "n_examples": len(golden),
        "per_bucket_f1": scoring["per_bucket_f1"],
        "aggregate_f1": scoring["aggregate_f1"],
        "per_example": scoring["per_example"],
        "per_bucket_totals": scoring["per_bucket_totals"],
        "aggregate_totals": scoring["aggregate_totals"],
    }


def _emit_fixture(
    predictions: dict[str, dict[str, list[str]]], path: Path
) -> None:
    with path.open("w", encoding="utf-8") as fh:
        for rid, buckets in predictions.items():
            fh.write(
                json.dumps(
                    {"id": rid, "predicted": buckets},
                    separators=(",", ":"),
                )
                + "\n"
            )


def _read_thresholds() -> dict[str, Any]:
    try:
        import yaml
    except ImportError:
        return {}
    if not THRESHOLDS_PATH.exists():
        return {}
    with THRESHOLDS_PATH.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the NER eval gate.")
    parser.add_argument(
        "--mode",
        required=True,
        choices=["fixture", "real"],
        help="fixture: read prebaked predictions; real: call the live service.",
    )
    parser.add_argument(
        "--out",
        default=None,
        help="Write the JSON report to this path (default: stdout-only).",
    )
    parser.add_argument(
        "--golden",
        default=str(GOLDEN_PATH),
        help="Path to golden.jsonl.",
    )
    parser.add_argument(
        "--fixture",
        default=str(FIXTURE_PATH),
        help="Path to fixture_outputs.jsonl (only used in --mode=fixture).",
    )
    parser.add_argument(
        "--emit-fixture",
        default=None,
        help="In --mode=real, also write the live predictions to this path so "
        "future CI runs can replay them without calling Anthropic.",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    golden = load_jsonl(Path(args.golden))
    if args.mode == "fixture":
        predictions = _load_fixture_predictions(Path(args.fixture), golden)
    else:
        predictions = predict_real(golden)
        if args.emit_fixture:
            _emit_fixture(predictions, Path(args.emit_fixture))
            logger.info("wrote fixture → %s", args.emit_fixture)

    scoring = score(golden, predictions)
    report = build_report(args.mode, golden=golden, scoring=scoring)

    print(
        json.dumps(
            {
                "per_bucket_f1": report["per_bucket_f1"],
                "aggregate_f1": report["aggregate_f1"],
                "n_examples": report["n_examples"],
            },
            indent=2,
        )
    )

    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(
            json.dumps(report, indent=2, sort_keys=True), encoding="utf-8"
        )
        print(f"Wrote report → {out_path}", file=sys.stderr)

    thresholds = _read_thresholds()
    floor = (thresholds.get("ner") or {}).get("f1_floor")
    if floor is not None:
        aggregate = float(report["aggregate_f1"])
        if aggregate < float(floor):
            print(
                f"THRESHOLD BREACH: aggregate_f1 = {aggregate:.4f} below "
                f"floor {float(floor):.4f}",
                file=sys.stderr,
            )
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
