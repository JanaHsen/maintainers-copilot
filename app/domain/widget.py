"""Pydantic domain models for embeddable widgets.

The widget repository (``app/repositories/widget_repository.py``) defines
its own internal :class:`app.repositories.widget_repository.Widget` model
mirroring the DB row; this module exposes the shapes used at the service
and router boundaries.

Three shapes:

  * :class:`Widget` — public read view, no plaintext token.
  * :class:`WidgetCreate` — request body for the create endpoint
    (Part 3 / admin panel).
  * :class:`WidgetCreated` — response payload of the create endpoint;
    contains the plaintext token returned once at create time (research
    R5 — the server never persists the plaintext after the create call
    returns).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class Widget(BaseModel):
    """Public read view of a widget. No plaintext token here."""

    id: uuid.UUID
    name: str
    allowed_origins: list[str]
    owner_user_id: uuid.UUID
    created_at: datetime
    revoked_at: datetime | None


class WidgetCreate(BaseModel):
    """Request shape for ``POST /widgets`` (Part 3)."""

    name: str = Field(..., min_length=1, max_length=128)
    allowed_origins: list[str] = Field(default_factory=list)


class WidgetCreated(BaseModel):
    """Response shape for ``POST /widgets``.

    Includes ``host_token`` (the plaintext) exactly once at create time;
    the server's only persisted artifact is ``sha256(host_token)`` in
    ``widgets.host_token_hash`` (research R5). The admin UI surfaces this
    field with a "copy now, you won't see it again" warning.
    """

    id: uuid.UUID
    name: str
    allowed_origins: list[str]
    owner_user_id: uuid.UUID
    created_at: datetime
    host_token: str
