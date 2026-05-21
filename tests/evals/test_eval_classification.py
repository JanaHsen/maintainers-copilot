"""Tests for the classifier eval gate (Rule 5 / Rule 10).

The HTTP call to ``/classify`` is mocked with ``httpx.MockTransport`` so
the metrics + gate logic is what's under test, not the model server.
"""

from __future__ import annotations

import json
from collections.abc import Callable

import httpx

from evals.classification import eval_classification as ec


def test_per_class_f1_perfect_predictions() -> None:
    pairs = [(c, c) for c in ec.CLASSES]
    assert ec.per_class_f1(pairs) == dict.fromkeys(ec.CLASSES, 1.0)


def test_per_class_f1_skips_none() -> None:
    pairs: list[tuple[str, str | None]] = [("bug", None), ("docs", "docs")]
    f1 = ec.per_class_f1(pairs)
    # docs is the only class with a non-None prediction, and it's correct
    assert f1["docs"] == 1.0
    assert f1["bug"] == 0.0


def test_macro_f1_averages_classes() -> None:
    # All four classes get F1 = 1.0 -> macro = 1.0
    assert ec.macro_f1({c: 1.0 for c in ec.CLASSES}) == 1.0
    # Two ones and two zeros -> macro = 0.5
    assert ec.macro_f1({"bug": 1.0, "docs": 1.0, "feature": 0.0, "question": 0.0}) == 0.5


def test_confusion_matrix_counts_predictions() -> None:
    pairs: list[tuple[str, str | None]] = [
        ("bug", "bug"),
        ("bug", "docs"),
        ("docs", "docs"),
        ("feature", None),
    ]
    matrix = ec.confusion_matrix(pairs)
    assert matrix["bug"] == {"bug": 1, "docs": 1}
    assert matrix["docs"] == {"docs": 1}
    assert matrix["feature"] == {"_unparseable": 1}


def _client_with_handler(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_classify_one_returns_label_on_200() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"label": "bug", "confidence": 0.91, "label_scores": {}}
        )

    with _client_with_handler(handler) as client:
        label = ec.classify_one(
            client,
            url="http://m",
            title="t",
            body="b",
            issue_number=1,
        )
    assert label == "bug"


def test_classify_one_returns_none_on_5xx() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="upstream down")

    with _client_with_handler(handler) as client:
        label = ec.classify_one(
            client,
            url="http://m",
            title="t",
            body="b",
            issue_number=2,
        )
    assert label is None


def test_classify_one_returns_none_on_unparseable_body() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"not_a_label": "?"})

    with _client_with_handler(handler) as client:
        label = ec.classify_one(
            client,
            url="http://m",
            title="t",
            body="b",
            issue_number=3,
        )
    assert label is None


def test_classify_one_returns_none_on_off_class_label() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"label": "maybe-bug", "confidence": 0.5})

    with _client_with_handler(handler) as client:
        label = ec.classify_one(
            client,
            url="http://m",
            title="t",
            body="b",
            issue_number=4,
        )
    assert label is None


def test_build_report_shape_includes_required_fields() -> None:
    pairs: list[tuple[str, str | None]] = [(c, c) for c in ec.CLASSES]
    per_class = ec.per_class_f1(pairs)
    macro = ec.macro_f1(per_class)
    matrix = ec.confusion_matrix(pairs)
    predictions = [
        {"issue_number": i, "true_class": c, "pred_class": c, "correct": True}
        for i, c in enumerate(ec.CLASSES, start=1)
    ]
    report = ec.build_report(
        run_ts="20260520T220000Z",
        macro=macro,
        per_class=per_class,
        matrix=matrix,
        predictions=predictions,
        macro_floor=0.65,
        failed_n=0,
    )
    # The brief mandates these specific keys in the report payload.
    for key in (
        "run_ts",
        "n_examples",
        "macro_f1",
        "macro_f1_floor",
        "macro_f1_passes",
        "per_class_f1",
        "confusion_matrix",
        "predictions",
    ):
        assert key in report, f"missing key: {key}"
    # Sanity: the report must be JSON-serializable as-is (round-trip).
    assert json.loads(json.dumps(report))["macro_f1"] == macro
