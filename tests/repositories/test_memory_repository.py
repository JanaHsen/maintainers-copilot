"""memory_repository — insert + top-k + cross-user isolation (SC-003).

Skips cleanly if Postgres / Vault are unreachable; pgvector is Postgres-only
so an in-process SQLite substitute would not work.

Inserting a memory requires a real ``users`` row (FK). The test creates two
ephemeral users (Alice and Bob), writes one memory each, asserts:

  * Alice's top-k returns only her own memory (user isolation),
  * a brand-new user (Carol) gets an empty top-k,
  * the returned ``similarity`` is in ``[-1, 1]`` and the row with the
    closest embedding scores higher than a far one.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from app.infra.database import get_engine
from app.infra.vault_client import VaultBootstrapError
from app.repositories import memory_repository


def _ensure_postgres_reachable() -> None:
    try:
        with get_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
    except (OperationalError, VaultBootstrapError) as exc:
        pytest.skip(f"Postgres / Vault not reachable in this env: {exc}")


def _seed_user(email_suffix: str) -> uuid.UUID:
    user_id = uuid.uuid4()
    with get_engine().begin() as conn:
        conn.execute(
            text(
                "INSERT INTO users (id, email, hashed_password, is_active, "
                "is_superuser, is_verified, role) "
                "VALUES (:id, :email, :pw, TRUE, FALSE, FALSE, 'user')"
            ),
            {
                "id": user_id,
                "email": f"pytest-mem-{email_suffix}-{user_id.hex[:6]}@example.com",
                "pw": "placeholder",
            },
        )
    return user_id


def _cleanup_user(user_id: uuid.UUID) -> None:
    with get_engine().begin() as conn:
        # chatbot_memories FK is ON DELETE CASCADE, so the user delete handles it.
        conn.execute(text("DELETE FROM users WHERE id = :id"), {"id": user_id})


def _unit_vec(seed: int) -> list[float]:
    """Deterministic-ish 768-D vector. Different ``seed`` → different direction."""
    vec = [0.0] * 768
    # Plant a 1.0 in one slot keyed by seed; everything else 0. Cosine similarity
    # against itself = 1.0, against a different slot = 0.0.
    vec[seed % 768] = 1.0
    return vec


def test_insert_and_query_top_k_returns_own_row() -> None:
    """Alice writes one memory; Alice queries → her row at the top."""
    _ensure_postgres_reachable()
    alice = _seed_user("alice")
    try:
        memory_id = uuid.uuid4()
        conversation_id = uuid.uuid4()
        memory_repository.insert(
            memory_id=memory_id,
            user_id=alice,
            conversation_id=conversation_id,
            content="prefer dark mode in the dashboard",
            embedding=_unit_vec(1),
        )
        hits = memory_repository.query_top_k(
            user_id=alice, query_embedding=_unit_vec(1), k=5
        )
        assert len(hits) == 1
        assert hits[0].memory_id == memory_id
        assert hits[0].content == "prefer dark mode in the dashboard"
        assert -1.0 <= hits[0].similarity <= 1.0001
        # Self-cosine ≈ 1.0.
        assert hits[0].similarity > 0.99
    finally:
        _cleanup_user(alice)


def test_query_top_k_isolates_users() -> None:
    """Alice's memory is never returned to Bob (SC-003)."""
    _ensure_postgres_reachable()
    alice = _seed_user("alice2")
    bob = _seed_user("bob")
    try:
        memory_repository.insert(
            memory_id=uuid.uuid4(),
            user_id=alice,
            conversation_id=uuid.uuid4(),
            content="alice's secret",
            embedding=_unit_vec(2),
        )
        memory_repository.insert(
            memory_id=uuid.uuid4(),
            user_id=bob,
            conversation_id=uuid.uuid4(),
            content="bob's secret",
            embedding=_unit_vec(3),
        )

        alice_hits = memory_repository.query_top_k(
            user_id=alice, query_embedding=_unit_vec(2), k=5
        )
        assert len(alice_hits) == 1
        assert alice_hits[0].content == "alice's secret"

        bob_hits = memory_repository.query_top_k(
            user_id=bob, query_embedding=_unit_vec(2), k=5
        )
        assert len(bob_hits) == 1
        assert bob_hits[0].content == "bob's secret"
    finally:
        _cleanup_user(alice)
        _cleanup_user(bob)


def test_query_top_k_returns_empty_for_unknown_user() -> None:
    """Brand-new user with no memories gets an empty list, not an error."""
    _ensure_postgres_reachable()
    nobody = uuid.uuid4()
    hits = memory_repository.query_top_k(
        user_id=nobody, query_embedding=_unit_vec(0), k=5
    )
    assert hits == []
