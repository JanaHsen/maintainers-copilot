"""Build the stratified, time-ordered train/val/test split + report.

Pipeline (research R4 / FR-016 / contracts C2-C5):

  raw JSONL  -> apply label_map.yaml -> drop unmappable & PRs
             -> sort by closed_at
             -> test = most recent ~15% (STRICT time boundary; ties go to
                test so train/val max < test min)
             -> remaining 85% split train/val (~70/15 overall), stratified
                by class
             -> write {train,val,test}.parquet + splits_report.json under
                processed/scikit-learn/{run_id}/

Usage::

    MINIO_HOST=localhost uv run python scripts/dataset/build_splits.py --run-id <id>
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import random
import sys
from collections import defaultdict
from datetime import datetime

import pandas as pd
import yaml

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from app.infra.minio_client import DATA_BUCKET, ensure_bucket, get_client  # noqa: E402

SOURCE = "scikit-learn/scikit-learn"
LABEL_MAP_PATH = os.path.join(os.path.dirname(__file__), "label_map.yaml")
TEST_FRACTION = 0.15
VAL_FRACTION = 0.15
SEED = 17


def _load_label_map() -> tuple[list[str], dict[str, set[str]], bool]:
    with open(LABEL_MAP_PATH, encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh)
    precedence: list[str] = cfg["precedence"]
    classes = {cls: set(names) for cls, names in cfg["classes"].items()}
    return precedence, classes, bool(cfg["drop_if_unmapped"])


def _target_class(
    labels: list[str], precedence: list[str], classes: dict[str, set[str]]
) -> str | None:
    label_set = set(labels)
    for cls in precedence:
        if label_set & classes[cls]:
            return cls
    return None


def _read_raw(run_id: str) -> list[dict[str, object]]:
    s3 = get_client()
    prefix = f"raw/scikit-learn/issues/{run_id}/"
    listed = s3.list_objects_v2(Bucket=DATA_BUCKET, Prefix=prefix)
    keys = sorted(o["Key"] for o in listed.get("Contents", []) if o["Key"].endswith(".jsonl"))
    if not keys:
        raise SystemExit(f"no raw objects under s3://{DATA_BUCKET}/{prefix}")
    issues: list[dict[str, object]] = []
    for key in keys:
        body = s3.get_object(Bucket=DATA_BUCKET, Key=key)["Body"].read()
        for line in body.decode("utf-8").splitlines():
            if line.strip():
                issues.append(json.loads(line))
    return issues


def _parse_dt(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _map_records(
    issues: list[dict[str, object]],
    precedence: list[str],
    classes: dict[str, set[str]],
) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    seen: set[int] = set()
    for issue in issues:
        number = issue.get("number")
        if not isinstance(number, int) or number in seen:
            continue
        seen.add(number)
        if issue.get("pull_request") is not None:
            continue  # PRs are not issues for this task
        if not issue.get("closed_at"):
            continue
        names = [
            label["name"] if isinstance(label, dict) else str(label)
            for label in issue.get("labels", [])
        ]
        target = _target_class(names, precedence, classes)
        if target is None:
            continue  # drop_if_unmapped
        records.append(
            {
                "issue_number": issue["number"],
                "title": issue.get("title") or "",
                "body": issue.get("body") or "",
                "labels": names,
                "target_class": target,
                "closed_at": _parse_dt(str(issue["closed_at"])),
            }
        )
    return records


def _assign_splits(records: list[dict[str, object]]) -> None:
    records.sort(key=lambda r: r["closed_at"])
    n = len(records)
    if n == 0:
        raise SystemExit("no mappable issues after applying label_map.yaml")

    test_count = max(1, round(n * TEST_FRACTION))
    boundary = records[n - test_count]["closed_at"]
    # Ties at the boundary go to test so train/val max < test min (FR-016).
    train_val = [r for r in records if r["closed_at"] < boundary]
    test = [r for r in records if r["closed_at"] >= boundary]
    for r in test:
        r["split"] = "test"

    rng = random.Random(SEED)
    by_class: dict[str, list[dict[str, object]]] = defaultdict(list)
    for r in train_val:
        by_class[str(r["target_class"])].append(r)

    val_target = round(n * VAL_FRACTION)
    val_quota = val_target / max(len(train_val), 1)
    for _cls, rows in by_class.items():
        rng.shuffle(rows)
        k = round(len(rows) * val_quota)
        for i, r in enumerate(rows):
            r["split"] = "val" if i < k else "train"


def _train_hash(records: list[dict[str, object]]) -> str:
    """SHA-256 over the canonicalized train rows (sorted by issue number)."""
    train_rows = sorted(
        (r for r in records if r["split"] == "train"),
        key=lambda r: int(r["issue_number"]),  # type: ignore[arg-type]
    )
    h = hashlib.sha256()
    for r in train_rows:
        canonical = {
            "issue_number": r["issue_number"],
            "title": r["title"],
            "body": r["body"],
            "target_class": r["target_class"],
        }
        h.update(json.dumps(canonical, sort_keys=True).encode("utf-8"))
        h.update(b"\n")
    return h.hexdigest()


def _report(records: list[dict[str, object]], run_id: str) -> dict[str, object]:
    counts: dict[str, dict[str, int]] = {
        s: defaultdict(int) for s in ("train", "val", "test")
    }
    for r in records:
        counts[str(r["split"])][str(r["target_class"])] += 1
    train_val_max = max(
        r["closed_at"] for r in records if r["split"] != "test"
    )
    test_min = min(r["closed_at"] for r in records if r["split"] == "test")
    return {
        "run_id": run_id,
        "source": SOURCE,
        "total_mapped": len(records),
        "counts": {s: dict(c) for s, c in counts.items()},
        "time_boundary": {
            "train_val_max_closed_at": train_val_max.isoformat(),
            "test_min_closed_at": test_min.isoformat(),
        },
        "training_data_sha256": _train_hash(records),
    }


def _put_parquet(
    s3: object, run_id: str, split: str, rows: list[dict[str, object]]
) -> None:
    frame = pd.DataFrame(rows)
    if not frame.empty:
        frame = frame.copy()
        frame["closed_at"] = frame["closed_at"].astype(str)
    buf = io.BytesIO()
    frame.to_parquet(buf, index=False)
    s3.put_object(  # type: ignore[attr-defined]
        Bucket=DATA_BUCKET,
        Key=f"processed/scikit-learn/{run_id}/{split}.parquet",
        Body=buf.getvalue(),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Build scikit-learn issue splits")
    parser.add_argument("--run-id", required=True, help="raw run_id to process")
    args = parser.parse_args()

    precedence, classes, _drop = _load_label_map()
    issues = _read_raw(args.run_id)
    records = _map_records(issues, precedence, classes)
    _assign_splits(records)
    report = _report(records, args.run_id)

    ensure_bucket(DATA_BUCKET)
    s3 = get_client()
    for split in ("train", "val", "test"):
        rows = [
            {k: v for k, v in r.items() if k != "split"}
            for r in records
            if r["split"] == split
        ]
        _put_parquet(s3, args.run_id, split, rows)
    s3.put_object(
        Bucket=DATA_BUCKET,
        Key=f"processed/scikit-learn/{args.run_id}/splits_report.json",
        Body=json.dumps(report, indent=2).encode("utf-8"),
    )

    total = report["total_mapped"]
    summed = sum(
        c for split in report["counts"].values() for c in split.values()  # type: ignore[union-attr]
    )
    print(f"total_mapped={total} sum_counts={summed} (must match)", flush=True)
    print(json.dumps(report["time_boundary"], indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
