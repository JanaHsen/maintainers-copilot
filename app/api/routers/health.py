"""GET /health — calls the health service only (Rule 1).

Always HTTP 200 while the process is up; the body conveys ok/degraded.
The trace id is read here (inside the request span) rather than in the
middleware, which runs outside it.
"""

from fastapi import APIRouter, Request, Response

from app.domain.health import HealthReport
from app.infra.request_context import REQUEST_ID_HEADER, TRACE_ID_HEADER
from app.infra.tracing import current_trace_id
from app.services.health_service import build_health_report

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthReport)
def get_health(request: Request, response: Response) -> HealthReport:
    request_id = getattr(request.state, "request_id", "")
    trace_id = current_trace_id()
    response.headers[REQUEST_ID_HEADER] = request_id
    response.headers[TRACE_ID_HEADER] = trace_id
    return build_health_report(request_id=request_id, trace_id=trace_id)
