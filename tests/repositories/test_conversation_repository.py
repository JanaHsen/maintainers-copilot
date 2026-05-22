"""conversation_repository — create + append_message + CHECK invariants.

Skips cleanly if Postgres / Vault are unreachable.

Covers:
  * authed-user conversation create + get,
  * widget-session conversation create + get,
  * actor-exclusivity CHECK (both user_id + widget_id set → IntegrityError),
  * message append + list ordering,
  * tool-consistency CHECK (role='user' but tool_name set → IntegrityError).
"""

from __future__ import annotations

import hashlib
import secrets
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, OperationalError

from app.infra.database import get_engine
from app.infra.vault_client import VaultBootstrapError
from app.repositories import conversation_repository


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
                "email": f"pytest-convo-{user_id.hex[:8]}@example.com",
            },
        )
    return user_id


def _seed_widget(owner_user_id: uuid.UUID) -> uuid.UUID:
    widget_id = uuid.uuid4()
    plaintext = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(plaintext.encode()).hexdigest()
    with get_engine().begin() as conn:
        conn.execute(
            text(
                "INSERT INTO widgets (id, name, host_token_hash, owner_user_id) "
                "VALUES (:id, :name, :hash, :owner)"
            ),
            {
                "id": widget_id,
                "name": "pytest-widget",
                "hash": token_hash,
                "owner": owner_user_id,
            },
        )
    return widget_id


def _cleanup_user(user_id: uuid.UUID) -> None:
    # users → widgets → conversations → messages all CASCADE on user delete.
    with get_engine().begin() as conn:
        conn.execute(text("DELETE FROM users WHERE id = :id"), {"id": user_id})


def test_create_conversation_for_authed_user() -> None:
    _ensure_postgres_reachable()
    user_id = _seed_user()
    try:
        cid = conversation_repository.create(
            user_id=user_id, widget_id=None, session_id=None
        )
        convo = conversation_repository.get(cid)
        assert convo is not None
        assert convo.user_id == user_id
        assert convo.widget_id is None
        assert convo.session_id is None
    finally:
        _cleanup_user(user_id)


def test_create_conversation_for_widget_session() -> None:
    _ensure_postgres_reachable()
    user_id = _seed_user()
    try:
        widget_id = _seed_widget(user_id)
        cid = conversation_repository.create(
            user_id=None, widget_id=widget_id, session_id="sess-123"
        )
        convo = conversation_repository.get(cid)
        assert convo is not None
        assert convo.user_id is None
        assert convo.widget_id == widget_id
        assert convo.session_id == "sess-123"
    finally:
        _cleanup_user(user_id)


def test_actor_exclusivity_check_rejects_both_set() -> None:
    """SQL CHECK chk_conversations_actor_exclusive rejects user_id + widget_id."""
    _ensure_postgres_reachable()
    user_id = _seed_user()
    try:
        widget_id = _seed_widget(user_id)
        with pytest.raises(IntegrityError):
            conversation_repository.create(
                user_id=user_id, widget_id=widget_id, session_id="sess-1"
            )
    finally:
        _cleanup_user(user_id)


def test_actor_exclusivity_check_rejects_neither_set() -> None:
    """CHECK also rejects rows with neither user_id nor widget_id."""
    _ensure_postgres_reachable()
    with pytest.raises(IntegrityError):
        conversation_repository.create(
            user_id=None, widget_id=None, session_id=None
        )


def test_actor_exclusivity_check_rejects_widget_without_session() -> None:
    _ensure_postgres_reachable()
    user_id = _seed_user()
    try:
        widget_id = _seed_widget(user_id)
        with pytest.raises(IntegrityError):
            conversation_repository.create(
                user_id=None, widget_id=widget_id, session_id=None
            )
    finally:
        _cleanup_user(user_id)


def test_append_message_and_list() -> None:
    _ensure_postgres_reachable()
    user_id = _seed_user()
    try:
        cid = conversation_repository.create(
            user_id=user_id, widget_id=None, session_id=None
        )
        m1 = conversation_repository.append_message(
            conversation_id=cid, role="user", content="hello"
        )
        m2 = conversation_repository.append_message(
            conversation_id=cid, role="assistant", content="hi there"
        )
        m3 = conversation_repository.append_message(
            conversation_id=cid,
            role="tool",
            content="(tool call)",
            tool_name="recall_memory",
            tool_input={"q": "hello"},
            tool_output={"hits": []},
        )
        rows = conversation_repository.list_messages(cid)
        assert [r.id for r in rows] == [m1, m2, m3]
        assert rows[0].role == "user"
        assert rows[2].role == "tool"
        assert rows[2].tool_name == "recall_memory"
        assert rows[2].tool_input == {"q": "hello"}
    finally:
        _cleanup_user(user_id)


def test_tool_consistency_check_rejects_tool_name_on_user_role() -> None:
    """CHECK chk_messages_tool_consistency rejects tool_name on non-tool role."""
    _ensure_postgres_reachable()
    user_id = _seed_user()
    try:
        cid = conversation_repository.create(
            user_id=user_id, widget_id=None, session_id=None
        )
        with pytest.raises(IntegrityError):
            conversation_repository.append_message(
                conversation_id=cid,
                role="user",
                content="oops",
                tool_name="recall_memory",
            )
    finally:
        _cleanup_user(user_id)


def test_tool_consistency_check_rejects_missing_tool_name_on_tool_role() -> None:
    _ensure_postgres_reachable()
    user_id = _seed_user()
    try:
        cid = conversation_repository.create(
            user_id=user_id, widget_id=None, session_id=None
        )
        with pytest.raises(IntegrityError):
            conversation_repository.append_message(
                conversation_id=cid,
                role="tool",
                content="(tool call)",
                tool_name=None,
            )
    finally:
        _cleanup_user(user_id)
