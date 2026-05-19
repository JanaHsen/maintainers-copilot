import logging
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.routers import api_router
from app.infra import database, minio_client, redis_client, vault_client
from app.infra.database import DatabaseUnreachableError
from app.infra.log_redaction import RedactingFilter
from app.infra.minio_client import MinioUnreachableError
from app.infra.request_context import RequestContextMiddleware
from app.infra.tracing import setup_tracing, shutdown_tracing
from app.infra.vault_client import (
    KEY_DATABASE_PASSWORD,
    KEY_MINIO_ROOT_PASSWORD,
    VaultBootstrapError,
)

logger = logging.getLogger("app")

# Keys the api itself requires at boot. github_pat is used only by the offline
# dataset script, so it is intentionally not required to start the api.
REQUIRED_VAULT_KEYS = [KEY_DATABASE_PASSWORD, KEY_MINIO_ROOT_PASSWORD]


def _configure_logging() -> None:
    """Stdout logging with secret redaction applied to every record (Rule 7)."""
    handler = logging.StreamHandler(sys.stdout)
    handler.addFilter(RedactingFilter())
    logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Bootstrap dependencies in order; refuse to boot on a fatal failure.

    Order: Vault -> DB -> Redis -> MinIO -> tracing. A failure of Vault, a
    required Vault key, Postgres, or MinIO is fatal (Rule 4): one specific
    log line is emitted and the exception propagates so uvicorn aborts
    startup and the container exits non-zero. Redis being down is tolerated
    (it surfaces as /health "degraded", not a refusal).
    """
    _configure_logging()
    try:
        vault_client.ping()
        vault_client.read_secrets(REQUIRED_VAULT_KEYS)
    except VaultBootstrapError as exc:
        logger.critical("REFUSE TO BOOT: Vault dependency failed: %s", exc)
        raise

    try:
        database.connect_with_retry()
    except DatabaseUnreachableError as exc:
        logger.critical("REFUSE TO BOOT: Postgres dependency failed: %s", exc)
        raise

    try:
        redis_client.ping()
    except redis_client.RedisUnreachableError as exc:
        logger.warning("Redis unreachable at boot; /health will report degraded: %s", exc)

    try:
        minio_client.bootstrap()
    except MinioUnreachableError as exc:
        logger.critical("REFUSE TO BOOT: MinIO dependency failed: %s", exc)
        raise

    logger.info("startup complete: all required dependencies reachable")
    try:
        yield
    finally:
        shutdown_tracing()
        database.get_engine().dispose()


app = FastAPI(title="Maintainer's Copilot", lifespan=lifespan)
app.add_middleware(RequestContextMiddleware)
# Instrument at import, before the ASGI/middleware stack is built and the app
# serves a request — instrumenting from the lifespan is too late to produce
# spans (Rule 7: observability must actually work, not just be wired).
setup_tracing(app)
app.include_router(api_router)
