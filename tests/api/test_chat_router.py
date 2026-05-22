"""Integration tests for the chat router (POST /chat + POST /widget/chat).

Covers the five contract behaviours specified in
``specs/003-chatbot-part2-brain/contracts/chat.openapi.yaml``:

  1. Authed happy path — register + login → POST /chat → 200 with
     ChatResponse body (chatbot_service monkeypatched to a ChatOk stub).
  2. Authed 401 — POST /chat without a session cookie → 401.
  3. Widget refusal happy path — create a widget, POST /widget/chat with
     a valid token + valid Origin → 200; the assistant message + tool_trace
     surface the refusal (chatbot_service monkeypatched to a ChatOk that
     contains a write_memory entry with is_error=True and kind=widget_actor_forbidden).
  4. Widget 401 — POST /widget/chat with an unknown X-Widget-Token → 401.
  5. Widget 403 — POST /widget/chat with a valid token but an Origin not
     in widget.allowed_origins → 403.

Bonus: assert ChatError(kind='anthropic_unreachable') → 503 through the
authed surface, exercising the Rule-11 kind→status mapping.

Same skip-guard pattern as ``tests/api/test_auth_router.py``: tests skip
cleanly if the async engine cannot reach Postgres / Vault. Cleanup uses
prefix-matching DELETEs so re-runs are idempotent.
"""

from __future__ import annotations

import asyncio
import os
import uuid

import pytest

# Host-env override pattern: when running against the live compose stack
# from the host (not inside the api container), point the service hostnames
# at the published ports on localhost. Same pattern as
# tests/scripts/test_build_corpus_smoke.py.
if not os.environ.get("VAULT_ADDR"):
    os.environ.setdefault("VAULT_ADDR", "http://localhost:8200")
    os.environ.setdefault("POSTGRES_HOST", "localhost")
    os.environ.setdefault("REDIS_HOST", "localhost")
    os.environ.setdefault("MINIO_HOST", "localhost")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402
from sqlalchemy.exc import OperationalError  # noqa: E402

from app.api.routers.auth import router as auth_router  # noqa: E402
from app.api.routers.chat import router as chat_router  # noqa: E402
from app.domain.chat import ToolTraceEntry  # noqa: E402
from app.infra.database import get_engine  # noqa: E402
from app.infra.database_async import (  # noqa: E402
    get_async_engine,
    get_async_sessionmaker,
)
from app.infra.vault_client import VaultBootstrapError  # noqa: E402
from app.repositories import widget_repository  # noqa: E402
from app.services import chatbot_service  # noqa: E402

# --- skip-guard + fixture helpers -----------------------------------------


def _ensure_stack_reachable() -> None:
    """Skip if either the async engine (auth) or the sync engine (widgets)
    cannot reach Postgres / Vault."""

    async def probe() -> None:
        engine = get_async_engine()
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))

    try:
        asyncio.run(probe())
        with get_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
    except (OperationalError, VaultBootstrapError, RuntimeError) as exc:
        pytest.skip(f"Postgres / Vault not reachable: {exc}")


def _build_app() -> FastAPI:
    """Build a minimal FastAPI app with both routers wired (same as prod)."""
    app = FastAPI()
    app.include_router(auth_router)
    app.include_router(chat_router)
    return app


def _delete_test_users(email_prefix: str) -> None:
    async def cleanup() -> None:
        sm = get_async_sessionmaker()
        async with sm() as session:
            await session.execute(
                text("DELETE FROM users WHERE email LIKE :pat"),
                {"pat": f"{email_prefix}%"},
            )
            await session.commit()

    asyncio.run(cleanup())


def _delete_test_widgets(name_prefix: str) -> None:
    """Widgets cleanup — direct sync SQL (this also cascades audit_log
    rows referencing the deleted widget owners through the user cascade)."""
    with get_engine().begin() as conn:
        conn.execute(
            text("DELETE FROM widgets WHERE name LIKE :pat"),
            {"pat": f"{name_prefix}%"},
        )


# --- tests ----------------------------------------------------------------


def test_authed_chat_happy_path(monkeypatch) -> None:
    """User Story 1: register + login + POST /chat → 200 ChatResponse."""
    _ensure_stack_reachable()

    email_prefix = "pytest-chat-router-authed-"
    _delete_test_users(email_prefix)

    from app.infra import auth_backend
    auth_backend.get_jwt_strategy.cache_clear()

    expected_conversation_id = uuid.uuid4()

    def fake_chat(
        *,
        conversation_id,
        user_message,
        actor,
        request_id="",
        trace_id="",
    ):
        return chatbot_service.ChatOk(
            assistant_message="hi",
            conversation_id=expected_conversation_id,
            tool_trace=[],
        )

    monkeypatch.setattr(
        "app.api.routers.chat.chatbot_service.chat", fake_chat
    )

    app = _build_app()
    client = TestClient(app)

    email = f"{email_prefix}{uuid.uuid4().hex[:8]}@example.com"
    password = "correct-horse-battery-staple"

    try:
        resp = client.post(
            "/auth/register",
            json={"email": email, "password": password},
        )
        assert resp.status_code == 201, resp.text

        resp = client.post(
            "/auth/login",
            data={"username": email, "password": password},
        )
        assert resp.status_code == 204, resp.text

        resp = client.post(
            "/chat", json={"conversation_id": None, "message": "hello"}
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["assistant_message"] == "hi"
        assert body["conversation_id"] == str(expected_conversation_id)
        assert body["tool_trace"] == []
        assert "request_id" in body
        assert "trace_id" in body
    finally:
        _delete_test_users(email_prefix)


def test_authed_chat_without_cookie_returns_401() -> None:
    """No session cookie → 401 (current_active_user enforces this)."""
    _ensure_stack_reachable()

    from app.infra import auth_backend
    auth_backend.get_jwt_strategy.cache_clear()

    app = _build_app()
    client = TestClient(app)

    resp = client.post(
        "/chat", json={"conversation_id": None, "message": "hello"}
    )
    assert resp.status_code == 401, resp.text


def test_authed_chat_anthropic_unreachable_returns_503(monkeypatch) -> None:
    """Bonus: ChatError(kind='anthropic_unreachable') → 503 (Rule 11)."""
    _ensure_stack_reachable()

    email_prefix = "pytest-chat-router-unreachable-"
    _delete_test_users(email_prefix)

    from app.infra import auth_backend
    auth_backend.get_jwt_strategy.cache_clear()

    def fake_chat(
        *,
        conversation_id,
        user_message,
        actor,
        request_id="",
        trace_id="",
    ):
        return chatbot_service.ChatError(
            kind="anthropic_unreachable",
            detail="anthropic api unreachable",
            conversation_id=None,
        )

    monkeypatch.setattr(
        "app.api.routers.chat.chatbot_service.chat", fake_chat
    )

    app = _build_app()
    client = TestClient(app)

    email = f"{email_prefix}{uuid.uuid4().hex[:8]}@example.com"
    password = "correct-horse-battery-staple"

    try:
        resp = client.post(
            "/auth/register",
            json={"email": email, "password": password},
        )
        assert resp.status_code == 201, resp.text
        resp = client.post(
            "/auth/login",
            data={"username": email, "password": password},
        )
        assert resp.status_code == 204, resp.text

        resp = client.post(
            "/chat", json={"conversation_id": None, "message": "hello"}
        )
        assert resp.status_code == 503, resp.text
        body = resp.json()
        assert body["detail"]["kind"] == "anthropic_unreachable"
    finally:
        _delete_test_users(email_prefix)


def test_widget_chat_refusal_happy_path(monkeypatch) -> None:
    """User Story 3 acceptance 1+2: widget memory refusal is sanitized."""
    _ensure_stack_reachable()

    email_prefix = "pytest-chat-router-widget-owner-"
    widget_prefix = "pytest-chat-router-widget-"
    _delete_test_users(email_prefix)
    _delete_test_widgets(widget_prefix)

    from app.infra import auth_backend
    auth_backend.get_jwt_strategy.cache_clear()

    # Seed an owner user for the widget (widgets.owner_user_id NOT NULL).
    owner_id = uuid.uuid4()
    with get_engine().begin() as conn:
        conn.execute(
            text(
                "INSERT INTO users (id, email, hashed_password, is_active, "
                "is_superuser, is_verified, role) "
                "VALUES (:id, :email, 'placeholder', TRUE, FALSE, TRUE, 'admin')"
            ),
            {
                "id": owner_id,
                "email": f"{email_prefix}{owner_id.hex[:8]}@example.com",
            },
        )

    try:
        widget_id, plaintext = widget_repository.create(
            name=f"{widget_prefix}{uuid.uuid4().hex[:8]}",
            allowed_origins=["http://localhost:8080"],
            owner_user_id=owner_id,
        )

        expected_conversation_id = uuid.uuid4()
        refusal_text = "I can't save things from this session."

        def fake_chat(
            *,
            conversation_id,
            user_message,
            actor,
            request_id="",
            trace_id="",
        ):
            return chatbot_service.ChatOk(
                assistant_message=refusal_text,
                conversation_id=expected_conversation_id,
                tool_trace=[
                    ToolTraceEntry(
                        tool_name="write_memory",
                        input={},
                        output={
                            "error": {
                                "kind": "widget_actor_forbidden",
                                "detail": "widget sessions cannot persist memory",
                            }
                        },
                        latency_ms=12,
                        is_error=True,
                    )
                ],
            )

        monkeypatch.setattr(
            "app.api.routers.chat.chatbot_service.chat", fake_chat
        )

        app = _build_app()
        client = TestClient(app)

        resp = client.post(
            "/widget/chat",
            json={
                "widget_id": str(widget_id),
                "session_id": "sess-1",
                "message": "please remember this for next time",
            },
            headers={
                "X-Widget-Token": plaintext,
                "Origin": "http://localhost:8080",
            },
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["assistant_message"] == refusal_text
        assert body["conversation_id"] == str(expected_conversation_id)
        assert len(body["tool_trace"]) == 1
        entry = body["tool_trace"][0]
        assert entry["tool_name"] == "write_memory"
        assert entry["is_error"] is True
        assert entry["output"]["error"]["kind"] == "widget_actor_forbidden"
    finally:
        _delete_test_widgets(widget_prefix)
        _delete_test_users(email_prefix)


def test_widget_chat_bad_token_returns_401() -> None:
    """User Story 3 acceptance 3: unknown X-Widget-Token → 401."""
    _ensure_stack_reachable()

    from app.infra import auth_backend
    auth_backend.get_jwt_strategy.cache_clear()

    app = _build_app()
    client = TestClient(app)

    resp = client.post(
        "/widget/chat",
        json={
            "widget_id": str(uuid.uuid4()),
            "session_id": "sess-1",
            "message": "hi",
        },
        headers={
            "X-Widget-Token": "not-a-real-token",
            "Origin": "http://localhost:8080",
        },
    )
    assert resp.status_code == 401, resp.text
    assert "invalid or revoked widget token" in resp.json()["detail"]


def test_widget_chat_bad_origin_returns_403() -> None:
    """User Story 3 acceptance 4: Origin not in allowed_origins → 403."""
    _ensure_stack_reachable()

    email_prefix = "pytest-chat-router-widget-origin-"
    widget_prefix = "pytest-chat-router-origin-"
    _delete_test_users(email_prefix)
    _delete_test_widgets(widget_prefix)

    from app.infra import auth_backend
    auth_backend.get_jwt_strategy.cache_clear()

    owner_id = uuid.uuid4()
    with get_engine().begin() as conn:
        conn.execute(
            text(
                "INSERT INTO users (id, email, hashed_password, is_active, "
                "is_superuser, is_verified, role) "
                "VALUES (:id, :email, 'placeholder', TRUE, FALSE, TRUE, 'admin')"
            ),
            {
                "id": owner_id,
                "email": f"{email_prefix}{owner_id.hex[:8]}@example.com",
            },
        )

    try:
        widget_id, plaintext = widget_repository.create(
            name=f"{widget_prefix}{uuid.uuid4().hex[:8]}",
            allowed_origins=["http://localhost:8080"],
            owner_user_id=owner_id,
        )

        app = _build_app()
        client = TestClient(app)

        resp = client.post(
            "/widget/chat",
            json={
                "widget_id": str(widget_id),
                "session_id": "sess-1",
                "message": "hi",
            },
            headers={
                "X-Widget-Token": plaintext,
                "Origin": "http://evil.example.com",
            },
        )
        assert resp.status_code == 403, resp.text
        assert "allowed_origins" in resp.json()["detail"]
    finally:
        _delete_test_widgets(widget_prefix)
        _delete_test_users(email_prefix)
