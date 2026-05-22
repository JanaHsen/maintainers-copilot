"""Unit tests for ``chatbot_service.chat`` (T011).

Six scenarios from spec §3 / tasks.md T011:

  1. Single-turn no-tool — Anthropic returns ``end_turn`` immediately;
     ``tool_trace`` is empty and one user + one assistant row land.
  2. Single tool call — Anthropic returns ``tool_use`` then ``end_turn``;
     the dispatch produces one trace entry that flows through verbatim.
  3. Multi-tool call in one assistant turn — two ``tool_use`` blocks in
     the same response → two trace entries in Anthropic's emit order.
  4. Memory write then recall across two ``chat()`` calls — uses the real
     memory tools; turn 2 (new conversation_id, same actor) recalls the
     fact planted in turn 1. SC-002 integration smoke.
  5. Widget actor refusal — ``write_memory`` from a widget session returns
     ``widget_actor_forbidden``; the trace entry is ``is_error=True`` and
     no row lands in ``chatbot_memories`` for the widget's conversation.
  6. Loop cap exhaustion — Anthropic loops forever on ``tool_use``; the
     service returns the fallback string with ``len(tool_trace) == 6``.

Anthropic is stubbed via ``monkeypatch`` so the tests are deterministic
and offline. The live Postgres + Redis stack is required (we exercise the
real repositories + STM); the suite skips cleanly when they're not
reachable.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from app.domain.conversation import AuthedUser, WidgetSession
from app.infra import anthropic_client as anthropic_client_module
from app.infra import redis_client
from app.infra.anthropic_client import ToolUseBlock, ToolUseResponse
from app.infra.database import get_engine
from app.infra.vault_client import VaultBootstrapError
from app.services import chatbot_service

# --- skip guards + cleanup helpers ----------------------------------------


def _ensure_postgres_reachable() -> None:
    try:
        with get_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
    except (OperationalError, VaultBootstrapError) as exc:
        pytest.skip(f"Postgres / Vault not reachable in this env: {exc}")


def _ensure_redis_reachable() -> None:
    try:
        redis_client.get_client().ping()
    except Exception as exc:  # noqa: BLE001 — any redis failure → skip
        pytest.skip(f"Redis not reachable in this env: {exc}")


def _seed_user(label: str) -> uuid.UUID:
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
                "email": f"pytest-chatbot-{label}-{user_id.hex[:6]}@example.com",
            },
        )
    return user_id


def _seed_widget(owner_user_id: uuid.UUID) -> uuid.UUID:
    widget_id = uuid.uuid4()
    with get_engine().begin() as conn:
        conn.execute(
            text(
                "INSERT INTO widgets (id, name, host_token_hash, owner_user_id) "
                "VALUES (:id, :name, :hash, :owner)"
            ),
            {
                "id": widget_id,
                "name": "pytest-chatbot-widget",
                "hash": f"hash-{widget_id.hex[:16]}",
                "owner": owner_user_id,
            },
        )
    return widget_id


def _cleanup_user(user_id: uuid.UUID) -> None:
    # users → widgets → conversations → messages all CASCADE on user delete.
    with get_engine().begin() as conn:
        # audit_log.actor_user_id is ON DELETE SET NULL — clean by-hand.
        conn.execute(
            text("DELETE FROM audit_log WHERE actor_user_id = :uid"),
            {"uid": user_id},
        )
        conn.execute(text("DELETE FROM users WHERE id = :id"), {"id": user_id})


def _count_messages(conversation_id: uuid.UUID) -> int:
    with get_engine().connect() as conn:
        row = conn.execute(
            text(
                "SELECT count(*) AS c FROM messages WHERE conversation_id = :cid"
            ),
            {"cid": conversation_id},
        ).first()
    assert row is not None
    return int(row.c)


def _count_memories_for_conversation(conversation_id: uuid.UUID) -> int:
    with get_engine().connect() as conn:
        row = conn.execute(
            text(
                "SELECT count(*) AS c FROM chatbot_memories "
                "WHERE conversation_id = :cid"
            ),
            {"cid": conversation_id},
        ).first()
    assert row is not None
    return int(row.c)


@pytest.fixture
def alice() -> Iterator[uuid.UUID]:
    _ensure_postgres_reachable()
    _ensure_redis_reachable()
    user_id = _seed_user("alice")
    try:
        yield user_id
    finally:
        # Clean Redis windows for any conversation created during the test.
        _cleanup_user(user_id)


@pytest.fixture
def widget_owner_and_widget() -> Iterator[tuple[uuid.UUID, uuid.UUID]]:
    _ensure_postgres_reachable()
    _ensure_redis_reachable()
    owner_id = _seed_user("widget-owner")
    widget_id = _seed_widget(owner_id)
    try:
        yield owner_id, widget_id
    finally:
        _cleanup_user(owner_id)


# --- stub scaffolding ------------------------------------------------------


def _make_response(
    *,
    stop_reason: str = "end_turn",
    text_value: str = "",
    tool_use_blocks: list[ToolUseBlock] | None = None,
) -> ToolUseResponse:
    """Build a ``ToolUseResponse`` with a fake ``raw.content`` so the loop
    can pass the assistant's prior content blocks back to Anthropic.

    The fake raw object mimics the SDK's ``Message.content`` list of
    typed blocks: ``text`` blocks have ``.text`` and ``tool_use`` blocks
    have ``.id``, ``.name``, ``.input``. We expose them as a list of
    plain dicts (which is also a legal Anthropic content shape for the
    next-turn request).
    """
    blocks = tool_use_blocks or []
    raw_content: list[dict[str, Any]] = []
    if text_value:
        raw_content.append({"type": "text", "text": text_value})
    for b in blocks:
        raw_content.append(
            {
                "type": "tool_use",
                "id": b.id,
                "name": b.name,
                "input": b.input,
            }
        )

    class _Raw:
        def __init__(self, content: list[dict[str, Any]]) -> None:
            self.content = content

    return ToolUseResponse(
        stop_reason=stop_reason,
        text=text_value,
        tool_use_blocks=blocks,
        usage_input_tokens=10,
        usage_output_tokens=20,
        raw=_Raw(raw_content),
    )


def _install_scripted_tool_use(
    monkeypatch: pytest.MonkeyPatch,
    responses: list[ToolUseResponse],
) -> list[dict[str, Any]]:
    """Replace ``anthropic_client.tool_use_chat`` with a sequence stub.

    Returns the list of recorded call kwargs so tests can assert what
    messages were sent on each iteration. Raises ``RuntimeError`` if the
    test exhausts the script.
    """
    recorded: list[dict[str, Any]] = []
    iterator = iter(responses)

    def _fake_tool_use_chat(**kwargs: Any) -> ToolUseResponse:
        recorded.append(kwargs)
        try:
            return next(iterator)
        except StopIteration as exc:
            raise RuntimeError("scripted responses exhausted") from exc

    monkeypatch.setattr(
        chatbot_service.anthropic_client,
        "tool_use_chat",
        _fake_tool_use_chat,
    )
    # Also patch the module the loop imports symbols from in case future
    # callers reach through the module path directly.
    monkeypatch.setattr(
        anthropic_client_module, "tool_use_chat", _fake_tool_use_chat
    )
    return recorded


# --- 1. single-turn no-tool ------------------------------------------------


def test_chat_single_turn_no_tool(
    monkeypatch: pytest.MonkeyPatch, alice: uuid.UUID
) -> None:
    """Anthropic returns ``end_turn`` immediately → empty tool trace."""
    _install_scripted_tool_use(
        monkeypatch,
        [_make_response(stop_reason="end_turn", text_value="Hello.")],
    )

    outcome = chatbot_service.chat(
        conversation_id=None,
        user_message="hi",
        actor=AuthedUser(user_id=alice, role="user"),
    )

    assert isinstance(outcome, chatbot_service.ChatOk)
    assert outcome.assistant_message == "Hello."
    assert outcome.tool_trace == []

    # One user + one assistant row landed.
    assert _count_messages(outcome.conversation_id) == 2


# --- 2. single tool call ---------------------------------------------------


def test_chat_single_tool_call(
    monkeypatch: pytest.MonkeyPatch, alice: uuid.UUID
) -> None:
    """One tool_use round → one trace entry; second round ends the turn."""
    _install_scripted_tool_use(
        monkeypatch,
        [
            _make_response(
                stop_reason="tool_use",
                text_value="",
                tool_use_blocks=[
                    ToolUseBlock(
                        id="t1",
                        name="classify_issue",
                        input={"title": "T", "body": "B"},
                    )
                ],
            ),
            _make_response(
                stop_reason="end_turn", text_value="The issue is a bug."
            ),
        ],
    )

    # Stub the dispatch so we don't need the real classifier to be reachable.
    monkeypatch.setitem(
        chatbot_service.TOOLS_DISPATCH,
        "classify_issue",
        lambda input, actor, conversation_id: {
            "label": "bug",
            "confidence": 0.9,
            "label_scores": {},
        },
    )

    outcome = chatbot_service.chat(
        conversation_id=None,
        user_message="classify this",
        actor=AuthedUser(user_id=alice, role="user"),
    )

    assert isinstance(outcome, chatbot_service.ChatOk)
    assert outcome.assistant_message == "The issue is a bug."
    assert len(outcome.tool_trace) == 1
    entry = outcome.tool_trace[0]
    assert entry.tool_name == "classify_issue"
    assert entry.is_error is False
    assert entry.output["label"] == "bug"

    # user + tool + assistant in messages table.
    assert _count_messages(outcome.conversation_id) == 3


# --- 3. multi-tool call in one assistant turn ------------------------------


def test_chat_multi_tool_call_in_one_assistant_turn(
    monkeypatch: pytest.MonkeyPatch, alice: uuid.UUID
) -> None:
    """Two tool_use blocks in one response → two trace entries in order."""
    _install_scripted_tool_use(
        monkeypatch,
        [
            _make_response(
                stop_reason="tool_use",
                text_value="",
                tool_use_blocks=[
                    ToolUseBlock(
                        id="t1",
                        name="retrieve_context",
                        input={"query": "rebase docs", "k": 3},
                    ),
                    ToolUseBlock(
                        id="t2",
                        name="summarize_issue",
                        input={"text": "long-issue-body"},
                    ),
                ],
            ),
            _make_response(stop_reason="end_turn", text_value="Done."),
        ],
    )

    monkeypatch.setitem(
        chatbot_service.TOOLS_DISPATCH,
        "retrieve_context",
        lambda input, actor, conversation_id: {
            "chunks": [
                {
                    "id": "c1",
                    "content_snippet": "snippet",
                    "source_type": "doc",
                    "source_id": "s1",
                }
            ]
        },
    )
    monkeypatch.setitem(
        chatbot_service.TOOLS_DISPATCH,
        "summarize_issue",
        lambda input, actor, conversation_id: {"summary": "the gist"},
    )

    outcome = chatbot_service.chat(
        conversation_id=None,
        user_message="multi-tool please",
        actor=AuthedUser(user_id=alice, role="user"),
    )

    assert isinstance(outcome, chatbot_service.ChatOk)
    assert outcome.assistant_message == "Done."
    assert len(outcome.tool_trace) == 2
    # Order matches Anthropic's emit order.
    assert outcome.tool_trace[0].tool_name == "retrieve_context"
    assert outcome.tool_trace[1].tool_name == "summarize_issue"
    assert all(not e.is_error for e in outcome.tool_trace)


# --- 4. memory write → recall across two chat() calls (SC-002 smoke) -------


def test_chat_memory_write_then_recall_across_conversations(
    monkeypatch: pytest.MonkeyPatch, alice: uuid.UUID
) -> None:
    """Plant a fact in convo A, recall it in convo B (same user)."""
    # Use the real memory tools' dispatch — do NOT override TOOLS_DISPATCH
    # for write_memory / recall_memory. Stub only embed + Anthropic.
    fixed_embedding = [0.0] * 768
    fixed_embedding[7] = 1.0

    from app.services.tools import recall_memory_tool, write_memory_tool

    monkeypatch.setattr(
        write_memory_tool,
        "_default_embed",
        lambda text, request_id="": fixed_embedding,
    )
    monkeypatch.setattr(
        recall_memory_tool,
        "_default_embed",
        lambda text, request_id="": fixed_embedding,
    )

    # Turn 1: model writes a memory.
    _install_scripted_tool_use(
        monkeypatch,
        [
            _make_response(
                stop_reason="tool_use",
                text_value="",
                tool_use_blocks=[
                    ToolUseBlock(
                        id="w1",
                        name="write_memory",
                        input={"content": "I prefer rebase over merge."},
                    )
                ],
            ),
            _make_response(stop_reason="end_turn", text_value="Saved."),
        ],
    )

    out1 = chatbot_service.chat(
        conversation_id=None,
        user_message="please remember: I prefer rebase over merge",
        actor=AuthedUser(user_id=alice, role="user"),
    )
    assert isinstance(out1, chatbot_service.ChatOk)
    assert out1.assistant_message == "Saved."
    assert out1.tool_trace[0].tool_name == "write_memory"
    assert out1.tool_trace[0].is_error is False

    # Turn 2: new conversation_id, same actor — model recalls.
    _install_scripted_tool_use(
        monkeypatch,
        [
            _make_response(
                stop_reason="tool_use",
                text_value="",
                tool_use_blocks=[
                    ToolUseBlock(
                        id="r1",
                        name="recall_memory",
                        input={
                            "query": "what merge strategy do I prefer?",
                            "k": 5,
                        },
                    )
                ],
            ),
            _make_response(
                stop_reason="end_turn", text_value="You prefer rebase."
            ),
        ],
    )

    out2 = chatbot_service.chat(
        conversation_id=None,
        user_message="what merge strategy do I prefer?",
        actor=AuthedUser(user_id=alice, role="user"),
    )
    assert isinstance(out2, chatbot_service.ChatOk)
    assert out2.assistant_message == "You prefer rebase."
    assert len(out2.tool_trace) == 1
    recall_entry = out2.tool_trace[0]
    assert recall_entry.tool_name == "recall_memory"
    assert recall_entry.is_error is False
    hits = recall_entry.output.get("hits", [])
    assert hits, "recall returned no hits"
    assert "rebase" in hits[0]["content"]


# --- 5. widget actor refusal -----------------------------------------------


def test_chat_widget_actor_refusal_on_write_memory(
    monkeypatch: pytest.MonkeyPatch,
    widget_owner_and_widget: tuple[uuid.UUID, uuid.UUID],
) -> None:
    """Widget session's write_memory call → is_error=True, no row written."""
    _owner_id, widget_id = widget_owner_and_widget
    actor = WidgetSession(widget_id=widget_id, session_id="visitor-1")

    # Tripwire: embed must not be called when the widget actor short-
    # circuits inside write_memory_tool.
    from app.services.tools import write_memory_tool

    def _fail_embed(*args: object, **kwargs: object) -> list[float]:
        raise AssertionError("embed must not run on widget refusal path")

    monkeypatch.setattr(write_memory_tool, "_default_embed", _fail_embed)

    _install_scripted_tool_use(
        monkeypatch,
        [
            _make_response(
                stop_reason="tool_use",
                text_value="",
                tool_use_blocks=[
                    ToolUseBlock(
                        id="w1",
                        name="write_memory",
                        input={"content": "remember this"},
                    )
                ],
            ),
            _make_response(
                stop_reason="end_turn", text_value="I can't save that."
            ),
        ],
    )

    outcome = chatbot_service.chat(
        conversation_id=None,
        user_message="remember this for next time",
        actor=actor,
    )

    assert isinstance(outcome, chatbot_service.ChatOk)
    assert outcome.assistant_message == "I can't save that."
    assert len(outcome.tool_trace) == 1
    entry = outcome.tool_trace[0]
    assert entry.is_error is True
    assert entry.output["error"]["kind"] == "widget_actor_forbidden"

    # SC-006: zero memories landed for this widget conversation.
    assert _count_memories_for_conversation(outcome.conversation_id) == 0


# --- 6. loop cap exhaustion ------------------------------------------------


def test_chat_loop_cap_exhaustion_returns_fallback(
    monkeypatch: pytest.MonkeyPatch, alice: uuid.UUID
) -> None:
    """Anthropic always returns tool_use → loop hits MAX_TOOL_ITERATIONS."""
    # Always return the same tool_use response — N+1 to be safe.
    forever = [
        _make_response(
            stop_reason="tool_use",
            text_value="",
            tool_use_blocks=[
                ToolUseBlock(
                    id=f"t{i}",
                    name="classify_issue",
                    input={"title": "T", "body": "B"},
                )
            ],
        )
        for i in range(chatbot_service.MAX_TOOL_ITERATIONS + 2)
    ]
    _install_scripted_tool_use(monkeypatch, forever)
    monkeypatch.setitem(
        chatbot_service.TOOLS_DISPATCH,
        "classify_issue",
        lambda input, actor, conversation_id: {
            "label": "bug",
            "confidence": 0.5,
            "label_scores": {},
        },
    )

    outcome = chatbot_service.chat(
        conversation_id=None,
        user_message="never converges",
        actor=AuthedUser(user_id=alice, role="user"),
    )
    assert isinstance(outcome, chatbot_service.ChatOk)
    assert outcome.assistant_message == chatbot_service.LOOP_EXHAUSTED_FALLBACK
    assert len(outcome.tool_trace) == chatbot_service.MAX_TOOL_ITERATIONS
