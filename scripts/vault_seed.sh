#!/usr/bin/env bash
# Idempotent, NON-CLOBBERING seed of secret/maintainers-copilot (kv-v2).
#
#   - database_password / minio_root_password : fixed dev values.
#   - github_pat : taken from $GITHUB_PAT if set & non-empty, otherwise the
#     value already in Vault is preserved (so a clean `docker-compose up`
#     re-seeding api/db secrets never wipes an operator-supplied PAT).
#
# Operators set a real PAT before the dataset step:
#   export GITHUB_PAT=ghp_xxx && bash scripts/vault_seed.sh
set -euo pipefail

if [ -f .env ]; then
  set -a
  source .env
  set +a
fi

: "${VAULT_DEV_ROOT_TOKEN_ID:?must be set in .env}"

# Prefer an explicit VAULT_ADDR (set by the compose vault-seed service to
# reach the `vault` container); fall back to localhost for host runs.
VAULT_URL="${VAULT_ADDR:-http://localhost:${VAULT_PORT:-8200}}"
SECRET_URL="${VAULT_URL}/v1/secret/data/maintainers-copilot"

echo "Waiting for Vault..."
for _ in {1..30}; do
  if curl -fsS -o /dev/null "${VAULT_URL}/v1/sys/health"; then
    break
  fi
  sleep 1
done

# Preserve any existing github_pat / anthropic_api_key / auth_jwt_secret /
# bootstrap_admin_* unless a new value is supplied via env. github_pat +
# anthropic_api_key are operator-supplied; auth_jwt_secret is auto-generated
# on first boot if absent (Rule 2 / research R2 — never read from env);
# bootstrap_admin_* come from .env via BOOTSTRAP_ADMIN_EMAIL /
# BOOTSTRAP_ADMIN_PASSWORD on first seed, then are preserved on subsequent
# runs so re-running this script never resets the admin password.
existing_pat=""
existing_anthropic=""
existing_auth_jwt=""
existing_bootstrap_email=""
existing_bootstrap_password=""
existing_json="$(curl -fsS -H "X-Vault-Token: ${VAULT_DEV_ROOT_TOKEN_ID}" "${SECRET_URL}" 2>/dev/null || true)"
if [ -n "${existing_json}" ]; then
  existing_pat="$(printf '%s' "${existing_json}" \
    | grep -o '"github_pat"[[:space:]]*:[[:space:]]*"[^"]*"' \
    | sed 's/.*:[[:space:]]*"\(.*\)"/\1/' || true)"
  existing_anthropic="$(printf '%s' "${existing_json}" \
    | grep -o '"anthropic_api_key"[[:space:]]*:[[:space:]]*"[^"]*"' \
    | sed 's/.*:[[:space:]]*"\(.*\)"/\1/' || true)"
  existing_auth_jwt="$(printf '%s' "${existing_json}" \
    | grep -o '"auth_jwt_secret"[[:space:]]*:[[:space:]]*"[^"]*"' \
    | sed 's/.*:[[:space:]]*"\(.*\)"/\1/' || true)"
  existing_bootstrap_email="$(printf '%s' "${existing_json}" \
    | grep -o '"bootstrap_admin_email"[[:space:]]*:[[:space:]]*"[^"]*"' \
    | sed 's/.*:[[:space:]]*"\(.*\)"/\1/' || true)"
  existing_bootstrap_password="$(printf '%s' "${existing_json}" \
    | grep -o '"bootstrap_admin_password"[[:space:]]*:[[:space:]]*"[^"]*"' \
    | sed 's/.*:[[:space:]]*"\(.*\)"/\1/' || true)"
fi

github_pat="${GITHUB_PAT:-${existing_pat}}"
if [ -z "${github_pat}" ]; then
  echo "note: no GITHUB_PAT supplied and none stored; seeding empty github_pat (api does not need it; the dataset fetch does)."
fi

anthropic_api_key="${ANTHROPIC_API_KEY:-${existing_anthropic}}"
if [ -z "${anthropic_api_key}" ]; then
  echo "note: no ANTHROPIC_API_KEY supplied and none stored; seeding empty anthropic_api_key (/summarize will return 503 until one is provided)."
fi

# auth_jwt_secret: never operator-supplied via env (Rule 2). If absent in
# Vault, generate a fresh 32-byte hex value here. Stable across restarts
# because we preserve the existing value.
auth_jwt_secret="${existing_auth_jwt}"
if [ -z "${auth_jwt_secret}" ]; then
  auth_jwt_secret="$(openssl rand -hex 32 2>/dev/null || head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n')"
  echo "note: generated a fresh auth_jwt_secret (chatbot Part 1 fastapi-users JWT signing key)."
fi

# bootstrap_admin_* on first seed come from BOOTSTRAP_ADMIN_EMAIL /
# BOOTSTRAP_ADMIN_PASSWORD env vars (sourced from .env above). On subsequent
# seeds we preserve whatever Vault already has so re-running this script
# never resets the admin password.
bootstrap_admin_email="${BOOTSTRAP_ADMIN_EMAIL:-${existing_bootstrap_email}}"
bootstrap_admin_password="${BOOTSTRAP_ADMIN_PASSWORD:-${existing_bootstrap_password}}"
if [ -z "${bootstrap_admin_email}" ] || [ -z "${bootstrap_admin_password}" ]; then
  echo "note: BOOTSTRAP_ADMIN_EMAIL / BOOTSTRAP_ADMIN_PASSWORD unset and not in Vault; admin-bootstrap will refuse to run until you set them in .env and re-seed."
fi

echo "Seeding secret/maintainers-copilot..."
curl -fsS -X POST \
  -H "X-Vault-Token: ${VAULT_DEV_ROOT_TOKEN_ID}" \
  -H "Content-Type: application/json" \
  -d "$(cat <<JSON
{
  "data": {
    "database_password": "dev_postgres_password",
    "minio_root_password": "dev_minio_password",
    "github_pat": "${github_pat}",
    "anthropic_api_key": "${anthropic_api_key}",
    "auth_jwt_secret": "${auth_jwt_secret}",
    "bootstrap_admin_email": "${bootstrap_admin_email}",
    "bootstrap_admin_password": "${bootstrap_admin_password}"
  }
}
JSON
)" \
  "${SECRET_URL}" \
  | grep -q '"request_id"' && echo "Done."
