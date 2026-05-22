# Feature Specification: Advanced RAG Pipeline — `/retrieve` endpoint + corpus build + eval gate

**Feature Branch**: `rag`

**Created**: 2026-05-21

**Status**: Draft

**Input**: User description: "The advanced RAG pipeline. By the end of this work, the api has a `/retrieve` endpoint that takes a maintainer question and returns the top relevant chunks from the project's docs and resolved issues, beating a naive baseline on a hand-curated golden set, with every choice off the baseline justified by a number (Rule 6). Two-stage funnel (hybrid first-stage + cross-encoder rerank), parent-document chunking, HyDE query transformation, metadata filtering, CI eval gate. GraphRAG explicitly rejected. Out of scope: chatbot, auth, memory, widget. Constitution v1.4.0 still applies."

## User Scenarios & Testing *(mandatory)*

### User Story 1 — Maintainer asks a question and gets relevant context (Priority: P1)

A maintainer interacts with the api through `POST /retrieve`, supplying a natural-language question (e.g. "how do I group a DataFrame by date and aggregate?"), an optional `k`, and optional source/time filters. The system returns the top reranked chunks of project documentation and past resolved-issue threads that are most likely to contain the answer, along with each chunk's content, source type (docs vs. issues), source identifier, score, and metadata. The response carries a request id and a trace id so the call is observable end-to-end. The api refuses to boot when the vector index isn't reachable or the corpus is empty so a misconfigured deployment never silently serves nothing.

**Why this priority**: This is the headline capability. Everything else in the slice exists to make this single call good — without it, no downstream chatbot or maintainer workflow can land.

**Independent Test**: With the corpus built once, a caller issues a `POST /retrieve` for a documented natural-English question, observes a non-empty list of ranked chunks, confirms each chunk carries the documented fields, confirms response headers include a request id and a trace id, and confirms that stopping the vector index causes the api to refuse to start with a specific log line. No other user story needs to ship for this slice to be demonstrable.

**Acceptance Scenarios**:

1. **Given** the corpus is built and the api is healthy, **When** a client sends a well-formed `/retrieve` request with a maintainer question, **Then** the response contains a ranked list of at most `k` chunks; each carries content, source type, source identifier, score, and metadata; and the response also includes a request id and a trace id.
2. **Given** the corpus is built, **When** the same `/retrieve` request is sent twice in quick succession, **Then** the responses are deterministic up to score ties (no flakiness in the result set).
3. **Given** the api is starting, **When** the vector index is unreachable or the embeddings table is empty, **Then** the api refuses to boot with a single specific log line per failure mode and a non-zero process exit (Rule 4 weights-integrity equivalent for the RAG corpus).
4. **Given** the api is healthy, **When** a `/retrieve` request has a malformed body, **Then** the api returns a typed 4xx response and does not crash (Rule 11).

---

### User Story 2 — Operator runs a reproducible one-shot corpus build (Priority: P1)

The operator runs a single offline script that fetches the pandas project's prose documentation and a held-out slice of resolved issues with their maintainer comments, filters that issue slice to exclude anything in the classifier's train/val/test splits, preprocesses the content, chunks it into matched child/parent pairs, embeds each child chunk, and writes the chunks + embeddings + metadata to the relational+vector store. The script never overwrites a prior run's outputs; every run gets a fresh corpus version id and writes a small summary report alongside the data. The build is reproducible: the same script run against the same source state yields the same chunk count and the same embedding identifiers.

**Why this priority**: Story 1 cannot deliver value without a populated index. The build is the upstream dependency, and it has its own correctness surface (no overlap with the classifier splits, byte-deterministic chunking, complete preprocessing) that must be testable in isolation.

**Independent Test**: Operator runs the documented corpus-build command against a clean relational+vector store with infrastructure already healthy; observes the script complete with a non-zero number of chunks reported, a summary report written next to the data, a fresh corpus version id assigned, and a verification that no issue in the held-out slice appears among the classifier's train/val/test issue numbers. A second run produces a new corpus version id, leaves the first one byte-unchanged, and the summary report shows the chunk count is identical given the same source state.

**Acceptance Scenarios**:

1. **Given** the infrastructure is healthy and the relational+vector store is empty, **When** the operator runs the corpus-build command with a corpus version id, **Then** the script populates the store with documentation chunks and held-out-issue chunks, writes a summary report under the corpus version's prefix in the blob store, and exits zero.
2. **Given** the classifier's train/val/test split is already present in the blob store, **When** the build assembles the held-out issue slice, **Then** zero issue numbers from that slice appear in any of the three classifier splits.
3. **Given** a prior corpus version is in place, **When** the operator runs the build again, **Then** the prior version's records and summary report are unchanged and a new corpus version id is assigned.
4. **Given** the same source state, **When** the build is re-run under a new version id, **Then** the chunk count and the per-source content checksums match the prior run.

---

### User Story 3 — Eval gate proves the advanced pipeline beats a naive baseline and CI enforces it (Priority: P1)

The operator commits a 25-example hand-curated golden set under the evaluation tree. Each golden row carries a question, an ideal answer, and a list of ground-truth chunk identifiers. Five of the 25 are labeled personally by the operator; the agreement between those operator labels and an automated judge is computed and reported. The slice also commits a naive baseline pipeline (fixed-size chunks, dense-only retrieval, no rerank, no query transformation) and the numbers it produces on the golden set. Every choice that distinguishes the advanced pipeline from the naive baseline — parent-document chunking, hybrid first-stage weighting, cross-encoder rerank, HyDE query transformation — is required to beat the naive baseline on at least one of recall@5 / recall@20 / MRR / nDCG; any choice that fails to do so is dropped from the pipeline. The evaluation also produces generation metrics from a documented judge (RAGAS or a frozen Claude judge), and a single threshold file gates CI: the build runs live retrieval against the golden set on every push, uploads a structured report under `evals/reports/{run_ts}/rag.json` in the blob store, and fails non-zero when any committed floor is breached.

**Why this priority**: The constitution's Rule 6 makes every architectural choice cite numbers. Without this story, the previous two stories are decorative — there's no way to defend that the pipeline is better than the trivial alternative. The eval gate is also what keeps the system honest going forward.

**Independent Test**: With the corpus already built and the advanced retrieval already wired (stories 1 + 2 satisfied), the operator runs the eval command; observes a printed table of recall@5, recall@20, MRR, nDCG for both the advanced pipeline and the naive baseline; confirms each advanced design choice has at least one metric where it beats the naive number; confirms the operator-labeled five examples agree with the automated judge above a stated agreement threshold; confirms the report has been uploaded to the blob store at the documented path; and confirms CI on the current commit reports green when the floors are met and red on a deliberately regressed change.

**Acceptance Scenarios**:

1. **Given** the corpus and the advanced pipeline are in place, **When** the eval command runs against `evals/rag/golden.jsonl`, **Then** the report records recall@5, recall@20, MRR, nDCG, per-question generation scores, and the agreement number between operator labels and the automated judge.
2. **Given** the naive baseline and the advanced pipeline both run on the same golden set, **When** the comparison is computed, **Then** each advanced design choice records at least one of recall@k / MRR / nDCG where it beats the baseline; choices that fail this are removed from the pipeline before merge.
3. **Given** the operator deliberately regresses retrieval quality below a floor in the threshold file, **When** CI runs on that push, **Then** the build fails non-zero with a specific message naming the metric and the floor it breached.
4. **Given** CI runs successfully on a push, **When** the operator inspects the blob store, **Then** an eval report appears under `evals/reports/{run_ts}/rag.json` containing the metrics, per-question results, and a pointer to the corpus version id that was evaluated.

---

### User Story 4 — Caller scopes retrieval by source type and time window (Priority: P2)

A caller restricts a `/retrieve` request to a subset of the corpus by passing a `filters` object: a source-type list (any combination of `docs` and `issues`) and an inclusive time window (`from` / `to` timestamps). The system applies the filter during first-stage retrieval — not after reranking — so the filtered candidate pool still has enough breadth for the reranker to do useful work. When the filter excludes every chunk in the corpus, the response is an empty list with the same envelope (no crash, typed response).

**Why this priority**: Maintainers often know which surface (docs vs. issues) is most likely to hold the answer, and a time window keeps historical issues from drowning recent doc changes. The capability is downstream of Story 1 (the unfiltered endpoint must work first) but unlocks materially better hit rates for narrow questions.

**Independent Test**: With Stories 1 + 2 + 3 satisfied, the operator issues a `/retrieve` request scoped to `source=docs` and a 30-day time window; observes only chunks whose source type is `docs` and whose source timestamp is inside the window; repeats with `source=issues` and observes the symmetric behavior; sends a deliberately empty intersection filter and observes an empty result list with no error.

**Acceptance Scenarios**:

1. **Given** the corpus contains chunks of both source types, **When** a `/retrieve` request specifies `filters.source = ["docs"]`, **Then** every returned chunk carries `source_type = docs`.
2. **Given** chunks span a year of source timestamps, **When** a `/retrieve` request specifies a 30-day time window, **Then** every returned chunk's source timestamp falls inside the window.
3. **Given** the corpus contains zero chunks matching a filter combination, **When** the request is sent, **Then** the response is a well-formed empty result list with a `200` status (not an error).
4. **Given** any filter combination, **When** the request is sent, **Then** the filter is applied during first-stage retrieval so the reranker sees a candidate pool drawn from the filtered subset.

---

### Edge Cases

- A `/retrieve` request with an empty question string is rejected as a typed 4xx — no embedding call is made.
- A `/retrieve` request with `k = 0` returns an empty list with a `200` status.
- The corpus build encounters a documentation file that's mostly code (e.g., an `.rst` with auto-generated API tables). Such files are skipped per the "prose, not code" intent and the skip is counted in the summary report.
- The GitHub fetch step hits a rate limit. The build pauses and resumes from the same cursor; no chunk is double-written and the run id stays the same.
- Two issues are duplicates after de-duplication on `(repo, issue_number)`. Only one set of chunks is written.
- The classifier's split data is missing or corrupt. The corpus build refuses to start with a specific log line; it does not silently build a corpus that may overlap with the splits.
- HyDE generates a hypothetical answer that is shorter than a configurable floor (e.g., a refusal). The system falls back to embedding the raw question and the fallback is recorded in the trace.
- The reranker model fails to load. The api refuses to boot; it does not silently serve stage-1-only results that would invalidate the eval gate's assumptions.
- An advanced design choice fails to beat the naive baseline on every metric. That choice is dropped from the pipeline; the next CI run reports the now-smaller pipeline against the same baseline.
- The operator-labeled five examples disagree with the automated judge below the agreement floor. The report flags it; the eval gate does not pass until the agreement is investigated.

## Requirements *(mandatory)*

### Functional Requirements

#### Retrieval API surface

- **FR-001**: The api MUST expose `POST /retrieve` accepting `{ question: string, k: int (optional), filters: { source: list, from: timestamp, to: timestamp } (optional) }`.
- **FR-002**: The api MUST return `{ chunks: [{ content, source_type, source_id, score, metadata }], request_id, trace_id }` where each chunk's `content` is the full parent-chunk text, `source_type` is one of `{docs, issues}`, `source_id` identifies the underlying document or issue, `score` is the reranker output, and `metadata` carries any caller-relevant fields (URL, timestamp, section heading, etc.).
- **FR-003**: The api MUST refuse to boot — emitting a single specific log line per failure mode and exiting non-zero — when (a) the vector index is unreachable; (b) the embeddings table is empty; or (c) the corpus version pointed to by configuration is absent.
- **FR-004**: The api MUST surface model-server / reranker / embedding failures as typed responses (e.g. 503 for unreachable, 504 for timeout) and never as a 500 (Rule 11).
- **FR-005**: Every `/retrieve` request and response MUST carry a request id and a trace id; both MUST appear in response headers and in the response body for the caller's correlation (Rule 7).

#### Corpus build

- **FR-006**: The corpus build MUST be a standalone script (not part of the api runtime) and the api MUST NOT import any of its modules at boot.
- **FR-007**: The corpus build MUST source documentation from the pandas project repository: the project README, the contributing guide, and prose content under the repo's `docs/` tree. Code-only files MUST be skipped and the skip count MUST appear in the run's summary report.
- **FR-008**: The corpus build MUST source resolved issues with maintainer responses via the same GraphQL fetch pattern already used by the classifier's offline pipeline.
- **FR-009**: The held-out issue slice MUST exclude every issue whose number appears in the classifier's `train`, `val`, or `test` split. The build MUST verify this exclusion against the classifier's `processed/pandas/{dataset_run_id}/*.parquet` files and MUST refuse to write any chunk if the split data is unavailable.
- **FR-010**: The corpus build MUST chunk each source using parent-document retrieval: small child chunks (≈400 characters each) for matching and larger parent chunks (≈2000 characters each) for returning. Every child chunk MUST carry a reference to its parent chunk.
- **FR-011**: The corpus build MUST embed each child chunk with a documented embedding model and persist `(chunk_id, parent_chunk_id, content, embedding, metadata)` tuples to the relational+vector store, with the `metadata` payload including at minimum `source_type`, `source_id`, `source_timestamp`, and `section_path`.
- **FR-012**: The corpus build MUST assign a fresh `corpus_run_id` per invocation, MUST NOT overwrite prior runs, and MUST write a `corpus_report.json` summary under that run id covering chunk counts (per source type), excluded issue count, skipped file count, source state hash, and the embedding model identifier.
- **FR-013**: The corpus build MUST be byte-reproducible: re-running it against the same source state under a new `corpus_run_id` MUST produce the same chunk count and the same per-chunk content checksums.

#### Retrieval pipeline

- **FR-014**: First-stage retrieval MUST be hybrid, combining a dense match (vector cosine over child-chunk embeddings) and a sparse match (BM25 or full-text search over the same child chunks), with a single tunable weight α ∈ [0, 1]. The weight MUST be selected by a sweep on the golden set, not hand-picked, and the chosen value MUST be recorded in `DECISIONS.md`.
- **FR-015**: First-stage retrieval MUST return the top 30 child chunks per query.
- **FR-016**: Second-stage retrieval MUST rerank those 30 candidates with a cross-encoder model and return the top 5 parent chunks (deduplicated by parent id). The cross-encoder choice MUST be documented in `DECISIONS.md`.
- **FR-017**: Query transformation MUST apply HyDE (hypothetical document embedding) before first-stage retrieval. A HyDE generation that produces below a configurable text-length floor MUST fall back to the raw question and that fallback MUST appear in the request's trace span.
- **FR-018**: Metadata filters (source-type subset and time window) MUST be applied during first-stage retrieval — i.e. inside the queries against the dense and sparse indices — so the reranker's candidate pool is drawn from the filtered subset, not the full corpus.

#### Evaluation

- **FR-019**: A naive baseline pipeline (fixed-≈400-character chunks, dense-only first-stage, no rerank, no HyDE) MUST be implemented and runnable against the golden set with the same eval command as the advanced pipeline.
- **FR-020**: Each advanced design choice — parent-document chunking, hybrid α weighting, cross-encoder rerank, HyDE — MUST beat the naive baseline on at least one of `recall@5`, `recall@20`, `MRR`, or `nDCG`. Any choice that fails this MUST be dropped from the pipeline before this slice merges.
- **FR-021**: `evals/rag/golden.jsonl` MUST contain exactly 25 examples; each row MUST be `{ question, ideal_answer, ground_truth_chunk_ids: [list of parent chunk ids] }`.
- **FR-022**: Five of the 25 golden examples MUST be hand-labeled personally by the operator. The agreement (Cohen's κ or simple match rate, documented choice) between those five operator labels and the same five labels produced by the automated judge MUST be computed and recorded in the eval report.
- **FR-023**: Generation evaluation MUST run on every push, producing per-question generation scores via RAGAS or a frozen Claude judge. The judge choice and the prompt template MUST be documented in `DECISIONS.md` and the prompt template MUST be version-controlled (Rule 9).
- **FR-024**: Real thresholds MUST be committed to `eval_thresholds.yaml` under a `rag:` section, at minimum `rag.recall_at_5_floor` and `rag.mrr_floor`. The CI gate MUST exit non-zero when any floor is breached and the message MUST name the metric and the floor it breached.
- **FR-025**: The CI gate MUST run live retrieval (not cached metrics) on every push, MUST upload `evals/reports/{run_ts}/rag.json` to the blob store, and the uploaded report MUST contain the metrics, per-question results, the corpus version id evaluated against, and the configuration (chunking, α, rerank model, HyDE settings) used.

#### Documentation

- **FR-026**: `DECISIONS.md` MUST record GraphRAG as explicitly rejected with the four-line argument from the course slides (general QA, small corpus, no strong entity relationships, no ground-truth ontology).
- **FR-027**: `DECISIONS.md` MUST defend every advanced design choice that survived the gate with the specific metric and delta over the naive baseline that justified keeping it.

### Key Entities

- **Document chunk**: A unit of corpus content with a child variant (≈400 chars, used for matching) and a parent variant (≈2000 chars, used for returning). Carries `chunk_id`, `parent_chunk_id`, `corpus_run_id`, `source_type` (`docs` | `issues`), `source_id` (file path or issue number), `source_timestamp`, `section_path`, and the child-chunk embedding.
- **Retrieval request**: Caller's question + optional `k` + optional filter object (source-type list + time window).
- **Retrieval response**: Ordered list of reranked parent chunks with score + metadata, plus request_id and trace_id for end-to-end correlation.
- **Golden example**: A `(question, ideal_answer, ground_truth_chunk_ids)` triple, plus a flag for whether the operator personally labeled it.
- **Eval report**: A structured record per CI run with retrieval metrics (recall@5, recall@20, MRR, nDCG), generation scores, per-question results, the `corpus_run_id` evaluated against, the pipeline configuration used, and the operator-vs-judge agreement on the five hand-labeled examples.
- **Naive baseline result**: A frozen snapshot of the baseline's metrics on the golden set, committed to the repo so subsequent advanced runs can diff against it without re-running the baseline.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A maintainer's natural-English question is answered with five ranked, relevant chunks within 2 seconds at the p95 latency budget for `/retrieve`, measured over the golden set (Rule 6 — bounded user-visible latency).
- **SC-002**: Across the four advanced design choices (parent-document chunking, hybrid α weighting, cross-encoder rerank, HyDE), every choice that ships in the final pipeline beats the naive baseline on at least one of `recall@5`, `recall@20`, `MRR`, or `nDCG` on the 25-example golden set; choices that don't are dropped before merge.
- **SC-003**: The CI evaluation gate catches a deliberately-regressed retrieval change (e.g. shuffling the reranker's input or muting HyDE) in a single build window — the build for that push fails non-zero with a message naming the breached floor.
- **SC-004**: The corpus build is fully reproducible: a re-run against the same source state under a new `corpus_run_id` produces the same chunk count, the same per-chunk content checksums, and a `corpus_report.json` whose source-state hash matches the first run.
- **SC-005**: Zero issue numbers from the held-out RAG corpus overlap with the classifier's `train`, `val`, or `test` splits — verified by an explicit set intersection over the canonical `processed/pandas/{dataset_run_id}/*.parquet` files.
- **SC-006**: The five operator-hand-labeled golden examples and the automated judge agree at or above a stated agreement floor (decision and floor recorded in `DECISIONS.md`); the eval report names the metric and the value on every CI run.
- **SC-007**: The eval report under `evals/reports/{run_ts}/rag.json` is present and complete on every successful CI run — retrieval metrics, generation scores, corpus version id, pipeline config.

## Assumptions

- **Existing infrastructure is reused.** Postgres+pgvector, the blob store, the secrets store, and the tracing backend already come up via the docker-compose stack shipped in prior days. This slice extends that stack; no new infrastructure services are introduced.
- **Classifier splits are the authoritative exclusion list.** The Day 1 dataset pipeline's `processed/pandas/{dataset_run_id}/{train,val,test}.parquet` is the canonical record of which issues are off-limits to the RAG corpus. The corpus build reads from those parquet files directly and refuses to run if they are missing or unreadable.
- **Embedding model selection is the operator's decision; the spec assumes one is chosen and committed.** The model identifier appears in `model_card`-style provenance under each `corpus_run_id` so a future model swap is auditable.
- **Cross-encoder runs locally.** Reranking does not depend on an external API. A failure to load the cross-encoder is a refuse-to-boot, not a graceful degradation, because shipping stage-1-only results would invalidate the eval gate's assumptions.
- **Generation judge runs against the same secrets-store key surface already in use.** If a frozen Claude judge is chosen, it reuses the Anthropic key already in Vault; if RAGAS is chosen, it uses the same key. Either way, the key is read from the secrets store at request time, never from `.env` (Rule 2).
- **Course-materials defaults are starting points, not unconditional choices.** Parent-document chunking and HyDE are introduced because the materials recommend them for this shape of corpus and query, but each is gated by the same "must beat naive baseline" rule (FR-020). If a default loses, it's dropped.
- **The classifier remains the deployed inference path.** This RAG slice extends the api with a `/retrieve` endpoint and a corpus pipeline; it does not change `/classify`, `/ner`, or `/summarize`, and it does not change the classifier's eval gate.
- **Course-materials' GraphRAG guidance applies to this corpus.** The decision to reject GraphRAG is based on the materials' "small corpora without strong entity relationships" criterion, which the pandas-issues + pandas-docs corpus matches. Should that shift (e.g. an explicit ontology lands), the decision is revisited in `DECISIONS.md`.
