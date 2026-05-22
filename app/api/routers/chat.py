"""POST /chat and POST /widget/chat — chat router (HTTP only).

Rule 1: routers map service outcomes to HTTP statuses; the agent loop
lives in :mod:`app.services.chatbot_service`. Rule 11: every typed
``ChatError`` kind maps to a single status via :data:`_KIND_TO_STATUS`.

Two endpoints, one chatbot:

  * ``POST /chat`` — authed maintainer's session-cookie-protected route.
    ``current_active_user`` from fastapi-users gates the call; the user's
    ``id`` + ``role`` become the :class:`AuthedUser` actor handed to the
    agent loop.

  * ``POST /widget/chat`` — anonymous widget visitor's route. The router
    validates two things before reaching the service: the
    ``X-Widget-Token`` header (sha256-compared against
    ``widget_repository.get_by_token_hash``) and the request ``Origin``
    (must be in ``widget.allowed_origins`` when present). The
    :class:`WidgetSession` actor it builds carries ``widget_id`` +
    ``session_id``; the agent loop pins all messages from one visitor
    session to one ``conversations`` row via
    ``conversation_repository.get_by_widget_session`` (spec §3).
"""

from __future__ import annotations

import hashlib

from fastapi import APIRouter, Depends, Header, HTTPException, status

from app.domain.chat import (
    ChatRequestAuthed,
    ChatRequestWidget,
    ChatResponse,
)
from app.domain.conversation import AuthedUser, WidgetSession
from app.infra.auth_backend import current_active_user
from app.infra.request_context import get_request_id, get_trace_id
from app.repositories import conversation_repository, widget_repository
from app.repositories.user_repository import User
from app.services import chatbot_service

router = APIRouter()


_KIND_TO_STATUS: dict[str, int] = {
    "anthropic_unreachable": status.HTTP_503_SERVICE_UNAVAILABLE,
    "anthropic_timeout": status.HTTP_504_GATEWAY_TIMEOUT,
    "anthropic_bad_request": status.HTTP_502_BAD_GATEWAY,
    "anthropic_internal": status.HTTP_502_BAD_GATEWAY,
    "anthropic_unexpected": status.HTTP_502_BAD_GATEWAY,
    "db_failed": status.HTTP_500_INTERNAL_SERVER_ERROR,
}


def _to_response(outcome: chatbot_service.ChatOutcome) -> ChatResponse:
    """Map a typed ``ChatOutcome`` to an HTTP shape per Rule 11."""
    if isinstance(outcome, chatbot_service.ChatError):
        raise HTTPException(
            status_code=_KIND_TO_STATUS.get(
                outcome.kind, status.HTTP_502_BAD_GATEWAY
            ),
            detail={
                "kind": outcome.kind,
                "detail": outcome.detail,
                "request_id": get_request_id(),
                "trace_id": get_trace_id(),
            },
        )
    return ChatResponse(
        assistant_message=outcome.assistant_message,
        conversation_id=outcome.conversation_id,
        tool_trace=list(outcome.tool_trace),
        request_id=get_request_id(),
        trace_id=get_trace_id(),
    )


@router.post("/chat", response_model=ChatResponse, tags=["chat"])
async def chat_authed(
    req: ChatRequestAuthed,
    user: User = Depends(current_active_user),  # noqa: B008 — FastAPI DI pattern
) -> ChatResponse:
    """One turn of an authed maintainer's conversation (FR-001)."""
    actor = AuthedUser(user_id=user.id, role=user.role)
    outcome = chatbot_service.chat(
        conversation_id=req.conversation_id,
        user_message=req.message,
        actor=actor,
        request_id=get_request_id(),
        trace_id=get_trace_id(),
    )
    return _to_response(outcome)


@router.post("/widget/chat", response_model=ChatResponse, tags=["chat"])
async def chat_widget(
    req: ChatRequestWidget,
    x_widget_token: str = Header(..., alias="X-Widget-Token"),
    origin: str | None = Header(None, alias="Origin"),
) -> ChatResponse:
    """One turn of an anonymous widget visitor's conversation (FR-002, FR-003).

    Three guards run before the agent loop:

      1. Token lookup: sha256(plaintext) → ``widget_repository.get_by_token_hash``;
         revoked or unknown tokens return 401.
      2. Origin check: if the ``Origin`` header is set, it MUST appear in
         ``widget.allowed_origins``. Same-origin / server-to-server calls
         may omit ``Origin`` — those are accepted (browsers always send
         it on cross-origin requests).
      3. Conversation reuse: look up the conversation by
         ``(widget_id, session_id)`` so a single visitor session
         accumulates context across messages (spec §3). If no row exists
         yet, the chatbot service creates one on the first turn.
    """
    # 1. Token lookup.
    token_hash = hashlib.sha256(x_widget_token.encode("utf-8")).hexdigest()
    widget = widget_repository.get_by_token_hash(token_hash)
    if widget is None or widget.id != req.widget_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid or revoked widget token",
        )

    # 2. Origin check.
    if origin is not None and origin not in widget.allowed_origins:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="origin not in widget.allowed_origins",
        )

    # 3. Conversation reuse: one row per (widget_id, session_id).
    conversation_id = conversation_repository.get_by_widget_session(
        widget.id, req.session_id
    )

    actor = WidgetSession(widget_id=widget.id, session_id=req.session_id)
    outcome = chatbot_service.chat(
        conversation_id=conversation_id,
        user_message=req.message,
        actor=actor,
        request_id=get_request_id(),
        trace_id=get_trace_id(),
    )
    return _to_response(outcome)
