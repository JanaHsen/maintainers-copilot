"""Summarize eval harness — fixture + real modes.

Real mode: call ``summarize_service.summarize`` in-process for each
``text`` in ``evals/summarize/golden.jsonl`` to produce a candidate
summary, then ask the rubric judge (``prompts/summarize_judge.md``,
frozen Claude Haiku) to score it on faithfulness / conciseness /
intent (each 1-5). Per-example overall is the mean of the three;
aggregate is the mean of the per-example overalls. The aggregate is
tracked against ``eval_thresholds.yaml``'s ``summarize.rubric_floor``
(Rule 5 / Rule 10 / R8).

Fixture mode: read ``evals/summarize/fixture_outputs.jsonl``
(id-keyed candidate + scores) instead of calling Anthropic. If the
file does not exist, the harness seeds it from ``golden.jsonl``
(candidate = ``reference_summary``, scores = 5/5/5) so CI stays green
deterministically. Operators regenerate the fixture from a real run
via ``--mode=real --emit-fixture=evals/summarize/fixture_outputs.jsonl``.
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

from app.infra import anthropic_client
from app.infra.anthropic_client import AnthropicError
from app.services.summarize_service import SummarizeError, SummarizeOk
from app.services.summarize_service import summarize as summarize_service

logger = logging.getLogger("evals.summarize.eval_summarize")

GOLDEN_PATH = Path(__file__).parent / "golden.jsonl"
FIXTURE_PATH = Path(__file__).parent / "fixture_outputs.jsonl"
PROMPTS_DIR = Path(__file__).resolve().parents[2] / "prompts"
JUDGE_PROMPT_PATH = PROMPTS_DIR / "summarize_judge.md"
THRESHOLDS_PATH = Path(__file__).resolve().parents[2] / "eval_thresholds.yaml"

JUDGE_MODEL = "claude-haiku-4-5-20251001"
JUDGE_MAX_TOKENS = 200

_RUBRIC_KEYS: tuple[str, ...] = ("faithfulness", "conciseness", "intent")


def _utc_run_ts() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def _golden_set_hash(rows: list[dict[str, Any]]) -> str:
    canonical = json.dumps(rows, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    return hashlib.sha256(canonical).hexdigest()


def _prompt_version() -> str:
    try:
        first_line = JUDGE_PROMPT_PATH.read_text(encoding="utf-8").splitlines()[0]
    except OSError:
        return "unknown"
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


def _split_prompt(raw: str) -> tuple[str, str]:
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
        raise RuntimeError(
            f"{JUDGE_PROMPT_PATH} missing '## System' / '## User' sections"
        )
    return sections["system"], sections["user"]


def _load_judge_prompt() -> tuple[str, str]:
    return _split_prompt(JUDGE_PROMPT_PATH.read_text(encoding="utf-8"))


def _parse_scores(raw: str) -> dict[str, int] | None:
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()
    try:
        data: Any = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    scores: dict[str, int] = {}
    for key in _RUBRIC_KEYS:
        if key not in data:
            return None
        v = data[key]
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            return None
        iv = int(round(float(v)))
        if iv < 1 or iv > 5:
            return None
        scores[key] = iv
    return scores


def judge_summary(source_text: str, candidate_summary: str) -> dict[str, int]:
    """Ask the rubric judge for a (faithfulness, conciseness, intent) score."""
    system, user_template = _load_judge_prompt()
    user = user_template.replace("{{source_text}}", source_text).replace(
        "{{candidate_summary}}", candidate_summary
    )
    raw = anthropic_client.complete(
        system=system,
        user=user,
        model=JUDGE_MODEL,
        max_tokens=JUDGE_MAX_TOKENS,
    )
    parsed = _parse_scores(raw)
    if parsed is None:
        raise ValueError(
            f"judge returned non-rubric JSON: {raw[:200]!r}"
        )
    return parsed


def _seed_perfect_fixture(golden: list[dict[str, Any]], out: Path) -> None:
    """Write a fixture with candidate = reference_summary and 5/5/5 scores."""
    with out.open("w", encoding="utf-8") as fh:
        for row in golden:
            payload = {
                "id": row["id"],
                "candidate_summary": row["reference_summary"],
                "scores": {key: 5 for key in _RUBRIC_KEYS},
            }
            fh.write(json.dumps(payload, separators=(",", ":")) + "\n")


def _load_fixture(
    fixture_path: Path, golden: list[dict[str, Any]]
) -> dict[str, dict[str, Any]]:
    if not fixture_path.exists():
        logger.info(
            "fixture %s missing; seeding perfect-prediction copy", fixture_path
        )
        _seed_perfect_fixture(golden, fixture_path)
    by_id: dict[str, dict[str, Any]] = {}
    for row in load_jsonl(fixture_path):
        rid = row.get("id")
        if not isinstance(rid, str):
            continue
        candidate = row.get("candidate_summary") or ""
        scores_raw = row.get("scores") or {}
        scores: dict[str, int] = {}
        for key in _RUBRIC_KEYS:
            v = scores_raw.get(key)
            if not isinstance(v, (int, float)) or isinstance(v, bool):
                v = 0
            scores[key] = int(v)
        by_id[rid] = {"candidate_summary": str(candidate), "scores": scores}
    return by_id


def predict_real(golden: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Run summarize_service + judge for each example; collect per-example data."""
    out: dict[str, dict[str, Any]] = {}
    for row in golden:
        outcome = summarize_service(row["text"])
        if isinstance(outcome, SummarizeOk):
            candidate = outcome.summary
        else:
            assert isinstance(outcome, SummarizeError)
            logger.warning(
                "summarize_service returned %s for %s: %s",
                outcome.kind,
                row["id"],
                outcome.detail,
            )
            out[row["id"]] = {
                "candidate_summary": "",
                "scores": dict.fromkeys(_RUBRIC_KEYS, 0),
            }
            continue
        try:
            scores = judge_summary(row["text"], candidate)
        except (AnthropicError, ValueError, json.JSONDecodeError) as exc:
            logger.warning("judge failed for %s: %s", row["id"], exc)
            scores = dict.fromkeys(_RUBRIC_KEYS, 0)
        out[row["id"]] = {"candidate_summary": candidate, "scores": scores}
    return out


def score(
    golden: list[dict[str, Any]],
    predictions: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    per_example: list[dict[str, Any]] = []
    per_dimension_sums: dict[str, float] = dict.fromkeys(_RUBRIC_KEYS, 0.0)
    overall_sum = 0.0
    n = 0
    for row in golden:
        rid = row["id"]
        pred = predictions.get(
            rid,
            {
                "candidate_summary": "",
                "scores": dict.fromkeys(_RUBRIC_KEYS, 0),
            },
        )
        scores = pred["scores"]
        dim_values = [float(scores.get(k, 0)) for k in _RUBRIC_KEYS]
        overall = sum(dim_values) / len(dim_values)
        for key, val in zip(_RUBRIC_KEYS, dim_values, strict=True):
            per_dimension_sums[key] += val
        overall_sum += overall
        n += 1
        per_example.append(
            {
                "id": rid,
                "candidate_summary": pred["candidate_summary"],
                "scores": {k: int(scores.get(k, 0)) for k in _RUBRIC_KEYS},
                "overall": overall,
            }
        )
    per_dimension_means = (
        {k: per_dimension_sums[k] / n for k in _RUBRIC_KEYS}
        if n
        else dict.fromkeys(_RUBRIC_KEYS, 0.0)
    )
    aggregate = overall_sum / n if n else 0.0
    return {
        "per_example": per_example,
        "per_dimension_means": per_dimension_means,
        "aggregate": aggregate,
        "n_scored": n,
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
            "summarizer_model": "claude-haiku-4-5-20251001",
            "summarizer_prompt_path": "prompts/summarizer.md",
            "judge_model": JUDGE_MODEL,
            "judge_prompt_path": "prompts/summarize_judge.md",
            "judge_prompt_version": _prompt_version(),
        },
        "golden_set_hash": _golden_set_hash(golden),
        "n_examples": len(golden),
        "per_dimension_means": scoring["per_dimension_means"],
        "aggregate": scoring["aggregate"],
        "n_scored": scoring["n_scored"],
        "per_example": scoring["per_example"],
    }


def _emit_fixture(
    predictions: dict[str, dict[str, Any]], path: Path
) -> None:
    with path.open("w", encoding="utf-8") as fh:
        for rid, pred in predictions.items():
            payload = {
                "id": rid,
                "candidate_summary": pred.get("candidate_summary", ""),
                "scores": {
                    k: int(pred.get("scores", {}).get(k, 0))
                    for k in _RUBRIC_KEYS
                },
            }
            fh.write(json.dumps(payload, separators=(",", ":")) + "\n")


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
    parser = argparse.ArgumentParser(description="Run the summarize eval gate.")
    parser.add_argument(
        "--mode",
        required=True,
        choices=["fixture", "real"],
        help="fixture: read prebaked predictions; real: call live services.",
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
        help="In --mode=real, also write the live predictions to this path "
        "so future CI runs can replay them without calling Anthropic.",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    golden = load_jsonl(Path(args.golden))
    if args.mode == "fixture":
        predictions = _load_fixture(Path(args.fixture), golden)
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
                "per_dimension_means": report["per_dimension_means"],
                "aggregate": report["aggregate"],
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
    floor = (thresholds.get("summarize") or {}).get("rubric_floor")
    if floor is not None:
        aggregate = float(report["aggregate"])
        if aggregate < float(floor):
            print(
                f"THRESHOLD BREACH: aggregate = {aggregate:.4f} below "
                f"floor {float(floor):.4f}",
                file=sys.stderr,
            )
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
