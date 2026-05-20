"""Placeholder /summarize endpoint — Claude Haiku-driven summary lands in slice (f)."""

from fastapi import APIRouter, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel

router = APIRouter()


class SummarizeRequest(BaseModel):
    title: str
    body: str
    comments: str | None = None


@router.post("/summarize")
def summarize(_req: SummarizeRequest) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        content={
            "detail": "summarize endpoint not yet implemented",
            "slice": "f",
        },
    )
