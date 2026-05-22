"""chatbot foundations: users/widgets/conversations/messages/chatbot_memories + audit evolution

Revision ID: 0003_chatbot
Revises: 0002_rag_chunks
Create Date: 2026-05-22

Backs the Chatbot Part 1 slice. Five new tables for authenticated maintainers
(``users``), embeddable widgets (``widgets``), conversation history
(``conversations``, ``messages``), and long-term memory
(``chatbot_memories`` with a pgvector(768) column). The existing
``audit_log`` table evolves additively (research R3): new actor + target
columns, indices, and a REVOKE UPDATE/DELETE + GRANT INSERT, SELECT to make
the table append-only at the role level.

Order of upgrade (FK-resolution):
  1. ``users`` — root of references.
  2. ``widgets`` — FK to users.
  3. ``conversations`` — FK to users + widgets; actor-exclusivity CHECK.
  4. ``messages`` — FK to conversations; role + tool-consistency CHECKs.
  5. ``chatbot_memories`` — FK to users; vector(768) column + IVFFlat index.
  6. ``audit_log`` ALTER — additive columns + indices + privilege grant.

``downgrade()`` reverses everything in strict LIFO order (Rule 3 — every
schema change is reversible). The pgvector extension itself is not dropped;
0001_baseline owns it and 0002_rag_chunks needs it for ``rag_chunks``.

Rule 3 (storage). Research R3 (audit_log additive evolution). Research R4
(768-D embeddings, ``vector_cosine_ops``). Research R5 (sha256 hex token
hash). Spec FR-019 (conversation actor-exclusivity).
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0003_chatbot"
down_revision: str | None = "0002_rag_chunks"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. users -----------------------------------------------------------------
    op.execute(
        """
        CREATE TABLE users (
            id              UUID         PRIMARY KEY,
            email           VARCHAR(320) NOT NULL UNIQUE,
            hashed_password VARCHAR(1024) NOT NULL,
            is_active       BOOLEAN      NOT NULL DEFAULT TRUE,
            is_superuser    BOOLEAN      NOT NULL DEFAULT FALSE,
            is_verified     BOOLEAN      NOT NULL DEFAULT FALSE,
            role            VARCHAR(16)  NOT NULL DEFAULT 'user',
            created_at      TIMESTAMPTZ  NOT NULL DEFAULT now(),
            CONSTRAINT chk_users_role CHECK (role IN ('user', 'admin'))
        )
        """
    )
    op.execute("CREATE INDEX ix_users_email ON users (email)")
    op.execute("CREATE INDEX ix_users_role ON users (role)")

    # 2. widgets ---------------------------------------------------------------
    # host_token_hash is sha256 hex (R5). Partial unique index ensures no two
    # non-revoked widgets share the same hash; revoked widgets keep their hash
    # row for audit but are excluded from the active-token uniqueness check.
    op.execute(
        """
        CREATE TABLE widgets (
            id                UUID         PRIMARY KEY,
            name              VARCHAR(128) NOT NULL,
            host_token_hash   CHAR(64)     NOT NULL UNIQUE,
            allowed_origins   TEXT[]       NOT NULL DEFAULT '{}',
            owner_user_id     UUID         NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            created_at        TIMESTAMPTZ  NOT NULL DEFAULT now(),
            revoked_at        TIMESTAMPTZ  NULL
        )
        """
    )
    op.execute("CREATE INDEX ix_widgets_owner ON widgets (owner_user_id)")
    op.execute(
        "CREATE UNIQUE INDEX ux_widgets_active_token "
        "ON widgets (host_token_hash) WHERE revoked_at IS NULL"
    )

    # 3. conversations ---------------------------------------------------------
    # Actor-exclusivity (FR-019) enforced at the DB level so the invariant
    # survives any future router bypass.
    op.execute(
        """
        CREATE TABLE conversations (
            id              UUID        PRIMARY KEY,
            user_id         UUID        NULL REFERENCES users(id) ON DELETE CASCADE,
            widget_id       UUID        NULL REFERENCES widgets(id) ON DELETE CASCADE,
            session_id      TEXT        NULL,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            last_message_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT chk_conversations_actor_exclusive CHECK (
                (user_id IS NOT NULL AND widget_id IS NULL AND session_id IS NULL)
                OR
                (user_id IS NULL AND widget_id IS NOT NULL AND session_id IS NOT NULL)
            )
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_conversations_user_id "
        "ON conversations (user_id, last_message_at DESC)"
    )
    op.execute(
        "CREATE INDEX ix_conversations_widget_session "
        "ON conversations (widget_id, session_id)"
    )

    # 4. messages --------------------------------------------------------------
    # Two CHECKs: role enum + tool-column consistency.
    op.execute(
        """
        CREATE TABLE messages (
            id               UUID        PRIMARY KEY,
            conversation_id  UUID        NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
            role             VARCHAR(16) NOT NULL,
            content          TEXT        NOT NULL,
            tool_name        VARCHAR(64) NULL,
            tool_input       JSONB       NULL,
            tool_output      JSONB       NULL,
            created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT chk_messages_role CHECK (role IN ('user', 'assistant', 'tool')),
            CONSTRAINT chk_messages_tool_consistency CHECK (
                (role = 'tool' AND tool_name IS NOT NULL)
                OR
                (role <> 'tool'
                 AND tool_name IS NULL AND tool_input IS NULL AND tool_output IS NULL)
            )
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_messages_conversation_created "
        "ON messages (conversation_id, created_at)"
    )

    # 5. chatbot_memories ------------------------------------------------------
    # 768-D embedding to match BAAI/bge-base-en-v1.5 (research R4 — same
    # embedding service as the RAG slice). conversation_id is UUID NOT NULL but
    # NOT a foreign key: widget-actor conversation ids are still valid memory
    # provenance markers even after the widget conversation row is gone.
    op.execute(
        """
        CREATE TABLE chatbot_memories (
            id               UUID        PRIMARY KEY,
            user_id          UUID        NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            conversation_id  UUID        NOT NULL,
            content          TEXT        NOT NULL,
            embedding        vector(768) NOT NULL,
            source           VARCHAR(32) NOT NULL DEFAULT 'episodic',
            created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT chk_chatbot_memories_source CHECK (source IN ('episodic'))
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_chatbot_memories_user_created "
        "ON chatbot_memories (user_id, created_at DESC)"
    )
    # IVFFlat with cosine ops, partial WHERE user_id IS NOT NULL. lists=100 is
    # a reasonable default for small-to-medium per-user memory counts; revisit
    # if any single user blows past ~10k memories.
    op.execute(
        "CREATE INDEX ix_chatbot_memories_embedding_ivfflat "
        "ON chatbot_memories USING ivfflat (embedding vector_cosine_ops) "
        "WITH (lists = 100) WHERE user_id IS NOT NULL"
    )

    # 6. audit_log evolution (additive) ---------------------------------------
    # Keeps the existing columns from migration 0001 (actor_id, action, target,
    # timestamp, payload). New code writes to the new columns; the legacy
    # actor_id / target columns are left NULL by Part 1.
    op.execute(
        """
        ALTER TABLE audit_log
          ADD COLUMN actor_user_id   UUID NULL REFERENCES users(id)   ON DELETE SET NULL,
          ADD COLUMN actor_widget_id UUID NULL REFERENCES widgets(id) ON DELETE SET NULL,
          ADD COLUMN target_type     TEXT NULL,
          ADD COLUMN target_id       TEXT NULL
        """
    )
    op.execute(
        "CREATE INDEX ix_audit_log_actor_user "
        "ON audit_log (actor_user_id)"
    )
    op.execute(
        "CREATE INDEX ix_audit_log_actor_widget "
        "ON audit_log (actor_widget_id)"
    )
    # Append-only at the role level. Application role inherits PUBLIC's
    # privileges; UPDATE/DELETE attempts become a Postgres permission error
    # (research R3). INSERT + SELECT remain so the repository can still write
    # rows and operators can still read them.
    op.execute("REVOKE UPDATE, DELETE ON audit_log FROM PUBLIC")
    op.execute("GRANT INSERT, SELECT ON audit_log TO PUBLIC")


def downgrade() -> None:
    # Reverse-LIFO order: undo the audit_log evolution, then drop the new
    # tables in reverse FK order.

    # 6. audit_log evolution
    op.execute("REVOKE INSERT, SELECT ON audit_log FROM PUBLIC")
    op.execute("GRANT INSERT, SELECT, UPDATE, DELETE ON audit_log TO PUBLIC")
    op.execute("DROP INDEX IF EXISTS ix_audit_log_actor_widget")
    op.execute("DROP INDEX IF EXISTS ix_audit_log_actor_user")
    op.execute(
        """
        ALTER TABLE audit_log
          DROP COLUMN IF EXISTS target_id,
          DROP COLUMN IF EXISTS target_type,
          DROP COLUMN IF EXISTS actor_widget_id,
          DROP COLUMN IF EXISTS actor_user_id
        """
    )

    # 5. chatbot_memories
    op.execute("DROP INDEX IF EXISTS ix_chatbot_memories_embedding_ivfflat")
    op.execute("DROP INDEX IF EXISTS ix_chatbot_memories_user_created")
    op.execute("DROP TABLE IF EXISTS chatbot_memories")

    # 4. messages
    op.execute("DROP INDEX IF EXISTS ix_messages_conversation_created")
    op.execute("DROP TABLE IF EXISTS messages")

    # 3. conversations
    op.execute("DROP INDEX IF EXISTS ix_conversations_widget_session")
    op.execute("DROP INDEX IF EXISTS ix_conversations_user_id")
    op.execute("DROP TABLE IF EXISTS conversations")

    # 2. widgets
    op.execute("DROP INDEX IF EXISTS ux_widgets_active_token")
    op.execute("DROP INDEX IF EXISTS ix_widgets_owner")
    op.execute("DROP TABLE IF EXISTS widgets")

    # 1. users
    op.execute("DROP INDEX IF EXISTS ix_users_role")
    op.execute("DROP INDEX IF EXISTS ix_users_email")
    op.execute("DROP TABLE IF EXISTS users")
