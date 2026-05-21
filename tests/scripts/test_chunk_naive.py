"""Cover the naive baseline chunker: fixed sizing, deterministic IDs."""

from __future__ import annotations

from datetime import UTC, datetime

from scripts.rag.chunk_naive import CHILD_CHARS, chunk_source

TS = datetime(2024, 1, 1, tzinfo=UTC)


def test_each_chunk_has_one_self_child() -> None:
    text = "This is one short paragraph that should fit in a single chunk."
    parents = chunk_source(
        corpus_run_id="r",
        source_type="docs",
        source_id="x",
        source_timestamp=TS,
        raw_text=text,
    )
    assert len(parents) == 1
    assert len(parents[0].children) == 1
    assert parents[0].children[0].content == parents[0].content


def test_long_text_splits_at_target_size() -> None:
    text = "Paragraph one. " * 200  # ~3000 chars
    parents = chunk_source(
        corpus_run_id="r",
        source_type="docs",
        source_id="x",
        source_timestamp=TS,
        raw_text=text,
    )
    # 3000 chars at 400-char target → at least 6, at most 10 chunks.
    assert 6 <= len(parents) <= 10
    for p in parents:
        assert len(p.content) <= CHILD_CHARS * 1.5


def test_same_input_same_ids() -> None:
    text = "Paragraph one. " * 50
    a = chunk_source(
        corpus_run_id="r",
        source_type="docs",
        source_id="x",
        source_timestamp=TS,
        raw_text=text,
    )
    b = chunk_source(
        corpus_run_id="r",
        source_type="docs",
        source_id="x",
        source_timestamp=TS,
        raw_text=text,
    )
    assert [p.id for p in a] == [p.id for p in b]


def test_section_path_is_empty() -> None:
    text = "Paragraph. " * 50
    parents = chunk_source(
        corpus_run_id="r",
        source_type="docs",
        source_id="x",
        source_timestamp=TS,
        raw_text=text,
    )
    assert all(p.section_path == "" for p in parents)
