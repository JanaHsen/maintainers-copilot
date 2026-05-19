from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.infra.request_context import RequestContextMiddleware
from app.infra.tracing import setup_tracing, shutdown_tracing


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Application lifespan.

    The full dependency bootstrap (Vault -> DB -> Redis -> MinIO -> tracing)
    and the refuse-to-boot contract land in later tasks; for now only tracing
    is wired so observability exists from the first request (Rule 7).
    """
    setup_tracing(app)
    try:
        yield
    finally:
        shutdown_tracing()


app = FastAPI(title="Maintainer's Copilot", lifespan=lifespan)
app.add_middleware(RequestContextMiddleware)
