"""Unit tests for write_memory_tool (T022).

Six branches per the contract in
``specs/002-chatbot-part1-foundations/contracts/memory-tools.md`` and the
task list:

  1. Happy path — Alice writes one memory, one row lands in ``chatbot_memories``
     and one matching ``audit_log`` row lands (action=``memory.write``,
     actor_user_id=Alice, target_id=memory_id).
  2. Widget actor refusal — ``WidgetSession`` returns
     ``WriteMemoryError(kind="widget_actor_forbidden")``; ``chatbot_memories``
     and ``audit_log`` rowcounts are unchanged.
  3. Embedding unreachable — the embed client raises
     ``ModelServerUnreachableError`` → ``embedding_unreachable``; no DB write.
  4. Embedding timeout — raises ``ModelServerTimeoutError`` → ``embedding_timeout``.
  5. Audit-write rollback — the audit insert raises; the memory row must NOT
     persist (single-transaction atomicity per FR-021).
  6. Redaction-before-persistence — content with ``sk-ant-…`` and an email
     lands in ``chatbot_memories.content`` with both literals absent and the
     ``[REDACTED]`` / ``[REDACTED_EMAIL]`` placeholders present.

Skips if Postgres / Vault are unreachable (same pattern Phase B uses) —
pgvector is Postgres-only so no in-process substitute exists.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from app.domain.conversation import AuthedUser, WidgetSession
from app.infra.database import get_engine
from app.infra.model_server_client import (
    ModelServerTimeoutError,
    ModelServerUnreachableError,
)
from app.infra.vault_client import VaultBootstrapError
from app.services.tools import write_memory_tool
from app.services.tools.write_memory_tool import (
    WriteMemoryError,
    WriteMemoryOk,
    write_memory,
)

# --- skip guard + helpers --------------------------------------------------


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
                "email": f"pytest-wmem-{label}-{user_id.hex[:6]}@example.com",
            },
        )
    return user_id


def _cleanup_user(user_id: uuid.UUID) -> None:
    with get_engine().begin() as conn:
        # chatbot_memories FK is ON DELETE CASCADE; audit_log.actor_user_id is
        # ON DELETE SET NULL so audit rows survive but are cleaned by
        # _delete_audit_rows_for_target below.
        conn.execute(text("DELETE FROM users WHERE id = :id"), {"id": user_id})


def _delete_audit_rows_for_target(target_id: str) -> None:
    with get_engine().begin() as conn:
        conn.execute(
            text("DELETE FROM audit_log WHERE target_id = :tid"),
            {"tid": target_id},
        )


def _count_memories_for_user(user_id: uuid.UUID) -> int:
    with get_engine().connect() as conn:
        row = conn.execute(
            text("SELECT count(*) AS c FROM chatbot_memories WHERE user_id = :uid"),
            {"uid": user_id},
        ).first()
    assert row is not None
    return int(row.c)


def _count_audit_rows_for_user(user_id: uuid.UUID) -> int:
    with get_engine().connect() as conn:
        row = conn.execute(
            text(
                "SELECT count(*) AS c FROM audit_log WHERE actor_user_id = :uid"
            ),
            {"uid": user_id},
        ).first()
    assert row is not None
    return int(row.c)


# --- fixtures --------------------------------------------------------------


@pytest.fixture
def alice() -> Iterator[uuid.UUID]:
    _ensure_postgres_reachable()
    user_id = _seed_user("alice")
    try:
        yield user_id
    finally:
        _cleanup_user(user_id)


# --- 1. happy path ---------------------------------------------------------


def test_write_memory_happy_path_persists_memory_and_audit_row(
    monkeypatch: pytest.MonkeyPatch, alice: uuid.UUID
) -> None:
    monkeypatch.setattr(
        write_memory_tool, "_default_embed", lambda text, request_id="": [0.1] * 768
    )

    memories_before = _count_memories_for_user(alice)
    audit_before = _count_audit_rows_for_user(alice)

    outcome = write_memory(
        content="hello world",
        actor=AuthedUser(user_id=alice, role="user"),
        conversation_id=uuid.uuid4(),
    )

    assert isinstance(outcome, WriteMemoryOk)
    target_id = str(outcome.memory_id)
    try:
        assert _count_memories_for_user(alice) == memories_before + 1
        assert _count_audit_rows_for_user(alice) == audit_before + 1

        with get_engine().connect() as conn:
            row = conn.execute(
                text(
                    "SELECT action, target_type, target_id, actor_user_id "
                    "FROM audit_log WHERE target_id = :tid"
                ),
                {"tid": target_id},
            ).first()
        assert row is not None
        assert row.action == "memory.write"
        assert row.target_type == "memory"
        assert row.target_id == target_id
        assert row.actor_user_id == alice
    finally:
        _delete_audit_rows_for_target(target_id)


# --- 2. widget actor refusal -----------------------------------------------


def test_write_memory_refuses_widget_actor_without_any_side_effects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _ensure_postgres_reachable()
    # If the embed alias gets called, the test fails — widget refusal must
    # short-circuit before any side effect.
    def _fail_embed(*args: object, **kwargs: object) -> list[float]:
        raise AssertionError("embed must not be called on widget refusal path")

    monkeypatch.setattr(write_memory_tool, "_default_embed", _fail_embed)

    with get_engine().connect() as conn:
        mem_before = int(
            conn.execute(text("SELECT count(*) AS c FROM chatbot_memories")).scalar()
            or 0
        )
        audit_before = int(
            conn.execute(text("SELECT count(*) AS c FROM audit_log")).scalar() or 0
        )

    outcome = write_memory(
        content="anything",
        actor=WidgetSession(widget_id=uuid.uuid4(), session_id="visitor-1"),
        conversation_id=uuid.uuid4(),
    )

    assert isinstance(outcome, WriteMemoryError)
    assert outcome.kind == "widget_actor_forbidden"

    with get_engine().connect() as conn:
        mem_after = int(
            conn.execute(text("SELECT count(*) AS c FROM chatbot_memories")).scalar()
            or 0
        )
        audit_after = int(
            conn.execute(text("SELECT count(*) AS c FROM audit_log")).scalar() or 0
        )
    assert mem_after == mem_before
    assert audit_after == audit_before


# --- 3. embedding unreachable ----------------------------------------------


def test_write_memory_returns_embedding_unreachable_on_transport_error(
    monkeypatch: pytest.MonkeyPatch, alice: uuid.UUID
) -> None:
    def _boom(*args: object, **kwargs: object) -> list[float]:
        raise ModelServerUnreachableError("boom")

    monkeypatch.setattr(write_memory_tool, "_default_embed", _boom)

    memories_before = _count_memories_for_user(alice)
    outcome = write_memory(
        content="hello",
        actor=AuthedUser(user_id=alice, role="user"),
        conversation_id=uuid.uuid4(),
    )
    assert isinstance(outcome, WriteMemoryError)
    assert outcome.kind == "embedding_unreachable"
    assert _count_memories_for_user(alice) == memories_before


# --- 4. embedding timeout --------------------------------------------------


def test_write_memory_returns_embedding_timeout(
    monkeypatch: pytest.MonkeyPatch, alice: uuid.UUID
) -> None:
    def _boom(*args: object, **kwargs: object) -> list[float]:
        raise ModelServerTimeoutError("read timeout")

    monkeypatch.setattr(write_memory_tool, "_default_embed", _boom)

    memories_before = _count_memories_for_user(alice)
    outcome = write_memory(
        content="hello",
        actor=AuthedUser(user_id=alice, role="user"),
        conversation_id=uuid.uuid4(),
    )
    assert isinstance(outcome, WriteMemoryError)
    assert outcome.kind == "embedding_timeout"
    assert _count_memories_for_user(alice) == memories_before


# --- 5. audit-write rollback -----------------------------------------------


def test_write_memory_rolls_back_memory_row_when_audit_insert_fails(
    monkeypatch: pytest.MonkeyPatch, alice: uuid.UUID
) -> None:
    """The memory + audit inserts share one transaction; an audit failure must
    leave ``chatbot_memories`` unchanged (FR-021). The implementation raises a
    private ``_AuditFailedError`` inside the ``with get_engine().begin()``
    block, which SQLAlchemy rolls back before re-raising to the outer caller.
    """
    monkeypatch.setattr(
        write_memory_tool, "_default_embed", lambda text, request_id="": [0.1] * 768
    )

    # Force the audit insert to blow up. We patch the attribute the tool
    # module imports (``audit_repository.record``) so the patch targets the
    # exact symbol the tool calls.
    def _record_boom(**kwargs: object) -> None:
        raise RuntimeError("audit boom")

    monkeypatch.setattr(
        write_memory_tool.audit_repository, "record", _record_boom
    )

    memories_before = _count_memories_for_user(alice)
    outcome = write_memory(
        content="this must roll back",
        actor=AuthedUser(user_id=alice, role="user"),
        conversation_id=uuid.uuid4(),
    )
    assert isinstance(outcome, WriteMemoryError)
    assert outcome.kind == "audit_failed"
    # Transaction rolled back: no new memory row.
    assert _count_memories_for_user(alice) == memories_before


# --- 6. redaction-before-persistence ---------------------------------------


def test_write_memory_redacts_secrets_and_pii_before_persistence(
    monkeypatch: pytest.MonkeyPatch, alice: uuid.UUID
) -> None:
    monkeypatch.setattr(
        write_memory_tool, "_default_embed", lambda text, request_id="": [0.1] * 768
    )

    raw = "Use sk-ant-AAAA0000000000000 to log in. Reach alice@example.com."
    outcome = write_memory(
        content=raw,
        actor=AuthedUser(user_id=alice, role="user"),
        conversation_id=uuid.uuid4(),
    )
    assert isinstance(outcome, WriteMemoryOk)
    target_id = str(outcome.memory_id)
    try:
        with get_engine().connect() as conn:
            row = conn.execute(
                text("SELECT content FROM chatbot_memories WHERE id = :id"),
                {"id": outcome.memory_id},
            ).first()
        assert row is not None
        persisted = row.content
        assert "sk-ant-AAAA0000000000000" not in persisted
        assert "alice@example.com" not in persisted
        assert "[REDACTED]" in persisted
        assert "[REDACTED_EMAIL]" in persisted
    finally:
        _delete_audit_rows_for_target(target_id)
