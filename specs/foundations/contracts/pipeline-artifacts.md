# Offline Pipeline & Notebook Artifact Contracts

The dataset pipeline and the notebook expose no HTTP surface; their "contract"
is the set of blob objects they MUST produce in MinIO with stable keys and
shapes, so Days 2–3 can consume them without coordination.

## Bucket & key layout

| Producer | Key pattern | Format |
|---|---|---|
| `fetch_issues.py` | `raw/pandas/issues/{run_id}/page_{n}.jsonl` | JSONL, one verbatim GitHub issue per line |
| `build_splits.py` | `processed/pandas/{run_id}/train.parquet` | Parquet (Mapped Issue columns) |
| `build_splits.py` | `processed/pandas/{run_id}/val.parquet` | Parquet |
| `build_splits.py` | `processed/pandas/{run_id}/test.parquet` | Parquet |
| `build_splits.py` | `processed/pandas/{run_id}/splits_report.json` | JSON (see data-model.md) |
| notebook | `artifacts/classifier/distilbert/{run_id}/pytorch_model.bin` | binary state_dict |
| notebook | `artifacts/classifier/distilbert/{run_id}/model_card.json` | JSON (see data-model.md) |

`{run_id}` is a UTC-derived identifier, unique per pipeline invocation.

## Invariants (machine-checkable; map to Success Criteria)

- **C1 (FR-013, SC-008)**: A new run MUST write under a fresh `run_id`; objects under any prior `run_id` MUST be byte-unchanged after the new run.
- **C2 (FR-014/FR-015)**: Every row in the three parquet files has `target_class ∈ {bug, feature, docs, question}`; no other value occurs.
- **C3 (FR-016, SC-006)**: `min(closed_at) over test` > `max(closed_at) over train ∪ val`, strictly.
- **C4 (FR-017, SC-007)**: `sum(splits_report.counts.*.*) == splits_report.total_mapped`, and each parquet row count equals its corresponding per-split sum.
- **C5 (stratification)**: Every `target_class` present in the mapped data appears in `train`, `val`, and `test` (no empty class slice; surface an error instead — spec edge case).
- **C6 (Rule 4 pre-positioning)**: `model_card.json.weights_sha256` equals the SHA-256 of the sibling `pytorch_model.bin`.

These invariants are the acceptance oracle for User Stories 2 and 3 and the
basis for Phase 2 verification tasks.
