"""Cover the /rerank router with a fake loaded cross-encoder."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from model_server import state
from model_server.rerank import LoadedReranker
from model_server.routers.rerank import router


class _FakeCE:
    """Stand-in for sentence-transformers CrossEncoder."""

    def predict(self, pairs, **_kwargs):  # type: ignore[no-untyped-def]
        # Score = (query, text) length sum, so order is deterministic
        # and the test can assert relative scores.
        return [float(len(q) + len(t)) for q, t in pairs]


@pytest.fixture
def client() -> TestClient:
    state.set_reranker(
        LoadedReranker(
            model=_FakeCE(), model_id="cross-encoder/ms-marco-MiniLM-L-6-v2"
        )
    )
    app = FastAPI()
    app.include_router(router)
    try:
        yield TestClient(app)
    finally:
        state.clear_artifacts()


def test_rerank_returns_scored_candidates_in_input_order(client: TestClient) -> None:
    resp = client.post(
        "/rerank",
        json={
            "query": "how do I groupby",
            "candidates": [
                {"id": "a", "text": "short"},
                {"id": "b", "text": "this is a much longer candidate text"},
                {"id": "c", "text": "med"},
            ],
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["model_id"] == "cross-encoder/ms-marco-MiniLM-L-6-v2"
    ids = [s["id"] for s in body["scores"]]
    assert ids == ["a", "b", "c"]  # input order
    # Longer candidate gets higher fake-score
    scores = {s["id"]: s["score"] for s in body["scores"]}
    assert scores["b"] > scores["a"]
    assert scores["b"] > scores["c"]


def test_empty_candidates_returns_422(client: TestClient) -> None:
    resp = client.post("/rerank", json={"query": "q", "candidates": []})
    assert resp.status_code == 422


def test_empty_query_returns_422(client: TestClient) -> None:
    resp = client.post(
        "/rerank",
        json={"query": "", "candidates": [{"id": "a", "text": "x"}]},
    )
    assert resp.status_code == 422


def test_candidates_over_cap_returns_422(client: TestClient) -> None:
    # Cap is 64; send 65.
    resp = client.post(
        "/rerank",
        json={
            "query": "q",
            "candidates": [{"id": f"c{i}", "text": "x"} for i in range(65)],
        },
    )
    assert resp.status_code == 422
