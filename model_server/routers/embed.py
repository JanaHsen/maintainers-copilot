"""POST /embed — online query embedding handler."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from model_server import state
from model_server.embed import embed_batch, embed_one

router = APIRouter()


class EmbedRequest(BaseModel):
    text: str | None = Field(default=None, min_length=1)
    texts: list[str] | None = Field(default=None, min_length=1)


class EmbedResponse(BaseModel):
    embedding: list[float] | None = None
    embeddings: list[list[float]] | None = None
    model_id: str
    dim: int


@router.post("/embed", response_model=EmbedResponse)
def embed(req: EmbedRequest) -> EmbedResponse:
    if req.text is None and req.texts is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="exactly one of 'text' or 'texts' must be provided",
        )
    if req.text is not None and req.texts is not None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="exactly one of 'text' or 'texts' must be provided",
        )
    loaded = state.get_embedder()
    if req.text is not None:
        return EmbedResponse(
            embedding=embed_one(loaded, req.text),
            model_id=loaded.model_id,
            dim=loaded.dim,
        )
    return EmbedResponse(
        embeddings=embed_batch(loaded, req.texts or []),
        model_id=loaded.model_id,
        dim=loaded.dim,
    )
