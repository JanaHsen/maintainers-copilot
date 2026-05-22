"""Chatbot agent loop — composes Anthropic tool-use with the six tool
primitives behind a single ``chat()`` entry point (Rule 1, Rule 7, Rule 11).

The loop:

  1. Open a top-level Phoenix span ``chat.turn`` so an operator can see one
     LLM turn as a connected tree of child spans (one per Anthropic call,
     one per tool execution).
  2. Resolve / validate the conversation row (Postgres) and persist the
     user message to both Postgres and Redis (Rule 7 / FR-008).
  3. Build the messages window from short-term memory, capped at
     ``WINDOW_MESSAGE_CAP`` entries (research R1).
  4. Call ``anthropic_client.tool_use_chat`` and dispatch any tool_use
     blocks via the ``TOOLS_DISPATCH`` registry. Tool failures NEVER
     escape — they become ``is_error=True`` ``tool_result`` blocks the
     model can adapt to (Rule 11, research R2).
  5. On ``stop_reason='end_turn'``: persist the assistant message and
     return ``ChatOk``.
  6. On ``MAX_TOOL_ITERATIONS`` exhaustion: append the typed fallback
     message and return ``ChatOk`` with ``loop_exhausted=True`` on the
     top-level span (research R3).

The function never raises. Transport-level Anthropic failures (timeout,
unreachable, 4xx/5xx) return ``ChatError`` variants the router maps to
HTTP statuses per Rule 11.

``PROMPT_HASH`` is computed once at import (``sha256(prompts/chatbot_system.md)``)
and attached to every ``chat.turn`` span so operators can correlate behavior
drift to prompt-version changes via Phoenix.
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from app.domain.chat import ToolTraceEntry
from app.domain.conversation import Actor, WidgetSession
from app.infra import anthropic_client
from app.infra.anthropic_client import (
    AnthropicAuthError,
    AnthropicBadRequestError,
    AnthropicError,
    AnthropicInternalError,
    AnthropicRateLimitError,
    AnthropicTimeoutError,
    AnthropicUnreachableError,
)
from app.infra.log_redaction import redact_for_persistence
from app.infra.tracing import get_tracer
from app.repositories import conversation_repository
from app.services import short_term_memory_service
from app.services.tools import TOOLS, TOOLS_DISPATCH

# --- constants -------------------------------------------------------------

MAX_TOOL_ITERATIONS = 6
WINDOW_MESSAGE_CAP = 20
WINDOW_MAX_TOKENS = 4000

CHAT_MODEL = "claude-sonnet-4-5-20250929"
CHAT_MAX_TOKENS = 1024

TTL_AUTHED_SECONDS = 86_400  # 24 h — authed maintainer's session window.
TTL_WIDGET_SECONDS = 3_600  # 1 h — widget visitor's session window.

LOOP_EXHAUSTED_FALLBACK = (
    "I ran out of attempts to finish that — please rephrase or simplify."
)

# Computed once at import (Rule 7) — operators correlate behavior changes to
# prompt-version changes in Phoenix without grepping git.
PROMPT_PATH = Path(__file__).resolve().parents[2] / "prompts" / "chatbot_system.md"
PROMPT_TEXT = PROMPT_PATH.read_text(encoding="utf-8")
PROMPT_HASH = hashlib.sha256(PROMPT_TEXT.encode("utf-8")).hexdigest()


# --- typed outcome ---------------------------------------------------------


ChatErrorKind = Literal[
    "anthropic_unreachable",
    "anthropic_timeout",
    "anthropic_bad_request",
    "anthropic_internal",
    "anthropic_unexpected",
    "db_failed",
]


@dataclass(frozen=True)
class ChatOk:
    """Successful chat-turn outcome — assistant message + tool trace."""

    assistant_message: str
    conversation_id: uuid.UUID
    tool_trace: list[ToolTraceEntry]


@dataclass(frozen=True)
class ChatError:
    """Typed failure outcome — see ``ChatErrorKind`` for the cases.

    ``conversation_id`` may be ``None`` if the conversation row never
    landed (the failure happened during create / lookup).
    """

    kind: ChatErrorKind
    detail: str
    conversation_id: uuid.UUID | None


ChatOutcome = ChatOk | ChatError


# --- helpers ---------------------------------------------------------------


def _actor_attrs(actor: Actor) -> tuple[str, str]:
    """Return ``(actor.kind, actor.id)`` strings for span attributes."""
    if isinstance(actor, WidgetSession):
        return ("widget", str(actor.widget_id))
    return ("authed", str(actor.user_id))


def _ttl_for_actor(actor: Actor) -> int:
    """24h for authed users, 1h for widget visitors (FR-015 / Part 1 R6)."""
    return TTL_WIDGET_SECONDS if isinstance(actor, WidgetSession) else TTL_AUTHED_SECONDS


def _resolve_conversation(
    *,
    conversation_id: uuid.UUID | None,
    actor: Actor,
) -> tuple[uuid.UUID | None, ChatError | None]:
    """Create-or-validate a conversation row for ``actor``.

    Returns ``(conversation_id, None)`` on success or
    ``(None_or_existing_id, ChatError)`` on failure. The DB exception path
    is mapped to ``db_failed`` per Rule 11.
    """
    if conversation_id is None:
        # Create a new conversation row for the right actor kind.
        try:
            if isinstance(actor, WidgetSession):
                new_id = conversation_repository.create(
                    user_id=None,
                    widget_id=actor.widget_id,
                    session_id=actor.session_id,
                )
            else:  # AuthedUser
                new_id = conversation_repository.create(
                    user_id=actor.user_id,
                    widget_id=None,
                    session_id=None,
                )
        except Exception as db_exc:  # noqa: BLE001 — typed mapping per Rule 11
            return None, ChatError(
                kind="db_failed",
                detail=str(db_exc),
                conversation_id=None,
            )
        return new_id, None

    # Existing conversation — validate the actor owns it.
    try:
        convo = conversation_repository.get(conversation_id)
    except Exception as db_exc:  # noqa: BLE001 — typed mapping per Rule 11
        return conversation_id, ChatError(
            kind="db_failed",
            detail=str(db_exc),
            conversation_id=conversation_id,
        )
    if convo is None:
        return conversation_id, ChatError(
            kind="db_failed",
            detail="conversation not found",
            conversation_id=conversation_id,
        )

    # Ownership check: AuthedUser owns by user_id; WidgetSession owns by
    # (widget_id, session_id) tuple. Anything else is a refusal.
    if isinstance(actor, WidgetSession):
        owned = (
            convo.widget_id == actor.widget_id
            and convo.session_id == actor.session_id
        )
    else:  # AuthedUser
        owned = convo.user_id == actor.user_id
    if not owned:
        return conversation_id, ChatError(
            kind="db_failed",
            detail="conversation not owned by actor",
            conversation_id=conversation_id,
        )
    return conversation_id, None


def _stm_window_to_messages(
    conversation_id: uuid.UUID,
) -> list[dict[str, Any]]:
    """Project the Redis STM window onto Anthropic message dicts.

    Take the last ``WINDOW_MESSAGE_CAP`` entries from the
    ``max_tokens``-bounded window (research R1 — message-count is the
    primary cap, the token estimator is a safety net).

    ``role='tool'`` entries are **skipped** here: replaying them would
    require reconstructing the original ``tool_use_id`` for each
    ``tool_result``, which is not stored in STM. The agent loop replays
    text turns from history and re-invokes tools on the current turn as
    needed; this is the simpler contract and matches the way Sonnet
    handles partial-history replay.
    """
    window = short_term_memory_service.get_window(
        conversation_id, max_tokens=WINDOW_MAX_TOKENS
    )
    # Cap at the last N entries (research R1 — message-count primary).
    if len(window) > WINDOW_MESSAGE_CAP:
        window = window[-WINDOW_MESSAGE_CAP:]

    messages: list[dict[str, Any]] = []
    for entry in window:
        if entry.role == "tool":
            # Skip — we don't replay tool_result blocks from history
            # (no stored tool_use_id to pair them with).
            continue
        if entry.role not in ("user", "assistant"):
            continue
        messages.append({"role": entry.role, "content": entry.content})
    return messages


def _persist_assistant_text(
    *,
    conversation_id: uuid.UUID,
    text: str,
    ttl_seconds: int,
) -> None:
    """Persist an assistant message to both Postgres and short-term memory."""
    conversation_repository.append_message(
        conversation_id=conversation_id,
        role="assistant",
        content=text,
    )
    short_term_memory_service.append(
        conversation_id,
        "assistant",
        text,
        ttl_seconds=ttl_seconds,
    )


def _persist_tool_message(
    *,
    conversation_id: uuid.UUID,
    tool_name: str,
    tool_input: dict[str, Any],
    tool_output: dict[str, Any],
    ttl_seconds: int,
) -> None:
    """Persist a tool message row to Postgres + Redis.

    ``content`` is a JSON-stringified ``tool_output`` so the Postgres row
    can be inspected without joining JSONB columns. ``tool_input`` and
    ``tool_output`` are also stored as structured JSONB for downstream
    analysis (per ``data-model.md §4``).
    """
    content = json.dumps(tool_output, default=str)
    conversation_repository.append_message(
        conversation_id=conversation_id,
        role="tool",
        content=content,
        tool_name=tool_name,
        tool_input=tool_input,
        tool_output=tool_output,
    )
    short_term_memory_service.append(
        conversation_id,
        "tool",
        content,
        tool_name=tool_name,
        tool_input=tool_input,
        tool_output=tool_output,
        ttl_seconds=ttl_seconds,
    )


def _map_anthropic_exc(exc: AnthropicError) -> ChatErrorKind:
    """Map an Anthropic SDK exception to a ``ChatErrorKind`` per Rule 11.

    Rate-limit and auth errors are folded into ``anthropic_unreachable``
    so the router gets one ``service-not-available`` shape; finer
    granularity lives on the Phoenix span via the exception record.
    """
    if isinstance(exc, AnthropicTimeoutError):
        return "anthropic_timeout"
    if isinstance(exc, AnthropicUnreachableError | AnthropicAuthError | AnthropicRateLimitError):
        return "anthropic_unreachable"
    if isinstance(exc, AnthropicBadRequestError):
        return "anthropic_bad_request"
    if isinstance(exc, AnthropicInternalError):
        return "anthropic_internal"
    return "anthropic_unexpected"


# --- public API ------------------------------------------------------------


def chat(
    *,
    conversation_id: uuid.UUID | None,
    user_message: str,
    actor: Actor,
    request_id: str = "",
    trace_id: str = "",
) -> ChatOutcome:
    """Run one chat turn through the agent loop.

    See module docstring for the loop sequence. Never raises — every
    failure mode is a typed ``ChatError`` or a ``ChatOk`` with the
    loop-cap fallback message (research R3).
    """
    tracer = get_tracer()
    with tracer.start_as_current_span("chat.turn") as turn_span:
        actor_kind, actor_id = _actor_attrs(actor)
        turn_span.set_attribute("actor.kind", actor_kind)
        turn_span.set_attribute("actor.id", actor_id)
        turn_span.set_attribute("prompt_hash", PROMPT_HASH)
        if trace_id:
            turn_span.set_attribute("trace_id", trace_id)
        if request_id:
            turn_span.set_attribute("request_id", request_id)

        # 1. Resolve / validate the conversation row.
        resolved_id, convo_error = _resolve_conversation(
            conversation_id=conversation_id, actor=actor
        )
        if convo_error is not None:
            turn_span.set_attribute("result", "db_failed")
            return convo_error
        assert resolved_id is not None  # narrowed by success path
        turn_span.set_attribute("conversation_id", str(resolved_id))

        ttl_seconds = _ttl_for_actor(actor)

        # 2. Persist the user message to both Postgres and Redis.
        try:
            conversation_repository.append_message(
                conversation_id=resolved_id,
                role="user",
                content=user_message,
            )
            short_term_memory_service.append(
                resolved_id,
                "user",
                user_message,
                ttl_seconds=ttl_seconds,
            )
        except Exception as db_exc:  # noqa: BLE001 — typed mapping per Rule 11
            turn_span.set_attribute("result", "db_failed")
            turn_span.record_exception(db_exc)
            return ChatError(
                kind="db_failed",
                detail=str(db_exc),
                conversation_id=resolved_id,
            )

        # 3. Build the messages window. The user message just persisted is
        #    the tail of the window — get_window picks it up automatically.
        messages: list[dict[str, Any]] = _stm_window_to_messages(resolved_id)

        # 4. Agent loop.
        tool_trace: list[ToolTraceEntry] = []
        total_input_tokens = 0
        total_output_tokens = 0
        iteration = 0
        for iteration in range(MAX_TOOL_ITERATIONS):
            try:
                response = anthropic_client.tool_use_chat(
                    messages=messages,
                    tools=TOOLS,
                    system=PROMPT_TEXT,
                    model=CHAT_MODEL,
                    max_tokens=CHAT_MAX_TOKENS,
                )
            except AnthropicError as exc:
                kind = _map_anthropic_exc(exc)
                turn_span.set_attribute("result", kind)
                turn_span.record_exception(exc)
                return ChatError(
                    kind=kind,
                    detail=str(exc),
                    conversation_id=resolved_id,
                )

            total_input_tokens += response.usage_input_tokens
            total_output_tokens += response.usage_output_tokens

            if response.stop_reason == "end_turn":
                # Persist the assistant text, append to messages (for trace
                # completeness), and return.
                assistant_text = response.text
                try:
                    _persist_assistant_text(
                        conversation_id=resolved_id,
                        text=assistant_text,
                        ttl_seconds=ttl_seconds,
                    )
                except Exception as db_exc:  # noqa: BLE001
                    turn_span.set_attribute("result", "db_failed")
                    turn_span.record_exception(db_exc)
                    return ChatError(
                        kind="db_failed",
                        detail=str(db_exc),
                        conversation_id=resolved_id,
                    )
                messages.append({"role": "assistant", "content": assistant_text})
                turn_span.set_attribute("loop_iterations", iteration + 1)
                turn_span.set_attribute("loop_exhausted", False)
                turn_span.set_attribute("tokens_in", total_input_tokens)
                turn_span.set_attribute("tokens_out", total_output_tokens)
                turn_span.set_attribute("tool_calls", len(tool_trace))
                turn_span.set_attribute("result", "ok")
                return ChatOk(
                    assistant_message=assistant_text,
                    conversation_id=resolved_id,
                    tool_trace=tool_trace,
                )

            if response.stop_reason == "tool_use":
                # Append the assistant's raw content (including tool_use blocks)
                # so the model sees its prior tool_use blocks on the next turn.
                raw_content = (
                    getattr(response.raw, "content", None) if response.raw is not None else None
                )
                if raw_content is None:
                    raw_content = []
                messages.append({"role": "assistant", "content": raw_content})

                tool_result_blocks: list[dict[str, Any]] = []
                for block in response.tool_use_blocks:
                    with tracer.start_as_current_span("tool.execute") as tool_span:
                        tool_span.set_attribute("tool.name", block.name)
                        # Redact tool_input compactly for span attribute; raw
                        # input still lives in the messages-table row.
                        try:
                            tool_input_repr = redact_for_persistence(
                                json.dumps(block.input, default=str)
                            )
                        except (TypeError, ValueError):
                            tool_input_repr = "<unserializable>"
                        tool_span.set_attribute("tool.input", tool_input_repr)
                        tool_span.set_attribute("actor.kind", actor_kind)
                        tool_span.set_attribute("actor.id", actor_id)
                        tool_span.set_attribute("conversation_id", str(resolved_id))

                        dispatch = TOOLS_DISPATCH.get(block.name)
                        started = time.perf_counter()
                        if dispatch is None:
                            output: dict[str, Any] = {
                                "error": {
                                    "kind": "unknown_tool",
                                    "detail": block.name,
                                }
                            }
                        else:
                            # Per Phase B contract: dispatch never raises.
                            output = dispatch(block.input, actor, resolved_id)
                        latency_ms = int((time.perf_counter() - started) * 1000)
                        is_error = "error" in output
                        tool_span.set_attribute("latency_ms", latency_ms)
                        tool_span.set_attribute("is_error", is_error)
                        if is_error:
                            err = output.get("error") or {}
                            err_kind = err.get("kind") if isinstance(err, dict) else None
                            if isinstance(err_kind, str):
                                tool_span.set_attribute("error.kind", err_kind)

                        # Persist the tool message + STM. If persistence
                        # itself fails, surface as db_failed (Rule 11).
                        try:
                            _persist_tool_message(
                                conversation_id=resolved_id,
                                tool_name=block.name,
                                tool_input=block.input,
                                tool_output=output,
                                ttl_seconds=ttl_seconds,
                            )
                        except Exception as db_exc:  # noqa: BLE001
                            turn_span.set_attribute("result", "db_failed")
                            turn_span.record_exception(db_exc)
                            return ChatError(
                                kind="db_failed",
                                detail=str(db_exc),
                                conversation_id=resolved_id,
                            )

                        tool_trace.append(
                            ToolTraceEntry(
                                tool_name=block.name,
                                input=block.input,
                                output=output,
                                latency_ms=latency_ms,
                                is_error=is_error,
                            )
                        )
                        tool_result_blocks.append(
                            {
                                "type": "tool_result",
                                "tool_use_id": block.id,
                                "content": json.dumps(output, default=str),
                                "is_error": is_error,
                            }
                        )

                # Anthropic protocol: every tool_result for the prior
                # assistant turn goes into ONE user-role message.
                if tool_result_blocks:
                    messages.append(
                        {"role": "user", "content": tool_result_blocks}
                    )
                # Continue the loop — re-call Anthropic with the new context.
                continue

            # Any other stop_reason (max_tokens / stop_sequence) — treat as
            # protocol failure; surface as unexpected so the operator can
            # see it in Phoenix.
            turn_span.set_attribute("result", "anthropic_unexpected")
            turn_span.set_attribute("anthropic.stop_reason", response.stop_reason)
            return ChatError(
                kind="anthropic_unexpected",
                detail=f"unexpected stop_reason: {response.stop_reason}",
                conversation_id=resolved_id,
            )

        # 5. Loop cap exhaustion (research R3). The for-loop's else clause
        #    runs when the loop completed all iterations without break.
        try:
            _persist_assistant_text(
                conversation_id=resolved_id,
                text=LOOP_EXHAUSTED_FALLBACK,
                ttl_seconds=ttl_seconds,
            )
        except Exception as db_exc:  # noqa: BLE001
            turn_span.set_attribute("result", "db_failed")
            turn_span.record_exception(db_exc)
            return ChatError(
                kind="db_failed",
                detail=str(db_exc),
                conversation_id=resolved_id,
            )
        turn_span.set_attribute("loop_iterations", MAX_TOOL_ITERATIONS)
        turn_span.set_attribute("loop_exhausted", True)
        turn_span.set_attribute("tokens_in", total_input_tokens)
        turn_span.set_attribute("tokens_out", total_output_tokens)
        turn_span.set_attribute("tool_calls", len(tool_trace))
        turn_span.set_attribute("result", "loop_exhausted")
        return ChatOk(
            assistant_message=LOOP_EXHAUSTED_FALLBACK,
            conversation_id=resolved_id,
            tool_trace=tool_trace,
        )


__all__ = [
    "CHAT_MAX_TOKENS",
    "CHAT_MODEL",
    "ChatError",
    "ChatErrorKind",
    "ChatOk",
    "ChatOutcome",
    "LOOP_EXHAUSTED_FALLBACK",
    "MAX_TOOL_ITERATIONS",
    "PROMPT_HASH",
    "PROMPT_PATH",
    "PROMPT_TEXT",
    "TTL_AUTHED_SECONDS",
    "TTL_WIDGET_SECONDS",
    "WINDOW_MAX_TOKENS",
    "WINDOW_MESSAGE_CAP",
    "chat",
]
