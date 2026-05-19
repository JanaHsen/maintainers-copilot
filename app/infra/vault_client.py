"""hvac kv-v2 reader for ``secret/maintainers-copilot/``.

This is the *only* place application secrets enter the process (Rule 2).
It fails loud, not soft (Rule 4): an unreachable Vault or a missing required
key raises a specific exception so the lifespan can refuse to boot.
"""

from typing import Any

import hvac  # type: ignore[import-untyped]
from hvac.exceptions import VaultError  # type: ignore[import-untyped]
from requests.exceptions import ConnectionError as RequestsConnectionError
from requests.exceptions import Timeout as RequestsTimeout

from app.config import get_settings

SECRET_MOUNT = "secret"
SECRET_PATH = "maintainers-copilot"


class VaultBootstrapError(RuntimeError):
    """Base for Vault failures that must prevent the process from booting."""


class VaultUnreachableError(VaultBootstrapError):
    """Vault could not be contacted or rejected the token."""


class MissingVaultKeyError(VaultBootstrapError):
    """A required key is absent from the kv-v2 secret."""


def _client() -> hvac.Client:
    settings = get_settings()
    return hvac.Client(
        url=settings.vault_addr,
        token=settings.vault_dev_root_token_id,
    )


def _read_all() -> dict[str, Any]:
    client = _client()
    try:
        resp = client.secrets.kv.v2.read_secret_version(
            path=SECRET_PATH,
            mount_point=SECRET_MOUNT,
            raise_on_deleted_version=True,
        )
    except (RequestsConnectionError, RequestsTimeout) as exc:
        raise VaultUnreachableError(
            f"Vault unreachable at {get_settings().vault_addr}"
        ) from exc
    except VaultError as exc:
        raise VaultUnreachableError(
            f"Vault rejected the request at {get_settings().vault_addr}"
        ) from exc
    data: dict[str, Any] = resp["data"]["data"]
    return data


def read_secrets(keys: list[str]) -> dict[str, str]:
    """Return the requested keys from the kv-v2 secret.

    Raises :class:`VaultUnreachableError` if Vault cannot be reached and
    :class:`MissingVaultKeyError` if any requested key is absent.
    """
    data = _read_all()
    missing = [k for k in keys if k not in data]
    if missing:
        raise MissingVaultKeyError(
            f"missing required Vault key(s): {', '.join(sorted(missing))}"
        )
    return {k: str(data[k]) for k in keys}


def ping() -> None:
    """Probe Vault reachability (used by /health and the boot sequence)."""
    client = _client()
    try:
        if not client.sys.is_initialized():
            raise VaultUnreachableError("Vault reports not initialized")
    except (RequestsConnectionError, RequestsTimeout, VaultError) as exc:
        raise VaultUnreachableError(
            f"Vault unreachable at {get_settings().vault_addr}"
        ) from exc
