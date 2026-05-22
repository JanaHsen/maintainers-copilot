# Phase 1 — Data Model: Chatbot Part 1 Foundations

Single Alembic migration `alembic/versions/0003_chatbot.py` (Rule 3). All tables additive; `audit_log` evolves additively per R3.

---

## 1. `users`

fastapi-users-managed; this Part owns the table creation.

```sql
CREATE TABLE users (
  id              UUID PRIMARY KEY,
  email           VARCHAR(320) NOT NULL UNIQUE,
  hashed_password VARCHAR(1024) NOT NULL,
  is_active       BOOLEAN NOT NULL DEFAULT TRUE,
  is_superuser    BOOLEAN NOT NULL DEFAULT FALSE,
  is_verified     BOOLEAN NOT NULL DEFAULT FALSE,
  role            VARCHAR(16) NOT NULL DEFAULT 'user',
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX ix_users_email ON users (email);
CREATE INDEX ix_users_role ON users (role);

ALTER TABLE users ADD CONSTRAINT chk_users_role
  CHECK (role IN ('user', 'admin'));
```

**Notes.**
- `id UUID`, generated client-side by fastapi-users (`uuid.uuid4()`).
- `role`: `'user'` (default) or `'admin'`. `'admin'` ≠ `is_superuser`; superuser is the fastapi-users internal flag (used for bootstrap), `role` is the application-level scope check (`require_admin`).
- Bootstrap: a `scripts/seed_admin.py` is **out of scope** for Part 1 (operator promotes a user manually via direct UPDATE in Part 1; Part 3 ships the admin panel that exposes role changes).

---

## 2. `chatbot_memories`

Long-term memory; pgvector(768) column.

```sql
CREATE TABLE chatbot_memories (
  id               UUID PRIMARY KEY,
  user_id          UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  conversation_id  UUID NOT NULL,
  content          TEXT NOT NULL,
  embedding        vector(768) NOT NULL,
  source           VARCHAR(32) NOT NULL DEFAULT 'episodic',
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX ix_chatbot_memories_user_created
  ON chatbot_memories (user_id, created_at DESC);

CREATE INDEX ix_chatbot_memories_embedding_ivfflat
  ON chatbot_memories USING ivfflat (embedding vector_cosine_ops)
  WITH (lists = 100)
  WHERE user_id IS NOT NULL;

ALTER TABLE chatbot_memories ADD CONSTRAINT chk_chatbot_memories_source
  CHECK (source IN ('episodic'));
```

**Notes.**
- 768 dimensions match `BAAI/bge-base-en-v1.5` and the RAG slice's vector column (R4).
- The IVFFlat partial index `WHERE user_id IS NOT NULL` matches the brief; in practice `user_id` is `NOT NULL` so the partial is a forward-compat hedge.
- IVFFlat `lists = 100` is the pgvector default-ish value for small-to-medium corpora; revisit if memory count grows past 10k per user.
- The CHECK constraint allows only `'episodic'` for Part 1. Future memory types (`'semantic'`, `'procedural'`) will be added by a future migration that loosens the CHECK.
- `conversation_id UUID NOT NULL` is **not** a foreign key — see note under `conversations` about widget conversation_ids. Application-layer validation prevents orphans.

---

## 3. `conversations`

```sql
CREATE TABLE conversations (
  id              UUID PRIMARY KEY,
  user_id         UUID NULL REFERENCES users(id) ON DELETE CASCADE,
  widget_id       UUID NULL REFERENCES widgets(id) ON DELETE CASCADE,
  session_id      TEXT NULL,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_message_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX ix_conversations_user_id ON conversations (user_id, last_message_at DESC);
CREATE INDEX ix_conversations_widget_session
  ON conversations (widget_id, session_id);

ALTER TABLE conversations ADD CONSTRAINT chk_conversations_actor_exclusive
  CHECK (
    (user_id IS NOT NULL AND widget_id IS NULL AND session_id IS NULL)
    OR
    (user_id IS NULL AND widget_id IS NOT NULL AND session_id IS NOT NULL)
  );
```

**Notes.**
- The CHECK enforces the spec's actor-exclusivity rule (FR-019). Database-level so the invariant survives any future router bypass.
- `widget_id` is a foreign key (the widget might be revoked → SET NULL would orphan, CASCADE removes the conversation row). Picking CASCADE intentionally: a revoked widget's conversations are no longer accessible anyway.

---

## 4. `messages`

```sql
CREATE TABLE messages (
  id               UUID PRIMARY KEY,
  conversation_id  UUID NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
  role             VARCHAR(16) NOT NULL,
  content          TEXT NOT NULL,
  tool_name        VARCHAR(64) NULL,
  tool_input       JSONB NULL,
  tool_output      JSONB NULL,
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX ix_messages_conversation_created
  ON messages (conversation_id, created_at);

ALTER TABLE messages ADD CONSTRAINT chk_messages_role
  CHECK (role IN ('user', 'assistant', 'tool'));

ALTER TABLE messages ADD CONSTRAINT chk_messages_tool_consistency
  CHECK (
    (role = 'tool' AND tool_name IS NOT NULL)
    OR
    (role <> 'tool' AND tool_name IS NULL AND tool_input IS NULL AND tool_output IS NULL)
  );
```

**Notes.**
- Two CHECK constraints: valid role; if the role is `'tool'` then `tool_name` is required, otherwise the three tool columns are all NULL. Keeps the table self-consistent.
- The long-term persistence of messages exists so an authenticated maintainer can scroll back through past conversations in the Streamlit UI (Part 3). Anonymous widget messages also land here, scoped to (widget_id, session_id).

---

## 5. `widgets`

```sql
CREATE TABLE widgets (
  id                UUID PRIMARY KEY,
  name              VARCHAR(128) NOT NULL,
  host_token_hash   CHAR(64) NOT NULL UNIQUE,
  allowed_origins   TEXT[] NOT NULL DEFAULT '{}',
  owner_user_id     UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  revoked_at        TIMESTAMPTZ NULL
);

CREATE INDEX ix_widgets_owner ON widgets (owner_user_id);
CREATE UNIQUE INDEX ux_widgets_active_token
  ON widgets (host_token_hash) WHERE revoked_at IS NULL;
```

**Notes.**
- `host_token_hash CHAR(64)` — sha256 hex (R5).
- The unique partial index `ux_widgets_active_token` enforces that no two non-revoked widgets can share a host-token hash. A revoked widget's hash is preserved for audit but cannot collide with a live one.
- `allowed_origins TEXT[]` — Postgres array, populated/consumed by the application; Part 3 will check `Origin` against this array on `/widget/iframe`.

---

## 6. `audit_log` evolution (additive)

Migration 0001's `audit_log` is kept; columns added per R3:

```sql
ALTER TABLE audit_log
  ADD COLUMN actor_user_id   UUID NULL REFERENCES users(id) ON DELETE SET NULL,
  ADD COLUMN actor_widget_id UUID NULL REFERENCES widgets(id) ON DELETE SET NULL,
  ADD COLUMN target_type     TEXT NULL,
  ADD COLUMN target_id       TEXT NULL;

CREATE INDEX ix_audit_log_actor_user  ON audit_log (actor_user_id);
CREATE INDEX ix_audit_log_actor_widget ON audit_log (actor_widget_id);

REVOKE UPDATE, DELETE ON audit_log FROM PUBLIC;
-- application role inherits no UPDATE/DELETE; granted only INSERT, SELECT:
GRANT INSERT, SELECT ON audit_log TO PUBLIC;
```

**Notes.**
- The existing columns (`id BIGINT identity`, `actor_id TEXT`, `action TEXT`, `target TEXT`, `timestamp TIMESTAMPTZ`, `payload JSONB`) are preserved.
- Part 1 code writes to: `actor_user_id` OR `actor_widget_id` (never both), `action`, `target_type`, `target_id`, `payload`, `timestamp` (default now()). The legacy `actor_id` and `target` columns are left NULL.
- Application-layer mutual-exclusivity check on `(actor_user_id XOR actor_widget_id)` lives in `audit_repository.record(...)` per R3. A SQL CHECK is intentionally not added so existing rows (none currently — table is empty) wouldn't violate it.
- The `REVOKE UPDATE, DELETE` is at the role level. Application code that attempts an UPDATE will get a Postgres permission error. The repository helper additionally raises `AuditLogImmutableError` if anyone tries to call `update()` or `delete()` on it, so the failure mode is typed.

---

## 7. Short-term memory (Redis)

Not a SQL table. Format documented here for completeness:

- **Key**: `convo:{conversation_id}`. Type: list (FIFO via RPUSH).
- **Value**: JSON-encoded message record:
  ```json
  {"role": "user|assistant|tool", "content": "...", "tool_name": "...", "tool_input": {}, "tool_output": {}, "ts": "2026-05-22T15:00:00Z"}
  ```
- **TTL**: 3600 seconds for widget conversations; 86400 seconds for authenticated conversations. TTL is set/refreshed on every append (`EXPIRE` after each `RPUSH`).
- **Window read**: `short_term_memory_service.get_window(conversation_id, max_tokens=4000)` LRANGE the list, decodes JSON, accumulates from the tail back until the token-count cap is reached.

---

## Entity ↔ Table mapping

| Spec entity            | Table                | Notes |
|------------------------|----------------------|-------|
| Maintainer (User)      | `users`              | fastapi-users manages it |
| Widget                 | `widgets`            | one-time token returned at create |
| Conversation           | `conversations`      | actor-exclusivity via SQL CHECK |
| Message                | `messages`           | tool-consistency via SQL CHECK |
| Memory (long-term)     | `chatbot_memories`   | source defaults to 'episodic' |
| Short-term Window      | Redis list `convo:*` | not a table |
| Audit Entry            | `audit_log` (evolved)| append-only via GRANT |

---

## State transitions

- **User**: `(unverified) → (verified) → (active|inactive)`. Role is independently mutable: `user ↔ admin`. Every role change writes an `audit_log` row (`action='user.role_changed'`).
- **Widget**: `(active) → (revoked, revoked_at SET)`. Irreversible — re-issuing requires creating a new widget row.
- **Conversation**: created at registration of the actor; never explicitly closed. `last_message_at` is updated on each append.

---

## Boot-time invariants

The lifespan (`app/main.py`) verifies after the Vault + DB + (now-fatal) Redis checks:

- `users` table exists. Missing → `REFUSE TO BOOT: users table missing`.
- `chatbot_memories` table exists. Missing → `REFUSE TO BOOT: chatbot_memories table missing`.
- `widgets` table exists. Missing → `REFUSE TO BOOT: widgets table missing`.

These three are wired via a single `_verify_chatbot_tables()` helper that issues `SELECT 1 FROM <table> LIMIT 1` and distinguishes missing-table (`ProgrammingError`) from unreachable-Postgres (`OperationalError`), the same pattern `_verify_rag_corpus()` uses.

---

## Migration outline (`alembic/versions/0003_chatbot.py`)

`upgrade()` order (so foreign keys resolve):

1. `users` (root of references).
2. `widgets` (FK to users).
3. `conversations` (FK to users + widgets).
4. `messages` (FK to conversations).
5. `chatbot_memories` (FK to users; embedding column and IVFFlat index).
6. ALTER `audit_log` (additive columns + indices).
7. REVOKE UPDATE/DELETE on `audit_log`; GRANT INSERT, SELECT.

`downgrade()` reverses in strict LIFO order, including dropping the IVFFlat index, dropping the new audit_log columns, and re-granting the prior privilege set. The downgrade restores the database to migration 0002's shape, satisfying Rule 3's "reversible migrations".
