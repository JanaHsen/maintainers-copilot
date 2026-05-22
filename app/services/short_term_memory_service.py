"""Short-term conversation memory (Redis) — append + windowed read.

Keeps the last N messages of a conversation in Redis so the chatbot
(Part 2) does not re-hit Postgres for the in-flight context window
(FR-015..FR-018). Persistence rules:

  * Redis key shape: ``convo:{conversation_id}``.
  * One LIST entry per message; each entry is a JSON object with the
    columns mirrored from ``data-model.md §4 messages``.
  * **TTL** is refreshed on every append. Caller passes ``ttl_seconds``:
    widget conversations default to **3600** (1 h), authenticated user
    conversations default to **86400** (24 h). The default arg here is the
    widget-default (3600); callers in Part 2 pass the authed default
    explicitly. (FR-015.)
  * **Redaction** is applied to ``content`` *before* JSON encoding (research
    R6 / Rule 7). If a user pastes ``sk-ant-…`` or an email into a message,
    the placeholder lands in Redis — the original literal never does.

Window read:

  * Coarse ``len(content) // 4`` token estimator per message.
  * Walks from the tail (most recent) backwards, accumulating the running
    total. When the next message would push the running total above
    ``max_tokens`` the walk stops and the messages collected so far are
    returned in chronological order (oldest first).
  * If a single message exceeds ``max_tokens`` on its own, an empty list is
    returned (caller's job to truncate large content upstream).
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any, cast

from app.domain.conversation import MemoryWindowMessage, MessageRole
from app.infra import redis_client
from app.infra.log_redaction import redact_for_persistence


def _key(conversation_id: uuid.UUID) -> str:
    return f"convo:{conversation_id}"


def append(
    conversation_id: uuid.UUID,
    role: MessageRole,
    content: str,
    *,
    tool_name: str | None = None,
    tool_input: dict[str, Any] | None = None,
    tool_output: dict[str, Any] | None = None,
    ttl_seconds: int = 3600,
) -> None:
    """Append a message to the conversation window; refresh the TTL.

    Redaction (research R6) is applied to ``content`` before JSON-encoding
    so a secret literal pasted by the user never lands in Redis. The TTL
    is reset to ``ttl_seconds`` after every append, so the window survives
    only while the conversation is active (FR-015).
    """
    redacted = redact_for_persistence(content)
    record = {
        "role": role,
        "content": redacted,
        "tool_name": tool_name,
        "tool_input": tool_input,
        "tool_output": tool_output,
        "created_at": datetime.now(UTC).isoformat(),
    }
    encoded = json.dumps(record)
    client = redis_client.get_client()
    key = _key(conversation_id)
    pipe = client.pipeline()
    pipe.rpush(key, encoded)
    pipe.expire(key, ttl_seconds)
    pipe.execute()


def get_window(
    conversation_id: uuid.UUID,
    max_tokens: int = 4000,
) -> list[MemoryWindowMessage]:
    """Return the most-recent messages within ``max_tokens`` (oldest first).

    Coarse estimator: ``len(content) // 4`` tokens per message. The walk
    starts at the tail (most recent), accumulates the running total, and
    stops when the next message would push the total over the budget. The
    collected messages are returned in chronological order (oldest first).
    """
    client = redis_client.get_client()
    raw_entries = cast(list[bytes], client.lrange(_key(conversation_id), 0, -1))
    if not raw_entries:
        return []

    decoded: list[dict[str, Any]] = []
    for entry in raw_entries:
        text = entry.decode("utf-8") if isinstance(entry, bytes) else entry
        decoded.append(json.loads(text))

    selected_reversed: list[dict[str, Any]] = []
    running_tokens = 0
    for record in reversed(decoded):
        content = record.get("content") or ""
        cost = len(content) // 4
        if running_tokens + cost > max_tokens:
            break
        selected_reversed.append(record)
        running_tokens += cost

    # Restore chronological order (oldest first).
    selected = list(reversed(selected_reversed))
    return [MemoryWindowMessage.model_validate(r) for r in selected]


def expire_at(conversation_id: uuid.UUID, ttl_seconds: int) -> None:
    """Explicitly reset the TTL of an existing conversation key.

    Used by Part 2 to switch a window's TTL between widget (3600 s) and
    authed (86400 s) when an actor identity is upgraded.
    """
    client = redis_client.get_client()
    client.expire(_key(conversation_id), ttl_seconds)


__all__ = ["append", "expire_at", "get_window"]
