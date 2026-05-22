"""Unit tests for recall_memory_tool (T023).

Four branches per the task list:

  1. Happy path — Alice has two memories; recall returns both, with the
     row whose embedding matches the query at position 0.
  2. Widget actor refusal — WidgetSession returns
     ``RecallMemoryError(kind="widget_actor_forbidden")``; no DB call.
  3. Embedding unreachable — the embed client raises
     ``ModelServerUnreachableError`` → ``embedding_unreachable``.
  4. Empty result — Alice has zero memories; returns
     ``RecallMemoryOk(hits=[])``.

Skips if Postgres / Vault are unreachable (pgvector is Postgres-only).
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from app.domain.conversation import AuthedUser, WidgetSession
from app.infra.database import get_engine
from app.infra.model_server_client import ModelServerUnreachableError
from app.infra.vault_client import VaultBootstrapError
from app.repositories import memory_repository
from app.services.tools import recall_memory_tool
from app.services.tools.recall_memory_tool import (
    RecallMemoryError,
    RecallMemoryOk,
    recall_memory,
)


def _ensure_postgres_reachable() -> None:
    try:
        with get_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
    except (OperationalError, VaultBootstrapError) as exc:
        pytest.skip(f"Postgres / Vault not reachable in this env: {exc}")


def _seed_user(label: str) -> uuid.UUID:
    user_id = uuid.uuid4()
    with get_engine().begin() as conn:
        conn.execute(
            text(
                "INSERT INTO users (id, email, hashed_password, is_active, "
                "is_superuser, is_verified, role) "
                "VALUES (:id, :email, 'placeholder', TRUE, FALSE, FALSE, 'user')"
            ),
            {
                "id": user_id,
                "email": f"pytest-rmem-{label}-{user_id.hex[:6]}@example.com",
            },
        )
    return user_id


def _cleanup_user(user_id: uuid.UUID) -> None:
    with get_engine().begin() as conn:
        # chatbot_memories FK is ON DELETE CASCADE.
        conn.execute(text("DELETE FROM users WHERE id = :id"), {"id": user_id})


def _unit_vec_slot(slot: int) -> list[float]:
    """768-D vector with a single 1.0 in ``slot``; cosine-orthogonal otherwise."""
    vec = [0.0] * 768
    vec[slot % 768] = 1.0
    return vec


@pytest.fixture
def alice() -> Iterator[uuid.UUID]:
    _ensure_postgres_reachable()
    user_id = _seed_user("alice")
    try:
        yield user_id
    finally:
        _cleanup_user(user_id)


# --- 1. happy path ---------------------------------------------------------


def test_recall_memory_returns_top_hit_for_matching_embedding(
    monkeypatch: pytest.MonkeyPatch, alice: uuid.UUID
) -> None:
    """Alice has two memories, one whose embedding matches the query slot.

    The matching row must land at position 0 of the returned hits.
    """
    # Memory 1: embedding sits in slot 7 — this is the one we'll match.
    target_id = uuid.uuid4()
    memory_repository.insert(
        memory_id=target_id,
        user_id=alice,
        conversation_id=uuid.uuid4(),
        content="Alice prefers Conventional Commits.",
        embedding=_unit_vec_slot(7),
    )
    # Memory 2: embedding sits in slot 200 — cosine-distant from slot 7.
    memory_repository.insert(
        memory_id=uuid.uuid4(),
        user_id=alice,
        conversation_id=uuid.uuid4(),
        content="Alice keeps weekends sacred.",
        embedding=_unit_vec_slot(200),
    )

    # The query embedding aligns with slot 7 → memory 1 wins.
    monkeypatch.setattr(
        recall_memory_tool,
        "_default_embed",
        lambda text, request_id="": _unit_vec_slot(7),
    )

    outcome = recall_memory(
        query="what commit style does Alice prefer?",
        actor=AuthedUser(user_id=alice, role="user"),
        k=5,
    )
    assert isinstance(outcome, RecallMemoryOk)
    assert len(outcome.hits) == 2
    assert outcome.hits[0].memory_id == target_id
    # Cosine similarity ≈ 1.0 for the matching row.
    assert outcome.hits[0].similarity > 0.99
    # The second hit is the cosine-orthogonal row → similarity ≈ 0.
    assert outcome.hits[1].similarity < 0.5


# --- 2. widget actor refusal -----------------------------------------------


def test_recall_memory_refuses_widget_actor_before_db(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _ensure_postgres_reachable()

    def _fail_embed(*args: object, **kwargs: object) -> list[float]:
        raise AssertionError("embed must not be called on widget refusal path")

    def _fail_query(**kwargs: object) -> list[object]:
        raise AssertionError("query_top_k must not be called on widget refusal path")

    monkeypatch.setattr(recall_memory_tool, "_default_embed", _fail_embed)
    monkeypatch.setattr(
        recall_memory_tool.memory_repository, "query_top_k", _fail_query
    )

    outcome = recall_memory(
        query="anything",
        actor=WidgetSession(widget_id=uuid.uuid4(), session_id="visitor-1"),
    )
    assert isinstance(outcome, RecallMemoryError)
    assert outcome.kind == "widget_actor_forbidden"


# --- 3. embedding unreachable ----------------------------------------------


def test_recall_memory_returns_embedding_unreachable_on_transport_error(
    monkeypatch: pytest.MonkeyPatch, alice: uuid.UUID
) -> None:
    def _boom(*args: object, **kwargs: object) -> list[float]:
        raise ModelServerUnreachableError("boom")

    monkeypatch.setattr(recall_memory_tool, "_default_embed", _boom)

    outcome = recall_memory(
        query="anything",
        actor=AuthedUser(user_id=alice, role="user"),
    )
    assert isinstance(outcome, RecallMemoryError)
    assert outcome.kind == "embedding_unreachable"


# --- 4. empty result --------------------------------------------------------


def test_recall_memory_returns_empty_hits_for_user_with_no_memories(
    monkeypatch: pytest.MonkeyPatch, alice: uuid.UUID
) -> None:
    monkeypatch.setattr(
        recall_memory_tool,
        "_default_embed",
        lambda text, request_id="": _unit_vec_slot(0),
    )

    outcome = recall_memory(
        query="anything",
        actor=AuthedUser(user_id=alice, role="user"),
    )
    assert isinstance(outcome, RecallMemoryOk)
    assert outcome.hits == []
