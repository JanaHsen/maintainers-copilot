"""RAG eval suite — STUB (Day 3 fills it).

Correct shape now so Day 3 is a fill-in, not a rebuild. The RAG pipeline +
golden set do not exist on Day 1, so this is not enforced (Rule 5/10
scoped deferral — see DECISIONS.md).
"""

from __future__ import annotations

import os

import yaml

THRESHOLDS_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "eval_thresholds.yaml"
)


def load_thresholds() -> dict[str, object]:
    with open(THRESHOLDS_PATH, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def run_eval() -> dict[str, object]:
    """Day 3: run the RAG golden set, return relevance / precision / latency.

    Day 1: returns a not-enforced placeholder so CI stays green-and-honest.
    """
    cfg = load_thresholds()
    return {
        "suite": "rag",
        "enforced": bool(cfg.get("enforced", False)),
        "status": "stub",
    }


if __name__ == "__main__":
    print(run_eval())
