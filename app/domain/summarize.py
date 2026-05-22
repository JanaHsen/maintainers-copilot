"""Pydantic models for the /summarize endpoint (Rule 1, Rule 9).

Aligns 1:1 with
``specs/002-chatbot-part1-foundations/contracts/summarize.openapi.yaml``.
The summarize service consumes ``SummarizeRequest.text`` (+ an optional
``max_sentences`` hint) and returns plain text inside
``SummarizeResponse.summary``.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class SummarizeRequest(BaseModel):
    text: str = Field(min_length=1, max_length=32_000)
    max_sentences: int = Field(default=3, ge=1, le=5)


class SummarizeResponse(BaseModel):
    summary: str
    request_id: str
    trace_id: str
