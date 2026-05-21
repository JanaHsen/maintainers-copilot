"""Model server settings: which run to serve and where the bucket lives.

Shared infrastructure (Vault, MinIO host/port, MinIO root user) is read
through :mod:`app.config`. Only model-server-specific knobs live here.
"""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class ModelServerSettings(BaseSettings):
    # `model_` is a pydantic-protected namespace by default; we use it for
    # the classifier run id env var, so opt out for this settings class.
    model_config = SettingsConfigDict(
        env_file=".env", extra="ignore", protected_namespaces=()
    )

    model_server_port: int = Field(default=8001)

    # The classifier artifact lives at
    # s3://{bucket}/artifacts/classifier/distilbert/{model_run_id}/
    model_run_id: str = Field(...)
    # The dataset whose train split the model was trained on. Used to
    # recompute training_data_hash during boot integrity verification.
    dataset_run_id: str = Field(...)

    minio_bucket: str = Field(default="maintainers-copilot")


@lru_cache
def get_settings() -> ModelServerSettings:
    return ModelServerSettings()
