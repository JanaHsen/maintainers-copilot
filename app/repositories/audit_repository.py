"""Audit repository — append-only writes to ``audit_log`` (Rule 1, R3).

The migration revoked UPDATE and DELETE on ``audit_log`` for PUBLIC and
granted only INSERT and SELECT. This repository enforces the same shape at
the application boundary:

  * :func:`record` is the only entrypoint for writes. It validates that
    exactly one of ``actor_user_id`` / ``actor_widget_id`` is set
    (research R3 — application-level mutual exclusivity; no SQL CHECK
    because the legacy ``actor_id``-only rows would violate it).
  * :func:`update` / :func:`delete` raise :class:`AuditLogImmutableError`
    so a caller cannot pretend the repo supports mutation.

The legacy ``actor_id`` and ``target`` columns from migration 0001 are left
NULL by Part 1 writes; new code uses ``actor_user_id`` / ``actor_widget_id``
and ``target_type`` / ``target_id``.
"""

from __future__ import annotations

import json
import uuid
from typing import Any, NoReturn

from sqlalchemy import text

from app.infra.database import get_engine


class AuditLogImmutableError(RuntimeError):
    """Raised when a caller attempts to mutate or delete an audit row.

    The same constraint is enforced at the role level by the migration's
    ``REVOKE UPDATE, DELETE`` — this exception is the typed, fast-fail
    surface so application code does not have to round-trip to Postgres
    just to discover the prohibition.
    """


_INSERT_SQL = text(
    """
    INSERT INTO audit_log
      (action, target_type, target_id, payload,
       actor_user_id, actor_widget_id)
    VALUES
      (:action, :target_type, :target_id, CAST(:payload AS JSONB),
       :actor_user_id, :actor_widget_id)
    """
)


def record(
    *,
    action: str,
    target_type: str | None,
    target_id: str | None,
    payload: dict[str, Any] | None,
    actor_user_id: uuid.UUID | None = None,
    actor_widget_id: uuid.UUID | None = None,
) -> None:
    """Append one ``audit_log`` row.

    Raises :class:`ValueError` if both actor ids are set or if neither is
    set — that invariant is enforced at the application layer (research R3).

    The legacy ``actor_id`` / ``target`` columns from migration 0001 are left
    NULL; Part 1 writes go exclusively to the new columns.
    """
    user_set = actor_user_id is not None
    widget_set = actor_widget_id is not None
    if user_set and widget_set:
        raise ValueError(
            "audit_repository.record(): exactly one of actor_user_id / "
            "actor_widget_id must be set, both were provided"
        )
    if not user_set and not widget_set:
        raise ValueError(
            "audit_repository.record(): exactly one of actor_user_id / "
            "actor_widget_id must be set, neither was provided"
        )

    with get_engine().begin() as conn:
        conn.execute(
            _INSERT_SQL,
            {
                "action": action,
                "target_type": target_type,
                "target_id": target_id,
                "payload": None if payload is None else json.dumps(payload),
                "actor_user_id": actor_user_id,
                "actor_widget_id": actor_widget_id,
            },
        )


def update(*args: Any, **kwargs: Any) -> NoReturn:
    """audit_log is append-only — this always raises.

    Mirrored at the role level by the migration's ``REVOKE UPDATE``; this
    function is the typed surface for callers that might otherwise build
    SQL by hand.
    """
    raise AuditLogImmutableError(
        "audit_log is append-only — UPDATE is not supported (research R3)"
    )


def delete(*args: Any, **kwargs: Any) -> NoReturn:
    """audit_log is append-only — this always raises."""
    raise AuditLogImmutableError(
        "audit_log is append-only — DELETE is not supported (research R3)"
    )
