"""Alembic environment.

The engine comes from app/infra/database.py, so migrations connect with the
Vault-supplied password (Rule 2) and never read a secret from env or ini.
Migrations are hand-written SQL, so there is no ORM target metadata.
"""

from alembic import context
from app.infra.database import get_engine

target_metadata = None


def run_migrations_offline() -> None:
    raise RuntimeError(
        "offline migrations are unsupported: the DB URL is Vault-derived, "
        "not stored in alembic.ini (Rule 2). Run migrations online."
    )


def run_migrations_online() -> None:
    connectable = get_engine()
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
