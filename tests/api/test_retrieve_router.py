"""Cover the /retrieve router with the service replaced by a stub."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routers.retrieve import router
from app.domain.retrieve import RetrievedChunk
from app.services import retrieve_service


def _app() -> FastAPI:
    a = FastAPI()
    a.include_router(router)
    return a


def test_ok_envelope_returns_200_with_request_and_trace_ids(monkeypatch) -> None:
    chunks = [
        RetrievedChunk(
            content="hello world",
            source_type="docs",
            source_id="doc/source/x.rst",
            score=0.91,
            metadata={"section_path": "X"},
            chunk_id="abc",
        ),
    ]
    monkeypatch.setattr(
        "app.api.routers.retrieve.retrieve_service",
        lambda _req, request_id="", trace_id="": retrieve_service.RetrieveOk(chunks=chunks),
    )
    client = TestClient(_app())
    r = client.post("/retrieve", json={"question": "q", "k": 5})
    assert r.status_code == 200
    body = r.json()
    assert len(body["chunks"]) == 1
    assert body["chunks"][0]["chunk_id"] == "abc"
    assert "request_id" in body and "trace_id" in body


def test_unreachable_returns_503(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.api.routers.retrieve.retrieve_service",
        lambda _req, request_id="", trace_id="": retrieve_service.RetrieveError(
            kind="unreachable", detail="model server down"
        ),
    )
    client = TestClient(_app())
    r = client.post("/retrieve", json={"question": "q"})
    assert r.status_code == 503
    assert r.json()["detail"]["kind"] == "unreachable"


def test_timeout_returns_504(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.api.routers.retrieve.retrieve_service",
        lambda _req, request_id="", trace_id="": retrieve_service.RetrieveError(
            kind="timeout", detail="slow"
        ),
    )
    client = TestClient(_app())
    r = client.post("/retrieve", json={"question": "q"})
    assert r.status_code == 504


def test_internal_returns_502(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.api.routers.retrieve.retrieve_service",
        lambda _req, request_id="", trace_id="": retrieve_service.RetrieveError(
            kind="internal", detail="5xx"
        ),
    )
    client = TestClient(_app())
    r = client.post("/retrieve", json={"question": "q"})
    assert r.status_code == 502


def test_empty_question_returns_422() -> None:
    client = TestClient(_app())
    r = client.post("/retrieve", json={"question": ""})
    assert r.status_code == 422


def test_k_above_cap_returns_422() -> None:
    client = TestClient(_app())
    r = client.post("/retrieve", json={"question": "q", "k": 100})
    assert r.status_code == 422


def test_filter_from_after_to_returns_422() -> None:
    client = TestClient(_app())
    r = client.post(
        "/retrieve",
        json={
            "question": "q",
            "filters": {"from": "2024-12-01T00:00:00Z", "to": "2024-01-01T00:00:00Z"},
        },
    )
    assert r.status_code == 422
