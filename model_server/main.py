"""Model server ASGI entrypoint.

Lifespan order: Vault (for MinIO creds) -> artifact integrity check ->
state_dict load into DistilBertForSequenceClassification -> serve. Any
artifact-integrity failure *and* any state_dict load failure are fatal:
one specific log line per mismatch type, and the exception propagates so
uvicorn aborts startup and the container exits non-zero (Rule 4).

Tracing + request-context middleware are wired so the model server's
spans show up alongside the api's in Phoenix and the request id flows
through both services (Rule 7).
"""

from __future__ import annotations

import logging
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.infra import vault_client
from app.infra.log_redaction import RedactingFilter
from app.infra.request_context import RequestContextMiddleware
from app.infra.vault_client import KEY_MINIO_ROOT_PASSWORD, VaultBootstrapError
from model_server import state
from model_server.boot_check import (
    ArtifactIntegrityError,
    Label2IdMismatchError,
    ModelCardMissingError,
    ModelCardSchemaError,
    TrainingDataHashMismatchError,
    TrainParquetMissingError,
    WeightsHashMismatchError,
    WeightsMissingError,
    verify_artifacts,
)
from model_server.inference import StateDictLoadError, load_model
from model_server.routers import api_router
from model_server.storage import ArtifactStorage, get_storage
from model_server.tracing import setup_tracing, shutdown_tracing

logger = logging.getLogger("model_server")


def _configure_logging() -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.addFilter(RedactingFilter())
    logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)


# Map every refuse-to-boot failure to its own specific log line (Rule 4).
_REFUSE_TO_BOOT_LINES: dict[type[Exception], str] = {
    ModelCardMissingError: "REFUSE TO BOOT: model_card.json missing",
    ModelCardSchemaError: "REFUSE TO BOOT: model_card.json schema invalid",
    Label2IdMismatchError: "REFUSE TO BOOT: architecture.label2id mismatch",
    WeightsMissingError: "REFUSE TO BOOT: state_dict.pt missing",
    WeightsHashMismatchError: "REFUSE TO BOOT: weights SHA-256 mismatch",
    TrainParquetMissingError: "REFUSE TO BOOT: train.parquet missing",
    TrainingDataHashMismatchError: "REFUSE TO BOOT: training_data_hash mismatch",
    StateDictLoadError: "REFUSE TO BOOT: state_dict.pt failed to load into model",
}


def run_boot_check(storage: ArtifactStorage) -> None:
    """Verify artifacts, then load weights into the model. Either failure is fatal."""
    try:
        verified = verify_artifacts(storage)
    except ArtifactIntegrityError as exc:
        prefix = _REFUSE_TO_BOOT_LINES.get(
            type(exc), "REFUSE TO BOOT: artifact integrity check failed"
        )
        logger.critical("%s: %s", prefix, exc)
        raise
    state.set_artifacts(verified)
    logger.info(
        "artifact integrity ok: weights_sha256=%s, train_hash=%s, classes=%s",
        verified.model_card["weights"]["weights_sha256"],
        verified.model_card["data"]["training_data_hash"],
        sorted(verified.label2id),
    )

    try:
        loaded = load_model(verified)
    except StateDictLoadError as exc:
        logger.critical("%s: %s", _REFUSE_TO_BOOT_LINES[StateDictLoadError], exc)
        raise
    state.set_model(loaded)
    logger.info("model loaded; ready to serve /classify")


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    _configure_logging()
    try:
        vault_client.ping()
        vault_client.read_secrets([KEY_MINIO_ROOT_PASSWORD])
    except VaultBootstrapError as exc:
        logger.critical("REFUSE TO BOOT: Vault dependency failed: %s", exc)
        raise
    run_boot_check(get_storage())
    try:
        yield
    finally:
        shutdown_tracing()
        state.clear_artifacts()


app = FastAPI(title="Maintainer's Copilot — Model Server", lifespan=lifespan)
app.add_middleware(RequestContextMiddleware)
# Instrument before the ASGI/middleware stack serves any request — same
# constraint as the api (Rule 7: observability must actually work).
setup_tracing(app)
app.include_router(api_router)


@app.get("/health")
def health() -> dict[str, str]:
    """Liveness probe. Reaches here only after a successful boot."""
    return {"status": "ok"}
