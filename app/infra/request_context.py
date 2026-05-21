"""Per-request correlation: a request id and the active OTel trace id.

Implemented as a *pure ASGI* middleware on purpose: Starlette's
BaseHTTPMiddleware runs the downstream app in a separate task, which drops
the OpenTelemetry context so the request span is invisible to handlers. A
plain ASGI middleware stays in the same context, so the trace id is real.

Every response carries ``X-Request-Id`` and ``X-Trace-Id``; the values are
also exposed via context vars (logging) and ``request.state`` (handlers).
"""

import uuid
from contextvars import ContextVar

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.infra.tracing import current_trace_id

REQUEST_ID_HEADER = "X-Request-Id"
TRACE_ID_HEADER = "X-Trace-Id"

_request_id_ctx: ContextVar[str] = ContextVar("request_id", default="")
_trace_id_ctx: ContextVar[str] = ContextVar("trace_id", default="")


def get_request_id() -> str:
    return _request_id_ctx.get()


def get_trace_id() -> str:
    return _trace_id_ctx.get()


class RequestContextMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        incoming = {k.decode(): v.decode() for k, v in scope.get("headers", [])}
        request_id = incoming.get(REQUEST_ID_HEADER.lower()) or uuid.uuid4().hex

        state = scope.setdefault("state", {})
        state["request_id"] = request_id
        rid_token = _request_id_ctx.set(request_id)

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                trace_id = current_trace_id()
                state["trace_id"] = trace_id
                _trace_id_ctx.set(trace_id)
                headers = MutableHeaders(scope=message)
                headers[REQUEST_ID_HEADER] = request_id
                headers[TRACE_ID_HEADER] = trace_id
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            _request_id_ctx.reset(rid_token)
