"""Unit tests for the pure pieces of the LLM baseline classifier script."""

from __future__ import annotations

from typing import Any

import pytest

from scripts.eval.llm_baseline_classifier import (
    CLASSES,
    Prediction,
    PriceConfig,
    _accuracy,
    _parse_label,
    _per_class_f1,
    _percentile,
    classify_one,
    compute_cost,
    compute_latency,
    compute_metrics,
)


def _pred(true_label: str, pred_label: str | None, *, latency: float = 100.0) -> Prediction:
    return Prediction(
        issue_number=1,
        true_label=true_label,
        pred_label=pred_label,
        raw_text=pred_label or "",
        latency_ms=latency,
        input_tokens=200,
        output_tokens=2,
    )


class TestParseLabel:
    def test_matches_canonical_label(self) -> None:
        assert _parse_label("bug") == "bug"

    def test_lowercases(self) -> None:
        assert _parse_label("BUG") == "bug"

    def test_strips_trailing_punctuation(self) -> None:
        assert _parse_label("docs.") == "docs"

    def test_strips_surrounding_whitespace(self) -> None:
        assert _parse_label("  feature  ") == "feature"

    def test_picks_first_token(self) -> None:
        assert _parse_label("question because the user...") == "question"

    def test_returns_none_on_unknown(self) -> None:
        assert _parse_label("maybe") is None

    def test_returns_none_on_empty(self) -> None:
        assert _parse_label("") is None
        assert _parse_label("   ") is None


class TestAccuracy:
    def test_all_correct(self) -> None:
        preds = [_pred("bug", "bug"), _pred("docs", "docs")]
        assert _accuracy(preds) == 1.0

    def test_half_correct(self) -> None:
        preds = [_pred("bug", "bug"), _pred("docs", "feature")]
        assert _accuracy(preds) == 0.5

    def test_skips_unparseable(self) -> None:
        # Unparseable predictions do not count in the denominator.
        preds = [_pred("bug", "bug"), _pred("docs", None)]
        assert _accuracy(preds) == 1.0


class TestPerClassF1:
    def test_perfect_predictions(self) -> None:
        preds = [_pred(c, c) for c in CLASSES]
        f1 = _per_class_f1(preds)
        assert all(f1[c] == 1.0 for c in CLASSES)

    def test_known_imbalanced(self) -> None:
        # 2 bug predicted right, 1 bug predicted as docs, 1 docs predicted right.
        # bug:   tp=2 fp=0 fn=1  -> P=1.0 R=0.667 F1=0.8
        # docs:  tp=1 fp=1 fn=0  -> P=0.5 R=1.0   F1=0.667
        preds = [
            _pred("bug", "bug"),
            _pred("bug", "bug"),
            _pred("bug", "docs"),
            _pred("docs", "docs"),
        ]
        f1 = _per_class_f1(preds)
        assert f1["bug"] == pytest.approx(0.8)
        assert f1["docs"] == pytest.approx(2 / 3)
        assert f1["feature"] == 0.0
        assert f1["question"] == 0.0


class TestPercentile:
    def test_p50_of_known_sample(self) -> None:
        assert _percentile([10.0, 20.0, 30.0, 40.0, 50.0], 0.50) == 30.0

    def test_p95_of_known_sample(self) -> None:
        assert _percentile([10.0, 20.0, 30.0, 40.0, 50.0], 0.95) == 50.0

    def test_empty_returns_zero(self) -> None:
        assert _percentile([], 0.5) == 0.0


class TestComputeMetrics:
    def test_shape(self) -> None:
        preds = [_pred("bug", "bug"), _pred("docs", "docs")]
        m = compute_metrics(preds)
        assert set(m) == {"accuracy", "macro_f1", "per_class_f1"}
        assert set(m["per_class_f1"]) == set(CLASSES)


class TestComputeLatency:
    def test_orders_independent_of_input(self) -> None:
        preds = [_pred("bug", "bug", latency=lat) for lat in [300.0, 100.0, 200.0]]
        lat = compute_latency(preds)
        assert lat["p50"] == 200.0
        assert lat["mean"] == pytest.approx(200.0)


class TestComputeCost:
    def test_cost_breakdown_uses_per_million(self) -> None:
        preds = [
            Prediction(
                issue_number=1,
                true_label="bug",
                pred_label="bug",
                raw_text="bug",
                latency_ms=100.0,
                input_tokens=1_000_000,
                output_tokens=1_000_000,
                cache_read_tokens=1_000_000,
                cache_creation_tokens=1_000_000,
            )
        ]
        price = PriceConfig(
            input_per_million=1.00,
            output_per_million=5.00,
            cache_read_discount=0.10,
            cache_write_multiplier=1.25,
        )
        cost, tokens = compute_cost(preds, price)
        # 1M input tokens at $1/M
        assert cost.input == pytest.approx(1.00)
        # 1M output tokens at $5/M
        assert cost.output == pytest.approx(5.00)
        # 1M cache-read tokens at $0.10/M (1.00 * 0.10)
        assert cost.cache_read == pytest.approx(0.10)
        # 1M cache-creation tokens at $1.25/M
        assert cost.cache_creation == pytest.approx(1.25)
        assert cost.total == pytest.approx(7.35)
        assert tokens.input_total == 1_000_000


class _FakeUsage:
    def __init__(
        self,
        input_t: int,
        output_t: int,
        cache_read: int = 0,
        cache_creation: int = 0,
    ) -> None:
        self.input_tokens = input_t
        self.output_tokens = output_t
        self.cache_read_input_tokens = cache_read
        self.cache_creation_input_tokens = cache_creation


class _FakeBlock:
    def __init__(self, text: str) -> None:
        self.text = text


class _FakeResponse:
    def __init__(self, text: str, usage: _FakeUsage) -> None:
        self.content = [_FakeBlock(text)]
        self.usage = usage


class _FakeMessages:
    def __init__(self, response: _FakeResponse) -> None:
        self._response = response

    def create(self, **_kwargs: Any) -> _FakeResponse:
        return self._response


class _FakeClient:
    def __init__(self, response: _FakeResponse) -> None:
        self.messages = _FakeMessages(response)


def test_classify_one_returns_parsed_prediction_with_usage() -> None:
    response = _FakeResponse("bug", _FakeUsage(input_t=180, output_t=2, cache_read=170))
    client = _FakeClient(response)
    pred = classify_one(
        client,  # type: ignore[arg-type]
        system_prompt="sys",
        user_template="Title: {{title}}\nBody: {{body}}\nLabel:",
        title="x",
        body="y",
        issue_number=42,
        true_label="bug",
        model="claude-haiku-4-5-20251001",
    )
    assert pred.pred_label == "bug"
    assert pred.true_label == "bug"
    assert pred.issue_number == 42
    assert pred.input_tokens == 180
    assert pred.output_tokens == 2
    assert pred.cache_read_tokens == 170
    assert pred.latency_ms >= 0


def test_classify_one_marks_unparseable_as_none() -> None:
    response = _FakeResponse("perhaps a bug?", _FakeUsage(input_t=10, output_t=4))
    client = _FakeClient(response)
    pred = classify_one(
        client,  # type: ignore[arg-type]
        system_prompt="s",
        user_template="{{title}} {{body}}",
        title="t",
        body="b",
        issue_number=1,
        true_label="docs",
        model="claude-haiku-4-5-20251001",
    )
    # First token of the response is "perhaps", which isn't in CLASSES, so
    # pred_label is None — the row still contributes to test_n but not to
    # accuracy / per-class F1.
    assert pred.pred_label is None
    assert pred.raw_text == "perhaps a bug?"
    assert pred.true_label == "docs"
