"""POST /retrieve — RAG retrieval router (HTTP only; orchestration in services)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, status

from app.domain.retrieve import RetrieveRequest, RetrieveResponse
from app.infra.request_context import get_request_id, get_trace_id
from app.services.retrieve_service import (
    RetrieveError,
    RetrieveOk,
)
from app.services.retrieve_service import (
    retrieve as retrieve_service,
)

router = APIRouter()


_KIND_TO_STATUS: dict[str, int] = {
    "unreachable": status.HTTP_503_SERVICE_UNAVAILABLE,
    "timeout": status.HTTP_504_GATEWAY_TIMEOUT,
    "bad_request": status.HTTP_502_BAD_GATEWAY,
    "internal": status.HTTP_502_BAD_GATEWAY,
    "unexpected": status.HTTP_502_BAD_GATEWAY,
}


@router.post("/retrieve", response_model=RetrieveResponse)
def retrieve(req: RetrieveRequest, _request: Request) -> RetrieveResponse:
    request_id = get_request_id()
    trace_id = get_trace_id()
    outcome = retrieve_service(req, request_id=request_id, trace_id=trace_id)
    if isinstance(outcome, RetrieveOk):
        return RetrieveResponse(
            chunks=outcome.chunks, request_id=request_id, trace_id=trace_id
        )
    if isinstance(outcome, RetrieveError):
        raise HTTPException(
            status_code=_KIND_TO_STATUS.get(outcome.kind, status.HTTP_502_BAD_GATEWAY),
            detail={
                "detail": outcome.detail,
                "kind": outcome.kind,
                "request_id": request_id,
                "trace_id": trace_id,
            },
        )
    # Unreachable in practice — both variants above are exhaustive.
    raise HTTPException(status_code=500, detail="unknown retrieve outcome")
