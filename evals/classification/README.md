# Classification eval

This directory carries the golden set and the gate runner used by CI to
verify that the deployed DistilBERT classifier hasn't regressed below
the macro-F1 floor in `eval_thresholds.yaml`.

## Golden set: `golden.jsonl`

25 examples sampled from the **val split** of the canonical pandas run
`20260519T133455Z` at
`s3://maintainers-copilot/processed/pandas/20260519T133455Z/val.parquet`.

The val split was chosen deliberately rather than the test split: the
test split is reserved for headline metrics reporting and the DistilBERT
vs Claude Haiku comparison in `DECISIONS.md`. Gating against the same
data we report against would let the gate pass on hand-picked numbers;
the val split is held out from headline reporting so a regression that
shows up here is independent evidence.

### Class counts (stratified)

| Class      | N |
|------------|---|
| `bug`      | 6 |
| `docs`     | 6 |
| `feature`  | 6 |
| `question` | 7 |

The `+1` on `question` reflects that it is the weakest class on the
test split (F1 0.4503) — extra gating signal where the model has the
most room to drift.

### Within-class selection

For each class, the first N rows sorted by `issue_number` ascending.
This is deterministic, reproducible, and explicitly not cherry-picked.
The runner `scripts/eval/build_classification_golden.py` regenerates
`golden.jsonl` from the live val.parquet when needed.

### Per-row schema

```jsonc
{
  "issue_number":      1234,                              // val.parquet primary key
  "title":             "BUG: ...",                        // verbatim
  "body":              "Reproducer: ...",                 // verbatim
  "true_class":        "bug",                             // {bug, docs, feature, question}
  "source":            "val_split",
  "selection_reason":  "first_6_of_class_bug_by_issue_number"
}
```

## Gate runner: `eval_classification.py`

Runs the deployed DistilBERT live against every row in `golden.jsonl`
by calling the model server's `/classify` endpoint, computes macro-F1
+ per-class F1 + the confusion matrix, writes a JSON report to MinIO
at `evals/reports/{ts}/classification.json`, and exits non-zero if
macro-F1 is below the floor in `eval_thresholds.yaml`. CI invokes
this on every push after the compose stack is healthy.

The threshold is **macro-F1 only at 0.65**. Per-class F1 and the
confusion matrix are still **computed and written to the report** —
they're what an operator looks at to diagnose a regression — but they
aren't gated. With 6-7 examples per class, a single misclassification
swings per-class F1 by ~15-17 points; gating on that would be gating
on noise.
