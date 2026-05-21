"""Build evals/classification/golden.jsonl from the val split in MinIO.

Selection rules (mirror evals/classification/README.md):

  - Source: ``processed/pandas/{DATASET_RUN_ID}/val.parquet``
    (val, not test; the test split is reserved for headline metrics
    reporting and DistilBERT vs Haiku comparison).
  - Stratified, deterministic: per class, take the first N rows sorted by
    ``issue_number`` ascending.
  - Class counts: 6 bug, 6 docs, 6 feature, 7 question. The +1 on
    ``question`` reflects that it is the weakest class on the test
    split (F1 0.4503) — extra gating signal where it matters.

Per-row schema written to JSONL:

  {issue_number, title, body, true_class, source, selection_reason}

Re-run when val.parquet changes or the class counts need tweaking. The
golden_set hash on subsequent eval reports will pin down which version of
this file produced the numbers.

Usage (with the compose stack up locally):

    VAULT_ADDR=http://localhost:8200 MINIO_HOST=localhost \\
        DATASET_RUN_ID=20260519T133455Z \\
        uv run python scripts/eval/build_classification_golden.py
"""

from __future__ import annotations

import io
import json
import logging
import os
import sys
from pathlib import Path

import pandas as pd

# The script lives outside the app package; make repo root importable.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from app.infra.minio_client import DATA_BUCKET, get_client  # noqa: E402

logger = logging.getLogger("build_golden")

GOLDEN_PATH = Path(__file__).resolve().parents[2] / "evals" / "classification" / "golden.jsonl"
CLASS_COUNTS: dict[str, int] = {"bug": 6, "docs": 6, "feature": 6, "question": 7}


def _read_val_split(dataset_run_id: str) -> pd.DataFrame:
    key = f"processed/pandas/{dataset_run_id}/val.parquet"
    logger.info("reading s3://%s/%s", DATA_BUCKET, key)
    body = get_client().get_object(Bucket=DATA_BUCKET, Key=key)["Body"].read()
    return pd.read_parquet(io.BytesIO(body))


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    dataset_run_id = os.environ.get("DATASET_RUN_ID")
    if not dataset_run_id:
        raise SystemExit("DATASET_RUN_ID must be set (e.g. 20260519T133455Z)")

    df = _read_val_split(dataset_run_id)
    required = {"issue_number", "title", "body", "target_class"}
    missing = required - set(df.columns)
    if missing:
        raise SystemExit(f"val.parquet missing required columns: {sorted(missing)}")

    rows: list[dict[str, object]] = []
    for cls, n in CLASS_COUNTS.items():
        in_class = df[df["target_class"] == cls].sort_values("issue_number")
        if len(in_class) < n:
            raise SystemExit(
                f"val split has only {len(in_class)} rows of class {cls!r}; "
                f"need {n}"
            )
        picked = in_class.head(n)
        for _, r in picked.iterrows():
            rows.append(
                {
                    "issue_number": int(r["issue_number"]),
                    "title": str(r["title"] or ""),
                    "body": str(r["body"] or ""),
                    "true_class": cls,
                    "source": "val_split",
                    "selection_reason": f"first_{n}_of_class_{cls}_by_issue_number",
                }
            )

    GOLDEN_PATH.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
        encoding="utf-8",
    )
    logger.info(
        "wrote %d rows to %s (counts: %s)",
        len(rows),
        GOLDEN_PATH,
        {cls: sum(1 for r in rows if r["true_class"] == cls) for cls in CLASS_COUNTS},
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
