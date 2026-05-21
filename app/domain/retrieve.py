"""Pydantic domain models for POST /retrieve (request, response, internal types).

Strictly distinct from the SQLAlchemy ORM (we have none here — the
repository layer returns these models directly). The spec-frozen wire
contract is in specs/rag/contracts/retrieve.openapi.yaml; this module
is the in-process source of truth.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

SourceType = Literal["docs", "issues"]


class RetrieveFilters(BaseModel):
    """Optional caller-supplied filters applied during stage 1 (FR-018)."""

    source: list[SourceType] | None = None
    from_: datetime | None = Field(default=None, alias="from")
    to: datetime | None = None

    model_config = {"populate_by_name": True}

    @model_validator(mode="after")
    def _from_lte_to(self) -> RetrieveFilters:
        if self.from_ is not None and self.to is not None and self.from_ > self.to:
            raise ValueError("filters.from must be <= filters.to")
        return self


class RetrieveRequest(BaseModel):
    """The POST /retrieve request body."""

    question: str = Field(..., min_length=1)
    k: int = Field(default=5, ge=0, le=20)
    filters: RetrieveFilters | None = None


class RetrievedChunk(BaseModel):
    """One parent chunk surfaced to the caller (the rerank winners)."""

    content: str
    source_type: SourceType
    source_id: str
    score: float
    metadata: dict[str, Any] = Field(default_factory=dict)
    chunk_id: str


class RetrieveResponse(BaseModel):
    """The POST /retrieve response body."""

    chunks: list[RetrievedChunk]
    request_id: str
    trace_id: str


# --- internal types (used between service + repository) -------------------


class ChildHit(BaseModel):
    """One stage-1 child-row match from chunk_repository (before rerank)."""

    chunk_id: str
    parent_id: str
    content: str
    source_type: SourceType
    source_id: str
    source_timestamp: datetime
    section_path: str
    score: float  # hybrid α * dense + (1-α) * sparse


class Parent(BaseModel):
    """One parent row, used after the rerank step's parent aggregation."""

    chunk_id: str
    content: str
    source_type: SourceType
    source_id: str
    source_timestamp: datetime
    section_path: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class ChunkFilters(BaseModel):
    """Repository-layer translation of RetrieveFilters (always concrete)."""

    source_types: list[SourceType]
    from_: datetime | None = None
    to: datetime | None = None
