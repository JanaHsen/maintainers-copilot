"""Short-term memory service — append/get_window/expire against Redis.

Covers FR-015..FR-018 + research R6 (redaction-at-persistence):

  * round-trip: append then get_window returns the same message;
  * TTL refresh: a second append after the first refreshes the key TTL;
  * window cap: the token-budget walks from the tail and excludes early
    messages that would push the running total over ``max_tokens``;
  * redaction: ``sk-ant-…`` and email addresses in content are replaced
    with placeholders **before** the JSON record reaches Redis (Rule 7);
  * tool message: a ``role='tool'`` record round-trips ``tool_name`` +
    ``tool_input`` + ``tool_output`` faithfully.

Skips if Redis is not reachable (same gating pattern Phase B uses for
Postgres) — local hosts often run the stack with Redis bound only to a
docker network.
"""

from __future__ import annotations

import json
import uuid

import pytest
from redis.exceptions import RedisError

from app.infra import redis_client
from app.services import short_term_memory_service as mem


def _ensure_redis_reachable() -> None:
    try:
        redis_client.get_client().ping()
    except RedisError as exc:
        pytest.skip(f"Redis not reachable in this env: {exc}")


def _clear(conversation_id: uuid.UUID) -> None:
    redis_client.get_client().delete(f"convo:{conversation_id}")


@pytest.fixture
def conversation_id() -> uuid.UUID:
    _ensure_redis_reachable()
    cid = uuid.uuid4()
    _clear(cid)
    try:
        yield cid
    finally:
        _clear(cid)


def test_append_then_get_window_round_trip(conversation_id: uuid.UUID) -> None:
    mem.append(conversation_id, "user", "hello world")
    window = mem.get_window(conversation_id)
    assert len(window) == 1
    assert window[0].role == "user"
    assert window[0].content == "hello world"
    assert window[0].tool_name is None
    assert window[0].created_at is not None


def test_ttl_refresh_on_every_append(conversation_id: uuid.UUID) -> None:
    """Each append resets the TTL to ``ttl_seconds``."""
    client = redis_client.get_client()
    key = f"convo:{conversation_id}"

    mem.append(conversation_id, "user", "first", ttl_seconds=120)
    ttl_after_first = client.ttl(key)
    assert 0 < ttl_after_first <= 120

    # Manually decay the TTL — simulates time passing.
    client.expire(key, 5)
    assert client.ttl(key) <= 5

    mem.append(conversation_id, "assistant", "second", ttl_seconds=120)
    ttl_after_second = client.ttl(key)
    # Refresh restored the TTL to ~120, well above the decayed 5.
    assert ttl_after_second > 5
    assert ttl_after_second <= 120


def test_window_cap_excludes_early_messages(conversation_id: uuid.UUID) -> None:
    """Token budget walks from the tail; older messages drop off."""
    # Each message: ~400 chars → ~100 tokens. With max_tokens=250, only the
    # most-recent two messages fit (200 ≤ 250 < 300). The filler is a
    # whitespace-separated phrase so the long-opaque-token redaction rule
    # (40+ chars run) does not collapse it.
    filler = " ".join(["the quick brown fox"] * 19)  # 379 chars
    for i in range(5):
        mem.append(conversation_id, "user", f"M{i}- {filler}")

    window = mem.get_window(conversation_id, max_tokens=250)
    assert len(window) == 2
    # Chronological order (oldest first).
    assert window[0].content.startswith("M3-")
    assert window[1].content.startswith("M4-")


def test_window_cap_returns_empty_if_single_message_exceeds_budget(
    conversation_id: uuid.UUID,
) -> None:
    # Whitespace-separated content so the long-token redaction rule leaves
    # it intact (we want a HIGH-cost message, not a redacted one).
    big_content = " ".join(["alpha"] * 250)  # 1499 chars → ~374 tokens
    mem.append(conversation_id, "user", big_content)
    window = mem.get_window(conversation_id, max_tokens=100)
    assert window == []


def test_redaction_applied_before_persistence(conversation_id: uuid.UUID) -> None:
    """Anthropic-key + email substrings are placeholdered before reaching Redis."""
    content = "Try sk-ant-FAKE0000111122223333 and contact bob@example.com please"
    mem.append(conversation_id, "user", content)

    # Inspect the raw Redis JSON: the literals must not be in the record.
    raw = redis_client.get_client().lrange(f"convo:{conversation_id}", 0, -1)
    assert raw, "expected one entry in Redis"
    decoded = json.loads(raw[0])
    assert "sk-ant-FAKE0000111122223333" not in decoded["content"]
    assert "bob@example.com" not in decoded["content"]
    assert "[REDACTED]" in decoded["content"]
    assert "[REDACTED_EMAIL]" in decoded["content"]


def test_tool_message_round_trip(conversation_id: uuid.UUID) -> None:
    """A role='tool' record preserves tool_name + tool_input + tool_output."""
    mem.append(
        conversation_id,
        "tool",
        "(tool call summary)",
        tool_name="recall_memory",
        tool_input={"query": "deployments"},
        tool_output={"hits": [{"id": "abc", "score": 0.91}]},
    )
    window = mem.get_window(conversation_id)
    assert len(window) == 1
    msg = window[0]
    assert msg.role == "tool"
    assert msg.tool_name == "recall_memory"
    assert msg.tool_input == {"query": "deployments"}
    assert msg.tool_output == {"hits": [{"id": "abc", "score": 0.91}]}


def test_expire_at_resets_ttl(conversation_id: uuid.UUID) -> None:
    """``expire_at`` resets the TTL of an existing key (widget→authed swap)."""
    mem.append(conversation_id, "user", "hi", ttl_seconds=60)
    client = redis_client.get_client()
    key = f"convo:{conversation_id}"
    assert 0 < client.ttl(key) <= 60

    mem.expire_at(conversation_id, ttl_seconds=86400)
    assert client.ttl(key) > 60
