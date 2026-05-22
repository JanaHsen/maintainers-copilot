# Runbook

Canonical operator commands. A clean clone followed only by the "Bring the
stack up" block reaches a healthy `/health` (SC-001).

## Prerequisites

Docker + docker-compose, `uv`, `curl`; a GitHub PAT (public-repo read) and
`ngrok` only for the dataset / notebook steps.

## Bring the stack up

```bash
cp .env.example .env
# edit .env BEFORE first stack-up:
#   - VAULT_DEV_ROOT_TOKEN_ID = any dev string (e.g. root)
#   - BOOTSTRAP_ADMIN_EMAIL   = the email of the first admin (Part 1 Fix 2)
#   - BOOTSTRAP_ADMIN_PASSWORD = the password for that admin
# The placeholders that ship in .env.example MUST be changed before any real
# deploy — they are visible in version control. The admin-bootstrap container
# is idempotent, but it reads whatever is in Vault on FIRST run; subsequent
# runs preserve that admin's credentials even if .env changes.
docker-compose up -d            # builds the image; vault-seed -> migrate -> admin-bootstrap -> api
```

`vault-seed` (one-shot) seeds Vault, `migrate` runs `alembic upgrade head`
and exits 0, `admin-bootstrap` creates the first admin user from the Vault
credentials (no-op if an admin already exists), then `api` starts once every
dependency is healthy.

```bash
docker-compose ps                       # vault-seed/migrate Exited(0); rest healthy
curl -s localhost:8000/health | jq      # 200, status "ok", 5 deps, request_id, trace_id
```

Open Phoenix at `http://localhost:6006` to see the request span tree.

### Degraded / refuse-to-boot demos

```bash
# Vault unreachable -> REFUSE TO BOOT: Vault dependency failed
docker-compose stop vault && docker-compose restart api
docker-compose logs --tail=20 api   # one "REFUSE TO BOOT: Vault ..." line; api exits non-zero
docker-compose start vault && docker-compose restart api

# Vault missing auth_jwt_secret -> REFUSE TO BOOT (T039)
# Wipe the key, restart api, restore via vault_seed.sh.
docker-compose exec vault vault kv patch secret/maintainers-copilot auth_jwt_secret=
docker-compose restart api
docker-compose logs --tail=20 api   # "REFUSE TO BOOT: Vault dependency failed: missing required Vault key(s): auth_jwt_secret"
bash scripts/vault_seed.sh && docker-compose restart api

# Redis unreachable -> REFUSE TO BOOT (T040 — was 'degraded' pre-Part-1)
docker-compose stop redis && docker-compose restart api
docker-compose logs --tail=20 api   # "REFUSE TO BOOT: Redis dependency failed: ..."
docker-compose start redis && docker-compose restart api

# Chatbot tables missing -> REFUSE TO BOOT (T041)
# Roll the migration back one step; restart api; roll forward to recover.
docker-compose exec api alembic downgrade -1   # drops migration 0003 (chatbot tables)
docker-compose restart api
docker-compose logs --tail=20 api   # "REFUSE TO BOOT: users table missing: ..."
docker-compose exec api alembic upgrade head && docker-compose restart api
```

## Tear down

```bash
docker-compose down       # keep volumes
docker-compose down -v    # also drop pg/minio data (clean slate)
```

## Chat endpoints (Part 2)

```bash
# Sign in as the admin seeded by Part 1 Fix 2.
curl -s -c /tmp/mc.cookies -X POST http://localhost:8000/auth/login \
  -d 'username=admin@example.com&password=changeme-please' -o /dev/null

# Authed chat turn.
curl -s -b /tmp/mc.cookies -X POST http://localhost:8000/chat \
  -H 'Content-Type: application/json' \
  -d '{"message":"Classify this issue: title=DataFrame.groupby crashes; body=Raises ValueError on empty input."}' | jq

# Follow-up in the same conversation_id (from the prior response).
curl -s -b /tmp/mc.cookies -X POST http://localhost:8000/chat \
  -H 'Content-Type: application/json' \
  -d '{"conversation_id":"<UUID>", "message":"Now summarize it."}' | jq

# Widget chat (anonymous). Token must be an existing widget's host token;
# Origin must be in widget.allowed_origins.
curl -s -X POST http://localhost:8000/widget/chat \
  -H 'X-Widget-Token: <plaintext token from widget creation>' \
  -H 'Origin: http://localhost:8080' \
  -H 'Content-Type: application/json' \
  -d '{"widget_id":"<UUID>","session_id":"visitor-1","message":"Please remember my repo is acme/widget."}' | jq

# Expect: assistant_message contains a refusal of long-term memory;
#         tool_trace shows write_memory with is_error=true.
#         SELECT count(*) FROM chatbot_memories WHERE conversation_id IN
#         (this widget's session) = 0.
```

## Chatbot eval

```bash
# Fixture mode (no Anthropic calls; what CI runs).
docker compose exec api python -m evals.chatbot.eval_chatbot \
  --mode=fixture --check-thresholds

# Real mode (operator-only — burns API).
docker compose exec api python -m evals.chatbot.eval_chatbot --mode=real

# Regenerate fixture from a fresh real run (after a prompt change, etc.).
docker compose exec api python -m evals.chatbot.eval_chatbot \
  --mode=real --emit-fixture=evals/chatbot/fixture_outputs.jsonl
```

The four floors live under `eval_thresholds.yaml.chatbot.*` and gate
merge via the CI step "Chatbot eval gate (Rule 5 / Rule 10 — US4)".

## Known issues, deferred

Two issues identified during Part 1 that have known follow-up slices rather
than in-Part fixes:

### Host-shell `pytest` failures + narrow CI test coverage

`tests/services/test_retrieve_service.py::test_happy_path_returns_chunks`
fails when run directly from the host (WSL terminal) because the dev venv
cannot resolve the Docker network hostname `vault:8200`. The same test
passes when run inside the api container.

**Workaround for local dev:**

```bash
docker-compose exec api pytest tests/services/test_retrieve_service.py
```

…or run from the host with the host-facing endpoints injected:

```bash
VAULT_ADDR=http://localhost:8200 POSTGRES_HOST=localhost REDIS_HOST=localhost \
  MINIO_HOST=localhost MODEL_SERVER_HOST=localhost \
  uv run pytest tests/services/test_retrieve_service.py
```

**The broader issue.** The CI workflow at `.github/workflows/ci.yml` runs
`uv run pytest` against exactly four files: `test_log_redaction.py` (Rule 7),
`test_refuse_to_boot.py` (Rule 4), `model_server/test_boot_check.py`
(Rule 4), and `test_eval_classification.py` (Rule 5). The constitutional
rules and eval gates are covered, but the wider unit / service / repository
/ router / integration suite — including every test added in
`002-chatbot-part1-foundations` — is **not exercised in CI**. Feature-level
regressions could slip through unless an operator runs the wider suite
manually.

**Tracked as a follow-up:** CI-expansion slice. Will widen the pytest call
to include `tests/api/`, `tests/services/`, `tests/repositories/`,
`tests/infra/`, `tests/integration/`, `tests/bootstrap/`, plus the
container-side network configuration needed for those tests to reach the
dev compose stack.

### `audit_log REVOKE UPDATE,DELETE` bypassed by Postgres superuser

Migration 0003 issues `REVOKE UPDATE, DELETE ON audit_log FROM PUBLIC` so
the audit log is structurally append-only. The dev compose Postgres
connection uses the `postgres` superuser role, which bypasses every REVOKE
rule. The protection is symbolic in the dev environment — the
`AuditLogImmutableError` raised by `audit_repository.update()` /
`audit_repository.delete()` does enforce the contract at the application
layer, but raw-SQL UPDATEs from the dev role succeed.

**Tracked as a follow-up:** security-hardening slice. Production
deployments must use a least-privilege application role (`INSERT` and
`SELECT` only on `audit_log`, no `UPDATE` / `DELETE`) before the gate
bites. The application connection string then resolves to that role via
Vault; the superuser role stays only for `migrate` and `vault-seed`
one-shots.

## Dataset pipeline (host)

The scripts read host-facing endpoints, so override the in-container
defaults. The GitHub PAT must be a **classic** token (no scopes needed for
public read) or a fine-grained token with **Public Repositories (read-only)**.

```bash
# 1. put a real PAT into Vault (idempotent, non-clobbering)
GITHUB_PAT='ghp_xxx' VAULT_ADDR=http://localhost:8200 bash scripts/vault_seed.sh

# 2. fetch -> raw/pandas/issues/{run_id}/page_*.jsonl
VAULT_ADDR=http://localhost:8200 MINIO_HOST=localhost \
  uv run python scripts/dataset/fetch_issues.py        # note the printed run_id

# 3. inventory labels -> scripts/dataset/observed_labels.txt
MINIO_HOST=localhost \
  uv run python scripts/dataset/inventory_labels.py --run-id <run_id>

# 4. build splits -> processed/pandas/{run_id}/{train,val,test}.parquet + splits_report.json
VAULT_ADDR=http://localhost:8200 MINIO_HOST=localhost \
  uv run python scripts/dataset/build_splits.py --run-id <run_id>
```

Re-running `fetch_issues.py` creates a new `run_id`; prior objects are
never overwritten.

## Fine-tuning notebook (Colab Pro)

```bash
ngrok http 9000          # expose local MinIO; copy the https URL
```

Open `notebooks/finetune_distilbert.ipynb` in Colab (GPU runtime), set the
ngrok MinIO endpoint, MinIO credentials, and the processed `run_id`, then
**Run all**. It pushes `state_dict.pt`, `loss_curve.png`, and
`model_card.json` to `artifacts/classifier/distilbert/{run_id}/`
(`weights_sha256` matches the state dict — contract C6). Not wired into
`api` (FR-020).
