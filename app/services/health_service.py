"""Probe every upstream dependency, one OTel child span per check.

A single GET /health is therefore one connected span tree in Phoenix
(Rule 7). The HTTP status stays 200 while the process is up; degradation is
conveyed in the body (FR-006/FR-007).
"""

import time
from collections.abc import Callable

from app.domain.health import DependencyName, DependencyStatus, HealthReport
from app.infra import minio_client, redis_client, vault_client
from app.infra.log_redaction import redact
from app.infra.tracing import get_tracer
from app.repositories import health_repository

# Each probe raises on failure; pgvector additionally asserts the extension.
_PROBES: list[tuple[DependencyName, Callable[[], None]]] = [
    ("postgres", health_repository.select_one),
    ("pgvector", lambda: _assert(health_repository.pgvector_present())),
    ("redis", redis_client.ping),
    ("minio", minio_client.ping),
    ("vault", vault_client.ping),
]


def _assert(present: bool) -> None:
    if not present:
        raise RuntimeError("pgvector extension not installed")


def _check(name: DependencyName, probe: Callable[[], None]) -> DependencyStatus:
    tracer = get_tracer()
    with tracer.start_as_current_span(f"health.check.{name}"):
        start = time.perf_counter()
        try:
            probe()
            reachable, detail = True, None
        except Exception as exc:  # noqa: BLE001 - every probe failure is a status, not a crash
            reachable = False
            detail = redact(f"{type(exc).__name__}: {exc}")
        latency_ms = (time.perf_counter() - start) * 1000.0
        return DependencyStatus(
            name=name, reachable=reachable, detail=detail, latency_ms=latency_ms
        )


def build_health_report(request_id: str, trace_id: str) -> HealthReport:
    dependencies = [_check(name, probe) for name, probe in _PROBES]
    status = "ok" if all(d.reachable for d in dependencies) else "degraded"
    return HealthReport(
        status=status,
        dependencies=dependencies,
        request_id=request_id,
        trace_id=trace_id,
    )
