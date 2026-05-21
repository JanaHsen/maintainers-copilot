"""Cover the chunker: deterministic IDs, section detection, size targets."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from scripts.rag.chunk_parent_document import (
    CHILD_CHARS,
    PARENT_CHARS,
    chunk_source,
)

FIXTURE = Path(__file__).resolve().parents[1].parent / "tests" / "fixtures" / "rag_smoke"
TS = datetime(2024, 1, 1, tzinfo=UTC)


def _read(name: str) -> str:
    return (FIXTURE / "docs" / name).read_text(encoding="utf-8")


class TestDeterministicIds:
    def test_same_input_same_ids(self) -> None:
        text = _read("groupby.rst")
        a = chunk_source(
            corpus_run_id="run-1",
            source_type="docs",
            source_id="doc/source/user_guide/groupby.rst",
            source_timestamp=TS,
            raw_text=text,
        )
        b = chunk_source(
            corpus_run_id="run-1",
            source_type="docs",
            source_id="doc/source/user_guide/groupby.rst",
            source_timestamp=TS,
            raw_text=text,
        )
        assert [p.id for p in a] == [p.id for p in b]
        assert [c.id for p in a for c in p.children] == [c.id for p in b for c in p.children]

    def test_different_corpus_run_id_different_ids(self) -> None:
        text = _read("groupby.rst")
        a = chunk_source(
            corpus_run_id="run-1",
            source_type="docs",
            source_id="x",
            source_timestamp=TS,
            raw_text=text,
        )
        b = chunk_source(
            corpus_run_id="run-2",
            source_type="docs",
            source_id="x",
            source_timestamp=TS,
            raw_text=text,
        )
        assert a[0].id != b[0].id


class TestSizeTargets:
    def test_smoke_fixture_chunks_within_size_budget(self) -> None:
        text = _read("groupby.rst")
        parents = chunk_source(
            corpus_run_id="r",
            source_type="docs",
            source_id="x",
            source_timestamp=TS,
            raw_text=text,
        )
        assert parents
        for p in parents:
            # Allow some slack (parents may go up to ~1.5x target if a paragraph
            # exceeds PARENT_CHARS on its own; not common at the fixture size).
            assert len(p.content) <= PARENT_CHARS * 1.5
            assert p.children, f"parent {p.id} has no children"
            for c in p.children:
                assert len(c.content) <= CHILD_CHARS * 1.5

    def test_child_text_is_subset_of_parent_when_short(self) -> None:
        # When a section is small enough to be a single parent, the
        # concatenation of children should reconstruct the parent (modulo
        # whitespace).
        text = "Title\n=====\n\n" + ("Short paragraph. " * 5)
        parents = chunk_source(
            corpus_run_id="r",
            source_type="docs",
            source_id="x",
            source_timestamp=TS,
            raw_text=text,
        )
        assert len(parents) == 1
        joined = " ".join(c.content for c in parents[0].children)
        assert "Short paragraph." in joined


class TestSectionDetection:
    def test_rst_underline_recognized(self) -> None:
        text = (
            "Intro\n=====\n\n"
            "Some intro text.\n\n"
            "Details\n-------\n\n"
            "Some details text."
        )
        parents = chunk_source(
            corpus_run_id="r",
            source_type="docs",
            source_id="x",
            source_timestamp=TS,
            raw_text=text,
        )
        section_paths = {p.section_path for p in parents}
        assert "Intro" in section_paths
        assert "Details" in section_paths

    def test_markdown_headings_recognized(self) -> None:
        text = "# Title\n\nSome content.\n\n## Subtitle\n\nMore content."
        parents = chunk_source(
            corpus_run_id="r",
            source_type="docs",
            source_id="x",
            source_timestamp=TS,
            raw_text=text,
        )
        section_paths = {p.section_path for p in parents}
        assert "Title" in section_paths
        assert "Subtitle" in section_paths

    def test_no_headings_yields_single_section(self) -> None:
        text = "Just one paragraph. With multiple sentences. No headings here."
        parents = chunk_source(
            corpus_run_id="r",
            source_type="docs",
            source_id="x",
            source_timestamp=TS,
            raw_text=text,
        )
        assert len(parents) == 1
        assert parents[0].section_path == ""


class TestParentChildLinkage:
    def test_children_reference_their_parent(self) -> None:
        text = _read("io_csv.rst")
        parents = chunk_source(
            corpus_run_id="r",
            source_type="docs",
            source_id="x",
            source_timestamp=TS,
            raw_text=text,
        )
        for p in parents:
            for c in p.children:
                assert c.parent_id == p.id
