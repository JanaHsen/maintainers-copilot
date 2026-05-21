---
description: "Task list for the advanced RAG pipeline — /retrieve, corpus build, eval gate"
---

# Tasks: Advanced RAG Pipeline — `/retrieve` + corpus build + eval gate

**Input**: Design documents from `/specs/rag/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/, quickstart.md

**Tests**: Targeted — refuse-to-boot tests (Rule 4), Rule-11 typed-error mapping tests, the corpus-build smoke (Rule 10 CI gate), and the eval gate itself. Broad unit coverage outside those is not generated.

**Operator decisions baked in (per task prompt — do not re-ask)**:
- Embedding model: `BAAI/bge-base-en-v1.5` (768 dim) — committed in T001.
- Cross-encoder: `cross-encoder/ms-marco-MiniLM-L-6-v2` — committed in T015.
- Generation judge: frozen Claude Haiku via existing `app/infra/anthropic_client.py` — committed in T035.

**Genuine stop-and-asks (called out inline)**:
- **T027** — five golden examples need operator hand-labeling (FR-022).
- **T037** — `eval_thresholds.yaml` `rag:` floors filled after advanced numbers visible.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependency on incomplete tasks).
- **[Story]**: US1 (retrieve), US2 (corpus build), US3 (eval gate), US4 (filtering); Setup / Foundational / Polish have no story label.
- Every task names file paths, an acceptance check, and the constitution rule numbers it respects.
- One task = one commit with a descriptive imperative message; **no task IDs in commit messages** (task IDs are scaffolding for this document only). Push after each.

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Make the relational+vector store ready for chunks; bake the ML deps used by both the model server (`/embed`, `/rerank`) and the corpus build (in-process embedding) into the toolchain.

- [X] T001 Add `alembic/versions/0002_rag_chunks.py` creating the `rag_chunks` table (columns per `specs/rag/data-model.md`), `vector(768)` for `BAAI/bge-base-en-v1.5`, the `content_tsv tsvector GENERATED ALWAYS AS (to_tsvector('english', content)) STORED` column, the GIN index over `content_tsv`, the partial `USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100) WHERE kind = 'child'` index, and the supporting `(parent_id)` / `(source_type, source_timestamp)` / `(corpus_run_id)` indices. Reversible downgrade. Acceptance: `docker compose run --rm migrate` runs the migration; `psql -d maintainers_copilot -c "\d rag_chunks"` shows all columns + indices; re-running migrate is a no-op success. Rules: 3, 4.

- [X] T002 [P] Add `sentence-transformers>=2.7` to the `ml` dependency group in `pyproject.toml`; pre-cache `BAAI/bge-base-en-v1.5` and `cross-encoder/ms-marco-MiniLM-L-6-v2` in `model_server/Dockerfile` after the existing distilbert pre-cache so first boot doesn't reach Hugging Face Hub. Acceptance: `uv lock` resolves cleanly; `docker compose build model-server` succeeds; the image's `huggingface/hub` cache contains both models. Rules: 4, 8.

- [X] T003 [P] Add `app/api/routers/retrieve.py`, `app/services/retrieve_service.py`, `app/services/hyde_service.py`, `app/repositories/chunk_repository.py`, `app/domain/retrieve.py`, `app/infra/embedding_client.py`, `app/infra/reranker_client.py`, `model_server/embed.py`, `model_server/rerank.py`, `model_server/routers/embed.py`, `model_server/routers/rerank.py` as **skeleton modules** with one-line docstrings only — empty function bodies / `pass`-with-`raise NotImplementedError`. Acceptance: `uv run python -c "import app.api.routers.retrieve, app.services.retrieve_service, model_server.embed, model_server.rerank"` exits 0; `uv run mypy` stays clean. Rules: 1, 9.

**Checkpoint**: Migration applied; ML deps installed; layered skeleton in place so subsequent tasks land in the right file from the first edit.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Land the test fixture every later phase tests against, and confirm the bare model-server image can boot with the new dep surface even before the new endpoints exist.

- [X] T004 Create `tests/fixtures/rag_smoke/` with **5 small doc files** (under 2KB each, representative of pandas docs/*.rst prose) and **5 small issue JSON files** mirroring the GraphQL response shape (`{number, title, body, comments: [...]}`). Document the fixture's structure in a short `tests/fixtures/rag_smoke/README.md`. Acceptance: `find tests/fixtures/rag_smoke -type f | wc -l` returns 11; `python -c "import json; [json.load(open(p)) for p in __import__('glob').glob('tests/fixtures/rag_smoke/issues/*.json')]"` exits 0. Rules: 9.

- [ ] T005 Confirm the bare `model-server` image still boots after the T002 dep additions — the existing seven DistilBERT refuse-to-boot conditions still apply and the api's `/health` smoke still passes. Acceptance: `docker compose up -d` reaches a healthy api per the existing slice-(j) CI flow; `docker compose logs model-server` shows DistilBERT boot OK (the new endpoints aren't implemented yet so they 404, which is the expected intermediate state). Rules: 4, 10.

**Checkpoint**: Tests have something to bite into; the existing stack is provably unbroken by the dep additions.

---

## Phase 3: User Story 2 — Reproducible one-shot corpus build (Priority: P1)

**Goal**: An operator runs `scripts/rag/build_corpus.py` once and ends up with chunks + embeddings + metadata in `rag_chunks`, a `corpus_report.json` in MinIO, and zero overlap with the classifier splits.

**Independent Test**: With the migration applied and the smoke fixture in place, run `uv run python scripts/rag/build_corpus.py --fixture tests/fixtures/rag_smoke --dataset-run-id 20260519T133455Z` and observe (a) the smoke completes in <30s, (b) `rag_chunks` has >0 parents and >0 children, (c) MinIO has `rag/corpus/{corpus_run_id}/corpus_report.json` with counts matching the database, (d) `excluded_issue_numbers.txt` is empty. Then re-run with the same fixture under a new `corpus_run_id` and observe the chunk count + per-chunk content checksums match the first run.

- [X] T006 [P] [US2] Implement `scripts/rag/fetch_docs.py`: **shallow + sparse-checkout** of `pandas-dev/pandas` at the configured ref, with the sparse-checkout patterns set to `README.md`, `CONTRIBUTING.md`, and `doc/source/**/*.rst` (note: `doc/source`, **not** `docs/`). Cache the clone under `~/.cache/maintainers-copilot/pandas-repo/` so subsequent runs reuse it (`git fetch --depth=1` on warm cache; full shallow clone on cold). Walk the sparse-checked-out tree and emit `(source_id, source_timestamp, raw_text)` tuples for prose-only files; skip code-only files (e.g. RST that's almost entirely auto-generated API tables) and increment a `skipped_files` counter. In fixture mode, read directly from `tests/fixtures/rag_smoke` instead of touching the cache. Acceptance: running it against `tests/fixtures/rag_smoke` returns exactly 5 doc tuples with `skipped_files == 0`; running against the real repo on a cold cache populates `~/.cache/maintainers-copilot/pandas-repo/` and the second invocation finishes substantially faster (uses the cache). Rules: 8, 9.

- [X] T007 [P] [US2] Implement `scripts/rag/fetch_issues_held_out.py`: GraphQL fetch via the existing PAT-from-Vault pattern from `scripts/dataset/fetch_issues_graphql.py`; **read `processed/pandas/{dataset_run_id}/{train,val,test}.parquet` from MinIO and refuse to start if any is missing**; assemble the held-out issue set by excluding every issue_number found in the three splits. **Maintainer-response filter (client-side, applied after the GraphQL fetch)**: drop any issue that has zero comments with `author_association ∈ {MEMBER, OWNER, COLLABORATOR}`. The GraphQL query already returns `author_association` on each comment node — this filter runs against the fetched data, no schema change. Emit `(source_id, source_timestamp, title, body, comments[])` tuples for the surviving (held-out **and** maintainer-touched) slice; in fixture mode read `tests/fixtures/rag_smoke/issues/*.json` and apply the same exclusions. Acceptance: a unit test passes a fake split that includes one fixture issue number and verifies it's excluded; a second unit test passes a fake comments list where no comment has a maintainer association and verifies the issue is dropped; the `excluded_issue_numbers.txt` artifact lists every split-overlapping issue encountered; against the real run, zero issues from the three splits land in the corpus and every surviving issue has at least one maintainer-association comment. Rules: 2, 9.

- [X] T008 [P] [US2] Implement `scripts/rag/chunk_parent_document.py`: split each source into ≈2000-char **parent** chunks at section boundaries (markdown headings / RST section markers) and ≈400-char **child** chunks inside each parent at sentence/paragraph boundaries; emit `(parent_id, parent_text, parent_index)` and `(child_id, parent_id, child_text, child_index)` tuples with **deterministic IDs** per `research.md` R7 (SHA-256 of `(corpus_run_id, source_type, source_id, section_path, parent_index, parent_content)` truncated to 26 chars, and similarly for children). Acceptance: chunking the same source twice produces byte-identical IDs; ratio of total child chars to total parent chars ≈ 1 (every parent's text is fully covered by its children). Rules: 9.

- [X] T009 [P] [US2] Implement `scripts/rag/chunk_naive.py`: split each source into fixed-≈400-char chunks with no parent/child distinction (used by the naive baseline pipeline). Acceptance: chunk count for a known source matches `ceil(len(text) / 400)`; IDs are deterministic in the same way as T008. Rules: 9.

- [X] T010 [P] [US2] Implement `scripts/rag/embed_and_upsert.py`: load `BAAI/bge-base-en-v1.5` via `sentence-transformers` once at startup; batch-embed child chunks (batch size 64; configurable); bulk-insert `(id, kind, parent_id, content, embedding, source_type, source_id, source_timestamp, section_path, child_index, parent_index, corpus_run_id)` into `rag_chunks` via `app/repositories/chunk_repository.py`'s bulk-insert helper (or directly via SQLAlchemy if the helper isn't there yet — T019 will collapse the duplication). Acceptance: against the smoke fixture, the embedding pass finishes in <15s on CPU with the pre-cached model; every inserted child row has a non-NULL `embedding` of length 768; parent rows have NULL `embedding`. Rules: 3, 9.

- [X] T011 [US2] Implement `scripts/rag/build_corpus.py` orchestrator: parse args (`--dataset-run-id` required, `--fixture` optional for the smoke path, `--corpus-run-id` defaulting to a fresh UTC stamp, **`--strategy` required with NO default** — accepted values `{parent_document, naive}`; the operator MUST choose explicitly per invocation until the chunking choice settles in Phase 5 — see T031 / T031-drop-case for when a default lands). Call `fetch_docs` + `fetch_issues_held_out` + the chunker selected by `--strategy` + `embed_and_upsert`; write `corpus_report.json` per the shape in `data-model.md`; upload `corpus_report.json`, `docs_index.jsonl`, `issues_index.jsonl`, `excluded_issue_numbers.txt` to MinIO under `rag/corpus/{corpus_run_id}/`. Acceptance: invoking the orchestrator without `--strategy` exits with a `argparse`-style "argument required" error (non-zero); invoking with `--strategy parent_document` against the fixture completes end-to-end in <30s and the four MinIO objects are present and well-formed; the `corpus_report.json` records the chosen `chunking.strategy`. Rules: 3, 4, 6, 9.

- [X] T012 [US2] Add `tests/scripts/test_build_corpus_smoke.py`: invoke `build_corpus.main(...)` in-process with `--fixture tests/fixtures/rag_smoke`, assert the four MinIO objects are present and that the database has the expected chunk counts. Use a fresh `corpus_run_id` per test invocation to avoid collisions. Acceptance: `uv run pytest tests/scripts/test_build_corpus_smoke.py -q` passes against the compose-up stack; wall time <30s. Rules: 9, 10.

- [X] T013 [US2] Add the corpus-build smoke as a CI step in `.github/workflows/ci.yml` (after the existing classifier eval gate, before stack-down). The step runs `uv run python scripts/rag/build_corpus.py --fixture tests/fixtures/rag_smoke --dataset-run-id 20260519T133455Z --corpus-run-id ci-smoke-${{ github.run_id }}` and exits non-zero on any failure. Acceptance: CI on a fresh push passes the new step in <30s; deliberately corrupting the fixture (e.g. removing one issue file) fails the step. Rules: 10.

**Checkpoint**: Corpus build is reproducible and CI-verified on every push. US1 can now sit on a populated index.

---

## Phase 4: User Story 1 — Maintainer asks a question and gets relevant context (Priority: P1) 🎯 MVP

**Goal**: `POST /retrieve` returns the top reranked parent chunks for a maintainer question. Refuses to boot without a populated `rag_chunks`. Rule-11 typed errors. End-of-phase: the MVP is demonstrable end-to-end from a clean clone.

**Independent Test**: With the corpus from Phase 3 in place, `curl -X POST localhost:8000/retrieve -d '{"question":"how do I group a DataFrame by date and aggregate?","k":5}'` returns a 200 with five chunks, each carrying the documented fields and trace+request IDs; stopping pgvector causes the next `docker compose up api` to refuse with the specific refuse-to-boot log line.

- [X] T014 [US1] Implement `model_server/embed.py` (the model + tokenizer holder) and `model_server/routers/embed.py` (the FastAPI router): load `BAAI/bge-base-en-v1.5` via `sentence-transformers` at boot; `POST /embed` returns `{ "embedding": [...float...], "model_id": "BAAI/bge-base-en-v1.5", "dim": 768 }` for `{ "text": "..." }` and the batch variant `{ "embeddings": [...] }` for `{ "texts": [...] }`. Extend `model_server/boot_check.py` to refuse to boot with the specific log line `REFUSE TO BOOT: embedding model failed to load` if the model can't load. Acceptance: `curl -sS -X POST localhost:8001/embed -d '{"text":"hello"}' | jq '.dim'` returns 768; deleting the cached `BAAI/bge-base-en-v1.5` weights and restarting model-server makes it refuse with the documented log line. Rules: 4, 9.

- [X] T015 [US1] Implement `model_server/rerank.py` and `model_server/routers/rerank.py`: load `cross-encoder/ms-marco-MiniLM-L-6-v2` via `sentence-transformers` at boot; `POST /rerank` returns `{ "scores": [{ "id": ..., "score": ... }, ...], "model_id": "cross-encoder/ms-marco-MiniLM-L-6-v2" }` for `{ "query": "...", "candidates": [{ "id": "...", "text": "..." }, ...] }`. Extend `model_server/boot_check.py` to refuse to boot with the specific log line `REFUSE TO BOOT: cross-encoder failed to load` if the cross-encoder can't load. Acceptance: a 30-candidate rerank call returns 30 scores in roughly the expected order on a hand-built query+candidates pair; deletion of the cached cross-encoder makes model-server refuse with the documented log line. Rules: 4, 9.

- [X] T016 [US1] Add `tests/model_server/test_embed_router.py` + `tests/model_server/test_rerank_router.py` (FastAPI `TestClient` + a fake model holder) plus `tests/model_server/test_boot_check_rag.py` proving each of the two new refuse-to-boot conditions. Acceptance: `uv run pytest tests/model_server/test_embed_router.py tests/model_server/test_rerank_router.py tests/model_server/test_boot_check_rag.py -q` passes. Rules: 4, 10.

- [ ] T017 [P] [US1] Implement `app/infra/embedding_client.py` over the existing `app/infra/model_server_client.py` httpx transport: a `embed(text: str, *, request_id: str) -> list[float]` and `embed_batch(texts: list[str], *, request_id: str) -> list[list[float]]` returning embeddings; the typed-error family `EmbedUnreachableError` / `EmbedTimeoutError` / `EmbedBadInputError` / `EmbedInternalError` per `specs/rag/contracts/embedding-client.md`. Acceptance: a unit test with `httpx.MockTransport` proves each error maps to the right typed exception (mirroring `tests/infra/test_model_server_client.py`). Rules: 1, 11.

- [ ] T018 [P] [US1] Implement `app/infra/reranker_client.py`: a `rerank(query: str, candidates: list[Candidate], *, request_id: str) -> list[Score]` over the same httpx transport; typed-error family `RerankUnreachableError` / `RerankTimeoutError` / `RerankBadInputError` / `RerankInternalError` per `specs/rag/contracts/reranker-client.md`. Longer per-call timeout than embed (rerank is the heaviest hop). Acceptance: mock-transport tests for each typed exception. Rules: 1, 11.

- [ ] T019 [US1] Implement `app/repositories/chunk_repository.py` (the ONLY place pgvector / tsvector SQL lives — Rule 1): `query_first_stage(embedding: list[float], query_text: str, *, alpha: float, k: int, filters: ChunkFilters | None, corpus_run_id: str) -> list[ChildHit]` that runs **one SQL query** combining `1 - (embedding <=> :embedding)` (dense cosine similarity) and `ts_rank_cd(content_tsv, plainto_tsquery('english', :query_text))` (sparse), weighted by `alpha * dense + (1 - alpha) * sparse`, optionally filtered by `source_type`/`source_timestamp`, returning the top `k` child rows + their `parent_id`s + scores. Also: `fetch_parents(parent_ids: list[str]) -> dict[str, Parent]` for the rerank-and-aggregate step; `is_empty(corpus_run_id: str) -> bool` for the boot check. Acceptance: an integration test against the compose Postgres seeded with the smoke corpus returns deterministic chunk IDs for a fixed query+embedding. Rules: 1, 3, 9.

- [ ] T020 [P] [US1] Implement `app/domain/retrieve.py`: `RetrieveRequest`, `RetrieveFilters`, `RetrievedChunk`, `RetrieveResponse`, `ChunkFilters`, `ChildHit`, `Parent`, `Candidate`, `Score` Pydantic models per `specs/rag/data-model.md`. Validators: `question.min_length=1`, `k ∈ [0, 20]`, `filters.source ⊂ {docs, issues}`, `filters.from <= filters.to`. Acceptance: a unit test exercises each validator's happy and unhappy paths. Rules: 1, 9.

- [ ] T021 [US1] Implement `app/services/retrieve_service.py`: orchestrate the request — **for this MVP phase**, use a feature-flag-gated pipeline that starts at the **naive shape** (no HyDE; α = 1.0 dense-only; no rerank; child chunks returned as-is). The full advanced shape (HyDE → embed → hybrid α → rerank → parent aggregation) lands piece-by-piece in Phase 5 behind the same service entry point. Maps every model_server typed exception onto `RetrieveOutcome` variants the router converts to HTTP statuses (Rule 11 — see `specs/rag/contracts/retrieve.openapi.yaml`). Acceptance: unit tests with mocked clients prove (a) the happy path, (b) each typed exception → correct outcome, (c) the feature flag toggles the pipeline. Rules: 1, 11.

- [ ] T022 [P] [US1] Implement `app/services/hyde_service.py`: synchronous `transform(question: str) -> tuple[str, bool]` returning `(maybe_transformed_question, hyde_applied)`; calls `app.infra.anthropic_client.complete` with the `prompts/hyde.md` system+user template (committed empty for now — T034 fills it). On generation length < `HYDE_MIN_LENGTH` or any `AnthropicError`, returns `(question, False)` and logs the fallback in the active span. Phase 4's `retrieve_service` does NOT call this yet; the wiring lands in T034. Acceptance: unit tests for happy path + length-fallback + exception-fallback (each verifies the fallback boolean is False). Rules: 1, 7, 11.

- [ ] T023 [US1] Implement `app/api/routers/retrieve.py`: `POST /retrieve` handler that calls `retrieve_service.retrieve(req, request_id, trace_id)` and maps `RetrieveOutcome` variants to the HTTP statuses in `specs/rag/contracts/retrieve.openapi.yaml` (200/422/502/503/504). Wires into `app/api/routers/__init__.py`. Acceptance: `curl -sS -X POST localhost:8000/retrieve -d '{"question":"hello","k":5}' -H "Content-Type: application/json"` returns 200 with chunks + headers `X-Request-Id`, `X-Trace-Id`; an empty-question body returns 422; killing model-server returns 503. Rules: 1, 7, 11.

- [ ] T024 [US1] Extend `app/main.py` lifespan with the four new boot checks (Rule 4 — see `specs/rag/data-model.md` "Lifecycle / boot-time invariants"): refuse to boot if (a) the pgvector extension isn't installed (`SELECT 1 FROM pg_extension WHERE extname='vector'`); (b) `RAG_CORPUS_RUN_ID` env var is unset; (c) `rag_chunks` table is empty; (d) `rag_chunks` has zero rows for the configured `corpus_run_id`. Each failure logs a single specific `REFUSE TO BOOT: ...` line and the lifespan raises so the container exits non-zero. Acceptance: a refuse-to-boot test mirrors `tests/infra/test_refuse_to_boot.py` for each of the four conditions; `docker compose up -d api` exits non-zero with the documented log line when each condition is forced. Rules: 4.

- [ ] T025 [US1] Add `tests/api/test_retrieve_router.py` covering the happy path (returns 200 with the documented envelope) and each Rule-11 typed mapping (503/504/502 for unreachable/timeout/internal); mock the retrieve_service via dependency-overrides. Acceptance: `uv run pytest tests/api/test_retrieve_router.py -q` passes; the integration test runs against the compose stack and returns real chunks. Rules: 7, 11.

**Checkpoint**: `/retrieve` is live with the naive shape; refuse-to-boot covers the new failure modes; Rule 11 mapping is proven; from a clean clone the stack reaches a working `/retrieve` after `docker compose up` + `build_corpus.py`.

---

## Phase 5: User Story 3 — Eval gate proves advanced beats naive + CI enforces (Priority: P1)

**Goal**: The 25-example golden set lives in the repo; the naive baseline numbers are committed; each of the four advanced design choices ships in its own commit alongside a `DECISIONS.md` entry citing the delta over baseline; the generation eval runs against a frozen Claude Haiku judge; `eval_thresholds.yaml` carries real floors and CI gates on them.

**Independent Test**: With US1 + US2 in place, the operator runs the documented eval command; observes the live retrieval metrics (recall@5, recall@20, MRR, nDCG) for both naive and advanced pipelines; confirms each advanced choice beats baseline on ≥1 metric (or the corresponding commit shows that choice was dropped); confirms CI on the current push uploads `evals/reports/{run_ts}/rag.json` to MinIO and reports green; a deliberate regression makes CI red with the breached-floor message.

- [ ] T026 [US3] Curate `evals/rag/golden.jsonl`: 25 examples drawn from the same val-split slice used in the corpus (mirror of slice-(i) — separate from the classifier test split for independence); each row is `{ question_id, question, ideal_answer, ground_truth_chunk_ids, operator_labeled: false, notes: ... }`. Leave the five rows that will be operator-labeled with `operator_labeled: true` and `ground_truth_chunk_ids: []` as placeholders (filled in T027). **Manual review gate (explicit)**: after the implementer drafts the 20 auto-labeled rows + the 5 placeholders, the **operator reads every one of the 25 questions** — for the 20 implementer-drafted rows, the operator explicitly approves each (and the implementer revises any rejected ones in the same task); for the 5 placeholders, the operator notes which ones they intend to personally label in T027. **T028 does NOT ship until the operator's approval of the 20 is on record** (record it as a "20/20 implementer-drafted golden questions approved by operator" line in `evals/rag/README.md`). Acceptance: `wc -l evals/rag/golden.jsonl` is 25; `jq -s 'group_by(.operator_labeled) | map({k:.[0].operator_labeled, n:length})'` shows `[{k:false,n:20},{k:true,n:5}]`; `evals/rag/README.md` records the selection logic, the approval line, and the 5 placeholder question IDs. Rules: 5, 9.

- [ ] T027 [US3] **STOP AND ASK**: the five `operator_labeled: true` rows in `golden.jsonl` need the operator to read the candidate chunks and personally write the `ground_truth_chunk_ids` list per FR-022. This task block is a wait state — the operator returns with the labels; the implementer pastes them into the JSONL and commits. Acceptance: all 25 rows have non-empty `ground_truth_chunk_ids`; `jq -s 'map(select(.operator_labeled == true)) | length'` returns 5. Rules: 5, 6.

- [ ] T028 [US3] [P] Implement `evals/rag/score.py`: pure helpers `recall_at_k`, `mrr`, `ndcg`, `confusion_pairs`, `cohen_kappa_for_operator_vs_judge` (the 5-example agreement metric for FR-022). No I/O — takes `predictions` + `golden` as in-memory lists. Acceptance: unit tests cover each metric against hand-rolled inputs with known expected values. Rules: 5, 9.

- [ ] T029 [US3] Implement `evals/rag/eval_rag.py` with two **modes** selected by `--mode {naive,advanced}`. **Naive mode**: queries `rag_chunks` directly via `chunk_repository.query_first_stage` with `alpha=1.0`, fixed-400-char child chunks only, no rerank, no HyDE, k=5, no metadata filter; computes the retrieval metrics over the golden set. **Advanced mode**: routes through the live `/retrieve` endpoint (this is what CI runs). Both modes write the report under `evals/reports/{run_ts}/rag.json` per the shape in `data-model.md`; both `--upload-report` to MinIO. Acceptance: `uv run python evals/rag/eval_rag.py --mode naive --skip-upload` against the smoke corpus prints a metrics block; the same command in advanced mode against the live api prints metrics + writes the report. Rules: 5, 9, 10.

- [ ] T030 [US3] Run the naive baseline once, commit the numbers: `uv run python evals/rag/eval_rag.py --mode naive --output evals/rag/baseline.json`. Commit `evals/rag/baseline.json` with the resulting metrics frozen. Add a `DECISIONS.md` "## RAG naive baseline" section recording the configuration, the four metrics, and the commit hash so subsequent advanced commits can cite a fixed comparison point. Acceptance: `evals/rag/baseline.json` is non-empty and well-formed; `DECISIONS.md` cites it. Rules: 5, 6.

- [ ] T031 [US3] **Advanced choice 1 — parent-document chunking.** Switch `retrieve_service` to use the parent-document chunks (children for matching, parents for returning) plus the max-child-score aggregation from `research.md` R2. Re-run `eval_rag.py --mode advanced`. **If parent-document beats baseline on ≥1 of recall@5/recall@20/MRR/nDCG**, ship the change and add a `DECISIONS.md` "## RAG parent-document chunking" entry citing the delta and defending max-aggregation against mean/sum. **If it doesn't beat baseline**, revert the service change in the same commit and write a DECISIONS.md entry recording the negative result and that the slice continues with fixed-400-char chunks. Commit message: `parent-document chunking: <kept|dropped>; delta vs baseline = ...`. Acceptance: live eval report shows the new metrics; baseline.json untouched. Rules: 5, 6.

- [ ] T032 [US3] **Advanced choice 2 — hybrid α sweep.** Implement `evals/rag/sweep_alpha.py` that runs the advanced pipeline with `alpha ∈ {0.0, 0.1, ..., 1.0}` and emits a per-α metrics table; pick the α that maximizes recall@5. Wire the chosen α as a config constant in `retrieve_service`. Re-run `eval_rag.py --mode advanced`. **If hybrid beats baseline on ≥1 metric**, ship and add a `DECISIONS.md` "## RAG hybrid α" entry citing the α value, the sweep table, and the delta. **If α=1.0 wins (dense-only)**, ship the simpler configuration and DECISIONS.md says so explicitly (and lists the trigger that would switch to a separate BM25 lib per `research.md` R1). Commit message: `hybrid α = <value>; sparse weight = <1-value>; delta vs baseline = ...`. Acceptance: the sweep table is committed under `evals/rag/alpha_sweep.json`; the chosen α appears in code + DECISIONS.md. Rules: 5, 6.

- [ ] T033 [US3] **Advanced choice 3 — cross-encoder rerank.** Wire `reranker_client` into `retrieve_service`: stage 1 returns 30 child hits; service builds the rerank candidate list, calls `/rerank`, aggregates by parent (max child score per parent), returns top 5 parents. Re-run `eval_rag.py --mode advanced`. **If rerank beats baseline (or beats the post-T032 numbers) on ≥1 metric**, ship and add a `DECISIONS.md` "## RAG cross-encoder rerank" entry citing the delta and defending max-aggregation against mean/sum (per `research.md` R2). **If it doesn't beat**, revert the service change in the same commit. Commit message: `cross-encoder rerank: <kept|dropped>; delta = ...`. Acceptance: live eval report; the rerank latency contribution is visible in the report's `pipeline_config.rerank_top_k`. Rules: 5, 6, 11.

- [ ] T034 [US3] **Advanced choice 4 — HyDE.** Write `prompts/hyde.md` (system + user template, two-section markdown matching `prompts/summarizer.md`) and wire `hyde_service` into `retrieve_service` ahead of the embed call (HyDE-transformed text → `/embed`); the existing fallback path stays. Re-run `eval_rag.py --mode advanced`. **If HyDE beats the post-T033 numbers on ≥1 metric**, ship and add a `DECISIONS.md` "## RAG HyDE" entry citing the delta + the fallback rate observed in eval traces. **If it doesn't beat**, revert the wiring in the same commit (`hyde_service` stays in the repo for future use). Commit message: `HyDE query transformation: <kept|dropped>; delta = ...`. Acceptance: live eval report shows `pipeline_config.hyde_enabled` reflecting the kept/dropped decision; `prompts/hyde.md` is committed regardless. Rules: 5, 6, 7, 9.

- [ ] T035 [US3] Add `prompts/rag_judge.md`: system + user template for the **frozen Claude Haiku judge** that scores each question's generated answer against `ideal_answer`. Two-section markdown matching the existing `prompts/summarizer.md` convention; the system block enforces a numeric `relevance` (0-1) and `faithfulness` (0-1) score with no prose. Commit message: `commit RAG judge prompt (Claude Haiku, frozen)`. Acceptance: the prompt parses via `model_server/prompts.py::load_system_user`; the system block contains the two scoring axes and a "respond with only JSON" instruction. Rules: 6, 9.

- [ ] T036 [US3] Extend `evals/rag/eval_rag.py` with the **generation eval**: for each golden question, take the top-5 chunks the advanced pipeline returned, render a generation prompt (question + chunks), call Claude Haiku (`app/infra/anthropic_client.complete`) with the judge prompt, parse the JSON `{ relevance, faithfulness }`, aggregate to mean scores. Compute the operator-vs-judge agreement on the 5 hand-labeled rows (per FR-022) using `score.py::cohen_kappa_for_operator_vs_judge`. Add both to the per-CI report. Commit message: `wire RAG generation eval via frozen Claude Haiku judge`. Acceptance: a dry run with `--mode advanced --max-questions 3` prints generation scores plus the agreement metric for the 5 labeled rows that fall in those 3; the report JSON carries `generation_metrics` + `operator_judge_agreement`. Rules: 2, 5, 6, 7, 11.

- [ ] T037 [US3] **STOP AND ASK**: With the advanced numbers visible from T031–T036, fill `eval_thresholds.yaml`'s `rag:` section per user input #5 — `rag.recall_at_5_floor` and `rag.mrr_floor` set to a 5-point buffer below the observed advanced numbers; include a `notes:` field explaining the buffer choice (mirror the existing `classifier.notes`). Acceptance: the YAML parses; values are non-zero (Rule 4 — no `enforced: true` with floor 0); the comment block above the values cites the source advanced report. Rules: 4, 5, 6.

- [ ] T038 [US3] Add `scripts/ci/seed_rag_corpus.sh` — the same release-seeded pattern as the classifier artifact in `scripts/ci/seed_classifier_artifact.sh`. CI downloads a published `rag-corpus-v1-<corpus_run_id>` release attachment containing the chunk dump + `corpus_report.json`, verifies the corpus report's source-state hash, bulk-loads `rag_chunks` from the dump, and uploads the corpus_report.json to MinIO. The release is created once by the operator after the local corpus build settles (mirror of slice-(j)'s release pattern). Acceptance: a local run of the script (after publishing a release with the smoke corpus dump) populates `rag_chunks` and writes the report to MinIO. Rules: 4, 8, 10.

- [ ] T039 [US3] Wire the live RAG eval gate into `.github/workflows/ci.yml`: between the classifier eval gate (existing) and stack-down, run `uv run python evals/rag/eval_rag.py --mode advanced --upload-report --check-thresholds`. Add a "Seed RAG corpus" step before "Stack up — api + model-server" that calls `scripts/ci/seed_rag_corpus.sh` (parallel to the existing classifier seed). Extend the model-server `Wait for model-server healthy` step to account for the embedding + cross-encoder boot times. Acceptance: CI on a fresh push completes the live RAG eval and writes `evals/reports/{run_ts}/rag.json` to MinIO; deliberately corrupting the corpus (e.g. emptying one chunk's content) flips the build red with the breached-floor message. Rules: 4, 5, 10.

**Checkpoint**: The eval gate is live; CI enforces the floors; every advanced choice that survived has a numbered defense in DECISIONS.md.

---

## Phase 6: User Story 4 — Caller scopes retrieval by source type and time window (Priority: P2)

**Goal**: `filters: { source: [...], from, to }` on the request body filters the candidate pool **inside** stage-1 SQL.

**Independent Test**: `curl -X POST /retrieve -d '{"question":"...","filters":{"source":["docs"],"from":"2024-01-01T00:00:00Z","to":"2024-12-31T23:59:59Z"}}'` returns only chunks with `source_type=="docs"` and `source_timestamp` inside the window; an empty-intersection filter returns `{"chunks": []}` with HTTP 200.

- [ ] T040 [P] [US4] Extend `app/repositories/chunk_repository.py::query_first_stage`'s SQL to accept `ChunkFilters` and translate them into a `WHERE` clause applied **before** the cosine + tsvector ranking — so the rerank candidate pool is drawn from the filtered subset (FR-018). Acceptance: a repository unit test seeds two docs and two issues with known timestamps and asserts each filter combination returns the expected subset. Rules: 1, 3.

- [ ] T041 [P] [US4] Wire the filter through the layered stack: `RetrieveRequest.filters` → `retrieve_service.retrieve(...)` → `chunk_repository.query_first_stage(filters=...)`. `app/domain/retrieve.py` already has the validation from T020; this task threads the value without changing the validators. Acceptance: `curl` with a filter returns the right subset; `from > to` returns 422 from the validator. Rules: 1.

- [ ] T042 [US4] Add `tests/services/test_retrieve_filters.py` exercising the four documented scenarios (`source=docs`, `source=issues`, time window only, empty intersection). Acceptance: tests pass against the live corpus. Rules: 5.

**Checkpoint**: Filtering works inside stage 1; no rerank-pool starvation.

---

## Phase 7: Polish & Cross-Cutting

- [ ] T043 Add the GraphRAG rejection entry to `DECISIONS.md`: a "## GraphRAG rejected" section with the four-line argument from the course slides (general QA, small corpus, no strong entity relationships, no ground-truth ontology) per FR-026. Commit message: `record GraphRAG rejection (general-QA / small corpus / no entity graph)`. Acceptance: the section exists and cites the four-line rationale. Rules: 6.

- [ ] T044 Update `ARCH.md` with a RAG-layer mermaid diagram showing the request flow: client → api `/retrieve` → service → repository (Postgres) + httpx → model_server (`/embed`, `/rerank`) → service (parent aggregate) → response. Reflect the corpus-build offline path as a separate diagram. Commit message: `arch: document the RAG retrieval + corpus-build flows`. Acceptance: `ARCH.md` renders the two diagrams. Rules: 1, 9.

- [ ] T045 Update `RUNBOOK.md` with the operator commands from `specs/rag/quickstart.md` (steps 1–10): migrate, build corpus, verify, restart with `RAG_CORPUS_RUN_ID`, smoke `/retrieve`, run naive baseline, sweep α, run advanced + upload report, publish release, tear down. Commit message: `runbook: rag corpus build + eval workflow`. Acceptance: a clean clone followed only by RUNBOOK commands reaches a working `/retrieve` against a populated corpus. Rules: 8.

---

## Dependencies & Execution Order

### Build-up chain (sequential — top to bottom; one task = one commit; push after each)

Setup (T001–T003) → Foundational (T004–T005) → US2 corpus build (T006–T013) → US1 retrieve MVP (T014–T025) → US3 eval gate (T026–T039 — naive baseline before each advanced choice; **STOP at T027** for the operator labels; **STOP at T037** for the floors) → US4 filtering (T040–T042) → Polish (T043–T045).

### Story mapping

- **US1 (P1, MVP)**: T014–T025 — depends on T001–T013.
- **US2 (P1)**: T006–T013 — depends on T001–T005.
- **US3 (P1)**: T026–T039 — depends on US1 + US2 (needs `/retrieve` + the corpus).
- **US4 (P2)**: T040–T042 — depends on US1.

### Parallel opportunities

- **T002, T003** in setup (different files, no shared state).
- **T006, T007, T008, T009, T010** within US2 (different files; orchestrator T011 depends on all of them).
- **T017, T018, T020, T022** within US1 (independent files, all under different layers).
- **T028, T035** within US3 (pure helpers + the prompt file).
- **T040, T041** within US4 (different layers, T042 tests both).

### The two genuine stop-and-asks

- **T027** — operator hand-labels five golden examples. No code is generated for this task; the implementer pauses, the operator returns with the labels, the implementer pastes them into `golden.jsonl` and commits.
- **T037** — operator picks `rag.recall_at_5_floor` and `rag.mrr_floor` after seeing the advanced numbers. The implementer pauses with the report in hand; the operator dictates the floors; the implementer writes them into `eval_thresholds.yaml` and commits.

## Implementation Strategy

### MVP first

1. Phases 1–2 (migration + smoke fixture).
2. Phase 3 (US2 corpus build — including the CI smoke).
3. Phase 4 (US1 retrieve MVP — `/retrieve` returns chunks, refuses to boot correctly, types its errors).
4. **STOP and VALIDATE** with `quickstart.md` steps 1–5 (a `curl` to `/retrieve` returns 200 with chunks) → demonstrable MVP.

### Incremental delivery

- MVP (US1 + US2) → eval gate (US3, with the two stop-and-asks) → filtering (US4) → polish.
- Each Phase-5 advanced choice (T031–T034) is its own commit with its own numbers attached. A choice that fails to beat baseline drops in the SAME commit it was tried in.

### Notes

- One task = one commit. Commit message is the imperative line in the task body, never the task ID.
- Push after each commit (mirror the slice cadence the foundations branch has been using).
- Each task lists the Rule numbers it respects (constitution requirement for `tasks.md`).
- The two stop-and-ask blocks (T027, T037) are the ONLY places the implementer pauses for operator input; every other decision in this list has a baked-in answer.
