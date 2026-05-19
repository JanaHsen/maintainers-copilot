# Decisions

Every materially-architectural choice, one-line-justified and backed by
numbers where applicable (Rule 6). Counts cite `splits_report.json` /
`observed_labels.txt` once the live dataset pipeline has run.

## Label mapping (pandas labels → {bug, feature, docs, question})

`scripts/dataset/label_map.yaml` maps pandas's real labels to four classes
with precedence `[bug, feature, docs, question]` for multi-label issues and
`drop_if_unmapped: true` so unmappable issues are excluded rather than
forced into a class (keeps the supervision signal trustworthy).

- `bug` ← `Bug`, `Regression`
- `feature` ← `Enhancement`, `Performance`, `API Design`
- `docs` ← `Docs`
- `question` ← `Usage Question`, `Needs Info`

Rationale: these are pandas's highest-signal, human-applied issue labels;
dropping unmappable issues avoids polluting classes (rejected alternative:
mapping leftovers to `question` as a catch-all).

> **Pending numeric grounding (Rule 6):** the per-label frequency counts
> backing this mapping are produced by `inventory_labels.py`
> (`observed_labels.txt`) on the first live `fetch_issues.py` run. This entry
> will be updated with the exact counts and any label-list adjustments once
> that run completes (it was blocked on operator GitHub-PAT provisioning, not
> on code).

## Dataset source: `pandas-dev/pandas` closed issues

Bound by the project scope; closed issues carry settled, human-applied
labels — the supervision signal for the 4-class task. Fetched via GitHub
REST (simpler/auditable pagination + rate limits than GraphQL), PAT read
from Vault, never `.env` (Rule 2). See `research.md` R2.

## Train / val / test split sizes

Stratified by class, then strict time order: test = most recent ~15%,
remaining 85% → train/val (~70/15 overall); ties at the boundary go to
test so `test_min_closed_at > train_val_max_closed_at` (FR-016/SC-006).

> **Pending numbers (Rule 6):** exact per-split per-class counts come from
> `processed/pandas/{run_id}/splits_report.json` and will be quoted here
> after the first live `build_splits.py` run (blocked on the dataset fetch,
> not on code). Split logic is verified against synthetic data (contracts
> C2–C5: only the four classes, counts sum to `total_mapped`, strict time
> boundary).

## Tracing backend: Phoenix (Arize)

Local OpenTelemetry → OTLP → Phoenix container: no external account/secret
(keeps the Rule 2 surface minimal), ships a usable trace UI, natively
models LLM spans for Days 3–4 without a backend swap. Wired from the first
commit so it is never retrofitted (Rule 7). Alternatives (Jaeger, Tempo,
hosted) rejected — see `research.md` R1.

## Rule 5 / Rule 10 scoped deferral (Day 1)

Golden sets and the trained classifier do not exist until Days 2–3, so
enforced eval gates would be a perpetually-red CI. Day 1 ships
correctly-shaped eval stubs + `eval_thresholds.yaml` with placeholder
values **not enforced**, and a CI that enforces what is enforceable now
(ruff, mypy, secret-grep, redaction test, image build, `/health` smoke).
This is a documented scoped deferral, not an unjustified violation (plan
Complexity Tracking).

## CI: first green run on `foundations`

`.github/workflows/ci.yml` is green on the `foundations` branch — ruff,
mypy `app/`, secret-grep, redaction + refuse-to-boot tests, image build,
and the compose `/health` smoke all pass:

- Run: https://github.com/JanaHsen/maintainers-copilot/actions/runs/26089474565
  (`conclusion=success`).
