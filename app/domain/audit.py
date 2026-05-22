"""Pydantic domain models for audit-log entries.

Aligned with the migration's evolved ``audit_log`` table (data-model.md §6
and research R3 — additive evolution). The table is append-only at the
role level (``REVOKE UPDATE, DELETE``); writes go through
``app/repositories/audit_repository.py`` which also enforces the
exactly-one-actor invariant at the application boundary.

Two types:

  * :class:`AuditAction` — closed literal of the action strings Part 1
    code emits. New action strings must be added here when the surface
    grows; the literal is what router / service layer code uses to ensure
    typos surface at type-check time.
  * :class:`AuditEntry` — one ``audit_log`` row. Exactly one of
    ``actor_user_id`` / ``actor_widget_id`` is set; the invariant is
    documented but not Pydantic-enforced because some legacy rows
    (migration 0001-era) only populated the deprecated ``actor_id`` text
    column — the model leaves both new actor fields optional to permit
    safely deserializing legacy rows.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel

AuditAction = Literal[
    "memory.write",
    "widget.create",
    "widget.revoke",
    "user.role_changed",
]
"""Closed literal of the action strings Part 1 code writes.

Each value corresponds to a specific call site:
  * ``memory.write`` — emitted inside ``write_memory_tool`` after the memory
    row commits (FR-021).
  * ``widget.create`` — emitted when an admin issues a new widget.
  * ``widget.revoke`` — emitted when an admin revokes a widget.
  * ``user.role_changed`` — emitted when an admin promotes/demotes a user
    via the role endpoint (Part 1 phase C).
"""


class AuditEntry(BaseModel):
    """One ``audit_log`` row as returned by SELECT.

    The new (post-0003) columns are first-class; the legacy ``actor_id`` /
    ``target`` text columns from migration 0001 are exposed so a future
    Part 3 admin UI can render historical rows that predate the schema
    evolution.
    """

    id: int
    action: AuditAction | str  # 'str' fallback for legacy rows with unknown action
    actor_user_id: uuid.UUID | None = None
    actor_widget_id: uuid.UUID | None = None
    target_type: str | None = None
    target_id: str | None = None
    payload: dict[str, Any] | None = None
    timestamp: datetime
    # Legacy columns (migration 0001), kept for read-through compatibility.
    actor_id: str | None = None
    target: str | None = None
