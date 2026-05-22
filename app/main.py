import logging
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy import text
from sqlalchemy.exc import OperationalError, ProgrammingError

from app.api.routers import api_router
from app.config import get_settings
from app.infra import database, minio_client, redis_client, vault_client
from app.infra.database import DatabaseUnreachableError, get_engine
from app.infra.log_redaction import RedactingFilter
from app.infra.minio_client import MinioUnreachableError
from app.infra.request_context import RequestContextMiddleware
from app.infra.tracing import setup_tracing, shutdown_tracing
from app.infra.vault_client import (
    KEY_AUTH_JWT_SECRET,
    KEY_DATABASE_PASSWORD,
    KEY_MINIO_ROOT_PASSWORD,
    VaultBootstrapError,
)
from app.repositories import chunk_repository

logger = logging.getLogger("app")

# Keys the api itself requires at boot. github_pat is used only by the offline
# dataset script, so it is intentionally not required to start the api.
# auth_jwt_secret is required because the auth router refuses to mount without
# a signing key (Rule 2 — no env fallback) and Part 1 mounts the auth router.
REQUIRED_VAULT_KEYS = [
    KEY_DATABASE_PASSWORD,
    KEY_MINIO_ROOT_PASSWORD,
    KEY_AUTH_JWT_SECRET,
]


def _configure_logging() -> None:
    """Stdout logging with secret redaction applied to every record (Rule 7)."""
    handler = logging.StreamHandler(sys.stdout)
    handler.addFilter(RedactingFilter())
    logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Bootstrap dependencies in order; refuse to boot on a fatal failure.

    Order: Vault -> DB -> Redis -> MinIO -> tracing. A failure of any of
    these dependencies is fatal (Rule 4): one specific log line is emitted
    and the exception propagates so uvicorn aborts startup and the container
    exits non-zero. Redis was previously tolerated as 'degraded'; Part 1
    promotes it to fatal because the chatbot's short-term memory (Redis)
    cannot degrade safely — a widget conversation with no short-term store
    loses turn-to-turn coherence.
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
        logger.critical("REFUSE TO BOOT: Redis dependency failed: %s", exc)
        raise

    try:
        minio_client.bootstrap()
    except MinioUnreachableError as exc:
        logger.critical("REFUSE TO BOOT: MinIO dependency failed: %s", exc)
        raise

    # RAG boot checks (data-model.md "Lifecycle / boot-time invariants").
    # Each failure logs ONE specific REFUSE TO BOOT line and propagates so
    # uvicorn aborts startup and the container exits non-zero (Rule 4).
    _verify_rag_corpus()

    logger.info("startup complete: all required dependencies reachable")
    try:
        yield
    finally:
        shutdown_tracing()
        database.get_engine().dispose()


class RagCorpusNotConfiguredError(RuntimeError):
    """RAG_CORPUS_RUN_ID env var is unset — refuse-to-boot."""


class RagCorpusEmptyError(RuntimeError):
    """rag_chunks has no rows for the configured corpus_run_id — refuse-to-boot."""


class PgvectorMissingError(RuntimeError):
    """The pgvector extension or the rag_chunks table is missing — refuse-to-boot."""


def _verify_rag_corpus() -> None:
    """Four-line refuse-to-boot surface for the RAG corpus state."""
    corpus_run_id = get_settings().rag_corpus_run_id
    if not corpus_run_id:
        logger.critical("REFUSE TO BOOT: RAG_CORPUS_RUN_ID not configured")
        raise RagCorpusNotConfiguredError("RAG_CORPUS_RUN_ID is unset")
    try:
        with get_engine().connect() as conn:
            present = conn.execute(
                text("SELECT 1 FROM pg_extension WHERE extname='vector'")
            ).first()
            if not present:
                logger.critical("REFUSE TO BOOT: pgvector extension absent")
                raise PgvectorMissingError("pg_extension 'vector' not installed")
            # Table-exists check separately so an empty-table failure isn't
            # confused with a missing-table failure.
            try:
                conn.execute(text("SELECT 1 FROM rag_chunks LIMIT 1"))
            except ProgrammingError as exc:
                logger.critical("REFUSE TO BOOT: rag_chunks table missing: %s", exc)
                raise PgvectorMissingError("rag_chunks table missing") from exc
    except OperationalError as exc:  # Postgres unreachable; covered upstream too
        logger.critical("REFUSE TO BOOT: Postgres unreachable during RAG check: %s", exc)
        raise

    if chunk_repository.is_empty(corpus_run_id):
        # Distinguish "table empty entirely" from "configured run id missing".
        with get_engine().connect() as conn:
            any_row = conn.execute(text("SELECT 1 FROM rag_chunks LIMIT 1")).first()
        if any_row is None:
            logger.critical("REFUSE TO BOOT: rag_chunks table empty")
        else:
            logger.critical(
                "REFUSE TO BOOT: configured corpus run id has no rows: %s",
                corpus_run_id,
            )
        raise RagCorpusEmptyError(
            f"no rag_chunks rows for corpus_run_id={corpus_run_id!r}"
        )
    logger.info("RAG corpus check ok: corpus_run_id=%s", corpus_run_id)


app = FastAPI(title="Maintainer's Copilot", lifespan=lifespan)
app.add_middleware(RequestContextMiddleware)
# Instrument at import, before the ASGI/middleware stack is built and the app
# serves a request — instrumenting from the lifespan is too late to produce
# spans (Rule 7: observability must actually work, not just be wired).
setup_tracing(app)
app.include_router(api_router)
