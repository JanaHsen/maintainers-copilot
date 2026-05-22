"""Tests for the source-type + time-window filter path (T042 / FR-018).

The filter wiring is already in place (T020 validator on RetrieveFilters,
T040 SQL WHERE clause in chunk_repository.query_first_stage, T041
RetrieveRequest -> ChunkFilters translation in retrieve_service). These
tests pin the contract by mocking chunk_repository so we don't depend on
DB state — they check that retrieve_service forwards the filters
unmodified through to the repository.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import patch

from app.domain.retrieve import (
    ChildHit,
    Parent,
    RetrieveFilters,
    RetrieveRequest,
)
from app.services import retrieve_service
from app.services.retrieve_service import RetrieveOk, _filters_from_request


def _make_request(**kw):
    """Build a RetrieveRequest with default question + k=5."""
    return RetrieveRequest(question="how do I X", k=5, **kw)


def test_filters_default_to_both_source_types_and_no_window() -> None:
    cf = _filters_from_request(_make_request())
    assert cf.source_types == ["docs", "issues"]
    assert cf.from_ is None
    assert cf.to is None


def test_filters_docs_only_passes_through() -> None:
    req = _make_request(filters=RetrieveFilters(source=["docs"]))
    cf = _filters_from_request(req)
    assert cf.source_types == ["docs"]


def test_filters_issues_only_passes_through() -> None:
    req = _make_request(filters=RetrieveFilters(source=["issues"]))
    cf = _filters_from_request(req)
    assert cf.source_types == ["issues"]


def test_filters_time_window_passes_through() -> None:
    req = _make_request(filters=RetrieveFilters(
        **{"from": datetime(2024, 1, 1, tzinfo=UTC),
           "to": datetime(2024, 12, 31, tzinfo=UTC)}
    ))
    cf = _filters_from_request(req)
    assert cf.from_ == datetime(2024, 1, 1, tzinfo=UTC)
    assert cf.to == datetime(2024, 12, 31, tzinfo=UTC)


def test_filters_from_after_to_is_rejected_by_validator() -> None:
    import pytest
    with pytest.raises(ValueError):
        RetrieveFilters(
            **{"from": datetime(2024, 12, 1, tzinfo=UTC),
               "to": datetime(2024, 1, 1, tzinfo=UTC)}
        )


def test_filters_threaded_through_retrieve_service_to_repository() -> None:
    """End-to-end mock: requested filters land in chunk_repository's call."""
    captured_filters = {}

    def _fake_query_first_stage(*, embedding, query_text, alpha, k, filters, corpus_run_id):
        captured_filters["value"] = filters
        return [
            ChildHit(
                chunk_id="c1", parent_id="p1", content="x",
                source_type="docs", source_id="readme.md",
                source_timestamp=datetime(2024, 6, 1, tzinfo=UTC),
                section_path="", score=0.9,
            )
        ]

    def _fake_fetch_parents(parent_ids):
        return {
            "p1": Parent(
                chunk_id="p1", content="parent text",
                source_type="docs", source_id="readme.md",
                source_timestamp=datetime(2024, 6, 1, tzinfo=UTC),
                section_path="", metadata={},
            )
        }

    def _fake_embed(text, *, request_id=""):
        return [0.0] * 768

    req = _make_request(filters=RetrieveFilters(source=["docs"]))

    # rerank is currently dropped (DECISIONS.md T033) so no need to mock it.

    with (
        patch.object(
            retrieve_service.chunk_repository,
            "query_first_stage",
            _fake_query_first_stage,
        ),
        patch.object(retrieve_service.chunk_repository, "fetch_parents", _fake_fetch_parents),
        patch.object(retrieve_service.embedding_client, "embed", _fake_embed),
    ):
        out = retrieve_service.retrieve(req)

    assert isinstance(out, RetrieveOk)
    assert captured_filters["value"].source_types == ["docs"]
