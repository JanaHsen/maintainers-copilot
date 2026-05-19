"""OpenTelemetry tracing wired to a local Arize Phoenix collector.

Tracing is initialized from the first commit and never retrofitted (Rule 7):
FastAPI and HTTPX are auto-instrumented so a single inbound request — and any
outbound call it makes — is one connected span tree in Phoenix.
"""

from fastapi import FastAPI
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

from app.config import get_settings

SERVICE_NAME = "maintainers-copilot-api"

_provider: TracerProvider | None = None


def setup_tracing(app: FastAPI) -> None:
    """Initialize the tracer provider, Phoenix OTLP exporter, and auto-instrumentation."""
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
    HTTPXClientInstrumentor().instrument()
    _provider = provider


def shutdown_tracing() -> None:
    """Flush and tear down the tracer provider on application shutdown."""
    global _provider
    if _provider is not None:
        _provider.shutdown()
        _provider = None


def get_tracer(name: str = SERVICE_NAME) -> trace.Tracer:
    """Return a tracer; callers create child spans for per-dependency checks."""
    return trace.get_tracer(name)


def current_trace_id() -> str:
    """Active trace id as 32-hex, or "" outside a span.

    Must be called from within the request span (e.g. a route handler);
    Starlette's BaseHTTPMiddleware runs outside it.
    """
    span_context = trace.get_current_span().get_span_context()
    if span_context.trace_id:
        return format(span_context.trace_id, "032x")
    return ""
