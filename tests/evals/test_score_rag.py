"""Tests for the RAG retrieval-metric helpers (T028).

Pure helpers, no I/O — these tests drive hand-rolled predictions +
golden sets and check the math against expected values."""

from __future__ import annotations

import math

import pytest

from evals.rag.score import mrr, ndcg, recall_at_k


def test_recall_at_k_perfect_hit_at_top() -> None:
    # one golden id, retrieved first → recall@1 = 1.0
    assert recall_at_k([["a", "b", "c"]], [{"a"}], k=1) == 1.0


def test_recall_at_k_partial_coverage_of_golden_set() -> None:
    # two golden ids, top-3 contains one of them → recall = 1/2 = 0.5
    pred = [["x", "a", "y"]]
    gold = [{"a", "b"}]
    assert recall_at_k(pred, gold, k=3) == 0.5


def test_recall_at_k_macro_average_skips_empty_golden() -> None:
    # q1: recall = 1/1 = 1.0; q2: empty golden, skipped; q3: recall = 0
    pred = [["a"], ["x"], ["z", "y"]]
    gold = [{"a"}, set(), {"q"}]
    # macro avg over q1 + q3 = (1.0 + 0.0) / 2 = 0.5
    assert recall_at_k(pred, gold, k=2) == 0.5


def test_recall_at_k_k_caps_prediction_window() -> None:
    pred = [["x", "y", "a"]]
    gold = [{"a"}]
    # k=2 doesn't include "a"; recall = 0
    assert recall_at_k(pred, gold, k=2) == 0.0
    # k=3 does → recall = 1
    assert recall_at_k(pred, gold, k=3) == 1.0


def test_recall_at_k_rejects_invalid_inputs() -> None:
    with pytest.raises(ValueError):
        recall_at_k([], [], k=5)
    with pytest.raises(ValueError):
        recall_at_k([["a"]], [{"a"}], k=0)
    with pytest.raises(ValueError):
        recall_at_k([["a"]], [{"a"}, {"b"}], k=1)


def test_mrr_first_hit_at_position_one() -> None:
    assert mrr([["a", "b", "c"]], [{"a"}]) == 1.0


def test_mrr_first_hit_at_position_three() -> None:
    # first golden hit at rank 3 → reciprocal rank = 1/3
    assert mrr([["x", "y", "a"]], [{"a"}]) == pytest.approx(1.0 / 3.0)


def test_mrr_no_hit_returns_zero_for_that_question() -> None:
    pred = [["x", "y", "z"]]
    gold = [{"a"}]
    assert mrr(pred, gold) == 0.0


def test_mrr_macro_average_across_questions() -> None:
    # q1 RR = 1/1, q2 RR = 1/2, q3 RR = 0 → mean = 0.5
    pred = [["a"], ["x", "b"], ["m", "n", "p"]]
    gold = [{"a"}, {"b"}, {"q"}]
    assert mrr(pred, gold) == pytest.approx((1.0 + 0.5 + 0.0) / 3.0)


def test_mrr_respects_k_cutoff() -> None:
    # hit at rank 11 → MRR@10 = 0; MRR (unbounded) = 1/11
    pred = [list("0123456789") + ["a"]]
    gold = [{"a"}]
    assert mrr(pred, gold, k=10) == 0.0
    assert mrr(pred, gold) == pytest.approx(1.0 / 11.0)


def test_ndcg_perfect_ranking_is_one() -> None:
    # golden = {a, b}, predicted top-2 = [a, b] → DCG = 1 + 1/log2(3),
    # IDCG = same → nDCG = 1.0
    pred = [["a", "b", "c"]]
    gold = [{"a", "b"}]
    assert ndcg(pred, gold, k=2) == pytest.approx(1.0)


def test_ndcg_relevant_at_rank_two_only() -> None:
    # golden = {b}, predicted = [a, b, c] → DCG = 1/log2(3), IDCG = 1/log2(2) = 1
    pred = [["a", "b", "c"]]
    gold = [{"b"}]
    expected = (1.0 / math.log2(3)) / 1.0
    assert ndcg(pred, gold, k=3) == pytest.approx(expected)


def test_ndcg_zero_when_nothing_in_top_k() -> None:
    pred = [["x", "y", "z"]]
    gold = [{"a"}]
    assert ndcg(pred, gold, k=3) == 0.0


def test_ndcg_macro_average_skips_empty_golden() -> None:
    pred = [["a", "b"], ["x"], ["m", "n"]]
    gold = [{"a"}, set(), {"n"}]
    # q1: DCG = 1.0, IDCG = 1.0 → 1.0
    # q3: DCG = 1/log2(3), IDCG = 1/log2(2) = 1 → 1/log2(3)
    q1 = 1.0
    q3 = 1.0 / math.log2(3)
    assert ndcg(pred, gold, k=2) == pytest.approx((q1 + q3) / 2.0)


def test_ndcg_rejects_invalid_inputs() -> None:
    with pytest.raises(ValueError):
        ndcg([], [], k=5)
    with pytest.raises(ValueError):
        ndcg([["a"]], [{"a"}], k=0)
