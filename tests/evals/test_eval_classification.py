"""Tests for the classifier eval gate (Rule 5 / Rule 10)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from evals.classification import eval_classification as ec


def _write(path: Path, payload: dict[str, object]) -> None:
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


@pytest.fixture
def isolated_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> dict[str, Path]:
    """Redirect THRESHOLDS_PATH / GOLDEN_PATH / LAST_EVAL_PATH at tmp files."""
    thresholds = tmp_path / "eval_thresholds.yaml"
    golden = tmp_path / "golden.jsonl"
    last_eval = tmp_path / "last_eval.json"
    monkeypatch.setattr(ec, "THRESHOLDS_PATH", thresholds)
    monkeypatch.setattr(ec, "GOLDEN_PATH", golden)
    monkeypatch.setattr(ec, "LAST_EVAL_PATH", last_eval)
    return {"thresholds": thresholds, "golden": golden, "last_eval": last_eval}


def _write_thresholds(path: Path, *, enforced: bool = True) -> None:
    flag = str(enforced).lower()
    path.write_text(
        f"""enforced: {flag}
classifier:
  macro_f1_floor: 0.74
  per_class_f1_floor:
    bug: 0.88
    docs: 0.83
    feature: 0.85
    question: 0.40
""",
        encoding="utf-8",
    )


def _write_golden(path: Path, rows: list[dict[str, object]]) -> None:
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row) + "\n")


def _example_rows() -> list[dict[str, object]]:
    return [
        {"id": str(i).zfill(2), "target_class": cls, "title": f"t{i}", "body": f"b{i}"}
        for i, cls in enumerate(["bug", "docs", "feature", "question"], start=1)
    ]


class TestMetrics:
    def test_accuracy_all_correct(self) -> None:
        pairs = [("bug", "bug"), ("docs", "docs"), ("feature", "feature")]
        assert ec._accuracy(pairs) == 1.0

    def test_accuracy_skips_none_pred(self) -> None:
        pairs = [("bug", "bug"), ("docs", None)]
        # None predictions are excluded from accuracy's denominator
        assert ec._accuracy(pairs) == 1.0

    def test_per_class_f1_perfect(self) -> None:
        pairs = [("bug", "bug"), ("docs", "docs"), ("feature", "feature"), ("question", "question")]
        f1 = ec._per_class_f1(pairs)
        assert f1 == {"bug": 1.0, "docs": 1.0, "feature": 1.0, "question": 1.0}

    def test_compute_metrics_shape(self) -> None:
        pairs = [("bug", "bug"), ("docs", "feature")]
        m = ec.compute_metrics(pairs)
        assert set(m) == {"accuracy", "macro_f1", "per_class_f1", "per_class_counts"}


class TestGoldenSetHash:
    def test_is_deterministic(self, isolated_paths: dict[str, Path]) -> None:
        _write_golden(isolated_paths["golden"], _example_rows())
        h1 = ec.golden_set_hash(ec.load_golden())
        h2 = ec.golden_set_hash(ec.load_golden())
        assert h1 == h2

    def test_changes_when_content_changes(
        self, isolated_paths: dict[str, Path]
    ) -> None:
        _write_golden(isolated_paths["golden"], _example_rows())
        before = ec.golden_set_hash(ec.load_golden())
        rows = _example_rows()
        rows[0]["title"] = "edited title"
        _write_golden(isolated_paths["golden"], rows)
        after = ec.golden_set_hash(ec.load_golden())
        assert before != after

    def test_independent_of_row_order(
        self, isolated_paths: dict[str, Path]
    ) -> None:
        rows = _example_rows()
        _write_golden(isolated_paths["golden"], rows)
        h1 = ec.golden_set_hash(ec.load_golden())
        _write_golden(isolated_paths["golden"], list(reversed(rows)))
        h2 = ec.golden_set_hash(ec.load_golden())
        assert h1 == h2


class TestCheck:
    def test_passes_when_all_metrics_meet_floors(
        self, isolated_paths: dict[str, Path], capsys: pytest.CaptureFixture[str]
    ) -> None:
        _write_thresholds(isolated_paths["thresholds"])
        _write_golden(isolated_paths["golden"], _example_rows())
        golden = ec.load_golden()
        _write(
            isolated_paths["last_eval"],
            {
                "golden_set_hash": ec.golden_set_hash(golden),
                "n_examples": len(golden),
                "macro_f1": 0.79,
                "per_class_f1": {"bug": 0.93, "docs": 0.88, "feature": 0.90, "question": 0.45},
            },
        )
        assert ec.check() == 0
        captured = capsys.readouterr()
        assert "PASS" in captured.out

    def test_fails_on_macro_floor_breach(
        self, isolated_paths: dict[str, Path], capsys: pytest.CaptureFixture[str]
    ) -> None:
        _write_thresholds(isolated_paths["thresholds"])
        _write_golden(isolated_paths["golden"], _example_rows())
        golden = ec.load_golden()
        _write(
            isolated_paths["last_eval"],
            {
                "golden_set_hash": ec.golden_set_hash(golden),
                "n_examples": len(golden),
                "macro_f1": 0.50,  # below 0.74
                "per_class_f1": {"bug": 0.93, "docs": 0.88, "feature": 0.90, "question": 0.45},
            },
        )
        assert ec.check() == 1
        assert "macro_f1" in capsys.readouterr().err

    def test_fails_on_per_class_floor_breach(
        self, isolated_paths: dict[str, Path], capsys: pytest.CaptureFixture[str]
    ) -> None:
        _write_thresholds(isolated_paths["thresholds"])
        _write_golden(isolated_paths["golden"], _example_rows())
        golden = ec.load_golden()
        _write(
            isolated_paths["last_eval"],
            {
                "golden_set_hash": ec.golden_set_hash(golden),
                "n_examples": len(golden),
                "macro_f1": 0.80,
                "per_class_f1": {"bug": 0.50, "docs": 0.88, "feature": 0.90, "question": 0.45},
            },
        )
        assert ec.check() == 1
        assert "bug" in capsys.readouterr().err

    def test_fails_on_golden_set_hash_mismatch(
        self, isolated_paths: dict[str, Path], capsys: pytest.CaptureFixture[str]
    ) -> None:
        _write_thresholds(isolated_paths["thresholds"])
        _write_golden(isolated_paths["golden"], _example_rows())
        # Provide a stale hash that doesn't match the current golden set.
        _write(
            isolated_paths["last_eval"],
            {
                "golden_set_hash": "0" * 64,
                "n_examples": 4,
                "macro_f1": 0.80,
                "per_class_f1": {"bug": 0.93, "docs": 0.88, "feature": 0.90, "question": 0.45},
            },
        )
        assert ec.check() == 1
        assert "golden_set_hash mismatch" in capsys.readouterr().err

    def test_returns_zero_when_not_enforced(
        self, isolated_paths: dict[str, Path]
    ) -> None:
        _write_thresholds(isolated_paths["thresholds"], enforced=False)
        _write_golden(isolated_paths["golden"], _example_rows())
        # No last_eval.json at all — should still pass because the gate is off.
        assert ec.check() == 0


def test_real_repo_golden_set_passes_check() -> None:
    """The committed last_eval.json must pass --check against the committed golden set.

    This is the load-bearing test: it's what guarantees `main` stays green.
    """
    assert ec.check() == 0
