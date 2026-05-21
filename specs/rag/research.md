# Phase 0 Research — Advanced RAG Pipeline

The decisions below resolve every architectural unknown in
[`plan.md`](./plan.md) that doesn't already have a fixed answer. The
items the operator explicitly deferred (embedding model, cross-encoder,
generation judge) are listed in **R8** as open and queued for after the
naive baseline numbers land.

## R1 — Sparse index: Postgres `tsvector` + GIN over `chunk.content`

**Decision**: Add a `content_tsv tsvector` generated column to
`rag_chunks` and a GIN index over it. First-stage retrieval's sparse
score is `ts_rank_cd(content_tsv, plainto_tsquery('english', :query))`,
combined with the dense cosine score via a single tunable weight α.

**Rationale**: Postgres' built-in full-text search is in-process with
pgvector (one SQL query — no second roundtrip, no second consistency
boundary). `ts_rank_cd` is documented and battle-tested. Short
(≈400-char) child chunks make the BM25-vs-tsvector distinction
marginal in our regime — both index term frequencies; the
length-normalization gap that motivates BM25 over tsvector at
document scale is small at chunk scale.

**Alternatives considered**:
- External BM25 (e.g. `rank_bm25`, OpenSearch, Tantivy). Rejected: a
  second store to seed, snapshot, and verify in CI; another
  dependency in the eval gate's bring-up window.
- pgvector-only (drop sparse). Rejected by spec FR-014 (hybrid is the
  requirement) and by RAG literature on the dense-only weakness at
  exact-term queries.

**Trigger to revisit**: if hybrid α tuning settles at α=1.0 (sparse
contributes nothing) AND a numbers-based reason emerges for a
better sparse backend, switch to `rank_bm25` first (in-process Python,
small dep). User input #3 names this as the explicit default.

## R2 — Parent-chunk aggregation: max child score

**Decision**: The cross-encoder scores all 30 child chunks from
stage 1. For each unique `parent_id` in those 30, the parent's
score is the **maximum** of its children's scores. Return the top 5
parents by aggregated score, with the parent's content (≈2000
chars) as the chunk surfaced to the caller.

**Rationale** (per user input #4):
- **Max preserves the "one strong child" signal.** A maintainer's
  question typically pivots on one passage; that passage's score
  shouldn't be diluted by unrelated siblings under the same parent
  heading.
- **Reranker outputs aren't probabilities.** A cross-encoder produces
  unnormalized relevance scores; summing across children biases
  toward parents with more children (i.e. longer documents),
  introducing a length artifact orthogonal to relevance. Mean
  partially corrects for that but throws away the "one strong child"
  signal.

**Alternatives considered**:
- **Sum**. Rejected: length bias.
- **Mean**. Rejected: dilutes a single high-relevance child by
  averaging in unrelated siblings; empirically known to underperform
  max on multi-passage parents.
- **k-th-best** (e.g. second-best child) as a robustness check.
  Possible follow-up but adds a tunable for marginal gain.

**Defended in DECISIONS.md** under "RAG parent-chunk aggregation"
with the alternatives explicitly compared.

## R3 — Embedding + cross-encoder hosting: in the existing model server

**Decision**: Both the embedding model and the cross-encoder load
inside `model_server` at boot, alongside DistilBERT. The api calls
`http://model-server:8001/embed` and `http://model-server:8001/rerank`
over the existing httpx transport from `app/infra/model_server_client.py`.

**Rationale** (per user input #1 — extends the DistilBERT isolation
pattern):
- Same dep surface (torch + transformers + sentence-transformers, all
  already pinned in the `ml` group).
- Same operational pattern: boot check, refuse-to-boot on load
  failure, single Docker image for ML deps.
- One observable trace per request: api → model-server → return.
  Adding a second model-host container would double the
  cross-service hop count for no isolation benefit.
- The `/classify`, `/ner`, `/summarize` endpoints continue serving
  unchanged; the boot-check additions are additive (two new fatal
  failure modes, no existing path altered).

**Alternatives considered**:
- A separate `embedding-server` container. Rejected: extra
  compose service, second image to build/pin/seed in CI, no
  isolation upside.
- Embedding model in-process inside `app/`. Rejected: would import
  torch into the api process and defeat the Day 2 separation that
  keeps the api image small and the ML-deps blast radius contained.

## R4 — Two model-server calls per `/retrieve` request, Rule-11 typed errors

**Decision**: A `/retrieve` request makes exactly two HTTP calls to
the model server:

  1. `POST /embed` with `{ "text": "<HyDE-transformed query>" }` →
     `{ "embedding": [...float...] }`.
  2. `POST /rerank` with `{ "query": "<original question>",
     "candidates": [{ "id": "<chunk_id>", "text": "<child_content>"
     }, ...30...] }` → `{ "scores": [{ "id": ..., "score": ... },
     ...] }`.

Both calls reuse the existing `app.infra.model_server_client` typed
exception family. The `app/services/retrieve_service.py` orchestrates
the sequence; failures map to HTTP statuses (per user input #2,
Rule 11):

| Failure                            | `/retrieve` status |
|------------------------------------|--------------------|
| `ModelServerUnreachableError`      | 503                |
| `ModelServerTimeoutError`          | 504                |
| `ModelServerInternalError`         | 502                |
| `ModelServerInvalidInputError`     | 502                |
| `ModelServerError` (other / shape) | 502                |
| `httpx.NetworkError` (uncaught)    | 503                |

A 500 is never returned. Tests are the
`tests/services/test_classifier_service.py` family transplanted.

**Alternatives considered**:
- Single batched call (`/embed_and_rerank`). Rejected: couples two
  independent model lifecycles into one API surface and ties future
  embedding-only callers (e.g. corpus build) to a wrapper they don't
  need.
- gRPC between api and model-server. Rejected: would require a new
  protobuf surface, new tracing wiring, and gains nothing over httpx
  for two calls per request.

## R5 — Corpus build location: `scripts/rag/`, offline only

**Decision**: The corpus build is a standalone CLI under
`scripts/rag/build_corpus.py` orchestrating
`scripts/rag/fetch_docs.py` + `scripts/rag/fetch_issues_held_out.py`
+ `scripts/rag/chunk_parent_document.py` +
`scripts/rag/embed_and_upsert.py`. The api at boot does NOT import
any of these modules.

**Rationale**: Mirrors the Day 1 `scripts/dataset/` pattern. Keeps
network fetches, parsing, and a long-running embed pass out of the
api's startup path (Rule 4: refuse-to-boot must be cheap and
deterministic — a corpus build is neither). Per-run `corpus_run_id`
versioning matches the existing pandas dataset / DistilBERT artifact
conventions.

**Alternatives considered**:
- Bake the build into the api lifespan ("on first start, populate
  if empty"). Rejected: first-boot would block for tens of minutes
  on GraphQL fetches; tests would race; refuse-to-boot would never
  fire because the system would self-heal. Day-1's hard line "the
  api at boot does not produce its own data" wins.

## R6 — CI corpus seeding: GitHub release, same pattern as the classifier artifact

**Decision**: Run the corpus build once locally, publish the resulting
chunks + report as a GitHub Release (tag
`rag-corpus-v1-{corpus_run_id}`), and have CI download + seed the
`rag_chunks` table from that release via a new
`scripts/ci/seed_rag_corpus.sh` modeled directly on the existing
`scripts/ci/seed_classifier_artifact.sh`.

**Rationale**: A live GraphQL fetch in CI would burn the GitHub PAT's
budget on every push, take ≥10 min, and make CI non-reproducible
(GitHub state drifts). Seeding from a release pins the corpus to a
specific snapshot; reproducibility is a property of the snapshot,
not of GitHub at run time.

**Smoke test surface**: A small fixture (5 docs + 5 issues, committed
under `tests/fixtures/rag_corpus_smoke/`) is built by the corpus
pipeline on every CI push so the build PATH is exercised even though
the full corpus comes from the release. The smoke fixture's expected
chunk count is asserted.

**Alternatives considered**:
- Build live in CI. Rejected — see above.
- Skip CI seeding and let CI's `/retrieve` test fall back to "model
  server unreachable, gate fails". Rejected: the eval gate is the
  whole point of this slice; failing it for infrastructure reasons
  is gate noise, not signal.

## R7 — Chunk IDs are deterministic content hashes

**Decision**: `chunk_id` = SHA-256 of `(corpus_run_id, source_type,
source_id, section_path, child_index, child_content)` truncated to a
ULID-shaped 26 chars. `parent_id` = same recipe with parent's
`(section_path, parent_index, parent_content)`.

**Rationale**: FR-013 (reproducible) plus FR-021 (golden set rows
reference `ground_truth_chunk_ids` — those IDs must survive a corpus
re-run if the underlying content didn't change). Deterministic hash
gives both: same input → same ID; any content drift → different ID
→ eval gate surfaces a mismatch immediately.

**Alternatives considered**:
- Auto-increment integer PK. Rejected: re-running the build would
  reshuffle IDs and break the golden set's references.
- UUIDv4 random. Rejected: same.

## R8 — Operator-deferred decisions (stop and ask after baseline)

Per user input #6, three choices are explicit "stop and ask" gates,
not Phase-0 unknowns. They surface to the operator after the corpus
build is wired and the naive baseline has run:

- **Embedding model**. Candidates: `BAAI/bge-small-en-v1.5` (384d) —
  MIT, fast on CPU, strong on retrieval benchmarks; or
  `intfloat/e5-small-v2` (384d) — also strong, slightly different
  prompt convention. The pgvector column dimension is committed at
  migration time based on this choice.
- **Cross-encoder**. Default candidate:
  `cross-encoder/ms-marco-MiniLM-L-6-v2` (per spec). The
  cross-encoder runs locally inside `model_server`; refusal to load
  is a refuse-to-boot.
- **Generation judge**. RAGAS (no per-question Anthropic spend, but a
  bigger dep tree and its own opinionated metrics) vs. a frozen
  Claude Haiku judge over a committed `prompts/rag_judge.md`. The
  Claude judge reuses the existing Anthropic key from Vault; RAGAS
  needs no new secret.

The naive baseline reveals **whether HyDE, hybrid weighting,
parent-document chunking, and cross-encoder rerank each pull their
weight** in our regime. The model and judge picks are conditioned
on that data — if e.g. dense-only retrieval already saturates the
golden set, picking a more expensive cross-encoder is waste.

## R9 — Hybrid α tuning, not hand-picking

**Decision**: Sweep α ∈ {0.0, 0.1, 0.2, ..., 1.0} on the 25-example
golden set. Pick the α that maximizes the **headline retrieval metric
chosen for the gate** (`recall@5` is the natural default given the
eval gate floors at recall@5 and MRR — pick the one with the higher
floor in the threshold file). Record the sweep table + the winning
α in `DECISIONS.md` under "RAG hybrid α".

**Rationale**: FR-014 — "chosen by sweep on the golden set, not
hand-picked". 11-point sweep is granular enough for the inflection
to be visible without overfitting to the 25-example sample.

## R10 — HyDE prompt and fallback

**Decision**: New committed prompt at `prompts/hyde.md` (system + user
template; the same two-section format the summarizer prompt uses).
Generation via Claude Haiku (already wired). On a generation that
produces text below 30 characters (configurable `HYDE_MIN_LENGTH`),
or that errors out, fall back to embedding the raw question. The
fallback emits an `attributes.rag.hyde.fallback = true` span attribute
so the eval gate can count fallback frequency.

**Rationale**: FR-017. Claude Haiku occasionally refuses ambiguous
questions; we should not lose the request to a "I cannot answer
that" 30-token response embedded as a query vector.

## R11 — Reranker batching

**Decision**: One `POST /rerank` call per `/retrieve` request, with
all 30 candidates in the body. The model server's `/rerank` handler
batches them through the cross-encoder in a single forward pass.

**Rationale**: 30 pairs comfortably fit in a single forward pass on
CPU; one HTTP round trip is fewer hops to instrument and fewer
network bytes than 30 single-pair calls.

## R12 — Embedding dimension committed at migration time

**Decision**: The `0002_rag_chunks.py` migration commits to
**`vector(384)`** if the operator picks `bge-small-en-v1.5` or
`e5-small-v2` (both 384d). If the operator chooses a different model
later, a new migration replaces the column type — this is normal
under Rule 3.

**Rationale**: pgvector requires a fixed dimension at column-creation
time. We accept that "embedding model swap = migration" because (a)
swaps are infrequent, (b) the existing alembic flow makes it
mechanical, and (c) the alternative (storing dimensions as an array
and inferring at query time) loses pgvector's IVFFlat index.
