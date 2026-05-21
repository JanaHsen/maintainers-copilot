"""Read-only MinIO accessor for the three artifacts the boot check inspects.

The model server pulls three objects at startup:

  * ``artifacts/classifier/distilbert/{model_run_id}/model_card.json``
  * ``artifacts/classifier/distilbert/{model_run_id}/state_dict.pt``
  * ``processed/pandas/{dataset_run_id}/train.parquet``

A ``Protocol`` is exposed so the boot-check logic can be exercised with
in-memory fakes (the refuse-to-boot tests do this — see
``tests/model_server/test_boot_check.py``).
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any, Protocol

import boto3  # type: ignore[import-untyped]
from botocore.client import Config  # type: ignore[import-untyped]
from botocore.exceptions import (  # type: ignore[import-untyped]
    BotoCoreError,
    ClientError,
)

from app.config import get_settings as get_app_settings
from app.infra.vault_client import KEY_MINIO_ROOT_PASSWORD, read_secrets
from model_server.config import get_settings


def _model_card_key(model_run_id: str) -> str:
    return f"artifacts/classifier/distilbert/{model_run_id}/model_card.json"


def _state_dict_key(model_run_id: str) -> str:
    return f"artifacts/classifier/distilbert/{model_run_id}/state_dict.pt"


def _train_parquet_key(dataset_run_id: str) -> str:
    return f"processed/pandas/{dataset_run_id}/train.parquet"


class ArtifactNotFoundError(RuntimeError):
    """A required artifact object is absent from MinIO."""


class ArtifactStorageError(RuntimeError):
    """MinIO returned an unexpected error fetching an artifact."""


class ArtifactStorage(Protocol):
    """Read-only access to the three boot-time artifacts."""

    def read_model_card(self) -> bytes: ...
    def read_state_dict(self) -> bytes: ...
    def read_train_parquet(self) -> bytes: ...


class MinioArtifactStorage:
    """Concrete :class:`ArtifactStorage` backed by an S3/MinIO client."""

    def __init__(
        self,
        client: Any,
        bucket: str,
        model_run_id: str,
        dataset_run_id: str,
    ) -> None:
        self._client = client
        self._bucket = bucket
        self._model_run_id = model_run_id
        self._dataset_run_id = dataset_run_id

    def _get_object(self, key: str) -> bytes:
        try:
            resp = self._client.get_object(Bucket=self._bucket, Key=key)
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            if code in {"NoSuchKey", "404", "NoSuchBucket"}:
                raise ArtifactNotFoundError(
                    f"s3://{self._bucket}/{key} not found"
                ) from exc
            raise ArtifactStorageError(
                f"unexpected MinIO error fetching s3://{self._bucket}/{key}: {code}"
            ) from exc
        except (BotoCoreError, OSError) as exc:
            raise ArtifactStorageError(
                f"MinIO unreachable while fetching s3://{self._bucket}/{key}"
            ) from exc
        body: bytes = resp["Body"].read()
        return body

    def read_model_card(self) -> bytes:
        return self._get_object(_model_card_key(self._model_run_id))

    def read_state_dict(self) -> bytes:
        return self._get_object(_state_dict_key(self._model_run_id))

    def read_train_parquet(self) -> bytes:
        return self._get_object(_train_parquet_key(self._dataset_run_id))


@lru_cache
def get_storage() -> MinioArtifactStorage:
    """Build the production :class:`MinioArtifactStorage` from settings + Vault."""
    app_settings = get_app_settings()
    ms_settings = get_settings()
    minio_secret = read_secrets([KEY_MINIO_ROOT_PASSWORD])[KEY_MINIO_ROOT_PASSWORD]
    client = boto3.client(
        "s3",
        endpoint_url=f"http://{app_settings.minio_host}:{app_settings.minio_port}",
        aws_access_key_id=app_settings.minio_root_user,
        aws_secret_access_key=minio_secret,
        region_name="us-east-1",
        config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
    )
    return MinioArtifactStorage(
        client=client,
        bucket=ms_settings.minio_bucket,
        model_run_id=ms_settings.model_run_id,
        dataset_run_id=ms_settings.dataset_run_id,
    )
