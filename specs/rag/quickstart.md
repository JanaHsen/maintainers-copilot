# Quickstart — RAG Pipeline

Operator runbook for the slice spec'd in [`spec.md`](./spec.md). Each
command is independently re-runnable; nothing here mutates anything in
production-shaped state without printing what it's about to do first.

## Prerequisites

- The docker-compose stack from Day 1+2 is up: `docker compose up -d`
  reaches a healthy api (slice-(g) `/health` smoke).
- Vault has `anthropic_api_key` and `github_pat` seeded — both are
  used by the corpus build (Haiku for HyDE; PAT for GraphQL).
- The canonical pandas dataset run `20260519T133455Z` is in MinIO at
  `processed/pandas/20260519T133455Z/{train,val,test}.parquet`. The
  corpus build refuses to start without these (FR-009).

## 1. Run a Postgres migration to create `rag_chunks`

```bash
docker compose run --rm migrate
# applies alembic 0002_rag_chunks: rag_chunks table + indices (vector(768) for BAAI/bge-base-en-v1.5)
```

## 2. Build the corpus

```bash
VAULT_ADDR=http://localhost:8200 \
  MINIO_HOST=localhost \
  PANDAS_REPO_REF=v2.2.0 \
  RAG_CORPUS_RUN_ID=$(date -u +%Y%m%dT%H%MZ) \
  uv run python scripts/rag/build_corpus.py \
    --dataset-run-id 20260519T133455Z
```

What the script does, in order:

1. Verifies `processed/pandas/20260519T133455Z/{train,val,test}.parquet`
   exists in MinIO. Refuses to start otherwise.
2. Fetches the pandas repo at `PANDAS_REPO_REF` (shallow + sparse-checkout
   covering `README.md`, `CONTRIBUTING.md`, and `doc/source/**/*.rst`;
   the clone is cached under `~/.cache/maintainers-copilot/pandas-repo/`
   so re-runs reuse it). Counts skipped code-only files (FR-007).
3. Fetches resolved issues with maintainer comments via GraphQL,
   excluding every `issue_number` present in the three classifier
   splits. Writes the excluded set to `excluded_issue_numbers.txt` —
   expected empty at the end (SC-005).
4. Chunks each source with the parent-document strategy (≈400-char
   children, ≈2000-char parents).
5. Calls `model-server` `/embed` (batched) to embed each child chunk.
6. Bulk-inserts every chunk into `rag_chunks` under the new
   `corpus_run_id`.
7. Uploads `corpus_report.json`, the per-source index JSONL files, and
   `excluded_issue_numbers.txt` to MinIO under
   `rag/corpus/{corpus_run_id}/`.

Expected wall time: 10–30 minutes depending on issue volume + network.

## 3. Verify the index

```bash
docker compose exec postgres psql -U postgres -d maintainers_copilot -c "
  SELECT corpus_run_id,
         source_type,
         COUNT(*) FILTER (WHERE kind='parent') AS parents,
         COUNT(*) FILTER (WHERE kind='child')  AS children
  FROM rag_chunks
  GROUP BY corpus_run_id, source_type
  ORDER BY corpus_run_id, source_type;
"
```

Expected: one row per `(corpus_run_id, source_type)` with non-zero
parents and children. Parent count for `issues` ≤ classifier-corpus
size minus 16,926 (the count in the three splits) — the slice is
held-out.

## 4. Point the api at the new corpus and restart

```bash
echo "RAG_CORPUS_RUN_ID=$RAG_CORPUS_RUN_ID" >> .env
docker compose up -d --no-deps --force-recreate api model-server
docker compose logs --tail=50 api model-server
```

The api refuses to boot if `RAG_CORPUS_RUN_ID` is unset, the table is
empty, or no rows exist for the configured run id (Rule 4 — see
data-model.md's "Lifecycle / boot-time invariants").

## 5. Smoke-test `/retrieve`

```bash
curl -sS -X POST http://localhost:8000/retrieve \
  -H "Content-Type: application/json" \
  -d '{
    "question": "how do I group a DataFrame by date and aggregate?",
    "k": 5
  }' | jq '.chunks | length, .chunks[0] | {source_type, source_id, score}'
```

Expected: `5` followed by a chunk record with a docs or issues source.

## 6. Run the naive baseline once, commit the numbers

```bash
RAG_CORPUS_RUN_ID=$RAG_CORPUS_RUN_ID \
  uv run python evals/rag/eval_rag.py \
    --mode naive \
    --output evals/rag/baseline.json
git add evals/rag/baseline.json
git commit -m "rag: commit naive baseline numbers (recall@5=X, MRR=Y)"
```

`baseline.json` is committed to the repo so subsequent CI runs can
diff the advanced pipeline against it without re-running the baseline.

## 7. Sweep hybrid α on the golden set

```bash
uv run python evals/rag/sweep_alpha.py \
  --output evals/rag/alpha_sweep.json
# prints a table; picks the α that maximizes recall@5
```

The chosen α is committed in `app/services/retrieve_service.py`'s
config and recorded in `DECISIONS.md` under "RAG hybrid α".

## 8. Run the advanced pipeline + the eval gate

```bash
uv run python evals/rag/eval_rag.py \
  --mode advanced \
  --upload-report
# uploads evals/reports/{ts}/rag.json to MinIO; prints a per-design-choice
# delta table vs evals/rag/baseline.json; exits non-zero on threshold breach
```

This is what CI runs on every push (slice equivalent of the classifier
eval gate from Day 2 slice (j)).

## 9. Publish the corpus snapshot for CI parity

```bash
# pull the bytes from MinIO once, attach to a GitHub release
mc cp s3/maintainers-copilot/rag/corpus/$RAG_CORPUS_RUN_ID/corpus_report.json \
   /tmp/rag-release/
# (and the chunk dump produced by scripts/rag/export_corpus.py)
gh release create rag-corpus-v1-$RAG_CORPUS_RUN_ID \
  --title "RAG corpus snapshot — $RAG_CORPUS_RUN_ID" \
  /tmp/rag-release/*
```

CI's seed step `scripts/ci/seed_rag_corpus.sh` downloads these assets
on every push and bulk-inserts them into the CI MinIO + Postgres so
`/retrieve` is live for the eval gate. Pattern: identical to slice-(j)
`seed_classifier_artifact.sh`.

## 10. Tear down

```bash
docker compose down
```

The `rag_chunks` rows live in the `pgdata` volume; `docker compose
down -v` clears them, and step 1+2+4 reproduces them from MinIO +
GitHub Release (or from a fresh fetch if the operator wants to test
the build path end-to-end).
