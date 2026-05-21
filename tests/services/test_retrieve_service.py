"""Cover retrieve_service orchestration with mocked embed + repository."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.domain.retrieve import ChildHit, RetrieveFilters, RetrieveRequest
from app.infra import embedding_client
from app.infra.model_server_client import (
    ModelServerInternalError,
    ModelServerInvalidInputError,
    ModelServerTimeoutError,
    ModelServerUnreachableError,
)
from app.repositories import chunk_repository
from app.services import retrieve_service
from app.services.retrieve_service import RetrieveError, RetrieveOk, retrieve


def _hit(idx: int) -> ChildHit:
    return ChildHit(
        chunk_id=f"c{idx}",
        parent_id=f"p{idx}",
        content=f"chunk {idx} text",
        source_type="docs",
        source_id=f"doc/source/x/{idx}.rst",
        source_timestamp=datetime(2024, 1, 1, tzinfo=UTC),
        section_path="Heading",
        score=1.0 - 0.01 * idx,
    )


def test_happy_path_returns_chunks(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        retrieve_service.get_settings,
        "__wrapped__",  # bypass lru_cache for tests
        lambda: type("S", (), {"rag_corpus_run_id": "test-run-1"})(),
    )
    monkeypatch.setattr(embedding_client, "embed", lambda _t, request_id="": [0.0] * 768)
    monkeypatch.setattr(
        chunk_repository,
        "query_first_stage",
        lambda **_kw: [_hit(0), _hit(1), _hit(2)],
    )
    outcome = retrieve(RetrieveRequest(question="how do I groupby", k=2))
    assert isinstance(outcome, RetrieveOk)
    assert len(outcome.chunks) == 2
    assert outcome.chunks[0].source_type == "docs"
    assert outcome.chunks[0].metadata["parent_id"] == "p0"


def test_k_zero_returns_empty_without_embed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _boom(*_a, **_kw):  # type: ignore[no-untyped-def]
        raise AssertionError("embed must NOT be called when k=0")

    monkeypatch.setattr(embedding_client, "embed", _boom)
    outcome = retrieve(RetrieveRequest(question="x", k=0))
    assert isinstance(outcome, RetrieveOk)
    assert outcome.chunks == []


@pytest.mark.parametrize(
    "exc, expected_kind",
    [
        (ModelServerUnreachableError("down"), "unreachable"),
        (ModelServerTimeoutError("slow"), "timeout"),
        (ModelServerInvalidInputError("bad"), "bad_request"),
        (ModelServerInternalError("5xx"), "internal"),
    ],
)
def test_embed_exception_maps_to_typed_outcome(
    monkeypatch: pytest.MonkeyPatch,
    exc: Exception,
    expected_kind: str,
) -> None:
    def _boom(*_a, **_kw):  # type: ignore[no-untyped-def]
        raise exc

    monkeypatch.setattr(embedding_client, "embed", _boom)
    outcome = retrieve(RetrieveRequest(question="x"))
    assert isinstance(outcome, RetrieveError)
    assert outcome.kind == expected_kind


def test_filters_threaded_through(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def _fake_query(**kw):  # type: ignore[no-untyped-def]
        captured.update(kw)
        return []

    monkeypatch.setattr(embedding_client, "embed", lambda _t, request_id="": [0.0] * 768)
    monkeypatch.setattr(chunk_repository, "query_first_stage", _fake_query)
    outcome = retrieve(
        RetrieveRequest(
            question="q",
            k=5,
            filters=RetrieveFilters(source=["docs"]),
        )
    )
    assert isinstance(outcome, RetrieveOk)
    filters = captured["filters"]
    assert filters.source_types == ["docs"]
