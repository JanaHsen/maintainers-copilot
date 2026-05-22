"""Integration test: cross-conversation memory recall + cross-user isolation
+ redaction-at-persistence (T024, US2 SC-002 + SC-003).

The smoking gun for User Story 2:

  1. Alice writes a memory in conversation A.
  2. Alice — in a DIFFERENT conversation B — calls `recall_memory` and gets
     her memory back at the top. (SC-002: memory persists across
     conversations for the same user.)
  3. Bob — recalling the same query as Alice — gets ZERO of Alice's
     memories. (SC-003: cross-account isolation.)
  4. A write whose content contains `sk-ant-AAAA0000000000000` and
     `alice@example.com` lands in `chatbot_memories.content` with neither
     literal present and both `[REDACTED]` / `[REDACTED_EMAIL]`
     placeholders present (research R6).

Skips cleanly if Postgres / Vault are unreachable. The embed alias is
monkeypatched to a fixed vector so the recall ordering is deterministic;
the semantic-relevance ordering test lives in T023.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from app.domain.conversation import AuthedUser
from app.infra.database import get_engine
from app.infra.vault_client import VaultBootstrapError
from app.services.tools import recall_memory_tool, write_memory_tool
from app.services.tools.recall_memory_tool import RecallMemoryOk, recall_memory
from app.services.tools.write_memory_tool import WriteMemoryOk, write_memory


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
                "email": f"pytest-x-{label}-{user_id.hex[:6]}@example.com",
            },
        )
    return user_id


def _cleanup_user(user_id: uuid.UUID) -> None:
    # chatbot_memories.user_id ON DELETE CASCADE; the cascade also leaves
    # audit_log rows with actor_user_id SET NULL but a NOT NULL target_id
    # still pointing at the dropped memory's id (string copy). We sweep
    # those by target_id at the end of each test.
    with get_engine().begin() as conn:
        conn.execute(text("DELETE FROM users WHERE id = :id"), {"id": user_id})


def _delete_audit_rows_for_target(target_id: str) -> None:
    with get_engine().begin() as conn:
        conn.execute(
            text("DELETE FROM audit_log WHERE target_id = :tid"),
            {"tid": target_id},
        )


@pytest.fixture
def alice_and_bob() -> Iterator[tuple[uuid.UUID, uuid.UUID]]:
    _ensure_postgres_reachable()
    alice = _seed_user("alice")
    bob = _seed_user("bob")
    try:
        yield alice, bob
    finally:
        _cleanup_user(alice)
        _cleanup_user(bob)


def test_memory_written_in_conversation_a_is_recalled_in_conversation_b(
    monkeypatch: pytest.MonkeyPatch,
    alice_and_bob: tuple[uuid.UUID, uuid.UUID],
) -> None:
    """SC-002 + SC-003 in one flow.

    Strategy: monkeypatch the embed alias on BOTH tool modules to return the
    same fixed vector for any input. That makes recall against any query
    deterministically pull the stored memory back — proving the cross-
    conversation visibility (SC-002). Bob's recall using the same vector
    still gets nothing because the SQL ``WHERE user_id = :user_id`` clause
    in `memory_repository.query_top_k` scopes by user (SC-003).
    """
    alice, bob = alice_and_bob
    fixed_vec = [0.1] * 768

    monkeypatch.setattr(
        write_memory_tool,
        "_default_embed",
        lambda text, request_id="": fixed_vec,
    )
    monkeypatch.setattr(
        recall_memory_tool,
        "_default_embed",
        lambda text, request_id="": fixed_vec,
    )

    conversation_a = uuid.uuid4()
    conversation_b = uuid.uuid4()  # noqa: F841 — used to document the scenario

    # 1. Alice writes a memory in conversation A.
    write_outcome = write_memory(
        content="Alice prefers Conventional Commits.",
        actor=AuthedUser(user_id=alice, role="user"),
        conversation_id=conversation_a,
    )
    assert isinstance(write_outcome, WriteMemoryOk)
    written_id = write_outcome.memory_id
    target_ids_to_cleanup = [str(written_id)]

    try:
        # 2. Alice, in conversation B (different uuid), recalls — top hit is
        #    the memory written in conversation A.
        alice_recall = recall_memory(
            query="what commit style does Alice prefer?",
            actor=AuthedUser(user_id=alice, role="user"),
        )
        assert isinstance(alice_recall, RecallMemoryOk)
        assert len(alice_recall.hits) >= 1
        top = alice_recall.hits[0]
        assert top.memory_id == written_id
        # Content was redacted-for-persistence but contained no secrets,
        # so the prefix survives untouched.
        assert top.content.startswith("Alice prefers Conventional Commits")

        # 3. Bob recalls the same query → ZERO of Alice's memories
        #    (cross-account isolation, SC-003).
        bob_recall = recall_memory(
            query="what commit style?",
            actor=AuthedUser(user_id=bob, role="user"),
        )
        assert isinstance(bob_recall, RecallMemoryOk)
        assert bob_recall.hits == []

        # 4. Redaction-at-persistence: a content blob with sk-ant-… and an
        #    email lands in chatbot_memories with neither literal present
        #    and both placeholders present.
        redact_outcome = write_memory(
            content=(
                "Use sk-ant-AAAA0000000000000 to log in. "
                "Reach alice@example.com."
            ),
            actor=AuthedUser(user_id=alice, role="user"),
            conversation_id=conversation_a,
        )
        assert isinstance(redact_outcome, WriteMemoryOk)
        target_ids_to_cleanup.append(str(redact_outcome.memory_id))

        with get_engine().connect() as conn:
            row = conn.execute(
                text("SELECT content FROM chatbot_memories WHERE id = :id"),
                {"id": redact_outcome.memory_id},
            ).first()
        assert row is not None
        persisted = row.content
        assert "sk-ant-AAAA0000000000000" not in persisted
        assert "alice@example.com" not in persisted
        assert "[REDACTED]" in persisted
        assert "[REDACTED_EMAIL]" in persisted
    finally:
        for tid in target_ids_to_cleanup:
            _delete_audit_rows_for_target(tid)
