"""Integration test: widget actor refusal at the primitive layer
(T025, US3 SC-004).

A real widget — created by `widget_repository.create` — is wrapped in a
`WidgetSession` and used as the actor for both memory primitives:

  * `write_memory(actor=WidgetSession(...))` returns
    ``WriteMemoryError(kind="widget_actor_forbidden")``; the
    ``chatbot_memories`` rowcount is unchanged across the call.
  * `recall_memory(actor=WidgetSession(...))` returns
    ``RecallMemoryError(kind="widget_actor_forbidden")``; the
    SQL recall path is never reached — proven by monkeypatching
    `memory_repository.query_top_k` to raise if called.

Skips cleanly if Postgres / Vault are unreachable.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from app.domain.conversation import WidgetSession
from app.infra.database import get_engine
from app.infra.vault_client import VaultBootstrapError
from app.repositories import widget_repository
from app.services.tools import recall_memory_tool, write_memory_tool
from app.services.tools.recall_memory_tool import RecallMemoryError, recall_memory
from app.services.tools.write_memory_tool import WriteMemoryError, write_memory


def _ensure_postgres_reachable() -> None:
    try:
        with get_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
    except (OperationalError, VaultBootstrapError) as exc:
        pytest.skip(f"Postgres / Vault not reachable in this env: {exc}")


def _seed_admin() -> uuid.UUID:
    user_id = uuid.uuid4()
    with get_engine().begin() as conn:
        conn.execute(
            text(
                "INSERT INTO users (id, email, hashed_password, is_active, "
                "is_superuser, is_verified, role) "
                "VALUES (:id, :email, 'placeholder', TRUE, FALSE, FALSE, 'admin')"
            ),
            {
                "id": user_id,
                "email": f"pytest-widget-admin-{user_id.hex[:6]}@example.com",
            },
        )
    return user_id


def _cleanup_user(user_id: uuid.UUID) -> None:
    with get_engine().begin() as conn:
        conn.execute(text("DELETE FROM users WHERE id = :id"), {"id": user_id})


def _cleanup_widget(widget_id: uuid.UUID) -> None:
    with get_engine().begin() as conn:
        conn.execute(text("DELETE FROM widgets WHERE id = :id"), {"id": widget_id})


def _count_memories() -> int:
    with get_engine().connect() as conn:
        row = conn.execute(
            text("SELECT count(*) AS c FROM chatbot_memories")
        ).first()
    assert row is not None
    return int(row.c)


@pytest.fixture
def widget_session() -> Iterator[WidgetSession]:
    _ensure_postgres_reachable()
    admin = _seed_admin()
    widget_id, _plaintext = widget_repository.create(
        name="demo",
        allowed_origins=["http://localhost:8080"],
        owner_user_id=admin,
    )
    try:
        yield WidgetSession(widget_id=widget_id, session_id="visitor-1")
    finally:
        # widgets table audit rows (if any) are swept by the audit_writes test.
        _cleanup_widget(widget_id)
        _cleanup_user(admin)


def test_widget_actor_refused_at_write_memory_with_no_db_change(
    monkeypatch: pytest.MonkeyPatch,
    widget_session: WidgetSession,
) -> None:
    """write_memory(WidgetSession) → widget_actor_forbidden; rowcount unchanged."""
    # Guarantee no DB write by failing the embed alias if reached.
    def _fail_embed(*args: object, **kwargs: object) -> list[float]:
        raise AssertionError("embed must not be called on widget refusal path")

    monkeypatch.setattr(write_memory_tool, "_default_embed", _fail_embed)

    before = _count_memories()
    outcome = write_memory(
        content="anything",
        actor=widget_session,
        conversation_id=uuid.uuid4(),
    )
    assert isinstance(outcome, WriteMemoryError)
    assert outcome.kind == "widget_actor_forbidden"
    assert _count_memories() == before


def test_widget_actor_refused_at_recall_memory_with_no_sql_recall(
    monkeypatch: pytest.MonkeyPatch,
    widget_session: WidgetSession,
) -> None:
    """recall_memory(WidgetSession) → widget_actor_forbidden; query_top_k never called."""

    def _fail_query(**kwargs: object) -> list[object]:
        raise AssertionError("query_top_k must not be called on widget refusal path")

    def _fail_embed(*args: object, **kwargs: object) -> list[float]:
        raise AssertionError("embed must not be called on widget refusal path")

    monkeypatch.setattr(recall_memory_tool, "_default_embed", _fail_embed)
    monkeypatch.setattr(
        recall_memory_tool.memory_repository, "query_top_k", _fail_query
    )

    outcome = recall_memory(query="anything", actor=widget_session)
    assert isinstance(outcome, RecallMemoryError)
    assert outcome.kind == "widget_actor_forbidden"
