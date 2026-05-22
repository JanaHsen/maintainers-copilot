---
description: "Task list for Chatbot Part 1 — Foundations (auth, memory, tool primitives, upstream services)"
---

# Tasks: Chatbot Part 1 — Foundations

**Input**: Design documents from `specs/002-chatbot-part1-foundations/`
**Prerequisites**: [plan.md](./plan.md), [spec.md](./spec.md), [research.md](./research.md), [data-model.md](./data-model.md), [contracts/](./contracts/), [quickstart.md](./quickstart.md)
**Conventions**: each task is a commit boundary; tests are not optional (constitution Rules 5, 7, 10). `[P]` = parallelizable. `[USn]` = user story this task serves (from spec.md). Constitution rule references are inline.

---

## Phase A — Setup & foundational adapters (no DB)

These tasks introduce the dependency, the new Vault key constant, the redaction extensions, and the async-engine isolation. They must land before Phase B (migration) so the codebase compiles + lints with the new pieces in place.

- [ ] T001 Add `fastapi-users[sqlalchemy]>=14` to `pyproject.toml`, run `uv lock`, and confirm `uv sync` succeeds inside the api container. (Rule 8.) Files: `pyproject.toml`, `uv.lock`. Acceptance: `docker compose build api` succeeds; `python -c "import fastapi_users"` runs inside the container. Tests: none.

- [ ] T002 [P] Add `KEY_AUTH_JWT_SECRET = "auth_jwt_secret"` to `app/infra/vault_client.py` next to the existing key constants. (Rule 2.) Files: `app/infra/vault_client.py`. Acceptance: constant exists; `mypy app/` passes; existing redaction-grep CI still finds zero literal secrets. Tests: extend `tests/infra/test_vault_client.py` with a missing-key case for `auth_jwt_secret`.

- [ ] T003 [P] Wire the Vault bootstrap script (`scripts/init_vault.sh` or compose `vault-init`) to set `auth_jwt_secret` to a fresh 32-byte hex random value on stack bring-up. (Rule 2, Rule 8.) Files: `scripts/init_vault.sh` (and any compose service that runs it). Acceptance: `docker compose up -d` followed by `vault kv get secret/maintainers-copilot` shows a populated `auth_jwt_secret`. Tests: covered indirectly by Phase G negative boot-check.

- [ ] T004 Extend `app/infra/log_redaction.py` with two new rules — JWT-shaped tokens (`eyJ...x.y.z`) and RFC-5322-shaped emails — AND export a new `redact_for_persistence(text: str) -> str` helper. (Rule 7, R6.) Files: `app/infra/log_redaction.py`. Acceptance: `redact_for_persistence("contact alice@example.com about sk-ant-AAAA0000")` returns a string with both substrings replaced by their placeholders. Tests: extend `tests/infra/test_log_redaction.py` with: JWT input, email input, benign technical phrase ("ConnectionError on requests package") asserts UNTOUCHED.

- [ ] T005 [P] Add `app/infra/database_async.py` with an `AsyncEngine` and `async_sessionmaker` connected via `postgresql+psycopg_async://…`; the engine reads its DB password from Vault through the existing `KEY_DATABASE_PASSWORD`. (Rule 1, Rule 2, R1.) Files: `app/infra/database_async.py`. Acceptance: importing the module does not eagerly connect; `mypy app/` passes; an async smoke fixture can open and close a session. Tests: `tests/infra/test_database_async.py` connects to the test Postgres, runs `SELECT 1`.

- [ ] T006 [P] Create `app/domain/auth.py` exporting `UserRead`, `UserCreate`, `UserUpdate` Pydantic schemas and a `Role` literal. (Rule 1, Rule 9.) Files: `app/domain/auth.py`. Acceptance: `mypy app/` passes; schemas align with `contracts/auth.openapi.yaml` (UUID id, email, is_active, is_superuser, is_verified, role∈{user,admin}). Tests: not required at this layer — covered by router tests in Phase C.

---

## Phase B — Data layer: migration `0003_chatbot.py` + repositories

Migration ships first because every repository imports `users` / `widgets` / etc. Repositories follow, each as its own commit so failures localize.

- [ ] T007 Create migration `alembic/versions/0003_chatbot.py` implementing the data-model.md DDL: `users`, `widgets`, `conversations`, `messages`, `chatbot_memories`, plus the additive `audit_log` ALTERs and `REVOKE UPDATE, DELETE` + `GRANT INSERT, SELECT`. (Rule 3, R3.) Files: `alembic/versions/0003_chatbot.py`. Acceptance: `docker compose exec api alembic upgrade head` against a fresh DB succeeds; `alembic downgrade -1` reverses cleanly; running `\d users \d chatbot_memories \d audit_log` shows the expected columns + indices. Tests: `tests/alembic/test_0003_migration.py` exercises upgrade-then-downgrade on an ephemeral DB.

- [ ] T008 [P] [US1] Create `app/repositories/user_repository.py` exposing `SQLAlchemyUserDatabase`-compatible glue tied to `app/infra/database_async.py`. (Rule 1, R1.) Files: `app/repositories/user_repository.py`. Acceptance: a fastapi-users `BaseUserManager` instantiation succeeds against this repo. Tests: `tests/repositories/test_user_repository.py` writes a row, fetches it by id and by email.

- [ ] T009 [P] [US2] Create `app/repositories/memory_repository.py` with `insert(...)` and `query_top_k(user_id, query_embedding, k=5)` using cosine distance (pgvector `<=>`). (Rule 1.) Files: `app/repositories/memory_repository.py`. Acceptance: write one row for user A, write one row for user B, `query_top_k` for user A returns A's row only. Tests: `tests/repositories/test_memory_repository.py` covers insert, top-k, user isolation (SC-003), empty result.

- [ ] T010 [P] [US2] [US3] Create `app/repositories/conversation_repository.py` with `create`, `get`, `append_message`, `list_messages`. (Rule 1.) Files: `app/repositories/conversation_repository.py`. Acceptance: `create({user_id: ...})` and `create({widget_id: ..., session_id: ...})` succeed; `create({user_id: ..., widget_id: ...})` raises the Postgres CHECK violation. Tests: `tests/repositories/test_conversation_repository.py` covers the CHECK invariant + message append.

- [ ] T011 [P] [US3] [US4] Create `app/repositories/widget_repository.py` with `create(name, allowed_origins, owner_user_id) -> (widget_id, plaintext_token)` (returning the one-time plaintext), `get_by_token_hash`, `revoke`. (Rule 1, R5.) Files: `app/repositories/widget_repository.py`. Acceptance: `create` returns a fresh 43-char URL-safe token and inserts the sha256 hash; `get_by_token_hash(sha256(plaintext))` returns the row; `revoke` sets `revoked_at` and the row no longer matches the unique active-token partial index. Tests: `tests/repositories/test_widget_repository.py`.

- [ ] T012 [P] [US4] Create `app/repositories/audit_repository.py` with `record(action, target_type, target_id, payload, actor_user_id=None, actor_widget_id=None)` and an explicit `AuditLogImmutableError` raised on `update()`/`delete()` callers. (Rule 1, R3.) Files: `app/repositories/audit_repository.py`. Acceptance: `record(...)` with both actor ids set raises `ValueError`; with neither set raises `ValueError`; SQL UPDATE attempt yields a `psycopg.errors.InsufficientPrivilege`. Tests: `tests/repositories/test_audit_repository.py`.

- [ ] T013 [P] Create Pydantic domain modules: `app/domain/memory.py` (Memory, MemoryWriteResult, MemoryRecallHit), `app/domain/conversation.py` (Conversation, Message, MessageRole, Actor, AuthedUser, WidgetSession), `app/domain/widget.py` (Widget, WidgetCreate, WidgetCreated), `app/domain/audit.py` (AuditEntry, AuditAction). (Rule 1, Rule 9.) Files: as listed. Acceptance: `mypy app/` passes. Tests: none — exercised by repository tests above.

---

## Phase C — Authentication (User Story 1)

**Story goal**: A maintainer can register, sign in, fetch their profile, and sign out. Role-gated endpoints reject `user`-role callers (FR-001..FR-007, SC-001, SC-010).

- [ ] T014 [US1] Create `app/infra/auth_backend.py` with the fastapi-users `JWTStrategy` (reading the secret from `read_secrets([KEY_AUTH_JWT_SECRET])`), the `CookieTransport(cookie_name="mc_session", cookie_httponly=True, cookie_samesite="lax")`, the `AuthenticationBackend("jwt-cookie", ...)`, the `UserManager`, and the `current_active_user` + `current_active_superuser` dependencies. (Rule 1, Rule 2, R1, R2.) Files: `app/infra/auth_backend.py`. Acceptance: importing it does not eagerly read the secret; calling `get_jwt_strategy()` reads from Vault exactly once. Tests: `tests/infra/test_auth_backend.py` mocks Vault and asserts the strategy uses the returned secret.

- [ ] T015 [US1] Create `app/services/auth_service.py` exposing a `require_admin` dependency that combines `current_active_user` with a role check. (Rule 1, Rule 9.) Files: `app/services/auth_service.py`. Acceptance: a user with `role='user'` calling a `require_admin`-gated endpoint receives 403; a user with `role='admin'` receives 200. Tests: covered in T017.

- [ ] T016 [US1] Create `app/api/routers/auth.py` wiring fastapi-users' register/login/logout routers + a `/users/me` GET. Mount under the existing `api_router` in `app/api/routers/__init__.py`. (Rule 1.) Files: `app/api/routers/auth.py`, `app/api/routers/__init__.py`. Acceptance: route table includes `POST /auth/register`, `POST /auth/login`, `POST /auth/logout`, `GET /users/me`. Tests: covered in T017.

- [ ] T017 [US1] Integration test for the auth round-trip. (Rule 7 — uses request-id middleware; SC-001, SC-010.) Files: `tests/api/test_auth_router.py`. Acceptance: tests pass: register → 201; login → 204 + Set-Cookie; `/users/me` with cookie → 200; `/users/me` without → 401; `require_admin` endpoint with `role='user'` → 403; same endpoint with admin → 200; logout → 204; subsequent `/users/me` → 401.

---

## Phase D — Short-term memory service (Redis)

Independent of Phase C; can run in parallel.

- [ ] T018 [P] Create `app/services/short_term_memory_service.py` with `append(conversation_id, role, content, *, tool_name=None, tool_input=None, tool_output=None)`, `get_window(conversation_id, max_tokens=4000)`, `expire_at(conversation_id, ttl_seconds)`. Token-budgeted window read using a coarse `len(content)//4` token approximation. (Rule 1, Rule 9, FR-015..FR-018.) Files: `app/services/short_term_memory_service.py`. Acceptance: append → get_window returns the appended message; TTL refreshes on each append; widget default 3600 s, authed default 86400 s. Tests: `tests/services/test_short_term_memory_service.py` against ephemeral Redis: append-then-read, TTL refresh, window cap, JSON encoding of `tool_*` columns. Apply `log_redaction.redact_for_persistence` to `content` before encoding (Rule 7, R6).

---

## Phase E — Memory tool primitives + their tests (User Stories 2 & 3)

**Story goal**: Authenticated maintainers' memories persist across conversations; widget actors are refused at the primitive layer (FR-008..FR-014, SC-002..SC-004, SC-007).

- [ ] T019 [US2] Create `app/services/tools/__init__.py` (subpackage marker). (Rule 9.) Files: `app/services/tools/__init__.py`. Acceptance: importable. Tests: none.

- [ ] T020 [US2] Create `app/services/tools/write_memory_tool.py` implementing the contract in [contracts/memory-tools.md](./contracts/memory-tools.md). Steps: actor-kind guard → `redact_for_persistence` → embed → single transaction (memory insert + audit insert) → Phoenix span → typed outcome. (Rule 1, Rule 7, Rule 11, R6.) Files: `app/services/tools/write_memory_tool.py`. Acceptance: signature matches contracts/memory-tools.md; returns `WriteMemoryOk` on happy path; returns each `WriteMemoryError` kind for its corresponding failure; never raises. Tests: in T023.

- [ ] T021 [US2] Create `app/services/tools/recall_memory_tool.py` implementing the contract in [contracts/memory-tools.md](./contracts/memory-tools.md). Steps: actor-kind guard → embed query → `memory_repository.query_top_k` → Phoenix span → typed outcome. (Rule 1, Rule 7, Rule 11.) Files: `app/services/tools/recall_memory_tool.py`. Acceptance: signature matches contract; returns `RecallMemoryOk(hits=[])` on empty result; returns `RecallMemoryError("widget_actor_forbidden", …)` for widget actors. Tests: in T024.

- [ ] T022 [P] [US2] Unit tests for `write_memory_tool` with mocked `embedding_client` and a real ephemeral Postgres. (Rule 5 sibling, FR-007/FR-011/FR-013.) Files: `tests/services/tools/test_write_memory_tool.py`. Acceptance: covers happy path, widget refusal, embedding-unreachable, embedding-timeout, audit-write rollback, redaction-before-persistence.

- [ ] T023 [P] [US2] Unit tests for `recall_memory_tool`. (Rule 5 sibling.) Files: `tests/services/tools/test_recall_memory_tool.py`. Acceptance: covers happy path, widget refusal, embedding-unreachable, empty-result returns `RecallMemoryOk(hits=[])`.

- [ ] T024 [US2] Integration test: cross-conversation memory recall + cross-user isolation. (FR-008/FR-010, SC-002, SC-003.) Files: `tests/integration/test_cross_conversation_memory_recall.py`. Acceptance: Alice writes memory in conversation A; Alice recalls in conversation B (different uuid) → top-1 hit; Bob recalls same query → 0 of Alice's memories returned. Also asserts that a write whose content contains `sk-ant-AAAA0000…` and `bob@example.com` lands with both substrings redacted in `chatbot_memories.content`.

- [ ] T025 [US3] Integration test: widget actor refusal end-to-end. (FR-011, SC-004.) Files: `tests/integration/test_widget_actor_refusal.py`. Acceptance: a WidgetSession actor invoking `write_memory` returns `WriteMemoryError(kind="widget_actor_forbidden")`; same for `recall_memory`; `chatbot_memories` rowcount unchanged across the test.

- [ ] T026 [US4] Integration test: audit-log writes for memory + widget create + widget revoke; UPDATE attempt fails. (FR-021/FR-022/FR-024, SC-006.) Files: `tests/integration/test_audit_writes.py`. Acceptance: each event lands exactly one matching `audit_log` row; `UPDATE audit_log …` raises `InsufficientPrivilege`.

---

## Phase F — Upstream services + their evals (User Story 5)

**Story goal**: `/ner` and `/summarize` exist and meet their floors (FR-028..FR-030, SC-005).

- [ ] T027 [P] Create `prompts/ner.md` with the system + instruction text for strict-JSON 4-bucket NER. Version-header on line 1. (Rule 9, R7.) Files: `prompts/ner.md`. Acceptance: file exists; contains an instruction to output JSON only, with the 4 named buckets and array-of-strings shape. Tests: covered by NER eval.

- [ ] T028 [P] [US5] Create `app/domain/ner.py` (NerRequest, EntityBuckets, NerResponse) and `app/domain/summarize.py` (SummarizeRequest, SummarizeResponse), aligned with contracts/. (Rule 1, Rule 9.) Files: `app/domain/ner.py`, `app/domain/summarize.py`. Acceptance: `mypy app/` passes; shapes match the OpenAPI contracts. Tests: none.

- [ ] T029 [US5] Create `app/services/ner_service.py` calling `anthropic_client.complete()` with `prompts/ner.md` + the input text, parsing strict JSON, returning typed outcome `NerOk | NerError`. (Rule 1, Rule 11, R7.) Files: `app/services/ner_service.py`. Acceptance: happy path returns `NerOk(entities=EntityBuckets(...))`; bad JSON returns `NerError("bad_format", ...)`; Anthropic error variants map to typed kinds. Tests: `tests/services/test_ner_service.py` with stubbed `anthropic_client.complete`.

- [ ] T030 [US5] Create `app/services/summarize_service.py` calling `anthropic_client.complete()` with the existing `prompts/summarizer.md` + the input text, returning typed outcome `SummarizeOk | SummarizeError`. (Rule 1, Rule 11.) Files: `app/services/summarize_service.py`. Acceptance: happy path returns a non-empty string; error variants map to typed kinds. Tests: `tests/services/test_summarize_service.py`.

- [ ] T031 [P] [US5] Create `app/api/routers/ner.py` (`POST /ner`) mapping `NerOk → 200`, `NerError("bad_format") → 502`, transport errors → 503/504. (Rule 1, Rule 11.) Files: `app/api/routers/ner.py`. Acceptance: routes registered; mypy passes; integration test in T035 passes. Tests: `tests/api/test_ner_router.py` happy + 503 anthropic-down.

- [ ] T032 [P] [US5] Create `app/api/routers/summarize.py` (`POST /summarize`) with the same outcome→status mapping. (Rule 1, Rule 11.) Files: `app/api/routers/summarize.py`. Acceptance: routes registered; integration test passes. Tests: `tests/api/test_summarize_router.py`.

- [ ] T033 [P] [US5] Build `evals/ner/golden.jsonl` with 10 carefully-curated examples; each entry has `text`, `expected.{repo_names,file_paths,error_types,package_names}`. Add `evals/ner/README.md` documenting selection logic + Anthropic model pin. (Rule 5, R7.) Files: `evals/ner/golden.jsonl`, `evals/ner/README.md`. Acceptance: 10 lines, valid JSON each; covers empty-bucket cases.

- [ ] T034 [US5] Build `evals/ner/eval_ner.py` running live `/ner` against the golden set, computing per-bucket exact-set micro-F1 + an aggregate F1, writing `evals/reports/{ts}/ner.json`. CLI flags: `--mode={fixture,real}`. (Rule 5, Rule 10, R8.) Files: `evals/ner/eval_ner.py`. Acceptance: `python -m evals.ner.eval_ner --mode=fixture` exits 0 with stdout containing per-bucket + aggregate F1; the JSON report lands in MinIO at the per-CI key the existing helper places it at (`evals/reports/{run_ts}/ner.json`).

- [ ] T035 [P] [US5] Create `prompts/summarize_judge.md` (rubric judge prompt, frozen Claude Haiku, 1-5 scale on faithfulness/conciseness/intent). Version-header line 1. (Rule 9, R8.) Files: `prompts/summarize_judge.md`. Acceptance: file exists, prompt is JSON-output-only with the three rubric dimensions.

- [ ] T036 [P] [US5] Build `evals/summarize/golden.jsonl` with 10 entries: `text` (issue body) + `reference_summary`. Add `evals/summarize/README.md`. (Rule 5.) Files: `evals/summarize/golden.jsonl`, `evals/summarize/README.md`. Acceptance: 10 lines, valid JSON each.

- [ ] T037 [US5] Build `evals/summarize/eval_summarize.py` running live `/summarize` against the golden set, asking the rubric judge to score each output, writing `evals/reports/{ts}/summarize.json`. (Rule 5, Rule 10, R8.) Files: `evals/summarize/eval_summarize.py`. Acceptance: `python -m evals.summarize.eval_summarize --mode=fixture` exits 0 with per-example rubric scores + aggregate.

- [ ] T038 [US5] Set non-zero floors in `eval_thresholds.yaml` for `ner.f1_floor` and `summarize.rubric_floor` based on a pilot run (5 pt buffer below observed). Add corresponding CI assertions in `.github/workflows/ci.yml`. (Rule 4, Rule 5, Rule 10.) Files: `eval_thresholds.yaml`, `.github/workflows/ci.yml`. Acceptance: floors are non-zero; CI runs `eval_ner.py` and `eval_summarize.py` after the RAG eval gate and fails the workflow if either breach.

---

## Phase G — Boot-checks extended (User Story 6)

- [ ] T039 [US6] Add `KEY_AUTH_JWT_SECRET` to `app/main.py`'s `REQUIRED_VAULT_KEYS`. (Rule 2, Rule 4.) Files: `app/main.py`. Acceptance: the lifespan refuses-to-boot with the existing `MissingVaultKeyError` path if the key is missing.

- [ ] T040 [US6] Promote Redis-unreachable to fatal in the lifespan: replace the existing `logger.warning(...)` branch with a `logger.critical("REFUSE TO BOOT: Redis dependency failed: %s", exc); raise`. (Rule 4, FR-035.) Files: `app/main.py`. Acceptance: with Redis stopped, the api exits non-zero on boot.

- [ ] T041 [US6] Add `_verify_chatbot_tables()` to `app/main.py` (run after `_verify_rag_corpus()`). Probes `users`, `chatbot_memories`, `widgets`. Distinguishes missing table (`ProgrammingError`) from unreachable Postgres. (Rule 4, FR-036, SC-008.) Files: `app/main.py`. Acceptance: dropping any one of those three tables causes `REFUSE TO BOOT: <table> missing`.

- [ ] T042 [US6] Integration test: negative boot-checks. (SC-008.) Files: `tests/integration/test_boot_check_negative.py`. Acceptance: parametrized over { missing `auth_jwt_secret`, Redis down, `users` table dropped } → asserts the api process exits non-zero within 30 s and the log contains the specific `REFUSE TO BOOT: …` line for the disabled dependency.

---

## Phase H — Polish & cross-cutting

- [ ] T043 Append a `DECISIONS.md` entry titled "Chatbot Part 1: NER + summarize built in Part 1, scope expansion vs. brief" citing the R3-R8 rationale and the Part 2 cost-of-deferral. (Rule 6.) Files: `DECISIONS.md`. Acceptance: entry added; references plan §Summary + research R3/R7/R8.

- [ ] T044 Append a `DECISIONS.md` entry for the parallel async-engine deviation (the one row in Complexity Tracking). (Rule 6.) Files: `DECISIONS.md`. Acceptance: entry references plan §Complexity Tracking + research R1.

- [ ] T045 Append a `DECISIONS.md` entry for the redaction-at-persistence-boundary choice. (Rule 6, Rule 7.) Files: `DECISIONS.md`. Acceptance: entry references R6 + the threat model statement.

- [ ] T046 [P] Update `RUNBOOK.md` with the two new "refuse-to-boot" failure modes (Vault auth key, Redis down, chatbot tables missing) and the operator-recovery steps. (Rule 8.) Files: `RUNBOOK.md`. Acceptance: operator can find each failure mode by grep.

- [ ] T047 [P] Extend `ARCH.md` with one paragraph for the chatbot foundations layer (mirroring the RAG-slice paragraph that already lives there). (Rule 9.) Files: `ARCH.md`. Acceptance: layer description added below the existing RAG paragraph.

- [ ] T048 Confirm `ruff` + `mypy app/` are clean across the full diff. Run `docker compose build` and `docker compose up -d` from a clean checkout. (Rule 8, Rule 10.) Acceptance: lint+typecheck pass; the stack comes up; `/health` returns 200; `/users/me` (no cookie) returns 401.

- [ ] T049 Run the quickstart.md end-to-end against the live stack and paste the outputs into a `Quickstart smoke (2026-05-22 run)` section appended to the bottom of `quickstart.md` so the file doubles as a smoke-test transcript. (Rule 5, Rule 10.) Files: `specs/002-chatbot-part1-foundations/quickstart.md`. Acceptance: every numbered story in quickstart.md has a captured response or a captured log line confirming the expected behavior.

---

## Dependencies

- **A → B → C/D → E → F → G → H** is the broad order.
- Within Phase A, **T001 blocks every later task that imports fastapi-users**. T002–T006 can land in parallel after T001.
- Within Phase B, **T007 blocks T008–T013**. T008–T013 can land in parallel after T007.
- Phase C depends on T007 (users table) + T005 (async engine) + T014 (auth_backend).
- Phase D (T018) depends on T004 (`redact_for_persistence`).
- Phase E (T020/T021) depend on T004 + T007 + T009 + T012; tests T022–T026 depend on the primitive being committed.
- Phase F (T029/T030) depend on no DB; their routers (T031/T032) depend on the services; their evals depend on the routers.
- Phase G (T039–T042) depends on the migration + auth + Redis service being in place.
- Phase H runs last.

## Parallel opportunities

- After T001 lands: T002, T003, T004, T005, T006 in parallel.
- After T007 lands: T008–T013 in parallel.
- T018 (Phase D) can land in parallel with Phase C if T004 has already shipped.
- T020 and T021 are written sequentially (small files, low value to parallelize), but their tests T022, T023, T024, T025, T026 can run in parallel afterwards.
- Phase F prompts (T027, T035) and golden sets (T033, T036) are pure-data — fully parallel.

## Story → tasks map

| Story | Tasks |
|-------|-------|
| US1 — Maintainer signs in | T008, T014–T017 |
| US2 — Long-term memory cross-conversation | T009, T010, T013, T019–T024 |
| US3 — Widget actor isolation | T010, T011, T013, T018, T025 |
| US4 — Operator audits | T011, T012, T013, T026 |
| US5 — Upstream services | T013, T027–T038 |
| US6 — Refuse-to-boot | T039–T042 |
| Cross-cutting (no single story) | T001–T007, T043–T049 |

## MVP scope

MVP would be **US1 + US2 + US3** (the three P1 stories): authenticated maintainer can register, sign in, write a memory, and recall it in a new conversation, while widget actors are refused. That maps to tasks T001–T026. US4 (audit), US5 (upstream services), US6 (refuse-to-boot extensions), and polish round out the Part.

## Format validation

Every task above starts with `- [ ]`, a sequential ID `T0NN`, optional `[P]` and `[USn]` labels, an imperative sentence, and at least one explicit file path. Rule references appear in parentheses where the task hits a constitutional gate.
