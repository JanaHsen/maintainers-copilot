"""write_memory tool primitive (T020).

Implements ``contracts/memory-tools.md`` exactly: typed-outcome function
that orchestrates actor-guard → redact → embed → single-transaction
(memory insert + audit insert) → Phoenix span. Never raises (Rule 11);
every failure mode is one of ``WriteMemoryError(kind=…)``.

The persistence-boundary redaction (``redact_for_persistence``) applies
to ``content`` BEFORE embedding (research R6 / Rule 7) so the embedding,
the persisted ``chatbot_memories.content``, AND the ``content_bytes`` audit
payload all reflect the redacted form.

The transaction is opened on the sync engine; both the memory insert and
the audit insert share the same ``Connection`` so they commit or roll
back atomically (FR-021).
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from typing import Any, Literal

from app.domain.conversation import Actor, AuthedUser, WidgetSession
from app.infra.database import get_engine
from app.infra.embedding_client import embed as _default_embed
from app.infra.log_redaction import redact_for_persistence
from app.infra.model_server_client import (
    ModelServerError,
    ModelServerTimeoutError,
    ModelServerUnreachableError,
)
from app.infra.tracing import get_tracer
from app.repositories import audit_repository, memory_repository

WriteMemoryErrorKind = Literal[
    "widget_actor_forbidden",
    "embedding_unreachable",
    "embedding_timeout",
    "audit_failed",
    "db_failed",
]


# Anthropic tool definition. The chatbot agent loop (Part 2) registers this
# alongside the other five wrappers via app.services.tools.TOOLS. Adding the
# constant here keeps the tool def colocated with the executor it describes
# (Rule 9).
TOOL_DEF: dict[str, Any] = {
    "name": "write_memory",
    "description": (
        "Save a fact about the user for future conversations. Use sparingly: "
        "only when the user shares a preference, an identity, or context "
        "likely to be useful later. Do not write trivial chat."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "content": {"type": "string"},
        },
        "required": ["content"],
    },
}


@dataclass(frozen=True)
class WriteMemoryOk:
    """Success outcome: a memory was persisted and an audit row written."""

    memory_id: uuid.UUID


@dataclass(frozen=True)
class WriteMemoryError:
    """Typed failure outcome — see ``WriteMemoryErrorKind`` for the cases."""

    kind: WriteMemoryErrorKind
    detail: str


WriteMemoryOutcome = WriteMemoryOk | WriteMemoryError


def write_memory(
    *,
    content: str,
    actor: Actor,
    conversation_id: uuid.UUID,
    source: Literal["episodic"] = "episodic",
    request_id: str = "",
    trace_id: str = "",
) -> WriteMemoryOutcome:
    """Persist one long-term memory for an authenticated maintainer.

    Steps (in order, per ``contracts/memory-tools.md``):

      1. Actor-kind guard: ``WidgetSession`` is refused before any DB work.
      2. ``redact_for_persistence`` on ``content`` (research R6 / Rule 7).
      3. Embed the redacted content via ``embedding_client.embed``.
      4. Single transaction: insert the memory row + the audit row; commit
         atomically (FR-021). On any DB exception the transaction rolls
         back and the function returns ``db_failed`` (or ``audit_failed``
         specifically if the audit insert raised).
      5. Phoenix span ``memory.write`` with attributes captured per the
         contract.
      6. Return ``WriteMemoryOk(memory_id=…)``.

    Never raises — every failure mode is a typed ``WriteMemoryError``.
    """
    tracer = get_tracer()
    with tracer.start_as_current_span("memory.write") as span:
        # Actor-kind attributes regardless of branch taken.
        if isinstance(actor, WidgetSession):
            span.set_attribute("actor.kind", "widget")
            span.set_attribute("actor.id", str(actor.widget_id))
        else:  # AuthedUser
            span.set_attribute("actor.kind", "authed")
            span.set_attribute("actor.id", str(actor.user_id))
        span.set_attribute("conversation_id", str(conversation_id))
        span.set_attribute("source", source)

        # 1. Widget actor refusal — short-circuit before any side effect.
        if isinstance(actor, WidgetSession):
            span.set_attribute("content_bytes", 0)
            span.set_attribute("result", "widget_actor_forbidden")
            return WriteMemoryError(
                kind="widget_actor_forbidden",
                detail="widget sessions cannot write long-term memory",
            )

        # 2. Redact-before-persistence (R6). content_bytes reflects the
        #    redacted form so the audit payload matches the persisted row.
        redacted_content = redact_for_persistence(content)
        content_bytes = len(redacted_content.encode("utf-8"))
        span.set_attribute("content_bytes", content_bytes)

        # 3. Embed.
        try:
            embedding = _default_embed(redacted_content, request_id=request_id)
        except ModelServerTimeoutError as exc:
            span.set_attribute("result", "embedding_timeout")
            span.record_exception(exc)
            return WriteMemoryError(kind="embedding_timeout", detail=str(exc))
        except ModelServerUnreachableError as exc:
            span.set_attribute("result", "embedding_unreachable")
            span.record_exception(exc)
            return WriteMemoryError(
                kind="embedding_unreachable", detail=str(exc)
            )
        except ModelServerError as exc:
            # Treat any other model-server error (5xx, malformed body,
            # invalid-input on a redacted string) as "unreachable" from the
            # caller's standpoint — they cannot fix it by retrying shape.
            span.set_attribute("result", "embedding_unreachable")
            span.record_exception(exc)
            return WriteMemoryError(
                kind="embedding_unreachable", detail=str(exc)
            )

        # 4. Single transaction: memory insert + audit insert.
        memory_id = uuid.uuid4()
        assert isinstance(actor, AuthedUser)  # narrowed by guard above
        try:
            with get_engine().begin() as conn:
                memory_repository.insert(
                    memory_id=memory_id,
                    user_id=actor.user_id,
                    conversation_id=conversation_id,
                    content=redacted_content,
                    embedding=embedding,
                    source=source,
                    connection=conn,
                )
                try:
                    audit_repository.record(
                        action="memory.write",
                        target_type="memory",
                        target_id=str(memory_id),
                        payload={
                            "conversation_id": str(conversation_id),
                            "memory_id": str(memory_id),
                            # sha256 of the REDACTED content (Part 2 brief
                            # §7). Lets the admin panel correlate audit rows
                            # to chatbot_memories rows without leaking raw
                            # content into the payload.
                            "content_hash": hashlib.sha256(
                                redacted_content.encode("utf-8")
                            ).hexdigest(),
                            "content_bytes": content_bytes,
                            "source": source,
                            "trace_id": trace_id,
                            "request_id": request_id,
                        },
                        actor_user_id=actor.user_id,
                        connection=conn,
                    )
                except Exception as audit_exc:  # noqa: BLE001 — typed mapping
                    span.set_attribute("result", "audit_failed")
                    span.record_exception(audit_exc)
                    raise _AuditFailedError(str(audit_exc)) from audit_exc
        except _AuditFailedError as exc:
            return WriteMemoryError(kind="audit_failed", detail=exc.detail)
        except Exception as db_exc:  # noqa: BLE001 — typed mapping per Rule 11
            span.set_attribute("result", "db_failed")
            span.record_exception(db_exc)
            return WriteMemoryError(kind="db_failed", detail=str(db_exc))

        span.set_attribute("result", "ok")
        return WriteMemoryOk(memory_id=memory_id)


class _AuditFailedError(Exception):
    """Internal sentinel to distinguish audit-insert failures from memory-insert.

    Raised inside the ``with get_engine().begin()`` block so SQLAlchemy rolls
    the transaction back; caught by the outer ``except _AuditFailedError`` so the
    public outcome is ``WriteMemoryError(kind="audit_failed")`` rather than
    the generic ``db_failed``.
    """

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


__all__ = [
    "TOOL_DEF",
    "WriteMemoryError",
    "WriteMemoryErrorKind",
    "WriteMemoryOk",
    "WriteMemoryOutcome",
    "write_memory",
]
