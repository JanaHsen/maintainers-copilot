"""First-admin bootstrap.

Runs as a one-shot container (``admin-bootstrap`` in ``docker-compose.yml``)
after ``migrate`` and ``vault-seed`` have completed. Idempotent: if any user
already has ``role='admin'`` the run is a no-op.

Why this exists: the role self-registration hole closed by Fix 1 means
``POST /auth/register`` always creates ``role='user'``. Without an external
mechanism a fresh stack would have no admin and the Part 3 admin panel
would be unreachable.

Credentials live in Vault (Rule 2) under the existing kv-v2 secret as keys
``bootstrap_admin_email`` and ``bootstrap_admin_password`` — seeded by
``scripts/vault_seed.sh`` from the operator's ``.env`` (placeholders in
``.env.example``; documented in README). A missing key raises
:class:`MissingVaultKeyError` with a hint pointing back at ``.env``.

The password is hashed with the same :class:`fastapi_users.password.PasswordHelper`
fastapi-users itself uses, so the bootstrap admin can log in via
``POST /auth/login`` immediately.
"""

from __future__ import annotations

import asyncio
import logging
import sys
import uuid

from fastapi_users.password import PasswordHelper
from sqlalchemy import text

from app.infra.database_async import get_async_sessionmaker
from app.infra.log_redaction import RedactingFilter
from app.infra.vault_client import (
    KEY_BOOTSTRAP_ADMIN_EMAIL,
    KEY_BOOTSTRAP_ADMIN_PASSWORD,
    MissingVaultKeyError,
    read_secrets,
)

logger = logging.getLogger("app.bootstrap.admin_bootstrap")


class BootstrapCredentialsEmptyError(MissingVaultKeyError):
    """Bootstrap credentials are present in Vault but empty.

    Distinguished from a fully-absent key so the operator sees the right
    fix: ``edit .env`` and re-run ``scripts/vault_seed.sh`` (not "the key
    is missing from the secret").
    """


def _read_credentials() -> tuple[str, str]:
    """Pull email + password from Vault, fail loud if either is missing/empty."""
    try:
        secrets = read_secrets(
            [KEY_BOOTSTRAP_ADMIN_EMAIL, KEY_BOOTSTRAP_ADMIN_PASSWORD]
        )
    except MissingVaultKeyError as exc:
        raise MissingVaultKeyError(
            f"{exc}; set BOOTSTRAP_ADMIN_EMAIL and BOOTSTRAP_ADMIN_PASSWORD "
            "in .env and re-run scripts/vault_seed.sh"
        ) from exc
    email = secrets[KEY_BOOTSTRAP_ADMIN_EMAIL].strip()
    password = secrets[KEY_BOOTSTRAP_ADMIN_PASSWORD]
    if not email or not password:
        raise BootstrapCredentialsEmptyError(
            "bootstrap_admin_email or bootstrap_admin_password is empty in "
            "Vault; set BOOTSTRAP_ADMIN_EMAIL and BOOTSTRAP_ADMIN_PASSWORD "
            "in .env and re-run scripts/vault_seed.sh"
        )
    return email, password


async def _existing_admin_id() -> str | None:
    sm = get_async_sessionmaker()
    async with sm() as session:
        row = (
            await session.execute(
                text("SELECT id FROM users WHERE role = 'admin' LIMIT 1")
            )
        ).first()
        return None if row is None else str(row[0])


async def _create_admin(email: str, hashed_password: str) -> str:
    user_id = uuid.uuid4()
    sm = get_async_sessionmaker()
    async with sm() as session:
        await session.execute(
            text(
                "INSERT INTO users "
                "(id, email, hashed_password, is_active, is_superuser, "
                " is_verified, role) "
                "VALUES "
                "(:id, :email, :hashed_password, TRUE, FALSE, TRUE, 'admin')"
            ),
            {
                "id": user_id,
                "email": email,
                "hashed_password": hashed_password,
            },
        )
        await session.commit()
    return str(user_id)


async def bootstrap_admin() -> int:
    """Idempotent bootstrap of the first admin user. Returns process exit code."""
    existing = await _existing_admin_id()
    if existing is not None:
        logger.info("Admin already exists, skipping bootstrap")
        return 0

    email, password = _read_credentials()
    helper = PasswordHelper()
    hashed = helper.hash(password)
    created_id = await _create_admin(email, hashed)
    logger.info("Bootstrap admin created: %s (id=%s)", email, created_id)
    return 0


def _configure_logging() -> None:
    """Stdout logging with the same redaction filter the api uses."""
    handler = logging.StreamHandler(sys.stdout)
    handler.addFilter(RedactingFilter())
    logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)


def main() -> int:
    _configure_logging()
    return asyncio.run(bootstrap_admin())


if __name__ == "__main__":
    sys.exit(main())
