# RAG eval — golden set + selection logic

This directory holds the hand-curated golden set + the eval harness
that drives the CI gate for `POST /retrieve`. Schema for each row of
`golden.jsonl` is documented in
[`specs/rag/data-model.md`](../../specs/rag/data-model.md#evalsraggoldenjsonl);
the broader contract is FR-021 / FR-022 in
[`specs/rag/spec.md`](../../specs/rag/spec.md).

## Selection logic — `golden.jsonl`

Exactly **25 rows**: **20 implementer-drafted** (`operator_labeled:
false`) + **5 operator-labeled placeholders** (`operator_labeled:
true`). The 5 placeholder question ids — matching the canonical
example in [`data-model.md`](../../specs/rag/data-model.md#evalsreportsrun_tsragjson)
under `operator_judge_agreement.labeled_question_ids` — are:

| placeholder id | topic | T027 source pointer |
|----------------|-------|---------------------|
| `q03`          | `SettingWithCopyWarning` — meaning + fix              | `docs:doc/source/user_guide/indexing.rst` (view-vs-copy section) |
| `q07`          | tz-naive + tz-aware merge — conversion path           | `docs:doc/source/user_guide/timeseries.rst` (time-zone handling) |
| `q12`          | `df.append` removal — recommended replacement         | `docs:doc/source/whatsnew/*` + `docs:doc/source/user_guide/merging.rst` |
| `q19`          | `set_index` perf on large DataFrames — workaround     | maintainer replies on issues (`issues:26064`, `issues:26182`, `issues:11056`) |
| `q24`          | multi-sheet Excel read + merge on a common key        | `docs:doc/source/user_guide/io.rst` (Excel section) |

For the 5 placeholders, `ground_truth_chunk_ids` is `[]` in T026; the
agent fills it in T027 (per instructor approval recorded in Slack:
agent does the hand-labels instead of the human operator).

### How the 20 implementer-drafted rows were curated

The questions were drafted to span the surface area the
maintainer-copilot will be asked about, weighting toward topics
present in the pandas docs + held-out resolved issues corpus
(`corpus_run_id=v1-full-20260521T2327Z`):

- **Selection coverage** — groupby, indexing (`.loc` / `.iloc` /
  `SettingWithCopyWarning`), time-series (resample, rolling), I/O
  (`read_csv`, Excel), reshaping (pivot, MultiIndex), dtype semantics
  (categorical, nullable Int64, string), iteration, sorting,
  duplicates, plotting, dev workflow (`CONTRIBUTING`). Each
  question maps to at least one parent chunk in the corpus.
- **Mix of source types** — questions whose ideal answer lives in
  the docs (e.g. `q22` on `iterrows`) vs questions whose canonical
  answer is a maintainer reply on an issue (e.g. `q18` on
  `read_csv` int → float upcast). The 20-row set is roughly
  60/40 docs/issues by retrieval target, mirroring the rough
  parent-count ratio in the corpus.
- **Ground-truth resolution** — for each draft question, the agent
  ran `POST /retrieve` (k=10) against the live api, inspected the
  candidate child snippets, and recorded the 1–3 parent chunk ids
  whose content genuinely contained the answer. The parent ids
  were resolved from the retrieved child ids via the
  `rag_chunks.parent_id` foreign key (the MVP retrieve service
  returns child ids; FR-021's "list of parent chunk ids" is
  enforced by translating child → parent before commit). The
  one-shot scratch helper that drove `/retrieve` for each draft
  question lived at `tests/manual/draft_golden.py` during the
  curation pass; not committed.
- **Bound on parent count per question** — between 1 and 3
  parent ids per row. Two questions (`q08`, `q09`) had only one
  high-confidence docs hit and stay at one parent; the rest carry
  2–3 parents that each cover a complementary facet of the
  answer.
- **De-duplication** — `q22` (iteration) and `q25` (duplicates)
  surfaced multiple children that all map to the same parent;
  the de-duplicated parent set is what lands in the row.

### Mapping to FR-021 / FR-022

- **FR-021** "exactly 25 examples; each row `{ question,
  ideal_answer, ground_truth_chunk_ids }`": ✅ — the JSONL has 25
  lines; each row also carries `question_id`, `operator_labeled`,
  and `notes` (extra fields are allowed and used by the eval
  harness).
- **FR-022** "five operator-labeled, agreement computed and
  reported": the 5 placeholders are reserved for T027; agreement
  between the operator-style labels (agent-produced in T027) and
  the automated judge (T036) is computed by the harness on every
  CI run and lands under `operator_judge_agreement.value` in the
  report.

## Operator approval log

- **2026-05-22** — 20/20 implementer-drafted golden questions
  drafted; **operator approval pending** (operator is offline; per
  the chain-level instruction "stop asking questions for approval,
  do everything", the agent commits the draft now and the operator
  reviews at the next session). The operator can flip any row by
  editing `golden.jsonl` directly — there is no implicit lock on
  the file.

## Files

- `golden.jsonl` — the 25-row golden set (this file).
- `eval_rag.py` — the eval harness invoked by CI (modes:
  `naive` / `advanced`; writes `evals/reports/{run_ts}/rag.json`).
- `score.py` — pure retrieval-metric helpers (`recall_at_k`,
  `mrr`, `ndcg`) — lands in T028.
- `baseline.json` — frozen naive-baseline numbers — lands in T030.
