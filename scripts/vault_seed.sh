#!/usr/bin/env bash
set -euo pipefail

if [ -f .env ]; then
  set -a
  source .env
  set +a
fi

: "${VAULT_DEV_ROOT_TOKEN_ID:?must be set in .env}"
: "${GITHUB_PAT:?must be set in shell: export GITHUB_PAT=ghp_xxx}"

# Prefer an explicit VAULT_ADDR (set by the compose vault-seed service to
# reach the `vault` container); fall back to localhost for host runs.
VAULT_URL="${VAULT_ADDR:-http://localhost:${VAULT_PORT:-8200}}"

echo "Waiting for Vault..."
for i in {1..30}; do
  if curl -fsS -o /dev/null "${VAULT_URL}/v1/sys/health"; then
    break
  fi
  sleep 1
done

echo "Seeding secret/maintainers-copilot..."
curl -fsS -X POST \
  -H "X-Vault-Token: ${VAULT_DEV_ROOT_TOKEN_ID}" \
  -H "Content-Type: application/json" \
  -d "$(cat <<JSON
{
  "data": {
    "database_password": "dev_postgres_password",
    "minio_root_password": "dev_minio_password",
    "github_pat": "${GITHUB_PAT}"
  }
}
JSON
)" \
  "${VAULT_URL}/v1/secret/data/maintainers-copilot" \
  | grep -q '"request_id"' && echo "Done."