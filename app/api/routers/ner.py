"""POST /ner — NER router (HTTP only; orchestration in services).

Rule 1: routers map service outcomes to HTTP statuses. The Anthropic
call AND the strict-JSON parsing live in :mod:`app.services.ner_service`
(Rule 11 / research R7).
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, status

from app.domain.ner import NerRequest, NerResponse
from app.infra.request_context import get_request_id, get_trace_id
from app.services.ner_service import (
    NerError,
    NerOk,
)
from app.services.ner_service import (
    extract as ner_service,
)

router = APIRouter()


_KIND_TO_STATUS: dict[str, int] = {
    "bad_request": status.HTTP_400_BAD_REQUEST,
    "bad_format": status.HTTP_502_BAD_GATEWAY,
    "internal": status.HTTP_502_BAD_GATEWAY,
    "unreachable": status.HTTP_503_SERVICE_UNAVAILABLE,
    "timeout": status.HTTP_504_GATEWAY_TIMEOUT,
    "unexpected": status.HTTP_502_BAD_GATEWAY,
}


@router.post("/ner", response_model=NerResponse)
def ner(req: NerRequest, _request: Request) -> NerResponse:
    request_id = get_request_id()
    trace_id = get_trace_id()
    outcome = ner_service(req.text, request_id=request_id)
    if isinstance(outcome, NerOk):
        return NerResponse(
            entities=outcome.entities,
            request_id=request_id,
            trace_id=trace_id,
        )
    if isinstance(outcome, NerError):
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
    raise HTTPException(status_code=500, detail="unknown ner outcome")
