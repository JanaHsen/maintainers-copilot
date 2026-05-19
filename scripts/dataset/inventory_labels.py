"""Inventory the labels present in a fetched raw issue run.

Reads every ``raw/scikit-learn/issues/{run_id}/*.jsonl`` object from MinIO,
counts unique GitHub label names, and writes
``scripts/dataset/observed_labels.txt`` (``<count>\\t<label>`` per line,
sorted by frequency descending). This grounds label_map.yaml in real data
(Rule 6).

Usage::

    MINIO_HOST=localhost uv run python scripts/dataset/inventory_labels.py --run-id <id>
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from app.infra.minio_client import DATA_BUCKET, get_client  # noqa: E402

OUTPUT_PATH = os.path.join(os.path.dirname(__file__), "observed_labels.txt")


def _iter_issue_lines(run_id: str) -> list[str]:
    s3 = get_client()
    prefix = f"raw/scikit-learn/issues/{run_id}/"
    listed = s3.list_objects_v2(Bucket=DATA_BUCKET, Prefix=prefix)
    keys = sorted(o["Key"] for o in listed.get("Contents", []))
    if not keys:
        raise SystemExit(f"no raw objects under s3://{DATA_BUCKET}/{prefix}")
    lines: list[str] = []
    for key in keys:
        body = s3.get_object(Bucket=DATA_BUCKET, Key=key)["Body"].read()
        lines.extend(
            line for line in body.decode("utf-8").splitlines() if line.strip()
        )
    return lines


def main() -> int:
    parser = argparse.ArgumentParser(description="Inventory raw scikit-learn issue labels")
    parser.add_argument("--run-id", required=True, help="raw run_id to inventory")
    args = parser.parse_args()

    counter: Counter[str] = Counter()
    for line in _iter_issue_lines(args.run_id):
        issue = json.loads(line)
        for label in issue.get("labels", []):
            name = label["name"] if isinstance(label, dict) else str(label)
            counter[name] += 1

    with open(OUTPUT_PATH, "w", encoding="utf-8") as fh:
        for label, count in counter.most_common():
            fh.write(f"{count}\t{label}\n")

    print(
        f"wrote {OUTPUT_PATH}: {len(counter)} unique labels "
        f"across {sum(counter.values())} label occurrences",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
