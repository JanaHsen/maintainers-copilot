"""Domain models for the /health report (Pydantic, distinct from ORM — Rule 1)."""

from typing import Literal

from pydantic import BaseModel, Field, model_validator

DependencyName = Literal["postgres", "pgvector", "redis", "minio", "vault"]


class DependencyStatus(BaseModel):
    name: DependencyName
    reachable: bool
    detail: str | None = None
    latency_ms: float


class HealthReport(BaseModel):
    status: Literal["ok", "degraded"]
    dependencies: list[DependencyStatus] = Field(min_length=1)
    request_id: str
    trace_id: str

    @model_validator(mode="after")
    def _status_matches_dependencies(self) -> "HealthReport":
        all_reachable = all(d.reachable for d in self.dependencies)
        expected = "ok" if all_reachable else "degraded"
        if self.status != expected:
            raise ValueError(
                f"status {self.status!r} inconsistent with dependency "
                f"reachability (expected {expected!r})"
            )
        return self
