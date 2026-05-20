"""POST /classify — DistilBERT inference over title+body.

The fine-tuned weights were verified at boot (see
:func:`model_server.boot_check.verify_artifacts`) and the model was loaded
in the lifespan. This handler just dispatches to
:func:`model_server.inference.predict` and shapes the JSON response.
``X-Request-Id`` and ``X-Trace-Id`` headers are set by the middleware
stack (Rule 7).
"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, Field

from model_server import state
from model_server.inference import predict

router = APIRouter()


class ClassifyRequest(BaseModel):
    title: str = Field(default="", description="Issue title")
    body: str = Field(default="", description="Issue body")


class ClassifyResponse(BaseModel):
    label: str
    confidence: float
    label_scores: dict[str, float]


@router.post("/classify", response_model=ClassifyResponse)
def classify(req: ClassifyRequest) -> ClassifyResponse:
    loaded = state.get_model()
    result = predict(loaded, req.title, req.body)
    return ClassifyResponse(
        label=result.label,
        confidence=result.confidence,
        label_scores=result.label_scores,
    )
