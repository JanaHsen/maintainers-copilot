"""Rule 4: prove the lifespan refuses to boot on Vault failures.

These exercise the failure path before any real infra is touched: the Vault
checks come first, so monkeypatching them is sufficient and the test needs
no running stack.
"""

import pytest

from app.infra import vault_client
from app.infra.vault_client import MissingVaultKeyError, VaultUnreachableError
from app.main import app, lifespan


async def test_refuse_to_boot_when_vault_unreachable(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom() -> None:
        raise VaultUnreachableError("Vault unreachable at http://vault:8200")

    monkeypatch.setattr(vault_client, "ping", boom)

    with pytest.raises(VaultUnreachableError):
        async with lifespan(app):
            pass


async def test_refuse_to_boot_when_required_key_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(vault_client, "ping", lambda: None)

    def missing(keys: list[str]) -> dict[str, str]:
        raise MissingVaultKeyError("missing required Vault key(s): database_password")

    monkeypatch.setattr(vault_client, "read_secrets", missing)

    with pytest.raises(MissingVaultKeyError):
        async with lifespan(app):
            pass
