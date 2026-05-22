"""audit_repository — append-only writes + immutability.

Covers:
  * happy path — one ``record(...)`` call lands exactly one row written to
    the new columns, with the legacy ``actor_id`` / ``target`` columns left
    NULL,
  * ValueError when both actor ids are set,
  * ValueError when neither actor id is set,
  * AuditLogImmutableError on ``update(...)`` / ``delete(...)``.

The GRANT/REVOKE at the Postgres role level (``REVOKE UPDATE, DELETE FROM
PUBLIC``) is **not** exercised here because the dev compose Postgres uses
the superuser role, which bypasses GRANT/REVOKE. That assertion belongs to
the integration suite (T026) where role isolation is set up. Documented
verbatim so the omission is intentional, not an oversight.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from app.infra.database import get_engine
from app.infra.vault_client import VaultBootstrapError
from app.repositories import audit_repository
from app.repositories.audit_repository import AuditLogImmutableError


def _ensure_postgres_reachable() -> None:
    try:
        with get_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
    except (OperationalError, VaultBootstrapError) as exc:
        pytest.skip(f"Postgres / Vault not reachable in this env: {exc}")


def _seed_user() -> uuid.UUID:
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
                "email": f"pytest-audit-{user_id.hex[:8]}@example.com",
            },
        )
    return user_id


def _cleanup_user(user_id: uuid.UUID) -> None:
    # ON DELETE SET NULL on audit_log.actor_user_id so the row stays.
    with get_engine().begin() as conn:
        conn.execute(text("DELETE FROM users WHERE id = :id"), {"id": user_id})


def test_record_happy_path_writes_to_new_columns() -> None:
    """Exactly one audit_log row lands; legacy columns are NULL."""
    _ensure_postgres_reachable()
    user_id = _seed_user()
    target_id = uuid.uuid4().hex
    try:
        before = _count_rows_for_target(target_id)
        audit_repository.record(
            action="memory.write",
            target_type="memory",
            target_id=target_id,
            payload={"content_bytes": 42, "source": "episodic"},
            actor_user_id=user_id,
        )
        after = _count_rows_for_target(target_id)
        assert after == before + 1

        with get_engine().connect() as conn:
            row = conn.execute(
                text(
                    "SELECT action, target_type, target_id, payload, "
                    "actor_user_id, actor_widget_id, actor_id, target "
                    "FROM audit_log WHERE target_id = :tid"
                ),
                {"tid": target_id},
            ).first()
        assert row is not None
        assert row.action == "memory.write"
        assert row.target_type == "memory"
        assert row.target_id == target_id
        assert row.payload == {"content_bytes": 42, "source": "episodic"}
        assert row.actor_user_id == user_id
        assert row.actor_widget_id is None
        # Legacy columns from migration 0001 are not written by Part 1 code.
        assert row.actor_id is None
        assert row.target is None
    finally:
        _cleanup_user(user_id)
        _delete_test_rows(target_id)


def test_record_rejects_both_actor_ids_set() -> None:
    """Setting both actor_user_id and actor_widget_id is invalid (research R3)."""
    _ensure_postgres_reachable()
    with pytest.raises(ValueError, match="both were provided"):
        audit_repository.record(
            action="memory.write",
            target_type="memory",
            target_id="x",
            payload=None,
            actor_user_id=uuid.uuid4(),
            actor_widget_id=uuid.uuid4(),
        )


def test_record_rejects_neither_actor_id_set() -> None:
    _ensure_postgres_reachable()
    with pytest.raises(ValueError, match="neither was provided"):
        audit_repository.record(
            action="memory.write",
            target_type="memory",
            target_id="x",
            payload=None,
        )


def test_update_raises_immutable_error() -> None:
    with pytest.raises(AuditLogImmutableError):
        audit_repository.update()


def test_delete_raises_immutable_error() -> None:
    with pytest.raises(AuditLogImmutableError):
        audit_repository.delete()


# --- helpers ---------------------------------------------------------------


def _count_rows_for_target(target_id: str) -> int:
    with get_engine().connect() as conn:
        row = conn.execute(
            text("SELECT count(*) AS c FROM audit_log WHERE target_id = :tid"),
            {"tid": target_id},
        ).first()
    assert row is not None
    return int(row.c)


def _delete_test_rows(target_id: str) -> None:
    """Clean up via the engine, NOT via the repository (which forbids delete).

    The repository's DELETE prohibition is application-level only; the engine
    role used by the test still has the superuser DELETE privilege so we can
    keep the table tidy between tests.
    """
    with get_engine().begin() as conn:
        conn.execute(
            text("DELETE FROM audit_log WHERE target_id = :tid"),
            {"tid": target_id},
        )
