"""MinIO (S3-compatible) client — the only blob store (Rule 3).

The root credential is read from Vault (Rule 2). Boot bootstraps the data
bucket and retries with bounded backoff so a compose start race is absorbed
while a genuinely down MinIO still fails loud (Rule 4).
"""

import time
from functools import lru_cache
from typing import Any

import boto3  # type: ignore[import-untyped]
from botocore.client import Config  # type: ignore[import-untyped]
from botocore.exceptions import (  # type: ignore[import-untyped]
    BotoCoreError,
    ClientError,
)

from app.config import get_settings
from app.infra.vault_client import KEY_MINIO_ROOT_PASSWORD, read_secrets

DATA_BUCKET = "maintainers-copilot"


class MinioUnreachableError(RuntimeError):
    """MinIO could not be reached after bounded retries (refuse-to-boot)."""


@lru_cache
def get_client() -> Any:
    settings = get_settings()
    minio_secret = read_secrets([KEY_MINIO_ROOT_PASSWORD])[KEY_MINIO_ROOT_PASSWORD]
    endpoint = f"http://{settings.minio_host}:{settings.minio_port}"
    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=settings.minio_root_user,
        aws_secret_access_key=minio_secret,
        region_name="us-east-1",
        config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
    )


def ensure_bucket(bucket: str = DATA_BUCKET) -> None:
    """Create ``bucket`` if it does not already exist (idempotent)."""
    client = get_client()
    try:
        client.head_bucket(Bucket=bucket)
    except ClientError:
        client.create_bucket(Bucket=bucket)


def bootstrap(attempts: int = 6, base_delay: float = 0.5) -> None:
    """Wait for MinIO and ensure the data bucket exists (boot path)."""
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            ensure_bucket()
            return
        except (BotoCoreError, ClientError, OSError) as exc:
            last_error = exc
            time.sleep(base_delay * (2**attempt))
    raise MinioUnreachableError(
        f"MinIO unreachable after {attempts} attempts"
    ) from last_error


def ping() -> None:
    """Single fast liveness probe for /health; raises on failure."""
    try:
        get_client().list_buckets()
    except (BotoCoreError, ClientError, OSError) as exc:
        raise MinioUnreachableError("MinIO unreachable") from exc
