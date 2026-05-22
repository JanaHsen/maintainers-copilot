# Contract: corpus-build artifacts

The corpus build produces three artifact surfaces. Two land in
Postgres + pgvector (the live index the api reads), one lands in MinIO
(the snapshot + audit trail).

## Postgres rows

See [`data-model.md`](../data-model.md) — the `rag_chunks` table is the
single canonical surface. Every successful build run inserts a fresh
set of rows under a new `corpus_run_id` and never modifies prior runs.

## MinIO objects

All written under the prefix `rag/corpus/{corpus_run_id}/`. Keys + their
content shapes:

| key                          | type        | purpose |
|------------------------------|-------------|---------|
| `corpus_report.json`         | JSON        | The build's headline summary (see [`data-model.md`](../data-model.md#corpus_reportjson-shape)). |
| `docs_index.jsonl`           | JSONL       | One row per fetched doc file with chunk counts, source hash, and skip status. |
| `issues_index.jsonl`         | JSONL       | One row per held-out issue with chunk counts. |
| `excluded_issue_numbers.txt` | plain text  | The classifier-split issue numbers the fetch encountered and skipped. Empty file = zero overlap (SC-005). |

### `corpus_report.json` integrity

The api at boot reads `corpus_report.json` for the configured
`RAG_CORPUS_RUN_ID` and asserts:

- `embedding_model_id` matches the model the model-server has loaded.
  Mismatch → refuse-to-boot (`REFUSE TO BOOT: corpus embedding model
  mismatch — corpus says {a}, model server says {b}`).
- `embedding_dim` matches the `vector(D)` column dimension.
- `counts.issues.excluded_overlap == 0` AND `excluded_issue_numbers.txt`
  is empty. Mismatch → refuse-to-boot (`REFUSE TO BOOT: corpus
  contains classifier-split issues`).

## Naive baseline corpus (FR-019)

The naive baseline pipeline reads from the **same** `rag_chunks` rows
but interprets them as fixed-≈400-char chunks (the child rows alone,
ignoring the parent/child join). No second table; the baseline is
defined by its retrieval configuration, not by separate storage. The
baseline_corpus_run_id is the same string.

## Operator runbook

See [`../quickstart.md`](../quickstart.md) for the build command, the
seed-from-release command (CI parity), and the eval command.
