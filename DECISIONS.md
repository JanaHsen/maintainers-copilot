# Decisions

Every materially-architectural choice, one-line-justified and backed by
numbers where applicable (Rule 6). Counts cite `splits_report.json` /
`observed_labels.txt` from the canonical dataset run.

## Dataset source: `pandas-dev/pandas` closed issues

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

## Label mapping (pandas labels → {bug, feature, docs, question})

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

## Train / val / test split sizes

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

## Training data integrity hash

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

## CI: first green run on `foundations`

`.github/workflows/ci.yml` is green on the `foundations` branch — ruff,
mypy `app/`, secret-grep, redaction + refuse-to-boot tests, image build,
and the compose `/health` smoke all pass:

- Run: https://github.com/JanaHsen/maintainers-copilot/actions/runs/26089474565
  (`conclusion=success`).