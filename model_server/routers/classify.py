"""Placeholder /classify endpoint — real DistilBERT inference lands in slice (c)."""

from fastapi import APIRouter, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel

router = APIRouter()


class ClassifyRequest(BaseModel):
    title: str
    body: str


@router.post("/classify")
def classify(_req: ClassifyRequest) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        content={
            "detail": "classify endpoint not yet implemented",
            "slice": "c",
        },
    )
