"""OpenTelemetry tracing for the model server, exported to Phoenix.

Mirrors :mod:`app.infra.tracing` but tags spans with the model-server
service name so the api and the model server appear as distinct nodes in
a connected trace tree (Rule 7).
"""

from __future__ import annotations

from fastapi import FastAPI
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

from app.config import get_settings

SERVICE_NAME = "maintainers-copilot-model-server"

_provider: TracerProvider | None = None


def setup_tracing(app: FastAPI) -> None:
    global _provider
    if _provider is not None:
        return
    settings = get_settings()
    resource = Resource.create({"service.name": SERVICE_NAME})
    provider = TracerProvider(resource=resource)
    exporter = OTLPSpanExporter(endpoint=settings.phoenix_otlp_endpoint, insecure=True)
    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    FastAPIInstrumentor.instrument_app(app)
    _provider = provider


def shutdown_tracing() -> None:
    global _provider
    if _provider is not None:
        _provider.shutdown()
        _provider = None
