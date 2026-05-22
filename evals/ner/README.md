# NER eval — golden set and scoring

This directory holds the curated golden set + the eval harness that
scores the strict-JSON 4-bucket NER endpoint (`POST /ner`) against
ground truth. The eval is enforced in CI (see
`eval_thresholds.yaml`'s `ner.f1_floor`).

## Files

- `golden.jsonl` — 10 hand-curated examples. Each line is JSON of the
  shape:

  ```
  {"id": "n01", "text": "<issue text>", "expected": {"repo_names": [...], "file_paths": [...], "error_types": [...], "package_names": [...]}}
  ```

  The four buckets follow research decision **R7** verbatim. All four
  keys are mandatory in every row, even when empty.

- `eval_ner.py` — the harness. Computes per-bucket micro-F1 + an
  aggregate (union-of-buckets) micro-F1. Writes a JSON report to
  `--out`. CLI shape mirrors `evals/rag/eval_rag.py`:

  ```
  python -m evals.ner.eval_ner --mode={fixture,real} --out=evals/reports/{ts}/ner.json
  ```

- `fixture_outputs.jsonl` — used by `--mode=fixture` so CI does NOT
  burn Anthropic credits. The fixture is keyed by `id` and contains
  the model's predicted buckets for each golden row. If this file is
  missing when `--mode=fixture` runs, the harness regenerates it as a
  perfect-prediction copy of `golden.jsonl`'s `expected` blocks (so
  CI stays green deterministically). The operator may regenerate the
  fixture from a real run by capturing predictions with `--mode=real
  --emit-fixture=evals/ner/fixture_outputs.jsonl`.

- `sample.jsonl` and `score.py` — predecessor (regex-based) NER
  artifacts that pre-date the LLM NER endpoint. Kept for reference;
  not used by the LLM eval gate.

## Selection logic

The 10 examples were curated to maximize bucket coverage on a small
budget:

| id  | Buckets exercised |
|-----|-------------------|
| n01 | All four (repo + paths + error + package) |
| n02 | All four with multiple file paths |
| n03 | Package-only (questions about install paths) |
| n04 | Totally empty — pure prose, no entities |
| n05 | All four with a stack-trace-shaped layout |
| n06 | Repo + path only — feature request |
| n07 | Repo + path + error + package |
| n08 | Paths + multiple error / warning + multiple packages, no repo |
| n09 | Repo + path + error + package |
| n10 | Totally empty — gratitude message |

Two empty-bucket cases (n04, n10) are intentional — the model must
output `{"repo_names": [], "file_paths": [], "error_types": [],
"package_names": []}` for them, not omit keys or invent entities.

## Model + prompt pins

| Pin              | Value                                        |
|------------------|----------------------------------------------|
| Anthropic model  | `claude-sonnet-4-5-20250929` (research R7)   |
| Prompt path      | `prompts/ner.md`                             |
| Prompt version   | `ner-2026-05-22-001` (header line 1)         |
| Scoring          | per-bucket micro-F1 + aggregate micro-F1     |

When the prompt or model is changed, bump the prompt version header
and rerun the eval; the harness records the prompt version in the
output report's `pipeline_config` block.
