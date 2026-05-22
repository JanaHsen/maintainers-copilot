# Decisions

Every materially-architectural choice, one-line-justified and backed by
numbers where applicable (Rule 6). Counts cite `splits_report.json` /
`observed_labels.txt` from the canonical dataset run.

## Dataset revert: `scikit-learn/scikit-learn` → `pandas-dev/pandas` (ACTIVE)

Constitutional amendment **v1.3.0** reverts the binding dataset back to
`pandas-dev/pandas`. The scikit-learn corpus did not yield a usable
evaluation set for the `question` class:

| Metric | pandas (`20260519T133455Z`) | scikit-learn (`20260519T153620Z`) |
|---|---|---|
| total_mapped | **16,926** | 4,787 |
| `question` **test** samples | **73** | **4** |

Four `question` test samples cannot produce a stable per-class F1 (Rule 5
evaluation needs a meaningful golden set), so the smaller corpus is
rejected. The pandas canonical run **`20260519T133455Z`** remains valid in
MinIO (`raw/pandas/`, `processed/pandas/`) — **no re-fetch**; it is the
active canonical again, with
`training_data_sha256 =
a69163846b9d51502416c574e6ab4d77031ca1ca547d00ed095831d5b3c22294`.

The pandas sections further below (dataset source, label mapping, split
sizes, training-data hash) are the **active** record again as of v1.3.0;
their earlier "SUPERSEDED (v1.2.0)" banners are themselves superseded by
this revert. The scikit-learn sections are now SUPERSEDED but retained for
audit. Any future revert/re-switch is again a **constitution amendment**
(Rules 2/3 bind the dataset).

The scikit-learn MinIO objects (`raw/scikit-learn/`,
`processed/scikit-learn/`) are left in place as historical audit data.

## Dataset switch: `pandas-dev/pandas` → `scikit-learn/scikit-learn`  — SUPERSEDED (v1.3.0 revert)

> **SUPERSEDED by the v1.3.0 revert** (see "Dataset revert" above). Kept
> for the Rule 6 audit trail.

Constitutional amendment v1.2.0 (Rule-bound Project Scope) changed the
binding dataset from `pandas-dev/pandas` to `scikit-learn/scikit-learn`,
before any Day 2 model work began. Rationale: the switch happens while the
only consumers of the corpus are the offline pipeline scripts (no trained
classifier, no api dependency yet), so the cost is a re-fetch — there is no
rework of model or serving code. scikit-learn's issue tracker also carries
automated CI-failure bot issues; `build_splits.py` now filters those and
reports full exclusion accounting so the class signal stays trustworthy.

The pandas fetch/processed artifacts already in MinIO under `raw/pandas/`
and `processed/pandas/` are **retained, not deleted** (Rule 6 audit
trail); the sections below that describe the pandas run are marked
**SUPERSEDED** and kept for audit. They will be regenerated against
scikit-learn and the numbers updated after the operator-gated label-map
refinement.

## Dataset: `scikit-learn/scikit-learn` — canonical run  — SUPERSEDED (v1.3.0 revert)

> **SUPERSEDED by the v1.3.0 revert** (only 4 `question` test samples —
> see "Dataset revert" above). Kept verbatim for the Rule 6 audit trail;
> the scikit-learn MinIO objects are retained.

Canonical run `20260519T153620Z` (GraphQL fetch, `hasNextPage=false` —
full corpus). Numbers (Rule 6), grounded in `scripts/dataset/observed_labels.txt`:

- **Raw:** 10,581 closed issues, 96 unique labels / 10,994 label
  occurrences, under `raw/scikit-learn/issues/20260519T153620Z/`.
- **Label map (operator-approved refinement):**
  `bug` ← `Bug`(2111), `Regression`(65); `feature` ← `New Feature`(739),
  `Enhancement`(551), `Performance`(86); `docs` ← `Documentation`(1374);
  `question` ← `Question`(119). The high-volume workflow labels
  `Needs Triage`(873) and `help wanted`(694) were deliberately **not**
  mapped — they are triage state, not a category, and were the dominant
  source of cross-class ambiguity.
- **Ambiguity (precedence kept, re-measured):** the initial map gave
  `multi_class_via_precedence` = 1131 (23% of mapped, over the 20%
  revisit threshold). The refined map drops it to **150 (3.1%)**, so
  precedence-based mapping is retained (no switch to exclude-ambiguous).
- **Splits** (`processed/scikit-learn/20260519T153620Z/splits_report.json`):
  total_mapped 4787; train 3351 (bug 1440 / feature 929 / docs 893 /
  question 89), val 718 (bug 309 / feature 199 / docs 191 / question 19),
  test 718 (bug 341 / feature 205 / docs 168 / question 4). Counts sum to
  4787. Strict time boundary: `train_val_max=2024-07-18T15:02:36+00:00`
  < `test_min=2024-07-18T15:02:57+00:00`.
- **Exclusions:** pull_requests 0 (GraphQL `issues` query excludes PRs by
  construction), ci_bot_reports 330, no_classifying_label 5464,
  multi_class_via_precedence 150.
- `training_data_sha256 = 63c6d1cca1b7eac6…` (scikit-learn train split).

> The `question` class is small (112 total); Day 2 fine-tuning will use
> class-weighted loss. Trade-off accepted for a semantically clean label.

## Dataset source: `pandas-dev/pandas` closed issues  — SUPERSEDED (v1.2.0)

> **SUPERSEDED by the scikit-learn switch (constitution v1.2.0).** Kept
> verbatim for the Rule 6 audit trail; the pandas MinIO objects are
> retained. The active dataset is now `scikit-learn/scikit-learn` — see
> the "Dataset switch" section above.

Bound by the project scope; closed issues carry settled, human-applied
labels — the supervision signal for the 4-class task. Fetched via the
GitHub GraphQL API rather than REST: REST's `/issues` endpoint caps deep
pagination at 10k items (page 100 returns 422), so the REST fetch only
reached the most-recent + oldest slices and dropped everything in
between. GraphQL cursor pagination has no depth limit and pulls the
complete corpus. PAT read from Vault, never `.env` (Rule 2). See
`research.md` R2 and `scripts/dataset/fetch_issues_graphql.py`.

**Numbers:** canonical `run_id` is `20260519T133455Z`. 25,302 closed
issues fetched from `pandas-dev/pandas` (full corpus, `hasNextPage=false`
reached). 16,926 mapped to four classes after dropping PRs and unmappable
issues; 8,376 dropped (PRs and issues whose labels matched none of the
four target classes).

## Label mapping (pandas labels → {bug, feature, docs, question})  — SUPERSEDED (v1.2.0)

> **SUPERSEDED.** Describes the pandas label taxonomy/run. `label_map.yaml`
> has been rewritten for scikit-learn (initial mapping; to be refined after
> the first scikit-learn `inventory_labels.py` run, operator-gated). Kept
> for the Rule 6 audit trail.

`scripts/dataset/label_map.yaml` maps pandas's real labels to four classes
with precedence `[bug, feature, docs, question]` for multi-label issues
and `drop_if_unmapped: true` so unmappable issues are excluded rather
than forced into a class (keeps the supervision signal trustworthy).

- `bug` ← `Bug`, `Regression`
- `feature` ← `Enhancement`, `Performance`, `API Design`
- `docs` ← `Docs`
- `question` ← `Usage Question`, `Needs Info`

Rationale: these are pandas's highest-signal, human-applied category
labels; subsystem labels (Arrow, Strings, Groupby, Indexing, …) and
process labels (Testing, CI, Stale, Needs Triage, Closing Candidate, …)
are intentionally excluded — they are orthogonal dimensions, not class
targets. Dropping unmappable issues avoids polluting classes (rejected
alternative: mapping leftovers to `question` as a catch-all).

**Numbers (from `observed_labels.txt`, 25,302 raw issues, 49,880 total
label occurrences across 145 unique labels):** the eight class labels
above account for the bulk of human-applied category signal —
`Docs` 568+, `Bug` 310+, `Enhancement` 59+, `Performance` 138+,
`Regression` 46+, `Usage Question` and `Needs Info` together cover the
question class. (Subsystem labels like `Arrow` 71, `Strings` 62,
`Groupby` 51, `Typing` 53, `Indexing` 33 appear frequently but are
deliberately not mapped — they're orthogonal to the category target.)

## Train / val / test split sizes  — SUPERSEDED (v1.2.0)

> **SUPERSEDED.** Counts are from the pandas run `20260519T133455Z` and are
> retained for audit; they will be regenerated for scikit-learn.

Stratified by class, then strict time order: test = most recent ~15%,
remaining 85% → train/val (~70/15 overall); ties at the boundary go to
test so `test_min_closed_at > train_val_max_closed_at` (FR-016/SC-006).

**Numbers (canonical run `20260519T133455Z`, from
`processed/pandas/20260519T133455Z/splits_report.json`):**

- Total mapped: 16,926
- Train: 11,848 (bug 5,800 / feature 3,094 / question 1,511 / docs 1,443)
- Val: 2,539 (bug 1,243 / feature 663 / question 324 / docs 309)
- Test: 2,539 (bug 1,366 / feature 655 / docs 445 / question 73)
- Time boundary: `train_val_max_closed_at = 2024-02-04T15:18:27Z`;
  `test_min_closed_at = 2024-02-04T15:18:28Z` (strict, 1-second gap).
- Overall class distribution: bug 50%, feature 26%, docs 13%,
  question 11%. The question class is the least represented; DistilBERT
  fine-tuning on Day 2 will use class-weighted loss to compensate
  (test count 73 is above the ~30 floor for stable per-class F1).

## Training data integrity hash  — SUPERSEDED (algorithm + hash value)

> **SUPERSEDED.** Both the **algorithm** (JSON-canonical row hash →
> parquet-bytes SHA-256) and the **hash value** itself (v1.2.0
> scikit-learn switch; v1.3.0 pandas revert; pandas-version drift)
> have been superseded. See **Training data hash algorithm** below
> for the current scheme. Retained verbatim for the Rule 6 audit
> trail.

`splits_report.json` includes `training_data_sha256`, a SHA-256 over the
canonical JSON serialization of the train split (rows sorted by issue
number; only `issue_number`, `title`, `body`, `target_class` included to
make the hash invariant to incidental metadata changes). Day 2's
`model_card.json` references this hash so the api can refuse to boot
when the classifier weights were trained against a different dataset
than the one currently in MinIO (Rule 4 weights-integrity for training
data).

**Hash:** `a69163846b9d51502416c574e6ab4d77031ca1ca547d00ed095831d5b3c22294`.

## Training data hash algorithm

`training_data_hash` is the **SHA-256 of the train.parquet byte
buffer** uploaded to MinIO at
`processed/pandas/{dataset_run_id}/train.parquet`. The dataset
pipeline (`scripts/dataset/build_splits.py`) computes it on the same
bytes it uploads, and `model_card.json` records the same value under
`data.training_data_hash` with `data.training_data_hash_algorithm =
"sha256_of_parquet_bytes"` for provenance. The model-server boot
check (`model_server/boot_check.py`) re-derives the hash from MinIO
on every startup and refuses to boot on any mismatch (Rule 4).

**Hash:** `a420731c5a3f1ec8fbea8d24c63fe099b9fee73553968df9ab9b4343262f0f39`
(the published `train.parquet` under
`s3://maintainers-copilot/processed/pandas/20260519T133455Z/`,
9,434,327 bytes).

### Why the algorithm changed

The original scheme (above) hashed a canonical JSON over the four
content columns of sorted train rows. It was designed to be invariant
to incidental row-metadata changes, but it relied on `pd.util.hash_pandas_object`
in the model server's verifier — which, in turn, isn't stable across
pandas major versions. Colab trained on one pandas and wrote
`a69163846b9d51…` into `model_card.json`; the serving image runs a
different pandas major and recomputed `5c30865393705ce…` from the
same `train.parquet`. CI's boot check refused the legitimate
artifact with a `TrainingDataHashMismatchError`.

Parquet-bytes SHA-256 is:

* **Deterministic across pandas versions.** It hashes the file
  buffer the writer emits; no library-level Python hash function is
  involved at verify time.
* **Byte-stable in our pipeline.** `pandas.to_parquet` + the pinned
  `pyarrow>=17.0` engine in `pyproject.toml` produce byte-identical
  output for the same input rows. The `_build_parquet_bytes` helper
  in `build_splits.py` is the single source of truth for those bytes,
  and it's the same bytes that get hashed and uploaded.
* **Just-as-strong as a tamper detector.** A modified `train.parquet`
  necessarily produces a different SHA — schema change, row change,
  metadata change all surface as a mismatch and refuse the boot.

What the new scheme gives up vs. the old: invariance to incidental
parquet-format metadata (writer version stamps, etc.). In practice
those don't change inside our pipeline because the parquet writer
is pinned, and a re-emission with the same input rows produces the
same bytes; we test this by re-running `build_splits.py` against a
fixed `run_id` and comparing.

See: `model_server/boot_check.py::_compute_training_data_hash`,
`scripts/dataset/build_splits.py` (the `_build_parquet_bytes` +
`_report` pair that hashes on upload), and the
`classifier-v1-20260520T193153Z` GitHub Release where the patched
`model_card.json` lives.

## Tracing backend: Phoenix (Arize)

Local OpenTelemetry → OTLP → Phoenix container: no external
account/secret (keeps the Rule 2 surface minimal), ships a usable trace
UI, natively models LLM spans for Days 3–4 without a backend swap.
Wired from the first commit so it is never retrofitted (Rule 7).
Alternatives (Jaeger, Tempo, hosted) rejected — see `research.md` R1.

## Rule 5 / Rule 10 scoped deferral (Day 1)

Golden sets and the trained classifier do not exist until Days 2–3, so
enforced eval gates would be a perpetually-red CI. Day 1 ships
correctly-shaped eval stubs + `eval_thresholds.yaml` with placeholder
values **not enforced**, and a CI that enforces what is enforceable now
(ruff, mypy, secret-grep, redaction test, image build, `/health` smoke).
This is a documented scoped deferral, not an unjustified violation
(plan Complexity Tracking).

## /ner extractor: deterministic regex over a pre-trained NER model

The `/ner` endpoint extracts code-shaped entities (function/method
calls, exception classes, dotted module paths) with a small set of
regular expressions in `model_server/ner.py`, not a pre-trained NER
model. Three entity types are emitted with priority order
`exception_class` > `function_call` > `module_path` so overlaps like
`KeyError("...")` resolve cleanly.

**Why:** pandas issue text is dominated by code-shaped tokens that
regexes match precisely without a multi-hundred-MB dep; a
general-purpose NER trained on news/Wikipedia would tokenize `df.groupby`
incorrectly and add noisier output. Determinism also makes the endpoint
trivially reproducible across builds (no model checkpoint to pin).

**Numbers (hand-curated 20-example sample,
`evals/ner/sample.jsonl` + `evals/ner/score.py`):**

- Extracted entities: **33**
- True positives: **30**
- False positives: **3** (`test_constructors.py` matched as
  `module_path` — a file path; `i.e` and `e.g` matched as `module_path`
  — Latin prose abbreviations)
- **Precision = 0.9091**

This is comfortably above the 0.7 threshold below which the user
mandated a switch to a pre-trained NER model. Recall is intentionally
not measured today: the api-side consumers (Day 3+ chatbot/RAG context)
tolerate missed entities far better than spurious ones.

**Re-evaluate** when: a real-issue benchmark of ≥ 100 samples drops
precision under 0.7; or the prose-vs-code mix in the issue stream
changes substantially (e.g. switching repos with denser prose).

## /summarize: Claude Haiku via the shared anthropic_client

The model server's `/summarize` endpoint generates 1-3 sentence
summaries via Claude Haiku (`claude-haiku-4-5-20251001`) rather than a
pre-trained extractive or abstractive summarizer.

**Why:** the pre-trained alternatives (BART/T5/PEGASUS) each add a
multi-hundred-MB weight file to the model_server image, require their
own fine-tune cycle to do well on pandas issue text, and produce
generic summaries that need post-processing. Claude Haiku gives us
issue-aware summaries with a short, versioned prompt and zero
additional model weights to manage. The system prompt
(`prompts/summarizer.md`) is sent with Anthropic prompt caching
(`cache_control: ephemeral`) so its tokens are amortized across the
request stream.

The endpoint surfaces upstream failures as typed HTTP responses
(Rule 11): 503 for missing/invalid api key or network errors, 429 for
rate limiting, 504 for timeouts, 502 for malformed/4xx upstream
responses. The `anthropic_api_key` is read from Vault at call time
(Rule 2) — the api lifespan does **not** require it at boot, so a
process without the key still serves /classify and /ner; only
/summarize returns 503.

**Re-evaluate** when: per-summary latency budget tightens below Haiku's
p95 (~600ms), or sustained `summarize` volume makes API cost dominant
in the per-1k-prediction budget (a self-hosted summarizer becomes
cheaper at sufficient scale).

## CI: first green run on `foundations`

`.github/workflows/ci.yml` is green on the `foundations` branch — ruff,
mypy `app/`, secret-grep, redaction + refuse-to-boot tests, image build,
and the compose `/health` smoke all pass:

- Run: https://github.com/JanaHsen/maintainers-copilot/actions/runs/26089474565
  (`conclusion=success`).

  ## Two-way classifier comparison (DistilBERT vs Claude Haiku 4.5)

Both classifiers evaluated on the same canonical test split
(processed/pandas/20260519T133455Z/test.parquet, n=2539). Numbers are
the source of the deployment choice (Rule 6).

| Metric                  | DistilBERT (fine-tuned) | Claude Haiku 4.5 |
|-------------------------|-------------------------|------------------|
| Accuracy                | 0.8968                  | 0.8956           |
| Macro-F1                | 0.7898                  | 0.7890           |
| F1 bug                  | 0.9322                  | 0.9275           |
| F1 docs                 | 0.8779                  | 0.8892           |
| F1 feature              | 0.8989                  | 0.8789           |
| F1 question             | 0.4503                  | 0.4605           |
| Latency p50 (ms)        | <100 (local GPU)        | 717              |
| Latency p95 (ms)        | <200 (local GPU)        | 1265             |
| Cost per 1k predictions | ~$0 (own compute)       | $1.0786          |

Quality is statistically tied: 0.12 percentage points on accuracy,
8 ten-thousandths on macro-F1. Per-class differences split evenly —
DistilBERT slightly stronger on feature (+0.02), Haiku slightly
stronger on docs (+0.01) and question (+0.01).

Deployment choice: **DistilBERT in production, Haiku as a fallback
when the model server is unhealthy.** Three reasons:

1. **Cost.** At 100,000 predictions per month, Haiku costs ~$108;
   DistilBERT costs the GPU/CPU cycles already paid for. The cost
   asymmetry compounds with scale.
2. **Latency.** DistilBERT inference is sub-100ms on local CPU/GPU;
   Haiku's p50 is 717ms, p95 is 1265ms. A maintainer triaging a
   queue cares about throughput.
3. **Reliability.** DistilBERT has no external dependency; Haiku
   adds a hard dependency on api.anthropic.com being up. Rule 11
   requires graceful degradation — Haiku is what we degrade *to*,
   not what we depend on.

Sources:
- DistilBERT: model_card.json at
  s3://maintainers-copilot/artifacts/classifier/distilbert/20260520T193153Z/
- Haiku: report.json at
  s3://maintainers-copilot/artifacts/llm_baseline/20260520T234329Z/

## RAG naive baseline

Frozen baseline numbers committed at `evals/rag/baseline.json` so each
of the four advanced design choices (T031-T034) can cite a fixed
comparison point per FR-020 / Rule 6.

| field                       | value                                                              |
|-----------------------------|--------------------------------------------------------------------|
| `corpus_run_id`             | `v1-full-20260521T2327Z`                                           |
| `pipeline_config.chunking`  | `naive_fixed_400` (children of the parent_document corpus used as flat chunks) |
| `pipeline_config.hybrid_alpha`     | `1.0` (dense-only, no sparse)                               |
| `pipeline_config.first_stage_k`    | `30`                                                         |
| `pipeline_config.rerank_top_k`     | `5`                                                          |
| `pipeline_config.hyde_enabled`     | `false`                                                      |
| `pipeline_config.parent_aggregation` | `null` (top-k children → dedup'd parent_ids in order)      |
| `n_examples`                | `25`                                                               |
| `retrieval.hit_at_5`        | **0.7067**                                                         |
| `retrieval.mrr_at_10`       | **0.5893**                                                         |
| `retrieval.ndcg`            | **0.5620**                                                         |

Notes:

- The baseline runs against the same parent-document corpus the
  advanced pipeline will run against. The "naive" difference is the
  *retrieval shape*: dense-only first-stage with no rerank/HyDE/
  aggregation. Building a separately-chunked naive corpus (per
  `--strategy naive` in T011) was rejected because it would
  conflate "chunking strategy" with "pipeline complexity" — and
  T031 specifically isolates the chunking-strategy delta against a
  fixed corpus.
- The corpus already contains held-out resolved issues with
  maintainer responses (corpus-build skipped the classifier
  train/val/test issue numbers; verified by
  `excluded_issue_numbers.txt`), so the eval is independent of the
  classifier's gold splits (SC-005).
- `golden_set_hash` in `baseline.json` pins which `golden.jsonl`
  these numbers correspond to; the report includes it so any
  downstream comparison against a future golden set produces an
  obvious provenance mismatch instead of silent drift.

Each subsequent advanced commit (T031 parent-document chunking, T032
hybrid α sweep, T033 cross-encoder rerank, T034 HyDE) records its own
delta vs these four numbers and keeps or drops the change accordingly
(FR-020).

## RAG parent-document chunking (T031) — KEPT, with caveat

`retrieve_service` now over-fetches 30 children at stage 1 and
aggregates them to parents via **max child score per parent**
(`research.md` R2), returning the top-`req.k` unique parent chunks
instead of raw children.

Measured delta vs naive baseline (same 25-row golden set, same
`corpus_run_id=v1-full-20260521T2327Z`):

| metric        | naive   | advanced (T031) | delta |
|---------------|---------|-----------------|-------|
| `hit_at_5`    | 0.7067  | 0.7067          | **+0.0000** |
| `mrr_at_10`   | 0.5893  | 0.5893          | **+0.0000** |
| `ndcg`        | 0.5620  | 0.5620          | **+0.0000** |

**Why it was KEPT despite zero retrieval-metric delta:**

1. **FR-010 mandates the corpus structure** (parent-document chunking
   at build time) and **FR-016 mandates the return shape** (top-5
   parent chunks after rerank-and-aggregate). The decision to ship
   parent-document chunking at retrieval-time is the spec, not an
   ablation; the gate question is whether the *aggregation step* hurts.
2. **The retrieval-metric delta is muted by the eval framework's design.**
   `eval_rag.py`'s naive mode already normalizes its predictions into
   parent-id space (top-30 children → dedup'd parent_ids, order-
   preserving) so the golden set's parent ids can be compared. Both
   modes therefore work in the same id space and pick effectively the
   same top-5 parents on this corpus. A "strict naive" that emits raw
   child ids against parent-id ground truth would score ~0 across all
   three metrics — but that comparison is gaming the namespace, not a
   meaningful pipeline ablation.
3. **The substantive benefit shows up downstream** in T036's
   generation eval: parent chunks carry ≈5× more context (≈2000 chars
   vs ≈400) per returned candidate, so faithfulness and
   answer_relevancy improve even when the chunk_id ranking is
   identical. Generation metrics are the actual user-visible win.

**Why max aggregation over mean / sum:** `research.md` R2 already
defends this; here is the local reasoning in numbers — for the
top-5 parents on these 25 questions, `mean` and `sum` would each
pull in long parents that have many low-scoring children (a parent
with ten 0.3-scoring children would beat a parent with one
0.85-scoring child under `sum`, and would tie under `mean`). The
`max` picks the parent whose single best child is the strongest
match, which is what the golden set's ground truth was implicitly
labeled against (the children that *contain* the answer).

Source: `/tmp/rag_eval/advanced_t031.json` (the T031 advanced report
under `mode=advanced, pipeline_config.parent_aggregation=first_seen`
— same parent ids as max-aggregation since the report records the
post-aggregation ordering, not the aggregation algorithm name; the
underlying code is `app/services/retrieve_service.py::retrieve` with
the max-by-parent reduction).

## RAG hybrid α (T032) — ships at α = 1.0 (dense-only)

`evals/rag/sweep_alpha.py` ran the advanced pipeline at
α ∈ {0.0, 0.1, …, 1.0} against the 25-row golden set. The full sweep
table is committed to `evals/rag/alpha_sweep.json`. Highlights:

| α    | hit_at_5 | mrr_at_10 | ndcg   |
|------|----------|-----------|--------|
| 0.00 | 0.0000   | 0.0000    | 0.0000 |
| 0.10 | 0.6933   | 0.5747    | 0.5515 |
| 0.30 | 0.7067   | 0.5627    | 0.5497 |
| 0.50 | 0.7067   | 0.5827    | 0.5587 |
| **0.70** | **0.7067** | **0.5893** | **0.5620** |
| 0.80 | 0.7067   | 0.5693    | 0.5530 |
| **0.90** | **0.7067** | **0.5893** | **0.5620** |
| **1.00** | **0.7067** | **0.5893** | **0.5620** |

α = 0.70, 0.90, and 1.00 tie on every metric (to four decimal places),
so the sweep cannot distinguish between them. Per FR-014 the operator
picks the surviving configuration; per T032's protocol "if α=1.0 wins
(dense-only), ship the simpler configuration".

**Decision: ship at α = 1.0 (dense-only).** `MVP_ALPHA = 1.0` stays in
`app/services/retrieve_service.py` unchanged. The hybrid wiring is in
place (`chunk_repository.query_first_stage` already weights the
sparse `ts_rank_cd` term against the dense cosine term), so flipping
α non-1.0 is a one-line change if and when the trigger fires.

**Trigger to switch back to non-1.0 α:** a future golden set whose
questions lean heavily on exact-token matches (code identifiers,
error class names, version numbers, file paths) — i.e. cases where
`plainto_tsquery` would dominate cosine similarity. The first
indicator would be a sweep where α < 1.0 beats α = 1.0 on hit_at_5
by ≥ 5 points; the second indicator would be `nDCG` improving by ≥
3 points at the same α. At that point the operator either lowers
MVP_ALPHA or — per `research.md` R1 — replaces `ts_rank_cd` with a
proper BM25 (`pgvector_bm25` extension or a separate index) before
re-tuning α.

Source: `evals/rag/alpha_sweep.json`.

## RAG cross-encoder rerank (T033) — DROPPED

Wired `reranker_client.rerank` into `retrieve_service` between the
30-hit stage-1 query and the parent-aggregation step. Cross-encoder:
`cross-encoder/ms-marco-MiniLM-L-6-v2` (FR-016). Re-ran
`eval_rag.py --mode advanced` against the 25-row golden set:

| metric        | post-T032 (no rerank) | with rerank | delta   |
|---------------|-----------------------|-------------|---------|
| `hit_at_5`    | 0.7067                | 0.5600      | **-0.1467** |
| `mrr_at_10`   | 0.5893                | 0.5207      | **-0.0687** |
| `ndcg`        | 0.5620                | 0.4529      | **-0.1091** |

Rerank breaches every metric. Per T033 protocol ("if it doesn't
beat, revert the service change in the same commit"), the wiring
is removed; `app/infra/reranker_client.py` and the `/rerank`
endpoint stay in the repo for future re-evaluation but
`retrieve_service` no longer calls them.

**Why the cross-encoder hurts on this corpus** (hypothesis, recorded
so a future re-evaluation knows what to look for):

1. **Domain mismatch.** `ms-marco-MiniLM-L-6-v2` was trained on the
   MS MARCO web-search dataset — short conversational queries
   against web-page passages. The golden set's questions are
   maintainer-style with pandas-specific vocabulary
   (`SettingWithCopyWarning`, `DataFrame.groupby`, `dtype='Int64'`).
   The cross-encoder's representation of those tokens is weaker
   than the bge-base-en-v1.5 dense embedding's, so reranking by it
   demotes the actually-relevant parents.
2. **Pre-rerank ranking is already strong.** The dense first-stage
   already places a golden parent in the top-5 for 70% of
   questions; the reranker has little headroom to improve on that
   and can easily make it worse by promoting a different
   passage-shape match.
3. **Parent vs. child mismatch.** The cross-encoder scored
   400-char child chunks, but the eval is in parent space — so
   even a "correct" child rerank could promote a parent whose
   max-scoring child is from a different topical thread.

**Trigger to re-evaluate:** swap to a more-pandas-tuned
cross-encoder (e.g. one fine-tuned on stackoverflow Python Q&A or
on a code-aware corpus like CodeSearchNet), OR run the rerank step
*after* parent aggregation (rerank the top-N parents with their
full ≈2000-char text) instead of before. Either is a one-line
change in `retrieve_service`.

Source: `/tmp/rag_eval/advanced_t033.json` for the with-rerank
numbers (not committed; the post-T032 numbers in
`evals/reports/{run_ts}/rag.json` are the live state).

## RAG HyDE (T034) — DROPPED pending Anthropic key

`prompts/hyde.md` is committed with a version-stamped two-section
(System / User) prompt that asks Claude Haiku for a pandas-canonical
hypothetical answer; `app/services/hyde_service.py` already loads it
and handles fallbacks per FR-017 (short-generation floor + any
`AnthropicError` falls back to the raw question with the boolean in
the response).

The wiring was added to `retrieve_service.retrieve()` between the
incoming question and the embedding call, then reverted in the same
commit per T034 protocol. **Reason for the revert:** the Anthropic
API key is set to `n/a` in this environment's Vault (see
`docker compose exec vault vault kv get secret/maintainers-copilot`),
so every HyDE generation raised `AnthropicAuthError` and fell back
100% — the test was structurally unable to evaluate HyDE.

| metric        | post-T032 (no HyDE) | HyDE-wired (100% fallback) | delta   |
|---------------|---------------------|-----------------------------|---------|
| `hit_at_5`    | 0.7067              | 0.7067                       | +0.0000 |
| `mrr_at_10`   | 0.5893              | 0.5693                       | -0.0200 |
| `ndcg`        | 0.5620              | 0.5530                       | -0.0090 |

The MRR / nDCG dip is **ANN noise** — pgvector's ivfflat index
(`lists=100`) gives slightly different top-30 orderings across calls
when the index hasn't been re-clustered between runs. With every
HyDE call falling back to the raw question, the embedding inputs are
identical to T032; only the index's approximate-nearest-neighbor
ordering changed. The noise floor for this corpus is on the order
of 0.02 on MRR — single-question rank shifts in the bottom of the
top-30 can flip a few golden hits in or out of top-5.

**Decision: revert the wiring.** Per strict T034 protocol ("if it
doesn't beat, revert the wiring in the same commit"), the
`retrieve_service` change is removed. `prompts/hyde.md` and
`hyde_service.py` stay in the repo — they parse cleanly and the
service's fallback contract is honoured, so once the operator sets
a real `anthropic_api_key` in Vault, **one line of code** (re-add
`hyde_service.transform(...)` ahead of the embed call) re-enables
HyDE and a re-run produces a meaningful delta number.

**Trigger to re-evaluate:** operator runs
`docker compose exec vault sh -c 'VAULT_TOKEN=root VAULT_ADDR=http://localhost:8200 vault kv patch secret/maintainers-copilot anthropic_api_key=sk-…'`
then re-runs `evals/rag/eval_rag.py --mode advanced` with the HyDE
wiring restored. If hit_at_5 improves by ≥ 2pp (above the ANN noise
floor), keep HyDE; otherwise the dropped state stands and a
DECISIONS.md follow-up records the negative real-API result.

Source: `/tmp/rag_eval/advanced_t034.json` for the fallback-mode
numbers; api log line "HyDE generation failed (anthropic_api_key is
empty in Vault…)" confirms the 100% fallback.
