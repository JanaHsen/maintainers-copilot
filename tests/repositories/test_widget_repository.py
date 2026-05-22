"""widget_repository — create / get_by_token_hash / revoke.

Skips cleanly if Postgres / Vault are unreachable.

Covers:
  * ``create`` returns a fresh 43-char URL-safe plaintext token and writes
    its sha256 hex (64-char) to the row,
  * ``get_by_token_hash`` matches a non-revoked widget by sha256 of the
    plaintext, returns ``None`` for an unknown hash,
  * ``revoke`` sets ``revoked_at`` and the row no longer matches
    ``get_by_token_hash`` (the partial unique index covers active rows only).
"""

from __future__ import annotations

import hashlib
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from app.infra.database import get_engine
from app.infra.vault_client import VaultBootstrapError
from app.repositories import widget_repository


def _ensure_postgres_reachable() -> None:
    try:
        with get_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
    except (OperationalError, VaultBootstrapError) as exc:
        pytest.skip(f"Postgres / Vault not reachable in this env: {exc}")


def _seed_user() -> uuid.UUID:
    user_id = uuid.uuid4()
    with get_engine().begin() as conn:
        conn.execute(
            text(
                "INSERT INTO users (id, email, hashed_password, is_active, "
                "is_superuser, is_verified, role) "
                "VALUES (:id, :email, 'placeholder', TRUE, FALSE, FALSE, 'user')"
            ),
            {
                "id": user_id,
                "email": f"pytest-widget-{user_id.hex[:8]}@example.com",
            },
        )
    return user_id


def _cleanup_user(user_id: uuid.UUID) -> None:
    with get_engine().begin() as conn:
        conn.execute(text("DELETE FROM users WHERE id = :id"), {"id": user_id})


def test_create_returns_fresh_token_and_stores_sha256() -> None:
    _ensure_postgres_reachable()
    owner = _seed_user()
    try:
        widget_id, plaintext = widget_repository.create(
            name="docs-site",
            allowed_origins=["https://example.com"],
            owner_user_id=owner,
        )
        # 32-byte URL-safe token: base64-url encodes 32 bytes to 43 chars
        # (no padding because token_urlsafe strips it).
        assert isinstance(plaintext, str)
        assert len(plaintext) == 43
        assert all(c.isalnum() or c in "-_" for c in plaintext)

        # The stored hash is the sha256 hex of the plaintext.
        expected_hash = hashlib.sha256(plaintext.encode()).hexdigest()
        with get_engine().connect() as conn:
            row = conn.execute(
                text("SELECT host_token_hash FROM widgets WHERE id = :id"),
                {"id": widget_id},
            ).first()
        assert row is not None
        assert row.host_token_hash == expected_hash
        assert len(row.host_token_hash) == 64  # sha256 hex
    finally:
        _cleanup_user(owner)


def test_get_by_token_hash_finds_active_widget() -> None:
    _ensure_postgres_reachable()
    owner = _seed_user()
    try:
        widget_id, plaintext = widget_repository.create(
            name="site",
            allowed_origins=[],
            owner_user_id=owner,
        )
        token_hash = hashlib.sha256(plaintext.encode()).hexdigest()
        found = widget_repository.get_by_token_hash(token_hash)
        assert found is not None
        assert found.id == widget_id
        assert found.owner_user_id == owner
        assert found.revoked_at is None

        # An unknown hash returns None.
        assert widget_repository.get_by_token_hash("0" * 64) is None
    finally:
        _cleanup_user(owner)


def test_revoke_sets_timestamp_and_hides_from_lookup() -> None:
    _ensure_postgres_reachable()
    owner = _seed_user()
    try:
        widget_id, plaintext = widget_repository.create(
            name="site",
            allowed_origins=[],
            owner_user_id=owner,
        )
        token_hash = hashlib.sha256(plaintext.encode()).hexdigest()

        # Pre-revoke: lookup succeeds.
        assert widget_repository.get_by_token_hash(token_hash) is not None

        widget_repository.revoke(widget_id)

        # Post-revoke: lookup returns None (partial unique index + WHERE clause).
        assert widget_repository.get_by_token_hash(token_hash) is None

        # The row still exists with revoked_at set.
        with get_engine().connect() as conn:
            row = conn.execute(
                text("SELECT revoked_at FROM widgets WHERE id = :id"),
                {"id": widget_id},
            ).first()
        assert row is not None
        assert row.revoked_at is not None
    finally:
        _cleanup_user(owner)
