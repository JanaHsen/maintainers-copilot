"""POST /rerank — cross-encoder rerank handler."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from model_server import state
from model_server.rerank import rerank as rerank_inference

router = APIRouter()


class RerankCandidate(BaseModel):
    id: str
    text: str


class RerankRequest(BaseModel):
    query: str = Field(..., min_length=1)
    candidates: list[RerankCandidate] = Field(..., min_length=1)


class RerankScore(BaseModel):
    id: str
    score: float


class RerankResponse(BaseModel):
    scores: list[RerankScore]
    model_id: str


@router.post("/rerank", response_model=RerankResponse)
def rerank(req: RerankRequest) -> RerankResponse:
    if len(req.candidates) > 64:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"candidates length {len(req.candidates)} exceeds server cap (64)",
        )
    loaded = state.get_reranker()
    scored = rerank_inference(
        loaded,
        query=req.query,
        candidates=[(c.id, c.text) for c in req.candidates],
    )
    return RerankResponse(
        scores=[RerankScore(id=cid, score=score) for cid, score in scored],
        model_id=loaded.model_id,
    )
