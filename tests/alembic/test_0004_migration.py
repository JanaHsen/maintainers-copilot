"""Migration 0004 round-trip: composite partial index on audit_log.

Postgres-only (partial indexes + the existing chatbot tables). Skips if
Postgres or Vault is unreachable, same pattern as ``test_0003_migration``.
"""

from __future__ import annotations

import pytest
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from alembic import command
from app.infra.database import get_engine
from app.infra.vault_client import VaultBootstrapError


def _ensure_postgres_reachable() -> None:
    try:
        with get_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
    except (OperationalError, VaultBootstrapError) as exc:
        pytest.skip(f"Postgres / Vault not reachable in this env: {exc}")


def _current_revision() -> str | None:
    with get_engine().connect() as conn:
        row = conn.execute(text("SELECT version_num FROM alembic_version")).first()
    return None if row is None else str(row[0])


def _alembic_config() -> Config:
    return Config("alembic.ini")


def _audit_log_index_names() -> set[str]:
    with get_engine().connect() as conn:
        rows = conn.execute(
            text(
                "SELECT indexname FROM pg_indexes "
                "WHERE schemaname='public' AND tablename='audit_log'"
            )
        ).all()
    return {r[0] for r in rows}


def test_0004_migration_round_trip() -> None:
    """upgrade head → downgrade -1 → upgrade head; index appears/disappears."""
    _ensure_postgres_reachable()

    cfg = _alembic_config()

    # Make sure we're at head before we begin.
    command.upgrade(cfg, "head")
    assert _current_revision() == "0004_chatbot_part2"
    assert "ix_audit_log_actor_action_time" in _audit_log_index_names()

    # Downgrade one step → 0004 is gone, index removed, 0003 still in place.
    command.downgrade(cfg, "-1")
    assert _current_revision() == "0003_chatbot"
    assert "ix_audit_log_actor_action_time" not in _audit_log_index_names()

    # Re-upgrade so the rest of the suite finds the index in place.
    command.upgrade(cfg, "head")
    assert _current_revision() == "0004_chatbot_part2"
    assert "ix_audit_log_actor_action_time" in _audit_log_index_names()
