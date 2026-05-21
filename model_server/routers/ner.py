"""POST /ner — deterministic regex extraction of code-shaped entities."""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, Field

from model_server import ner as ner_module

router = APIRouter()


class NerRequest(BaseModel):
    text: str = Field(default="")


class NerEntity(BaseModel):
    text: str
    type: str
    start: int
    end: int


class NerResponse(BaseModel):
    entities: list[NerEntity]


@router.post("/ner", response_model=NerResponse)
def ner(req: NerRequest) -> NerResponse:
    entities = [
        NerEntity(text=e.text, type=e.type, start=e.start, end=e.end)
        for e in ner_module.extract(req.text)
    ]
    return NerResponse(entities=entities)
