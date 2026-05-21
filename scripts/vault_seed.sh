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

# Preserve any existing github_pat / anthropic_api_key unless a new one is
# supplied via env. Both keys are operator-supplied — github_pat for the
# dataset fetch, anthropic_api_key for the model server's /summarize.
existing_pat=""
existing_anthropic=""
existing_json="$(curl -fsS -H "X-Vault-Token: ${VAULT_DEV_ROOT_TOKEN_ID}" "${SECRET_URL}" 2>/dev/null || true)"
if [ -n "${existing_json}" ]; then
  existing_pat="$(printf '%s' "${existing_json}" \
    | grep -o '"github_pat"[[:space:]]*:[[:space:]]*"[^"]*"' \
    | sed 's/.*:[[:space:]]*"\(.*\)"/\1/' || true)"
  existing_anthropic="$(printf '%s' "${existing_json}" \
    | grep -o '"anthropic_api_key"[[:space:]]*:[[:space:]]*"[^"]*"' \
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
    "anthropic_api_key": "${anthropic_api_key}"
  }
}
JSON
)" \
  "${SECRET_URL}" \
  | grep -q '"request_id"' && echo "Done."
