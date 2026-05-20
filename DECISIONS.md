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

## Training data integrity hash  — hash value SUPERSEDED (v1.2.0)

> **SUPERSEDED hash value.** The mechanism still applies; the specific hash
> below is for the pandas train split and will change with the scikit-learn
> regeneration. Retained for audit.

`splits_report.json` includes `training_data_sha256`, a SHA-256 over the
canonical JSON serialization of the train split (rows sorted by issue
number; only `issue_number`, `title`, `body`, `target_class` included to
make the hash invariant to incidental metadata changes). Day 2's
`model_card.json` references this hash so the api can refuse to boot
when the classifier weights were trained against a different dataset
than the one currently in MinIO (Rule 4 weights-integrity for training
data).

**Hash:** `a69163846b9d51502416c574e6ab4d77031ca1ca547d00ed095831d5b3c22294`.

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