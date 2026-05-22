"""Migration 0003 round-trip: upgrade-then-downgrade-then-upgrade.

pgvector is Postgres-only — there is no SQLite substitute. The test connects
to the docker-compose Postgres via the existing ``app.infra.database`` engine
(same Vault-derived URL the rest of the suite uses); if Vault or Postgres
are unreachable the test is skipped, the same pattern other integration tests
in this repo follow.
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


def test_0003_migration_round_trip() -> None:
    """upgrade head → downgrade -1 → upgrade head leaves all chatbot tables in place.

    Tables verified: users, widgets, conversations, messages, chatbot_memories.
    Audit log evolution verified: actor_user_id / actor_widget_id / target_type
    / target_id columns exist after upgrade, are absent after downgrade.
    """
    _ensure_postgres_reachable()

    cfg = _alembic_config()

    # Park exactly on 0003 (not head) so this test stays insulated from
    # later additive revisions (e.g. 0004_chatbot_part2 in Part 2). We
    # downgrade first in case the suite already left us at a later head,
    # then upgrade to land on 0003 if we were below it.
    command.downgrade(cfg, "0003_chatbot")
    command.upgrade(cfg, "0003_chatbot")
    assert _current_revision() == "0003_chatbot"

    chatbot_tables = (
        "users",
        "widgets",
        "conversations",
        "messages",
        "chatbot_memories",
    )

    with get_engine().connect() as conn:
        for t in chatbot_tables:
            row = conn.execute(
                text(
                    "SELECT 1 FROM information_schema.tables "
                    "WHERE table_schema='public' AND table_name=:t"
                ),
                {"t": t},
            ).first()
            assert row is not None, f"{t} missing after upgrade"

        # audit_log evolution: new columns present.
        cols = {
            r[0]
            for r in conn.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema='public' AND table_name='audit_log'"
                )
            ).all()
        }
        for c in ("actor_user_id", "actor_widget_id", "target_type", "target_id"):
            assert c in cols, f"audit_log missing column {c} after upgrade"

        # IVFFlat index on chatbot_memories.
        idx = conn.execute(
            text(
                "SELECT indexname FROM pg_indexes "
                "WHERE schemaname='public' AND tablename='chatbot_memories'"
            )
        ).all()
        names = {r[0] for r in idx}
        assert "ix_chatbot_memories_embedding_ivfflat" in names
        assert "ix_chatbot_memories_user_created" in names

        # Active-token partial unique index on widgets.
        widx = {
            r[0]
            for r in conn.execute(
                text(
                    "SELECT indexname FROM pg_indexes "
                    "WHERE schemaname='public' AND tablename='widgets'"
                )
            ).all()
        }
        assert "ux_widgets_active_token" in widx

    # Downgrade one step → 0003 is gone.
    command.downgrade(cfg, "-1")
    assert _current_revision() == "0002_rag_chunks"

    with get_engine().connect() as conn:
        for t in chatbot_tables:
            row = conn.execute(
                text(
                    "SELECT 1 FROM information_schema.tables "
                    "WHERE table_schema='public' AND table_name=:t"
                ),
                {"t": t},
            ).first()
            assert row is None, f"{t} still present after downgrade"

        cols = {
            r[0]
            for r in conn.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema='public' AND table_name='audit_log'"
                )
            ).all()
        }
        for c in ("actor_user_id", "actor_widget_id", "target_type", "target_id"):
            assert c not in cols, f"audit_log still has column {c} after downgrade"

    # Re-upgrade so the rest of the suite finds the tables in place. We go
    # to head (not 0003) to restore whatever the suite expects.
    command.upgrade(cfg, "head")
