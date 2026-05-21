# Implementation Plan: Advanced RAG Pipeline — `/retrieve` + corpus build + eval gate

**Branch**: `rag` | **Date**: 2026-05-21 | **Spec**: [`spec.md`](./spec.md)

**Input**: Feature specification from `specs/rag/spec.md`

## Summary

Extend the api with `POST /retrieve` and ship the corpus pipeline that
feeds it. Two-stage retrieval (hybrid first-stage + cross-encoder rerank)
over a parent-document chunked corpus drawn from pandas docs and a
held-out slice of resolved issues (no overlap with classifier splits).
HyDE rewrites the query before stage 1; metadata filters scope the
candidate pool inside stage 1's SQL. Embedding and rerank inference live
in the existing model server alongside DistilBERT — the api stays a thin
HTTP-shaped layer. A 25-example golden set under `evals/rag/golden.jsonl`
plus a committed naive baseline gate every advanced choice on
"beats baseline on ≥1 of recall@5 / recall@20 / MRR / nDCG, or get
dropped" (Rule 6). CI runs live retrieval every push and uploads the
report to MinIO at `evals/reports/{run_ts}/rag.json` (Rules 5, 10).

## Technical Context

**Language/Version**: Python 3.12 (existing project pin in `pyproject.toml`).

**Primary Dependencies**:
- `app/` (RAG additions): FastAPI, SQLAlchemy 2.x + asyncpg / psycopg, Pydantic v2, httpx (reusing the existing `app/infra/model_server_client.py` transport), OpenTelemetry instrumentation.
- `model_server/` (RAG additions): `sentence-transformers` for the cross-encoder (`ms-marco-MiniLM-L-6-v2` default — operator-confirmable, see "Operator-deferred decisions" below), the embedding model (operator-confirmable), running on the existing torch + transformers stack pinned in the `ml` dependency group.
- `scripts/rag/` (corpus build): `httpx` + GraphQL for the pandas issue + comment fetch, `markdown-it-py` + `docutils`/`rst-to-text` for `docs/` prose extraction, the embedding model via the model server's `/embed` endpoint (no direct torch dep in scripts/).
- pgvector extension already provisioned by `alembic/versions/0001_baseline.py`.

**Storage**:
- Postgres 16 + pgvector for chunks + embeddings + sparse index.
- MinIO for raw corpus snapshots, the corpus build's `corpus_report.json`, the naive baseline numbers, and the per-CI eval reports under `evals/reports/{run_ts}/rag.json`.
- Vault for the Anthropic key (HyDE generation, generation-judge if Claude is chosen) and Postgres password — both already in the Vault surface.

**Testing**: `pytest` for unit tests (metrics, parsers, the parent-chunk aggregation), `httpx.MockTransport` for the model-server-side HTTP shape (existing pattern from `tests/infra/test_model_server_client.py`), an end-to-end CI eval that runs live retrieval against the docker-compose stack.

**Target Platform**: Same docker-compose stack as the rest of the project. Linux containers; `model-server` and `api` images built from the existing Dockerfiles.

**Project Type**: Web service (api) + offline data scripts. No frontend in this slice.

**Performance Goals**: `/retrieve` p95 ≤ 2s on the 25-example golden set (SC-001). Corpus build target ≤ 30 min for the held-out issue slice + the docs tree; reproducibility (same input → same chunk count + content checksums) takes precedence over speed.

**Constraints**:
- The held-out RAG issue slice MUST NOT intersect with `processed/pandas/{dataset_run_id}/{train,val,test}.parquet` (SC-005). The build refuses to start if those parquets are missing.
- Refuse-to-boot extensions: api when pgvector unreachable or `rag_chunks` table empty or the configured `corpus_run_id` is absent; model_server when the embedding model or the cross-encoder fails to load.
- `eval_thresholds.yaml`'s `rag:` section MUST be **non-zero** before merge (Rule 4). It stays empty between the baseline-ships push and the floors-land push — see "Operator-deferred decisions" #5.

**Scale/Scope**:
- Corpus chunk count: low tens of thousands (pandas docs ~few-hundred prose files, held-out issues ~10k after split-exclusion).
- pgvector embedding dimension: per the chosen model (operator decision; the table is parameterized to make a swap a migration, not a schema rebuild).

**Operator-deferred decisions** (recorded here so they don't masquerade as "NEEDS CLARIFICATION" — these are explicit "stop and ask" gates per user input #6):

1. **Embedding model choice**. Default candidates: `BAAI/bge-small-en-v1.5` (384d, MIT-licensed, fast on CPU) or `intfloat/e5-small-v2`. Decided after the corpus build is wired and the naive baseline has run.
2. **Cross-encoder choice**. Default candidate: `cross-encoder/ms-marco-MiniLM-L-6-v2` (local, free, fast). Decided after baseline numbers visible.
3. **Generation judge choice**. RAGAS (no LLM dependency for judging but its own dep weight) vs. a frozen Claude Haiku judge over a committed `prompts/rag_judge.md`. Decided alongside #1/#2.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

Rule-by-rule application to this slice:

- **Rule 1 (Layered architecture).** New code respects the layers verbatim:
  `app/api/routers/retrieve.py` (HTTP only) → `app/services/retrieve_service.py` (orchestration: HyDE → embed → first-stage → rerank → parent-aggregate) → `app/repositories/chunk_repository.py` (the only place `vector_cosine_distance` or `ts_rank_cd` is written) → `app/domain/retrieve.py` (Pydantic models). Adapters land in `app/infra/`: `embedding_client.py` and `reranker_client.py` (both call the model server over the **existing** `app/infra/model_server_client.py` transport with new typed methods). The corpus build is offline and lives under `scripts/rag/`, with shared GraphQL plumbing factored out of `scripts/dataset/fetch_issues_graphql.py`. **PASS.**

- **Rule 2 (Secrets discipline).** No new secret-shaped values land outside the Vault adapter. The Anthropic key for HyDE generation and (if chosen) the Claude judge is read from Vault at call time — the same path slice (f) wired for `/summarize`. The Postgres password comes from Vault via the existing `app/infra/database.py`. **PASS.**

- **Rule 3 (Storage discipline).** Postgres+pgvector for chunks + dense + sparse. MinIO for the corpus snapshot, the baseline numbers, and the per-CI eval reports. Redis is NOT touched in this slice (no ephemeral state needed for retrieval). Every schema change ships as an Alembic migration: `alembic/versions/0002_rag_chunks.py` adds the `rag_chunks` table + vector column + tsvector GIN index + supporting indices. No volume drop, no out-of-band DDL. **PASS.**

- **Rule 4 (Refuse to boot).** Three new fatal startup checks:
  - api: refuse if pgvector is unreachable; refuse if the `rag_chunks` table is empty; refuse if `RAG_CORPUS_RUN_ID` env points at a run with no rows. Specific log line per failure mode.
  - model_server: extend `model_server/boot_check.py` to also load the embedding model and the cross-encoder; refuse with a new specific log line per failure (`REFUSE TO BOOT: embedding model failed to load`, `REFUSE TO BOOT: cross-encoder failed to load`). The four existing DistilBERT checks remain. The `/classify`, `/ner`, `/summarize` endpoints continue to be served unaffected (per user input #1).
  - The threshold-zero check from Rule 4's existing list applies here too: `eval_thresholds.yaml`'s new `rag:` floors must be non-zero before any push that touches RAG (see "Operator-deferred decisions" #5). **PASS.**

- **Rule 5 (Evals are the grade).** `evals/rag/golden.jsonl` (25 examples, 5 operator-labeled), `evals/rag/eval_rag.py` (live retrieval, full metric suite), per-push report to MinIO at `evals/reports/{run_ts}/rag.json`. The CI gate fails non-zero on any floor breach. **PASS.**

- **Rule 6 (Decisions backed by numbers).** Every advanced choice in this slice (parent-document chunking, hybrid α, cross-encoder rerank, HyDE) carries a `DECISIONS.md` entry citing its delta over the naive baseline on the golden set. The parent-chunk aggregation choice (max child score) gets its own entry defending against mean/sum (user input #4). The sparse-index choice (Postgres tsvector + GIN) is documented as the default with the trigger that would switch us off it (user input #3). **PASS.**

- **Rule 7 (Observability).** `/retrieve` requests carry trace + request IDs through the existing middleware (no new code needed — the `RequestContextMiddleware` and `setup_tracing` shipped on Day 1 cover this). The HyDE-fallback path emits a child span. The two model-server calls (embed, rerank) are auto-instrumented by `HTTPXClientInstrumentor`. Log redaction continues to apply to all new error paths. **PASS.**

- **Rule 8 (Tooling).** New deps go through `uv lock`. The compose stack already runs all needed infra; this slice does NOT add a new compose service. A clean clone + `cp .env.example .env` + `docker-compose up` followed by the documented corpus-build command MUST reach a working `/retrieve`. **PASS.**

- **Rule 9 (No vibe coding).** Every file is named for what lives in it: `retrieve_service.py`, `chunk_repository.py`, `embedding_client.py`, `reranker_client.py`, `hyde.py`, `parent_aggregate.py`, `corpus_build_docs.py`, `corpus_build_issues.py`. No `utils.py` / `helpers.py` / `misc.py`. **PASS.**

- **Rule 10 (CI discipline).** Existing CI workflow extended with: the RAG eval gate step (after the classifier eval gate, before stack-down), the RAG corpus-build smoke (running the corpus pipeline against a 5-doc / 5-issue fixture in CI so the build path is exercised on every push, with the full corpus seeded the same way the classifier artifact is — see Phase 0 R6), and the model-server health wait gains the embedding-model + cross-encoder load time. **PASS.**

- **Rule 11 (Resilient tool use).** The `/retrieve` service catches `ModelServerUnreachableError` / `ModelServerTimeoutError` / `ModelServerInternalError` (the typed family from `app/infra/model_server_client.py`) and surfaces them as 503 / 504 / 502 — never as a 500. A HyDE generation failure falls back to embedding the raw question (recorded in the trace, not surfaced as an error to the caller). The api also returns 503 if the cross-encoder is unloaded, since shipping stage-1-only results would silently break the eval-gate assumptions. **PASS.**

**Constitution Check verdict (pre-Phase-0)**: All eleven rules pass. The
Complexity Tracking table at the end of this file is empty — no
justified deviations.

## Project Structure

### Documentation (this feature)

```text
specs/rag/
├── spec.md              # Feature spec (already in place)
├── plan.md              # This file
├── research.md          # Phase 0 output (resolves Phase 0 questions)
├── data-model.md        # Phase 1 output (chunk + embedding + report shapes)
├── quickstart.md        # Phase 1 output (operator runbook for corpus build + eval)
├── contracts/
│   ├── retrieve.openapi.yaml     # Phase 1 — /retrieve request/response schema
│   ├── embedding-client.md       # Phase 1 — model-server /embed contract
│   ├── reranker-client.md        # Phase 1 — model-server /rerank contract
│   └── corpus-artifacts.md       # Phase 1 — MinIO key conventions + corpus_report.json shape
├── checklists/
│   └── requirements.md           # already in place
└── tasks.md             # Phase 2 (/speckit-tasks command — NOT created by this command)
```

### Source Code (repository root)

```text
# Application code — extends the existing layered tree, NO new top-level
# directories under app/.
app/
├── api/
│   └── routers/
│       └── retrieve.py            # POST /retrieve handler — HTTP only (Rule 1)
├── services/
│   ├── retrieve_service.py        # orchestration: HyDE → embed → stage 1 → rerank → parent-aggregate
│   └── hyde_service.py            # Anthropic call for HyDE; falls back to raw question (Rule 11)
├── repositories/
│   └── chunk_repository.py        # ONLY place pgvector / tsvector SQL lives
├── domain/
│   └── retrieve.py                # Pydantic models for request/response/chunk
├── infra/
│   ├── embedding_client.py        # model-server /embed via the existing httpx transport
│   └── reranker_client.py         # model-server /rerank via the existing httpx transport

# Model server gains two new endpoints — same isolation as DistilBERT.
model_server/
├── embed.py                       # batch-tolerant /embed handler (loads embedding model at boot)
├── rerank.py                      # /rerank over (query, candidates[]) pairs (loads cross-encoder at boot)
├── boot_check.py                  # extended: embedding + cross-encoder load failures = REFUSE TO BOOT
└── routers/
    ├── embed.py                   # FastAPI router glue
    └── rerank.py                  # FastAPI router glue

# Migrations + offline pipeline.
alembic/versions/0002_rag_chunks.py    # rag_chunks (id, parent_id, content, embedding vector(D), source_type, source_id, source_timestamp, section_path, corpus_run_id, content_tsv tsvector) + GIN(content_tsv) + ivfflat(embedding)

scripts/rag/
├── build_corpus.py                # orchestrator: docs + issues → chunks → embed → upsert
├── fetch_docs.py                  # README + contributing + docs/ prose; skips code-only files
├── fetch_issues_held_out.py       # GraphQL issues + comments, excluding classifier-split issue_numbers
├── chunk_parent_document.py       # ≈400-char child / ≈2000-char parent with shared parent_id
├── chunk_naive.py                 # naive fixed-≈400-char baseline (FR-019)
└── embed_and_upsert.py            # batches /embed calls, bulk-inserts chunks via repository

# Evaluation.
evals/rag/
├── golden.jsonl                   # 25 examples (Rule 5)
├── README.md                      # selection logic, the 5 operator-labeled examples, judge choice
├── eval_rag.py                    # live retrieval against golden; writes evals/reports/{ts}/rag.json
├── baseline.json                  # frozen naive-baseline numbers (committed; diffed against advanced)
└── score.py                       # shared metric helpers (recall@k, MRR, nDCG, agreement)

# Eval thresholds + CI.
eval_thresholds.yaml               # rag.recall_at_5_floor, rag.mrr_floor (filled in by user input #5)
.github/workflows/ci.yml           # gains the "RAG eval gate" step + corpus-build smoke

# Tests.
tests/
├── services/test_retrieve_service.py     # orchestration with mocked model-server + repository
├── services/test_hyde_service.py         # fallback path
├── repositories/test_chunk_repository.py # SQL shape (against a real ephemeral Postgres? or mocked)
├── infra/test_embedding_client.py        # httpx.MockTransport
├── infra/test_reranker_client.py         # httpx.MockTransport
├── model_server/test_embed_router.py     # FastAPI TestClient + fake model
├── model_server/test_rerank_router.py    # FastAPI TestClient + fake model
└── evals/test_eval_rag.py                # pure metric helpers + report shape
```

**Structure Decision**: Strict adherence to the layered architecture
already in place. No new top-level Python directories under `app/`;
this slice adds files only inside the existing `app/api/routers/`,
`app/services/`, `app/repositories/`, `app/domain/`, and `app/infra/`
layers. The model server gains two new endpoints alongside `/classify`,
`/ner`, `/summarize` — same isolation pattern as DistilBERT, per user
input #1. The corpus build is offline; `app/` does not import from
`scripts/rag/`. The migration scaffolds the schema; no out-of-band DDL.

## Complexity Tracking

> No constitution violations to justify in this slice.

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|--------------------------------------|
| _(none)_  |            |                                      |
