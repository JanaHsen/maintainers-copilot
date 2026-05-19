"""GET /health — calls the health service only (Rule 1).

Always HTTP 200 while the process is up; the body conveys ok/degraded.
"""

from fastapi import APIRouter, Request

from app.domain.health import HealthReport
from app.services.health_service import build_health_report

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthReport)
def get_health(request: Request) -> HealthReport:
    request_id = getattr(request.state, "request_id", "")
    trace_id = getattr(request.state, "trace_id", "")
    return build_health_report(request_id=request_id, trace_id=trace_id)
