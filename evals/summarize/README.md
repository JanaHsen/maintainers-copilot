# Summarize eval — golden set and rubric scoring

This directory holds the curated golden set + the eval harness that
scores the `POST /summarize` endpoint against a rubric judge. The eval
is enforced in CI (see `eval_thresholds.yaml`'s
`summarize.rubric_floor`).

## Files

- `golden.jsonl` — 10 hand-curated examples. Each line is JSON of the
  shape:

  ```
  {"id": "s01", "text": "<issue body>", "reference_summary": "<one-paragraph reference>"}
  ```

  The `reference_summary` is a human-written baseline summary kept for
  operator review; the rubric judge scores the candidate summary
  against the `text` (the rubric is anchored on faithfulness to the
  source, not lexical overlap with the reference).

- `eval_summarize.py` — the harness. For each example, runs the live
  `summarize_service` to produce a candidate summary, then asks the
  rubric judge (`prompts/summarize_judge.md`, frozen Claude Haiku) to
  score it on `faithfulness`, `conciseness`, `intent` (1-5). Computes
  the per-example overall as the mean of the three dimensions, and
  the aggregate as the mean of the per-example overalls. Writes a
  JSON report to `--out`.

  CLI shape mirrors `evals/rag/eval_rag.py`:

  ```
  python -m evals.summarize.eval_summarize --mode={fixture,real} --out=evals/reports/{ts}/summarize.json
  ```

- `fixture_outputs.jsonl` — used by `--mode=fixture` so CI does NOT
  burn Anthropic credits. The fixture is keyed by `id` and contains
  both the candidate summary AND the rubric scores per example. If
  this file is missing when `--mode=fixture` runs, the harness seeds
  it from `golden.jsonl` (candidate = `reference_summary`, scores =
  perfect 5/5/5) so CI stays green deterministically. Operators
  regenerate the fixture from a real run with `--mode=real
  --emit-fixture=evals/summarize/fixture_outputs.jsonl`.

## Selection logic

The 10 examples cover the four intent types the model is asked to
identify (bug report / feature request / documentation / question)
across the issue surface of an OSS data-science project (the
pandas-style corpus). Each is short enough that the judge's
`faithfulness` rating is unambiguous if the model fabricates.

| id  | Intent type      |
|-----|------------------|
| s01 | Bug report       |
| s02 | Feature request  |
| s03 | Documentation    |
| s04 | Question         |
| s05 | Regression       |
| s06 | Bug report       |
| s07 | Bug + docs Q     |
| s08 | Feature request  |
| s09 | Question         |
| s10 | Bug report       |

## Model + prompt pins

| Pin                  | Value                                            |
|----------------------|--------------------------------------------------|
| Summarizer model     | `claude-haiku-4-5-20251001` (default; R8)        |
| Summarizer prompt    | `prompts/summarizer.md` (existing)               |
| Judge model          | `claude-haiku-4-5-20251001` (frozen; R8)         |
| Judge prompt         | `prompts/summarize_judge.md`                     |
| Judge prompt version | `summarize-judge-2026-05-22-001` (header line 1) |
| Scoring              | mean of (faithfulness, conciseness, intent) 1-5  |

When the judge prompt or model is changed, bump the prompt version
header and rerun the eval. The harness records both pins in the
output report's `pipeline_config` block.
