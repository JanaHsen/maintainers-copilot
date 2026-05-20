"""Placeholder /ner endpoint — code-shape entity extraction lands in slice (e)."""

from fastapi import APIRouter, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel

router = APIRouter()


class NerRequest(BaseModel):
    text: str


@router.post("/ner")
def ner(_req: NerRequest) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        content={
            "detail": "ner endpoint not yet implemented",
            "slice": "e",
        },
    )
