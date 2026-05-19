"""Per-request correlation: a request id and the active OTel trace id.

Every response carries ``X-Request-Id`` and ``X-Trace-Id`` so a log line, a
Phoenix trace, and an HTTP response can all be cross-referenced (Rule 7).
The values are also exposed via context vars for the logging path and via
``request.state`` for handlers (e.g. the /health report).
"""

import uuid
from contextvars import ContextVar

from opentelemetry import trace
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

REQUEST_ID_HEADER = "X-Request-Id"
TRACE_ID_HEADER = "X-Trace-Id"

_request_id_ctx: ContextVar[str] = ContextVar("request_id", default="")
_trace_id_ctx: ContextVar[str] = ContextVar("trace_id", default="")


def get_request_id() -> str:
    return _request_id_ctx.get()


def get_trace_id() -> str:
    return _trace_id_ctx.get()


def _current_trace_id() -> str:
    span_context = trace.get_current_span().get_span_context()
    if span_context.trace_id:
        return format(span_context.trace_id, "032x")
    return ""


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        request_id = request.headers.get(REQUEST_ID_HEADER) or uuid.uuid4().hex
        trace_id = _current_trace_id()

        request.state.request_id = request_id
        request.state.trace_id = trace_id
        rid_token = _request_id_ctx.set(request_id)
        tid_token = _trace_id_ctx.set(trace_id)
        try:
            response = await call_next(request)
        finally:
            _request_id_ctx.reset(rid_token)
            _trace_id_ctx.reset(tid_token)

        response.headers[REQUEST_ID_HEADER] = request_id
        response.headers[TRACE_ID_HEADER] = trace_id
        return response
