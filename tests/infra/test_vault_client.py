"""Constants & lookup contract for app.infra.vault_client.

The module is the single secrets adapter (Rule 2). Tests pin the public key
names so a rename doesn't silently break the lifespan or a downstream caller.
"""

from app.infra import vault_client


def test_key_constants_exposed() -> None:
    # Existing keys.
    assert vault_client.KEY_DATABASE_PASSWORD == "database_password"
    assert vault_client.KEY_MINIO_ROOT_PASSWORD == "minio_root_password"
    assert vault_client.KEY_GITHUB_PAT == "github_pat"
    assert vault_client.KEY_ANTHROPIC_API_KEY == "anthropic_api_key"
    # New for chatbot Part 1 (research R2).
    assert vault_client.KEY_AUTH_JWT_SECRET == "auth_jwt_secret"
