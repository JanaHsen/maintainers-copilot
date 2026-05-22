"""Widget repository — the only place ``widgets`` SQL lives (Rule 1).

Three functions:

  * :func:`create` — issue a new widget. Generates a 32-byte URL-safe
    token (research R5), stores its sha256 hex hash, returns
    ``(widget_id, plaintext_token)``. Plaintext is returned exactly once
    here; the server never stores it.
  * :func:`get_by_token_hash` — look up a non-revoked widget by sha256
    hash. Revoked rows are intentionally excluded.
  * :func:`revoke` — set ``revoked_at = now()`` on the widget. Idempotent
    against repeat calls (subsequent revokes simply overwrite the
    timestamp).

The unique partial index ``ux_widgets_active_token`` enforces "no two
non-revoked widgets share a host_token_hash" at the DB level; this module
relies on that index rather than re-deriving uniqueness in SQL.
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import datetime

from pydantic import BaseModel
from sqlalchemy import text

from app.infra.database import get_engine


class Widget(BaseModel):
    """One ``widgets`` row.

    Mirrors the migration's columns. ``revoked_at`` is ``None`` for active
    widgets; a non-None value means the host-token has been revoked and the
    row no longer serves authentication.
    """

    id: uuid.UUID
    name: str
    host_token_hash: str
    allowed_origins: list[str]
    owner_user_id: uuid.UUID
    created_at: datetime
    revoked_at: datetime | None


_INSERT_WIDGET_SQL = text(
    """
    INSERT INTO widgets
      (id, name, host_token_hash, allowed_origins, owner_user_id)
    VALUES
      (:id, :name, :hash, :origins, :owner)
    """
)


_GET_BY_TOKEN_HASH_SQL = text(
    """
    SELECT id, name, host_token_hash, allowed_origins, owner_user_id,
           created_at, revoked_at
    FROM widgets
    WHERE host_token_hash = :hash
      AND revoked_at IS NULL
    """
)


_REVOKE_SQL = text(
    "UPDATE widgets SET revoked_at = now() WHERE id = :id"
)


def _hash_token(plaintext: str) -> str:
    return hashlib.sha256(plaintext.encode("utf-8")).hexdigest()


def create(
    *,
    name: str,
    allowed_origins: list[str],
    owner_user_id: uuid.UUID,
) -> tuple[uuid.UUID, str]:
    """Issue a new widget and return ``(widget_id, plaintext_token)``.

    The plaintext is shown to the operator once at create time and never
    persisted; only the sha256 hex hash lands in ``widgets.host_token_hash``
    (research R5).
    """
    widget_id = uuid.uuid4()
    plaintext = secrets.token_urlsafe(32)  # 43-char base64-url, 256 bits
    token_hash = _hash_token(plaintext)
    with get_engine().begin() as conn:
        conn.execute(
            _INSERT_WIDGET_SQL,
            {
                "id": widget_id,
                "name": name,
                "hash": token_hash,
                "origins": allowed_origins,
                "owner": owner_user_id,
            },
        )
    return widget_id, plaintext


def get_by_token_hash(token_hash: str) -> Widget | None:
    """Return the matching non-revoked widget, or ``None`` if absent/revoked."""
    with get_engine().connect() as conn:
        row = conn.execute(
            _GET_BY_TOKEN_HASH_SQL, {"hash": token_hash}
        ).first()
    if row is None:
        return None
    return Widget(
        id=row.id,
        name=row.name,
        host_token_hash=row.host_token_hash,
        allowed_origins=list(row.allowed_origins) if row.allowed_origins else [],
        owner_user_id=row.owner_user_id,
        created_at=row.created_at,
        revoked_at=row.revoked_at,
    )


def revoke(widget_id: uuid.UUID) -> None:
    """Mark the widget as revoked. Subsequent lookups by hash skip it."""
    with get_engine().begin() as conn:
        conn.execute(_REVOKE_SQL, {"id": widget_id})
