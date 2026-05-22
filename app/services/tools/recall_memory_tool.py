"""recall_memory tool primitive (T021).

Implements ``contracts/memory-tools.md`` exactly: typed-outcome function
that orchestrates actor-guard → embed query → ``memory_repository.query_top_k``
→ Phoenix span. Never raises (Rule 11); every failure mode is one of
``RecallMemoryError(kind=…)``.

Unlike ``write_memory_tool``, no persistence-boundary redaction is applied to
the query string: the query never leaves the request lifetime (it is not
persisted), and the log-handler redaction layer still applies to any log
emission per Rule 7 / research R6.

Cross-account isolation is enforced inside ``memory_repository.query_top_k``
via the ``WHERE user_id = :user_id`` clause (FR-010, SC-003). This module
trusts that boundary and never reads memories outside the caller's user_id.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from app.domain.conversation import Actor, AuthedUser, WidgetSession
from app.domain.memory import MemoryRecallHit
from app.infra.embedding_client import embed as _default_embed
from app.infra.model_server_client import (
    ModelServerError,
    ModelServerTimeoutError,
    ModelServerUnreachableError,
)
from app.infra.tracing import get_tracer
from app.repositories import memory_repository

RecallMemoryErrorKind = Literal[
    "widget_actor_forbidden",
    "embedding_unreachable",
    "embedding_timeout",
    "db_failed",
]


@dataclass(frozen=True)
class RecallMemoryOk:
    """Success outcome: a (possibly empty) list of cosine-similarity-ranked hits."""

    hits: list[MemoryRecallHit]


@dataclass(frozen=True)
class RecallMemoryError:
    """Typed failure outcome — see ``RecallMemoryErrorKind`` for the cases."""

    kind: RecallMemoryErrorKind
    detail: str


RecallMemoryOutcome = RecallMemoryOk | RecallMemoryError


def recall_memory(
    *,
    query: str,
    actor: Actor,
    k: int = 5,
    request_id: str = "",
    trace_id: str = "",  # noqa: ARG001 — accepted per contract for parity with write_memory
) -> RecallMemoryOutcome:
    """Return the top ``k`` memories for an authenticated maintainer.

    Steps (in order, per ``contracts/memory-tools.md``):

      1. Actor-kind guard: ``WidgetSession`` is refused before any DB work.
      2. Embed the query via ``embedding_client.embed``. Map
         ``ModelServerTimeoutError`` → ``embedding_timeout``; any other
         ``ModelServerError`` family member (unreachable, internal, malformed
         response) → ``embedding_unreachable``.
      3. Call ``memory_repository.query_top_k(user_id=…, query_embedding=…, k=k)``.
         Cross-account isolation lives in the repository's SQL ``WHERE user_id``
         clause (FR-010, SC-003).
      4. Phoenix span ``memory.recall`` with attributes ``actor.kind``,
         ``actor.id``, ``k``, ``hits_returned``, ``top_similarity`` (only when
         at least one hit), and ``result``.
      5. Return ``RecallMemoryOk(hits=…)`` (possibly empty).

    Never raises — every failure mode is a typed ``RecallMemoryError``.
    """
    tracer = get_tracer()
    with tracer.start_as_current_span("memory.recall") as span:
        # Actor-kind attributes regardless of branch taken.
        if isinstance(actor, WidgetSession):
            span.set_attribute("actor.kind", "widget")
            span.set_attribute("actor.id", str(actor.widget_id))
        else:  # AuthedUser
            span.set_attribute("actor.kind", "authed")
            span.set_attribute("actor.id", str(actor.user_id))
        span.set_attribute("k", k)

        # 1. Widget actor refusal — short-circuit before any side effect.
        if isinstance(actor, WidgetSession):
            span.set_attribute("hits_returned", 0)
            span.set_attribute("result", "widget_actor_forbidden")
            return RecallMemoryError(
                kind="widget_actor_forbidden",
                detail="widget sessions cannot read long-term memory",
            )

        # 2. Embed the query.
        try:
            query_embedding = _default_embed(query, request_id=request_id)
        except ModelServerTimeoutError as exc:
            span.set_attribute("result", "embedding_timeout")
            span.record_exception(exc)
            return RecallMemoryError(kind="embedding_timeout", detail=str(exc))
        except ModelServerUnreachableError as exc:
            span.set_attribute("result", "embedding_unreachable")
            span.record_exception(exc)
            return RecallMemoryError(
                kind="embedding_unreachable", detail=str(exc)
            )
        except ModelServerError as exc:
            # Treat any other model-server error (5xx, malformed body, 4xx on
            # an empty query) as "unreachable" from the caller's standpoint —
            # consistent with write_memory_tool's mapping.
            span.set_attribute("result", "embedding_unreachable")
            span.record_exception(exc)
            return RecallMemoryError(
                kind="embedding_unreachable", detail=str(exc)
            )

        # 3. Top-k lookup. The repository scopes by user_id at the SQL boundary;
        #    any exception is mapped to db_failed per Rule 11.
        assert isinstance(actor, AuthedUser)  # narrowed by guard above
        try:
            hits = memory_repository.query_top_k(
                user_id=actor.user_id,
                query_embedding=query_embedding,
                k=k,
            )
        except Exception as db_exc:  # noqa: BLE001 — typed mapping per Rule 11
            span.set_attribute("result", "db_failed")
            span.record_exception(db_exc)
            return RecallMemoryError(kind="db_failed", detail=str(db_exc))

        # 4. Span attributes + result.
        span.set_attribute("hits_returned", len(hits))
        if hits:
            span.set_attribute("top_similarity", hits[0].similarity)
        span.set_attribute("result", "ok")
        return RecallMemoryOk(hits=hits)


__all__ = [
    "RecallMemoryError",
    "RecallMemoryErrorKind",
    "RecallMemoryOk",
    "RecallMemoryOutcome",
    "recall_memory",
]
