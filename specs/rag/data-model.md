# Phase 1 Data Model — Advanced RAG Pipeline

Concrete shapes for every entity the spec names. ER + JSON Schema-ish
forms; the Pydantic models in `app/domain/retrieve.py` and the SQLAlchemy
mapping in `app/repositories/chunk_repository.py` are derived from these.

## Postgres — `rag_chunks` table

Added by Alembic migration `0002_rag_chunks.py`. Single table holds both
child and parent variants, distinguished by `kind`. Parent rows have
`parent_id = id`. Child rows reference their parent via `parent_id`.

| column              | type                          | notes |
|---------------------|-------------------------------|-------|
| `id`                | `text PRIMARY KEY`            | Deterministic SHA-256 truncated to 26 chars (R7). |
| `kind`              | `text NOT NULL`               | `'child'` or `'parent'`. |
| `parent_id`         | `text NOT NULL`               | For `kind='parent'`, equals `id`. For `kind='child'`, references the parent's `id`. |
| `content`           | `text NOT NULL`               | The chunk text. ≈400 chars for child, ≈2000 for parent. |
| `embedding`         | `vector(768) NULL`            | Populated for `kind='child'` only. NULL for `kind='parent'`. Dimension fixed at 768 to match `BAAI/bge-base-en-v1.5` (see research.md R12). |
| `source_type`       | `text NOT NULL`               | `'docs'` or `'issues'`. |
| `source_id`         | `text NOT NULL`               | For `docs`: relative file path inside the pandas repo (e.g. `doc/source/user_guide/10min.rst`). For `issues`: `issue_number` as a string. |
| `source_timestamp`  | `timestamptz NOT NULL`        | For `docs`: the file's last-commit timestamp. For `issues`: the issue's `closed_at`. |
| `section_path`      | `text NOT NULL`               | Header path inside the source (e.g. `Intro to data structures > Series`). Empty string allowed. |
| `child_index`       | `int NOT NULL`                | 0-indexed position of the child inside its parent. 0 for parent rows. |
| `parent_index`      | `int NOT NULL`                | 0-indexed position of the parent inside its source. |
| `corpus_run_id`     | `text NOT NULL`               | The run id this chunk was built under. |
| `content_tsv`       | `tsvector GENERATED ALWAYS AS (to_tsvector('english', content)) STORED` | Sparse-index source column (R1). |

### Indices

| index name                            | definition |
|---------------------------------------|------------|
| `rag_chunks_pkey`                     | PRIMARY KEY (`id`) |
| `ix_rag_chunks_parent_id`             | `(parent_id)` — fast parent lookup during aggregation. |
| `ix_rag_chunks_source_type_timestamp` | `(source_type, source_timestamp)` — supports metadata filter in stage-1 SQL (FR-018). |
| `ix_rag_chunks_corpus_run_id`         | `(corpus_run_id)` — fast version filter (the api pins one run at boot). |
| `gin_rag_chunks_content_tsv`          | `USING GIN (content_tsv)` — sparse text search (R1). |
| `ivfflat_rag_chunks_embedding`        | `USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100) WHERE kind = 'child'` — dense ANN; partial so only child rows are indexed. |

### Invariants

- A parent row has `kind='parent'` and `id = parent_id`.
- A child row has `kind='child'`, `parent_id != id`, and a non-NULL
  `embedding`.
- Within a `corpus_run_id`, `(source_type, source_id, parent_index)` is
  unique for parents; `(parent_id, child_index)` is unique for children.
- Re-running the corpus build against the same source state with a new
  `corpus_run_id` produces the same `(content, parent_index, child_index)`
  shape for every source — and therefore the same `id`s (R7).

### State transitions

None at row level. A new corpus run inserts a new set of rows under a
new `corpus_run_id`; rows from prior runs are not modified.

## MinIO — corpus snapshot

| key                                                             | content |
|-----------------------------------------------------------------|---------|
| `rag/corpus/{corpus_run_id}/corpus_report.json`                 | The build's summary (see below). |
| `rag/corpus/{corpus_run_id}/docs_index.jsonl`                   | One row per fetched doc file with `{source_id, source_timestamp, sha256_of_source, parent_count, child_count, was_skipped, skip_reason}`. |
| `rag/corpus/{corpus_run_id}/issues_index.jsonl`                 | One row per held-out issue with `{issue_number, closed_at, comment_count, parent_count, child_count}`. |
| `rag/corpus/{corpus_run_id}/excluded_issue_numbers.txt`         | The intersection check's outcome: every classifier-split issue number that the held-out fetch encountered and skipped. One per line. Empty file proves zero overlap. |

### `corpus_report.json` shape

```json
{
  "corpus_run_id": "20260521T1530Z",
  "dataset_run_id": "20260519T133455Z",
  "embedding_model_id": "BAAI/bge-base-en-v1.5",
  "embedding_dim": 768,
  "chunking": {
    "child_chars": 400,
    "parent_chars": 2000,
    "strategy": "parent_document"
  },
  "counts": {
    "docs": { "parents": 1234, "children": 5678, "skipped_files": 17 },
    "issues": { "parents": 9876, "children": 54321, "excluded_overlap": 0 }
  },
  "source_state": {
    "pandas_repo_commit": "<git sha>",
    "docs_root_sha256": "<sha256 of concatenated source files (sorted)>",
    "issue_fetch_run_id": "<the classifier dataset run_id whose splits gated this fetch>"
  },
  "started_at": "2026-05-21T15:30:00Z",
  "finished_at": "2026-05-21T15:42:11Z"
}
```

## MinIO — per-CI eval report

| key                                                    | content |
|--------------------------------------------------------|---------|
| `evals/reports/{run_ts}/rag.json`                      | Live-eval output. Uploaded on every CI push. |

### `evals/reports/{run_ts}/rag.json` shape

```json
{
  "run_ts": "20260522T0930Z",
  "corpus_run_id": "20260521T1530Z",
  "pipeline_config": {
    "embedding_model_id": "BAAI/bge-base-en-v1.5",
    "cross_encoder_id": "cross-encoder/ms-marco-MiniLM-L-6-v2",
    "hyde_enabled": true,
    "hyde_prompt_path": "prompts/hyde.md",
    "hybrid_alpha": 0.6,
    "first_stage_k": 30,
    "rerank_top_k": 5,
    "parent_aggregation": "max",
    "chunking": "parent_document"
  },
  "golden_set_hash": "<sha256 of canonical golden.jsonl>",
  "n_examples": 25,
  "retrieval_metrics": {
    "recall_at_5":  0.78,
    "recall_at_20": 0.92,
    "mrr":          0.64,
    "ndcg":         0.71
  },
  "generation_metrics": {
    "judge": "claude-haiku",
    "mean_relevance": 0.83,
    "mean_faithfulness": 0.81,
    "per_question_scores": [ { "question_id": "...", "relevance": ..., "faithfulness": ... }, ... ]
  },
  "operator_judge_agreement": {
    "metric": "cohen_kappa",
    "value": 0.78,
    "labeled_question_ids": ["q03", "q07", "q12", "q19", "q24"]
  },
  "predictions": [
    {
      "question_id": "q01",
      "question": "how do I group a DataFrame by date and aggregate?",
      "retrieved_chunk_ids": ["<parent_id>", ...5...],
      "ground_truth_chunk_ids": ["<parent_id>", ...],
      "first_correct_rank": 2,
      "hyde_fallback": false
    },
    ...
  ],
  "thresholds": {
    "recall_at_5_floor": 0.65,
    "recall_at_5_passes": true,
    "mrr_floor": 0.50,
    "mrr_passes": true
  }
}
```

## Repo files — golden set + baseline

### `evals/rag/golden.jsonl`

Exactly 25 lines. Schema per row:

```json
{
  "question_id": "q01",
  "question": "how do I group a DataFrame by date and aggregate?",
  "ideal_answer": "Use df.groupby(df['date'].dt.floor('D')).agg(...). The DataFrame.groupby docs describe the freq-aliases like 'M', 'W', and ...",
  "ground_truth_chunk_ids": ["<parent_id_1>", "<parent_id_2>"],
  "operator_labeled": false,
  "notes": "..."
}
```

Five rows have `"operator_labeled": true`; the rest are auto-labeled by
the chosen judge and reviewed.

### `evals/rag/baseline.json`

Frozen naive-baseline numbers, committed to the repo:

```json
{
  "corpus_run_id": "20260521T1530Z",
  "pipeline_config": {
    "embedding_model_id": "BAAI/bge-base-en-v1.5",
    "cross_encoder_id": null,
    "hyde_enabled": false,
    "hybrid_alpha": 1.0,
    "first_stage_k": 5,
    "rerank_top_k": 5,
    "parent_aggregation": null,
    "chunking": "naive_fixed_400"
  },
  "golden_set_hash": "<sha256 of canonical golden.jsonl>",
  "retrieval_metrics": {
    "recall_at_5":  0.X,
    "recall_at_20": 0.X,
    "mrr":          0.X,
    "ndcg":         0.X
  },
  "measured_at": "2026-05-21T..."
}
```

## API — `POST /retrieve`

See [`contracts/retrieve.openapi.yaml`](./contracts/retrieve.openapi.yaml)
for the wire format. Pydantic domain models in
`app/domain/retrieve.py`:

```python
class RetrieveFilters(BaseModel):
    source: list[Literal["docs", "issues"]] | None = None  # default: both
    from_: datetime | None = Field(default=None, alias="from")
    to: datetime | None = None

class RetrieveRequest(BaseModel):
    question: str = Field(..., min_length=1)
    k: int = Field(default=5, ge=0, le=20)
    filters: RetrieveFilters | None = None

class RetrievedChunk(BaseModel):
    content: str           # parent chunk text
    source_type: Literal["docs", "issues"]
    source_id: str
    score: float           # reranker output
    metadata: dict[str, Any]
    chunk_id: str          # parent_id; opaque to caller but useful for trace
    request_id: str
    trace_id: str

class RetrieveResponse(BaseModel):
    chunks: list[RetrievedChunk]
    request_id: str
    trace_id: str
```

### Validation rules from spec

- `question` non-empty (FR-001, edge case "empty question rejected as 4xx").
- `k` in `[0, 20]`. `k=0` returns an empty list with 200 (edge case).
- `filters.source` ∈ `{[], ["docs"], ["issues"], ["docs","issues"]}` (FR-010).
- `filters.from <= filters.to` when both provided (validated at the
  domain boundary; 422 on violation).

## API — error envelope (Rule 11)

Failures from the model server map to typed HTTP statuses in
`/retrieve` per **R4** in `research.md`:

```json
{
  "detail": "model server unreachable",
  "kind":   "unreachable",
  "request_id": "...",
  "trace_id":   "..."
}
```

`kind` ∈ `{unreachable, timeout, internal, bad_request, unexpected}`.

## Lifecycle / boot-time invariants (Rule 4)

The api refuses to boot when **any** of these holds (in addition to the
Day 1+2 set):

| Condition | Specific log line prefix |
|---|---|
| pgvector extension missing | `REFUSE TO BOOT: pgvector extension absent` |
| `rag_chunks` table empty | `REFUSE TO BOOT: rag_chunks table empty` |
| `RAG_CORPUS_RUN_ID` env unset | `REFUSE TO BOOT: RAG_CORPUS_RUN_ID not configured` |
| `rag_chunks` has zero rows for the configured `corpus_run_id` | `REFUSE TO BOOT: configured corpus run id has no rows` |

The model server refuses to boot when **any** of these holds:

| Condition | Specific log line prefix |
|---|---|
| Embedding model fails to load | `REFUSE TO BOOT: embedding model failed to load` |
| Cross-encoder fails to load | `REFUSE TO BOOT: cross-encoder failed to load` |

The seven existing DistilBERT refuse-to-boot conditions in
`model_server/boot_check.py` continue to apply.
