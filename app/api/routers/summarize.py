"""POST /summarize — summarize router (HTTP only; orchestration in services).

Rule 1: routers map service outcomes to HTTP statuses. The Anthropic
call lives in :mod:`app.services.summarize_service` (Rule 11).
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, status

from app.domain.summarize import SummarizeRequest, SummarizeResponse
from app.infra.request_context import get_request_id, get_trace_id
from app.services.summarize_service import (
    SummarizeError,
    SummarizeOk,
)
from app.services.summarize_service import (
    summarize as summarize_service,
)

router = APIRouter()


_KIND_TO_STATUS: dict[str, int] = {
    "bad_request": status.HTTP_400_BAD_REQUEST,
    "internal": status.HTTP_502_BAD_GATEWAY,
    "unreachable": status.HTTP_503_SERVICE_UNAVAILABLE,
    "timeout": status.HTTP_504_GATEWAY_TIMEOUT,
    "unexpected": status.HTTP_502_BAD_GATEWAY,
}


@router.post("/summarize", response_model=SummarizeResponse)
def summarize(req: SummarizeRequest, _request: Request) -> SummarizeResponse:
    request_id = get_request_id()
    trace_id = get_trace_id()
    outcome = summarize_service(
        req.text,
        max_sentences=req.max_sentences,
        request_id=request_id,
    )
    if isinstance(outcome, SummarizeOk):
        return SummarizeResponse(
            summary=outcome.summary,
            request_id=request_id,
            trace_id=trace_id,
        )
    if isinstance(outcome, SummarizeError):
        raise HTTPException(
            status_code=_KIND_TO_STATUS.get(
                outcome.kind, status.HTTP_502_BAD_GATEWAY
            ),
            detail={
                "detail": outcome.detail,
                "kind": outcome.kind,
                "request_id": request_id,
                "trace_id": trace_id,
            },
        )
    # Unreachable in practice — both variants above are exhaustive.
    raise HTTPException(status_code=500, detail="unknown summarize outcome")
