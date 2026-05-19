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
