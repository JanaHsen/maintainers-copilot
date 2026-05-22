"""Cover the /summarize router with the service replaced by a stub (Rule 1 / Rule 11)."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routers.summarize import router
from app.services import summarize_service


def _app() -> FastAPI:
    a = FastAPI()
    a.include_router(router)
    return a


def test_ok_envelope_returns_200_with_request_and_trace_ids(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.api.routers.summarize.summarize_service",
        lambda _text, *, max_sentences=3, request_id="": summarize_service.SummarizeOk(
            summary="The user reports a regression."
        ),
    )
    client = TestClient(_app())
    r = client.post("/summarize", json={"text": "some issue text"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["summary"].startswith("The user reports")
    assert "request_id" in body and "trace_id" in body


def test_unreachable_returns_503(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.api.routers.summarize.summarize_service",
        lambda _text, *, max_sentences=3, request_id="": summarize_service.SummarizeError(
            kind="unreachable", detail="anthropic down"
        ),
    )
    client = TestClient(_app())
    r = client.post("/summarize", json={"text": "some issue text"})
    assert r.status_code == 503
    assert r.json()["detail"]["kind"] == "unreachable"


def test_timeout_returns_504(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.api.routers.summarize.summarize_service",
        lambda _text, *, max_sentences=3, request_id="": summarize_service.SummarizeError(
            kind="timeout", detail="slow"
        ),
    )
    client = TestClient(_app())
    r = client.post("/summarize", json={"text": "some issue text"})
    assert r.status_code == 504


def test_internal_returns_502(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.api.routers.summarize.summarize_service",
        lambda _text, *, max_sentences=3, request_id="": summarize_service.SummarizeError(
            kind="internal", detail="5xx"
        ),
    )
    client = TestClient(_app())
    r = client.post("/summarize", json={"text": "some issue text"})
    assert r.status_code == 502


def test_empty_text_returns_422() -> None:
    client = TestClient(_app())
    r = client.post("/summarize", json={"text": ""})
    assert r.status_code == 422


def test_max_sentences_above_cap_returns_422() -> None:
    client = TestClient(_app())
    r = client.post("/summarize", json={"text": "x", "max_sentences": 9})
    assert r.status_code == 422
