# Phase 1 — Data Model: Chatbot Part 2 Brain

No new tables. One optional additive migration.

## Reuse from Part 1

- `conversations` — already supports authed-user + widget-session actors via CHECK constraint.
- `messages` — already accepts role∈{user,assistant,tool} with optional tool_name/tool_input/tool_output. Part 2 writes `tool` rows for every executed tool: `tool_name=<wrapper name>`, `tool_input=<dict>`, `tool_output=<dict or {"error": ...}>`.
- `chatbot_memories` — already pgvector(768) with IVFFlat cosine partial index. Part 2 calls Part 1's `memory_repository.insert` / `query_top_k` via the existing tool primitives.
- `widgets` — already supplies host_token_hash + allowed_origins. Part 2 calls `widget_repository.get_by_token_hash` and validates `Origin` at router boundary.
- `audit_log` — already evolved to carry `actor_user_id`, `actor_widget_id`, `action`, `target_type`, `target_id`, `payload`. Part 2 writes `action='memory.write'` rows from the `write_memory_tool` path (Part 1 already does this).

## Optional migration `0004_chatbot_part2.py`

Adds one composite index on `audit_log` to speed up Part 3's admin-panel "list this user's audit entries by action and time" query. Pure additive; no data changes.

```sql
CREATE INDEX IF NOT EXISTS ix_audit_log_actor_action_time
  ON audit_log (actor_user_id, action, "timestamp" DESC)
  WHERE actor_user_id IS NOT NULL;
```

`downgrade()` drops the index.

The migration is OPTIONAL — Part 3 may not need it if the admin panel queries by simple time scan + filter. We ship it now because Part 2 has the migration cadence and adding it after the admin panel runs against a populated audit_log table would be a non-trivial concurrent-index operation.

## In-flight shapes (not persisted as their own table)

- **ChatRequest** (authed): `{conversation_id: UUID | None, message: str}`.
- **ChatRequest** (widget): `{widget_id: UUID, session_id: str, message: str}` + `X-Widget-Token` header.
- **ChatResponse**: `{assistant_message: str, conversation_id: UUID, tool_trace: list[ToolTraceEntry], request_id, trace_id}`.
- **ToolTraceEntry**: `{tool_name: str, input: dict, output: dict, latency_ms: int, is_error: bool}`.

## Boot-time invariants

No new boot checks added in Part 2. The Part 1 boot-check surface (Vault keys, Redis, chatbot tables, RAG corpus) remains authoritative.
