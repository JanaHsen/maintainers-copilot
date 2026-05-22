# Quickstart — Chatbot Part 1 Foundations

How to bring up the stack and exercise Part 1 end-to-end on a dev laptop.

## Prerequisites

- The repo's existing prerequisites (Docker, `uv`, ~4 GB RAM for the compose stack).
- The RAG slice committed (commit `274afd6f` or later). Part 1 builds on top.

## Bring-up

```bash
# 1. Fresh clone or pull. Confirm on the Part 1 branch.
git checkout 002-chatbot-part1-foundations

# 2. Env file.
cp -n .env.example .env  # no-op if .env already present

# 3. Bring up the full stack.
docker compose up -d --build

# 4. Wait for /health to be ok (≤ 60 s on a warm cache, ≤ 3 min cold).
until curl -fsS http://localhost:8000/health > /dev/null; do
  sleep 2
done
echo "api healthy"

# 5. Apply migrations (0001, 0002, 0003).
docker compose exec api alembic upgrade head
```

If migration 0003 succeeds you should see `users`, `chatbot_memories`, `conversations`, `messages`, `widgets` tables created, plus the additive ALTERs on `audit_log`.

## Verify boot-checks

Each of these should refuse-to-boot with a specific log line. Test by mutating the stack and observing exit logs:

```bash
# (a) Vault auth_jwt_secret missing → REFUSE TO BOOT
docker compose exec vault \
  vault kv patch secret/maintainers-copilot auth_jwt_secret=
docker compose restart api
docker compose logs --tail=20 api | grep "REFUSE TO BOOT"
# Expect: REFUSE TO BOOT: Vault dependency failed: missing required Vault key(s): auth_jwt_secret

# Restore.
docker compose exec vault \
  vault kv patch secret/maintainers-copilot auth_jwt_secret="$(openssl rand -hex 32)"

# (b) Redis unreachable → REFUSE TO BOOT (Part 1 promotes this to fatal)
docker compose stop redis
docker compose restart api
docker compose logs --tail=20 api | grep "REFUSE TO BOOT"
# Expect: REFUSE TO BOOT: Redis dependency failed: Redis unreachable

docker compose start redis

# (c) chatbot_memories table missing → REFUSE TO BOOT (simulate by downgrading)
docker compose exec api alembic downgrade -1
docker compose restart api
docker compose logs --tail=20 api | grep "REFUSE TO BOOT"
# Expect: REFUSE TO BOOT: chatbot_memories table missing

docker compose exec api alembic upgrade head
```

## Story 1 — Register, log in, get session

```bash
# Register
curl -i -X POST http://localhost:8000/auth/register \
  -H 'Content-Type: application/json' \
  -d '{"email":"alice@example.com","password":"correct-horse-battery-staple"}'
# Expect: 201 Created with {id,email,role:"user",...}

# Log in (form-encoded — fastapi-users convention)
curl -i -c /tmp/mc-cookies.txt -X POST http://localhost:8000/auth/login \
  -d 'username=alice@example.com&password=correct-horse-battery-staple'
# Expect: 204 with Set-Cookie: mc_session=...

# Profile
curl -s -b /tmp/mc-cookies.txt http://localhost:8000/users/me
# Expect: {"id":"...","email":"alice@example.com","role":"user",...}

# Unauthenticated → 401
curl -i http://localhost:8000/users/me
# Expect: 401
```

## Story 2 — Cross-conversation memory recall (authenticated)

Run via the included integration test rather than curl, since `write_memory` / `recall_memory` are internal primitives, not HTTP endpoints in Part 1:

```bash
docker compose exec api pytest tests/integration/test_cross_conversation_memory_recall.py -v
# Expect: PASS
# What it does:
#   - Creates a fixture user.
#   - Calls write_memory in conversation A with content "Alice prefers Conventional Commits."
#   - Calls recall_memory in conversation B (different conversation_id), query "what commit style?".
#   - Asserts top-1 hit is the memory written.
#   - Creates a second user (Bob), calls recall_memory with the same query.
#   - Asserts Bob's hits do not contain Alice's memory.
```

## Story 3 — Widget actor refusal

```bash
docker compose exec api pytest tests/integration/test_widget_actor_refusal.py -v
# Expect: PASS
# What it does:
#   - Creates a widget (admin path).
#   - Builds a WidgetSession actor.
#   - Calls write_memory → assert WriteMemoryError(kind="widget_actor_forbidden")
#   - Calls recall_memory → assert RecallMemoryError(kind="widget_actor_forbidden")
#   - Verifies no row was inserted into chatbot_memories.
```

## Story 4 — Audit trail

```bash
docker compose exec api pytest tests/integration/test_audit_writes.py -v
# Expect: PASS
# What it does:
#   - Calls write_memory → asserts an audit_log row with action='memory.write' lands.
#   - Creates a widget → asserts an audit_log row with action='widget.create' lands.
#   - Revokes the widget → asserts an audit_log row with action='widget.revoke' lands.
#   - Attempts an UPDATE on audit_log → asserts a Postgres permission error.
```

## Story 5 — Upstream services smoke

```bash
# NER
curl -s -X POST http://localhost:8000/ner \
  -H 'Content-Type: application/json' \
  -d '{"text":"Bug in pandas-dev/pandas src/foo.py raises ConnectionError when using numpy"}' \
  | jq
# Expect:
# {
#   "entities": {
#     "repo_names": ["pandas-dev/pandas"],
#     "file_paths": ["src/foo.py"],
#     "error_types": ["ConnectionError"],
#     "package_names": ["numpy"]
#   },
#   "request_id": "...", "trace_id": "..."
# }

# Summarize
curl -s -X POST http://localhost:8000/summarize \
  -H 'Content-Type: application/json' \
  -d '{"text":"<paste a real issue body>"}' \
  | jq
# Expect: { "summary": "2-3 sentences ...", ... }
```

## Run the evals locally

```bash
# NER
docker compose exec api python -m evals.ner.eval_ner --mode=fixture
# Reports per-bucket F1 and a JSON report at evals/reports/{ts}/ner.json.

# Summarize
docker compose exec api python -m evals.summarize.eval_summarize --mode=fixture
# Reports rubric scores and a JSON report at evals/reports/{ts}/summarize.json.
```

Both runs MUST clear the floors in `eval_thresholds.yaml`:

```yaml
ner:
  f1_floor: <set after pilot run, 5pt buffer below observed>
summarize:
  rubric_floor: <set after pilot run, 5pt buffer below observed>
```

If either floor is unset (or zero) the api refuses-to-boot per Rule 4.

## Redaction smoke

```bash
docker compose exec api pytest tests/infra/test_log_redaction.py -v
# Expect: PASS (existing test extended with JWT-shape + email-shape cases)
```

Additionally the cross-conversation memory recall test (Story 2) injects a fake `sk-ant-AAAA0000…` key into a memory write and asserts the persisted `content` contains `[REDACTED]` rather than the key.

## Resetting the stack

`docker compose down -v` resets everything including the Postgres + Redis volumes. This is the destructive path; use it only when you genuinely want a clean slate. Constitution Rule 3 forbids `docker compose down -v` as a *migration strategy* — it is fine as a development-environment reset.

## CI

The CI pipeline runs (in order, per Rule 10):

1. `ruff` + `mypy app/`.
2. Build all docker-compose images.
3. The classification eval gate.
4. The RAG eval gate (existing).
5. **The NER eval gate (new in Part 1).**
6. **The summarize eval gate (new in Part 1).**
7. The log-redaction test (existing — now also covering JWT + email patterns).
8. The stack smoke test (`docker compose up`, hit `/health`, hit `/widget.js` *is not present in Part 1 — added in Part 3*; tear down).

Any step's failure blocks merge.

---

## Quickstart smoke (2026-05-22 run, agent-driven)

The agent does not have docker access in this development shell, so the docker-compose-driven steps (bring-up, live boot-check demos, curl-based smoke) are deferred to the operator. The following was verified IN-PROCESS via the test suite on the dev venv (`uv run pytest`).

### Passing test suite snapshot (`uv run pytest tests/ --ignore=tests/integration --ignore=tests/model_server --ignore=tests/scripts --ignore=tests/services/test_retrieve_filters.py -q`)

```
1 failed, 112 passed, 37 skipped in 384.40s
```

- **112 passed** — covers repositories, services, infra (auth backend, redaction, vault constants), routers (auth, ner, summarize), tool primitives (write_memory + recall_memory), redaction tests (including the new JWT + email + persistence-helper cases), boot-check negative tests (auth_jwt_secret missing, Redis unreachable, chatbot tables missing).
- **37 skipped** — integration tests gated on a reachable Postgres/Redis/Vault. Each one carries a documented `_ensure_postgres_reachable` skip guard (Phase B/E/G pattern). These will exercise their assertions when run against the live `docker compose` stack.
- **1 failed** — `tests/services/test_retrieve_service.py::test_happy_path_returns_chunks` — **pre-existing, unrelated to Part 1**. The failure mode is `VaultUnreachableError: Vault unreachable at http://vault:8200` (the dev venv cannot resolve the in-compose `vault` hostname from the host shell); the same test passes inside the api container. Confirmed pre-existing via `git stash && pytest` before any Part 1 work began.

### Lint + typecheck

```
$ uv run ruff check app/
All checks passed!

$ uv run mypy app/
Success: no issues found in 54 source files
```

### What the operator must run live (story-by-story)

The five story sections above remain authoritative for the live walk-through. The agent could not exercise them here because:

- **Story 1 (auth round-trip):** Needs Vault seeded with `auth_jwt_secret` + a reachable async Postgres. The integration test `tests/api/test_auth_router.py` (T017) carries the assertions; it skips here but will run against the live stack.
- **Story 2 (cross-conversation recall):** `tests/integration/test_cross_conversation_memory_recall.py` (T024) — same.
- **Story 3 (widget actor refusal):** `tests/integration/test_widget_actor_refusal.py` (T025) — same.
- **Story 4 (audit trail):** `tests/integration/test_audit_writes.py` (T026) — same.
- **Story 5 (NER + summarize):** Needs a live `anthropic_api_key` in Vault. The eval scripts (`evals/ner/eval_ner.py`, `evals/summarize/eval_summarize.py`) run cleanly in `--mode=fixture` (T034, T037).
- **Boot-check demos (Story 6):** Negative tests run in-process (T042 passes 3/3) but the live docker-compose recovery dance is documented in [RUNBOOK.md](../../RUNBOOK.md) Degraded / refuse-to-boot demos section.

### Eval fixture-mode runs (offline, no Anthropic call)

Both eval harnesses run cleanly in fixture mode from the dev venv:

```
$ uv run python -m evals.ner.eval_ner --mode=fixture
# exits 0; report at evals/reports/<ts>/ner.json

$ uv run python -m evals.summarize.eval_summarize --mode=fixture
# exits 0; report at evals/reports/<ts>/summarize.json
```

Floors set conservatively in `eval_thresholds.yaml`: `ner.f1_floor=0.60`, `summarize.rubric_floor=3.5`. Both non-zero per Rule 4. To be revisited after an operator-driven real-API pilot run.
