"""Part 2 redaction extension: chatbot → write_memory persistence path.

Asserts every surface that touches user-supplied content with a secret-
or PII-shaped substring receives the redacted form:

  1. ``chatbot_memories.content`` — already proven for Part 1's direct
     `write_memory` call; this test re-proves it via the chatbot service.
  2. ``audit_log.payload.content_hash`` — sha256 over the REDACTED content,
     not the raw. The hash that lands in the audit row must equal
     ``sha256(redact_for_persistence(raw_content))``.
  3. ``messages.tool_input`` — Sonnet may pass user-supplied content verbatim
     in the tool_use block's input. The Part 2 ``_persist_tool_message``
     redaction extension stops it landing raw in JSONB.

Skips cleanly if Postgres/Redis/Vault aren't reachable.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from collections.abc import Iterable
from typing import Any
from unittest.mock import patch

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from app.domain.conversation import AuthedUser
from app.infra.database import get_engine
from app.infra.database_async import get_async_engine, get_async_sessionmaker
from app.infra.log_redaction import (
    PLACEHOLDER,
    PLACEHOLDER_EMAIL,
    redact_for_persistence,
)
from app.infra.vault_client import VaultBootstrapError
from app.services import chatbot_service
from app.services.tools.write_memory_tool import write_memory

ANTHROPIC_KEY = "sk-ant-api03-AbCdEf1234567890_-XyZ"
EMAIL_ADDR = "leaky@example.com"
RAW_FACT = (
    f"My API key is {ANTHROPIC_KEY} and you can reach me at {EMAIL_ADDR}."
)


def _ensure_async_postgres_reachable() -> None:
    async def probe() -> None:
        engine = get_async_engine()
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))

    try:
        asyncio.run(probe())
    except (OperationalError, VaultBootstrapError, RuntimeError) as exc:
        pytest.skip(f"Async Postgres / Vault not reachable: {exc}")


def _seed_user(email_prefix: str) -> uuid.UUID:
    """Seed a fresh user via raw SQL — admin status irrelevant for this test."""
    user_id = uuid.uuid4()
    email = f"{email_prefix}{uuid.uuid4().hex[:8]}@example.com"

    async def insert() -> None:
        sm = get_async_sessionmaker()
        async with sm() as session:
            await session.execute(
                text(
                    "INSERT INTO users "
                    "(id, email, hashed_password, is_active, is_superuser, "
                    " is_verified, role) "
                    "VALUES "
                    "(:id, :email, 'fake-hash', TRUE, FALSE, TRUE, 'user')"
                ),
                {"id": user_id, "email": email},
            )
            await session.commit()

    asyncio.run(insert())
    return user_id


def _delete_user(user_id: uuid.UUID) -> None:
    async def cleanup() -> None:
        sm = get_async_sessionmaker()
        async with sm() as session:
            # Cascade clears conversations + memories + audit_log actor refs.
            await session.execute(
                text("DELETE FROM users WHERE id = :id"), {"id": user_id}
            )
            await session.commit()

    asyncio.run(cleanup())


def _query_memory_content(user_id: uuid.UUID) -> str | None:
    with get_engine().connect() as conn:
        row = conn.execute(
            text(
                "SELECT content FROM chatbot_memories "
                "WHERE user_id = :uid ORDER BY created_at DESC LIMIT 1"
            ),
            {"uid": user_id},
        ).first()
    return None if row is None else str(row[0])


def _query_audit_payload(user_id: uuid.UUID) -> dict[str, Any] | None:
    with get_engine().connect() as conn:
        row = conn.execute(
            text(
                "SELECT payload FROM audit_log "
                "WHERE actor_user_id = :uid AND action = 'memory.write' "
                "ORDER BY timestamp DESC LIMIT 1"
            ),
            {"uid": user_id},
        ).first()
    if row is None:
        return None
    payload = row[0]
    return payload if isinstance(payload, dict) else json.loads(payload)


def _query_tool_message_input(user_id: uuid.UUID) -> dict[str, Any] | None:
    """Most recent role='tool' message for tool_name='write_memory' for user."""
    with get_engine().connect() as conn:
        row = conn.execute(
            text(
                "SELECT m.tool_input "
                "FROM messages m JOIN conversations c ON c.id = m.conversation_id "
                "WHERE c.user_id = :uid AND m.role = 'tool' "
                "AND m.tool_name = 'write_memory' "
                "ORDER BY m.created_at DESC LIMIT 1"
            ),
            {"uid": user_id},
        ).first()
    if row is None:
        return None
    val = row[0]
    return val if isinstance(val, dict) else json.loads(val)


def test_write_memory_audit_content_hash_is_over_redacted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Part 2 brief §7: audit_log.payload.content_hash = sha256(REDACTED)."""
    _ensure_async_postgres_reachable()

    user_id = _seed_user("pytest-redact-direct-")
    try:
        actor = AuthedUser(user_id=user_id, role="user")
        # Stub embedding to avoid hitting the model server.
        from app.services.tools import write_memory_tool as wmt
        monkeypatch.setattr(wmt, "_default_embed", lambda text, request_id="": [0.0] * 768)

        outcome = write_memory(
            content=RAW_FACT,
            actor=actor,
            conversation_id=uuid.uuid4(),
        )
        from app.services.tools.write_memory_tool import WriteMemoryOk
        assert isinstance(outcome, WriteMemoryOk), outcome

        # 1. memory.content is redacted.
        stored = _query_memory_content(user_id)
        assert stored is not None
        assert ANTHROPIC_KEY not in stored
        assert EMAIL_ADDR not in stored
        assert PLACEHOLDER in stored
        assert PLACEHOLDER_EMAIL in stored

        # 2. audit payload's content_hash is sha256 of the redacted content.
        payload = _query_audit_payload(user_id)
        assert payload is not None
        assert "content_hash" in payload, payload
        expected_hash = hashlib.sha256(
            redact_for_persistence(RAW_FACT).encode("utf-8")
        ).hexdigest()
        assert payload["content_hash"] == expected_hash
        # And the hash should NOT match the raw content's hash.
        raw_hash = hashlib.sha256(RAW_FACT.encode("utf-8")).hexdigest()
        assert payload["content_hash"] != raw_hash
    finally:
        _delete_user(user_id)


def test_chatbot_service_tool_message_redacts_user_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Part 2 _persist_tool_message: tool_input MUST land redacted in JSONB.

    Sonnet relays the user's raw message into write_memory.content. We
    assert that ``messages.tool_input.content`` carries the redacted form,
    not the raw secret.
    """
    _ensure_async_postgres_reachable()

    user_id = _seed_user("pytest-redact-chatbot-")
    try:
        actor = AuthedUser(user_id=user_id, role="user")

        # Stub embedding (model server may be slow).
        from app.services.tools import write_memory_tool as wmt
        monkeypatch.setattr(wmt, "_default_embed", lambda text, request_id="": [0.0] * 768)

        # Script Anthropic: one tool_use(write_memory, content=RAW_FACT),
        # then end_turn("saved.").
        from app.infra.anthropic_client import ToolUseBlock, ToolUseResponse

        scripted: Iterable[ToolUseResponse] = iter(
            [
                ToolUseResponse(
                    stop_reason="tool_use",
                    text="",
                    tool_use_blocks=[
                        ToolUseBlock(
                            id="tu_1",
                            name="write_memory",
                            input={"content": RAW_FACT},
                        )
                    ],
                    usage_input_tokens=10,
                    usage_output_tokens=10,
                    raw=type("R", (), {"content": []})(),
                ),
                ToolUseResponse(
                    stop_reason="end_turn",
                    text="Saved.",
                    tool_use_blocks=[],
                    usage_input_tokens=10,
                    usage_output_tokens=2,
                    raw=type("R", (), {"content": []})(),
                ),
            ]
        )

        def fake_tool_use_chat(**_kwargs: Any) -> ToolUseResponse:
            return next(scripted)

        with patch(
            "app.services.chatbot_service.anthropic_client.tool_use_chat",
            side_effect=fake_tool_use_chat,
        ):
            outcome = chatbot_service.chat(
                conversation_id=None,
                user_message="Save this fact for me.",
                actor=actor,
            )

        from app.services.chatbot_service import ChatOk
        assert isinstance(outcome, ChatOk), outcome

        # 3. messages.tool_input.content is redacted.
        tool_input = _query_tool_message_input(user_id)
        assert tool_input is not None, "no tool message persisted"
        assert "content" in tool_input, tool_input
        persisted_content = tool_input["content"]
        assert ANTHROPIC_KEY not in persisted_content
        assert EMAIL_ADDR not in persisted_content
        assert PLACEHOLDER in persisted_content
        assert PLACEHOLDER_EMAIL in persisted_content

        # Also re-assert the underlying memory + audit surfaces.
        stored = _query_memory_content(user_id)
        assert stored is not None
        assert ANTHROPIC_KEY not in stored
        assert EMAIL_ADDR not in stored

        payload = _query_audit_payload(user_id)
        assert payload is not None
        assert payload.get("content_hash") == hashlib.sha256(
            redact_for_persistence(RAW_FACT).encode("utf-8")
        ).hexdigest()
    finally:
        _delete_user(user_id)
