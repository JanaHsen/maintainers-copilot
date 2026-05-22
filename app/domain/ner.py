"""Pydantic models for the /ner endpoint (Rule 1, Rule 9; research R7).

The four-bucket entity schema is fixed (R7) and aligns 1:1 with
``specs/002-chatbot-part1-foundations/contracts/ner.openapi.yaml``.
``NerService`` parses the strict-JSON Anthropic response into
``EntityBuckets`` directly.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class EntityBuckets(BaseModel):
    """The four mandatory entity buckets (research R7)."""

    repo_names: list[str]
    file_paths: list[str]
    error_types: list[str]
    package_names: list[str]


class NerRequest(BaseModel):
    text: str = Field(min_length=1, max_length=32_000)


class NerResponse(BaseModel):
    entities: EntityBuckets
    request_id: str
    trace_id: str
