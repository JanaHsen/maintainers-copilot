"""Negative boot-check parametrized test for Part 1 (Rule 4, SC-008).

Three scenarios, each driven via monkeypatching the lifespan's dependency
adapters so the test runs in-process without needing the docker-compose
stack. We assert that:

  - the lifespan raises the expected exception type;
  - the critical log line containing the expected ``REFUSE TO BOOT: …``
    substring is emitted before the raise.

The scenarios are:

  1. Vault key ``auth_jwt_secret`` is absent → ``MissingVaultKeyError``.
  2. Redis is unreachable → ``RedisUnreachableError`` (Part 1 promoted this
     from a warning to fatal; T040).
  3. The ``users`` table is missing (migration 0003 not applied) →
     ``ChatbotTableMissingError`` (T041).

The Postgres + MinIO probes are stubbed out so each scenario exercises only
the failure mode under test.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator

import pytest

from app.infra import database, minio_client, redis_client, vault_client
from app.infra.vault_client import (
    KEY_AUTH_JWT_SECRET,
    KEY_DATABASE_PASSWORD,
    KEY_MINIO_ROOT_PASSWORD,
    MissingVaultKeyError,
)
from app.main import (
    ChatbotTableMissingError,
    app,
    lifespan,
)


@pytest.fixture
def stub_healthy_baseline(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Replace each upstream probe with a pass-by-default no-op.

    Each scenario then overrides ONE probe to fail, so the test localizes
    the failure mode under test without bringing up the real stack.
    """
    # Skip basicConfig(force=True) — it would remove caplog's handler and the
    # CRITICAL log assertions below would see an empty record list even
    # though the lifespan did emit the line (visible in stdout).
    monkeypatch.setattr("app.main._configure_logging", lambda: None)
    monkeypatch.setattr(vault_client, "ping", lambda: None)
    monkeypatch.setattr(
        vault_client,
        "read_secrets",
        lambda keys: {k: "dev-stub-value" for k in keys},
    )
    monkeypatch.setattr(database, "connect_with_retry", lambda **_: None)
    monkeypatch.setattr(redis_client, "ping", lambda: None)
    monkeypatch.setattr(minio_client, "bootstrap", lambda: None)
    # The chatbot/RAG verifiers touch real tables; stub them to pass so a
    # scenario can target one specific failure mode.
    monkeypatch.setattr("app.main._verify_rag_corpus", lambda: None)
    monkeypatch.setattr("app.main._verify_chatbot_tables", lambda: None)
    yield


async def test_refuse_to_boot_when_auth_jwt_secret_missing(
    stub_healthy_baseline: None,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Vault.read_secrets is missing auth_jwt_secret → MissingVaultKeyError."""

    def missing_auth_key(keys: list[str]) -> dict[str, str]:
        if KEY_AUTH_JWT_SECRET in keys:
            raise MissingVaultKeyError(
                f"missing required Vault key(s): {KEY_AUTH_JWT_SECRET}"
            )
        return {
            KEY_DATABASE_PASSWORD: "dev_postgres_password",
            KEY_MINIO_ROOT_PASSWORD: "dev_minio_password",
        }

    monkeypatch.setattr(vault_client, "read_secrets", missing_auth_key)

    with caplog.at_level(logging.CRITICAL, logger="app"):
        with pytest.raises(MissingVaultKeyError):
            async with lifespan(app):
                pass

    assert any(
        "REFUSE TO BOOT: Vault dependency failed" in rec.getMessage()
        and KEY_AUTH_JWT_SECRET in rec.getMessage()
        for rec in caplog.records
    ), "expected a critical log line naming auth_jwt_secret"


async def test_refuse_to_boot_when_redis_unreachable(
    stub_healthy_baseline: None,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Part 1 promoted Redis-unreachable from warning to fatal (T040)."""

    def boom() -> None:
        raise redis_client.RedisUnreachableError("Redis unreachable")

    monkeypatch.setattr(redis_client, "ping", boom)

    with caplog.at_level(logging.CRITICAL, logger="app"):
        with pytest.raises(redis_client.RedisUnreachableError):
            async with lifespan(app):
                pass

    assert any(
        "REFUSE TO BOOT: Redis dependency failed" in rec.getMessage()
        for rec in caplog.records
    ), "expected a critical log line for Redis failure"


async def test_refuse_to_boot_when_chatbot_users_table_missing(
    stub_healthy_baseline: None,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Migration 0003 not applied → _verify_chatbot_tables raises (T041)."""

    def missing_users() -> None:
        raise ChatbotTableMissingError("users table missing")

    monkeypatch.setattr("app.main._verify_chatbot_tables", missing_users)

    with caplog.at_level(logging.CRITICAL, logger="app"):
        with pytest.raises(ChatbotTableMissingError):
            async with lifespan(app):
                pass
