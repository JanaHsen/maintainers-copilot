"""Cover the /ner router with the service replaced by a stub (Rule 1 / Rule 11)."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routers.ner import router
from app.domain.ner import EntityBuckets
from app.services import ner_service


def _app() -> FastAPI:
    a = FastAPI()
    a.include_router(router)
    return a


def test_ok_envelope_returns_200_with_request_and_trace_ids(monkeypatch) -> None:
    buckets = EntityBuckets(
        repo_names=["pandas-dev/pandas"],
        file_paths=["src/io/parsers.py"],
        error_types=["ConnectionError"],
        package_names=["requests"],
    )
    monkeypatch.setattr(
        "app.api.routers.ner.ner_service",
        lambda _text, request_id="": ner_service.NerOk(entities=buckets),
    )
    client = TestClient(_app())
    r = client.post("/ner", json={"text": "some issue text"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["entities"]["repo_names"] == ["pandas-dev/pandas"]
    assert body["entities"]["file_paths"] == ["src/io/parsers.py"]
    assert body["entities"]["error_types"] == ["ConnectionError"]
    assert body["entities"]["package_names"] == ["requests"]
    assert "request_id" in body and "trace_id" in body


def test_unreachable_returns_503(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.api.routers.ner.ner_service",
        lambda _text, request_id="": ner_service.NerError(
            kind="unreachable", detail="anthropic down"
        ),
    )
    client = TestClient(_app())
    r = client.post("/ner", json={"text": "some issue text"})
    assert r.status_code == 503
    assert r.json()["detail"]["kind"] == "unreachable"


def test_bad_format_returns_502(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.api.routers.ner.ner_service",
        lambda _text, request_id="": ner_service.NerError(
            kind="bad_format", detail="not JSON"
        ),
    )
    client = TestClient(_app())
    r = client.post("/ner", json={"text": "some issue text"})
    assert r.status_code == 502
    assert r.json()["detail"]["kind"] == "bad_format"


def test_timeout_returns_504(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.api.routers.ner.ner_service",
        lambda _text, request_id="": ner_service.NerError(
            kind="timeout", detail="slow"
        ),
    )
    client = TestClient(_app())
    r = client.post("/ner", json={"text": "some issue text"})
    assert r.status_code == 504


def test_empty_text_returns_422() -> None:
    client = TestClient(_app())
    r = client.post("/ner", json={"text": ""})
    assert r.status_code == 422
