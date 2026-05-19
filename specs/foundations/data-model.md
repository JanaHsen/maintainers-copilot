# Phase 1 Data Model: Day 1 Foundations

Two model surfaces: (A) the Postgres schema introduced by Alembic migration
`0001_baseline`, and (B) the in-memory / on-blob structures the API and
pipeline produce. ORM models live behind `app/repositories/` and MUST NOT
leak past that layer; domain models are Pydantic and distinct (Rule 1).

## A. Relational schema (Alembic `0001_baseline`)

### Extension

- `CREATE EXTENSION IF NOT EXISTS vector;` — pgvector enabled in the baseline migration (Rule 3). No vector columns are created Day 1; the extension is provisioned for later days.

### Table: `audit_log`

| Column | Type | Notes |
|---|---|---|
| `id` | `bigint` PK, identity | |
| `actor_id` | `text` nullable | who/what performed the action (no FK Day 1) |
| `action` | `text` not null | e.g. `health.check` |
| `target` | `text` nullable | object acted on |
| `timestamp` | `timestamptz` not null, default `now()` | |
| `payload` | `jsonb` nullable | structured detail |

Index: `ix_audit_log_timestamp` on `timestamp`. No rows are required Day 1;
the table exists so the audit path is migration-backed from the start.

**Migration constraints**: `0001_baseline` is reversible (`downgrade` drops
both tables; the `vector` extension `DROP` is guarded). Re-running
`alembic upgrade head` is idempotent (already-at-head = no-op success),
satisfying FR-004 and the "migrate run twice" edge case.

## B. Domain & artifact structures

### `DependencyStatus` (Pydantic, `app/domain/health.py`)

| Field | Type | Notes |
|---|---|---|
| `name` | `str` | one of `postgres`, `pgvector`, `redis`, `minio`, `vault` |
| `reachable` | `bool` | result of this dependency's probe |
| `detail` | `str \| None` | error class/summary when unreachable (redacted) |
| `latency_ms` | `float` | probe duration |

### `HealthReport` (Pydantic, `app/domain/health.py`)

| Field | Type | Notes |
|---|---|---|
| `status` | `Literal["ok","degraded"]` | `ok` iff every dependency reachable; `degraded` if any non-Vault dependency is down (Vault-down ⇒ process never started, so it cannot appear here) |
| `dependencies` | `list[DependencyStatus]` | one entry per upstream check |
| `request_id` | `str` | from request-context middleware |
| `trace_id` | `str` | OTel trace id of this request |

Validation: `dependencies` non-empty; `status == "ok"` ⟺ all
`reachable == True`. HTTP status is always `200` when the process is up
(FR-006/FR-007) — health content, not transport, conveys degradation.

### Raw Issue Record — `raw/pandas/issues/{run_id}/page_{n}.jsonl`

One JSON object per line = the verbatim GitHub issue payload (unmodified).
`run_id` is a UTC timestamp-derived id; re-runs use a new `run_id` and never
overwrite (FR-013, SC-008).

### Mapped Issue (in-memory, `build_splits.py`)

| Field | Type | Notes |
|---|---|---|
| `issue_number` | `int` | |
| `title` | `str` | |
| `body` | `str` | text fed to the classifier later |
| `labels` | `list[str]` | original pandas labels |
| `target_class` | `Literal["bug","feature","docs","question"]` | from `label_map.yaml` precedence; unmappable rows dropped before this stage |
| `closed_at` | `datetime` | time key for the strict ordering |
| `split` | `Literal["train","val","test"]` | assigned by the split logic |

### `label_map.yaml`

```yaml
precedence: [bug, feature, docs, question]   # tie-break for multi-label issues
classes:
  bug:      ["Bug", "Regression"]
  feature:  ["Enhancement", "Feature Request"]
  docs:     ["Docs"]
  question: ["Usage Question", "Needs Info"]
drop_if_unmapped: true                        # issues mapping to none → excluded
```

(Concrete label lists are filled from observed pandas labels during
implementation; rationale recorded in `DECISIONS.md`, Rule 6.)

### `splits_report.json` — `processed/pandas/{run_id}/splits_report.json`

| Field | Type | Notes |
|---|---|---|
| `run_id` | `str` | matches the processed prefix |
| `source` | `str` | `pandas-dev/pandas` |
| `total_mapped` | `int` | rows after dropping unmappable |
| `counts.{split}.{class}` | `int` | per-split per-class counts |
| `time_boundary.train_val_max_closed_at` | `datetime` | latest train/val timestamp |
| `time_boundary.test_min_closed_at` | `datetime` | earliest test timestamp; MUST be `>` the line above (FR-016, SC-006) |

Invariant: sum of all `counts.*.*` == `total_mapped` (SC-007).

### `model_card.json` — `artifacts/classifier/distilbert/{run_id}/`

| Field | Type | Notes |
|---|---|---|
| `architecture` | `str` | `distilbert-base-uncased` |
| `hyperparameters` | `object` | epochs, lr, batch size, max_len |
| `training_data_hash` | `str` | hash of the train split consumed |
| `final_val_metrics` | `object` | accuracy / macro-F1 on val |
| `weights_sha256` | `str` | SHA-256 of the pushed `state_dict` (pre-positions the Rule 4 weights-integrity check for Day 2) |

State transitions: none Day 1 (no stateful workflow entities). The only
ordering invariant is the dataset time boundary above.
