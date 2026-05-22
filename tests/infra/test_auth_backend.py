"""auth_backend wiring — Vault-sourced JWT key + cookie transport settings.

These tests do NOT touch Postgres or Redis. They cover:

  * the JWT strategy reads its signing key from Vault on first call
    (research R2, Rule 2);
  * the strategy factory is cached so subsequent calls do not re-read Vault;
  * the cookie transport carries the contract-specified flags
    (``mc_session`` / HttpOnly / SameSite=Lax / max-age = 1 h).
"""

from __future__ import annotations

import importlib

import pytest

from app.infra import auth_backend as auth_backend_module
from app.infra.vault_client import KEY_AUTH_JWT_SECRET


@pytest.fixture(autouse=True)
def _reset_strategy_cache():
    """Drop the ``lru_cache`` so each test starts from a clean Vault read."""
    auth_backend_module.get_jwt_strategy.cache_clear()
    yield
    auth_backend_module.get_jwt_strategy.cache_clear()


def test_get_jwt_strategy_reads_secret_from_vault(monkeypatch) -> None:
    """The strategy carries the secret returned by ``read_secrets``."""
    calls: list[list[str]] = []

    def fake_read_secrets(keys: list[str]) -> dict[str, str]:
        calls.append(list(keys))
        return {KEY_AUTH_JWT_SECRET: "vault-sourced-test-secret"}

    monkeypatch.setattr(auth_backend_module, "read_secrets", fake_read_secrets)
    strategy = auth_backend_module.get_jwt_strategy()
    assert strategy.secret == "vault-sourced-test-secret"
    assert calls == [[KEY_AUTH_JWT_SECRET]]


def test_get_jwt_strategy_is_cached(monkeypatch) -> None:
    """Second call must not re-read Vault."""
    call_count = {"n": 0}

    def fake_read_secrets(keys: list[str]) -> dict[str, str]:
        call_count["n"] += 1
        return {KEY_AUTH_JWT_SECRET: "vault-sourced-test-secret"}

    monkeypatch.setattr(auth_backend_module, "read_secrets", fake_read_secrets)
    auth_backend_module.get_jwt_strategy()
    auth_backend_module.get_jwt_strategy()
    assert call_count["n"] == 1


def test_strategy_lifetime_is_one_hour(monkeypatch) -> None:
    """The cookie max-age in the contract (3600 s) ties to the JWT lifetime."""
    monkeypatch.setattr(
        auth_backend_module,
        "read_secrets",
        lambda _keys: {KEY_AUTH_JWT_SECRET: "x"},
    )
    strategy = auth_backend_module.get_jwt_strategy()
    assert strategy.lifetime_seconds == 3600
    assert auth_backend_module.SECRET_LIFETIME_SECONDS == 3600


def test_cookie_transport_contract() -> None:
    """Cookie name + HttpOnly + SameSite=Lax + Max-Age match auth.openapi.yaml."""
    t = auth_backend_module.cookie_transport
    assert t.cookie_name == "mc_session"
    assert t.cookie_httponly is True
    assert t.cookie_samesite == "lax"
    assert t.cookie_max_age == 3600


def test_auth_backend_uses_cookie_transport() -> None:
    """The AuthenticationBackend wires the cookie transport + the JWT factory."""
    b = auth_backend_module.auth_backend
    assert b.name == "jwt-cookie"
    assert b.transport is auth_backend_module.cookie_transport
    # ``get_strategy`` is the factory passed to AuthenticationBackend.
    assert b.get_strategy is auth_backend_module.get_jwt_strategy


def test_module_import_does_not_read_vault(monkeypatch) -> None:
    """Importing ``auth_backend`` must not eagerly call read_secrets."""
    calls = {"n": 0}

    def fake_read_secrets(_keys: list[str]) -> dict[str, str]:
        calls["n"] += 1
        return {KEY_AUTH_JWT_SECRET: "x"}

    monkeypatch.setattr("app.infra.vault_client.read_secrets", fake_read_secrets)
    # Re-import to confirm side effects on import path.
    importlib.reload(auth_backend_module)
    assert calls["n"] == 0
