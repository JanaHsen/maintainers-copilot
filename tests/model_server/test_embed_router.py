"""Cover the /embed router with a fake loaded embedder."""

from __future__ import annotations

from collections.abc import Iterable

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from model_server import state
from model_server.embed import LoadedEmbedder
from model_server.routers.embed import router


class _FakeST:
    """Stand-in for the sentence-transformers SentenceTransformer."""

    def encode(self, x, **_kwargs):  # type: ignore[no-untyped-def]
        # Return a constant 768-dim vector for single-text, list of vectors
        # for batch.
        if isinstance(x, str):
            return [0.0] * 768
        if isinstance(x, Iterable):
            return [[0.0] * 768 for _ in x]
        raise TypeError("unexpected encode input")

    def get_sentence_embedding_dimension(self) -> int:
        return 768


@pytest.fixture
def client() -> TestClient:
    state.set_embedder(
        LoadedEmbedder(model=_FakeST(), model_id="BAAI/bge-base-en-v1.5", dim=768)
    )
    app = FastAPI()
    app.include_router(router)
    try:
        yield TestClient(app)
    finally:
        state.clear_artifacts()


def test_text_returns_single_embedding(client: TestClient) -> None:
    resp = client.post("/embed", json={"text": "hello"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["dim"] == 768
    assert body["model_id"] == "BAAI/bge-base-en-v1.5"
    assert isinstance(body["embedding"], list)
    assert len(body["embedding"]) == 768


def test_texts_returns_batch(client: TestClient) -> None:
    resp = client.post("/embed", json={"texts": ["a", "b", "c"]})
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["embeddings"]) == 3
    assert all(len(v) == 768 for v in body["embeddings"])


def test_missing_input_returns_422(client: TestClient) -> None:
    resp = client.post("/embed", json={})
    assert resp.status_code == 422


def test_both_inputs_returns_422(client: TestClient) -> None:
    resp = client.post("/embed", json={"text": "a", "texts": ["b"]})
    assert resp.status_code == 422


def test_empty_text_returns_422(client: TestClient) -> None:
    resp = client.post("/embed", json={"text": ""})
    assert resp.status_code == 422
