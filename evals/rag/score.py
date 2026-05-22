"""Pure retrieval-metric helpers for the RAG eval suite.

Inputs are in-memory lists: `predictions` is a list of ordered parent
chunk-id lists (one per golden question, ranked by descending score),
and `golden` is the parallel list of `set[str]` ground-truth parent ids.

The three helpers — `recall_at_k`, `mrr`, `ndcg` — return a single float
(macro-average across questions). Empty golden sets (a question with no
positive labels) are skipped from the average; an empty `predictions` /
`golden` pair raises ValueError so a silently-zero metric never lands in
the report.

No I/O, no network, no DB. The RAGAS-style generation eval lives
separately in `eval_rag.py` (T036) and calls Claude Haiku directly via
`app.infra.anthropic_client`.
"""

from __future__ import annotations

import math
from collections.abc import Sequence


def _check(predictions: Sequence[Sequence[str]], golden: Sequence[set[str]]) -> None:
    if len(predictions) != len(golden):
        raise ValueError(
            f"predictions ({len(predictions)}) and golden ({len(golden)}) "
            "must be the same length"
        )
    if not predictions:
        raise ValueError("empty predictions/golden — nothing to score")


def recall_at_k(
    predictions: Sequence[Sequence[str]],
    golden: Sequence[set[str]],
    k: int,
) -> float:
    """Mean per-question Recall@k.

    For each question with non-empty golden set: count how many of the
    top-k predicted ids are in the golden set, divide by |golden|.
    Macro-average across questions with non-empty golden sets.
    """
    _check(predictions, golden)
    if k <= 0:
        raise ValueError(f"k must be positive, got {k}")
    scores: list[float] = []
    for pred, gt in zip(predictions, golden, strict=True):
        if not gt:
            continue
        top_k = list(pred)[:k]
        hits = sum(1 for cid in top_k if cid in gt)
        scores.append(hits / len(gt))
    if not scores:
        return 0.0
    return sum(scores) / len(scores)


def mrr(
    predictions: Sequence[Sequence[str]],
    golden: Sequence[set[str]],
    k: int | None = None,
) -> float:
    """Mean Reciprocal Rank.

    For each question with non-empty golden set: find the rank (1-indexed)
    of the first predicted id that is in the golden set; the reciprocal
    is 1/rank if found within the first `k` positions, else 0. Macro-average.

    Pass `k=10` for MRR@10; default (None) is unbounded — every predicted
    position counts.
    """
    _check(predictions, golden)
    if k is not None and k <= 0:
        raise ValueError(f"k must be positive, got {k}")
    scores: list[float] = []
    for pred, gt in zip(predictions, golden, strict=True):
        if not gt:
            continue
        limit = len(pred) if k is None else min(k, len(pred))
        rr = 0.0
        for idx in range(limit):
            if pred[idx] in gt:
                rr = 1.0 / (idx + 1)
                break
        scores.append(rr)
    if not scores:
        return 0.0
    return sum(scores) / len(scores)


def ndcg(
    predictions: Sequence[Sequence[str]],
    golden: Sequence[set[str]],
    k: int | None = None,
) -> float:
    """Mean Normalized Discounted Cumulative Gain.

    Binary relevance (in golden = 1, else 0). DCG = sum over rank i of
    rel_i / log2(i + 1) with 1-indexed rank. IDCG is computed against the
    ideal ranking — every golden id appears first. Macro-average across
    questions with non-empty golden sets; `k` (optional) truncates the
    ranking.
    """
    _check(predictions, golden)
    if k is not None and k <= 0:
        raise ValueError(f"k must be positive, got {k}")
    scores: list[float] = []
    for pred, gt in zip(predictions, golden, strict=True):
        if not gt:
            continue
        limit = len(pred) if k is None else min(k, len(pred))
        dcg = 0.0
        for idx in range(limit):
            if pred[idx] in gt:
                dcg += 1.0 / math.log2(idx + 2)
        ideal_n = min(len(gt), limit)
        idcg = sum(1.0 / math.log2(i + 2) for i in range(ideal_n))
        if idcg == 0.0:
            scores.append(0.0)
        else:
            scores.append(dcg / idcg)
    if not scores:
        return 0.0
    return sum(scores) / len(scores)


__all__ = ["recall_at_k", "mrr", "ndcg"]
