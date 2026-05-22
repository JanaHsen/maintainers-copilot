# Implementation Plan: Chatbot Part 1 — Foundations (auth, memory, tool stubs, upstream services)

**Branch**: `002-chatbot-part1-foundations` | **Date**: 2026-05-22 | **Spec**: [`spec.md`](./spec.md)

**Input**: Feature specification from `specs/002-chatbot-part1-foundations/spec.md`

## Summary

Stand up the four primitives Part 2's agent and Part 3's surfaces will consume:

1. **Authentication** — fastapi-users with email + password; JWT in HTTP-only cookie; signing key from Vault; two roles (`user`, `admin`); `require_admin` dependency. Refuse-to-boot if the auth-jwt Vault key is missing.
2. **Long-term memory (`chatbot_memories`)** — pgvector(768) over `BAAI/bge-base-en-v1.5` embeddings (same dimensionality the RAG slice committed). IVFFlat cosine index, partial WHERE user_id IS NOT NULL. The two memory tool primitives `write_memory` / `recall_memory` live in `app/services/tools/` and refuse when the actor is a widget session.
3. **Short-term memory (Redis)** — JSON-encoded message records on a per-conversation Redis list. TTL refreshed on append (1h widget, 24h authed). Token-budgeted window read.
4. **Two upstream services** — `ner_service` (Anthropic structured extraction → `repo_names`, `file_paths`, `error_types`, `package_names`) and `summarize_service` (Anthropic call using existing `prompts/summarizer.md`). Each exposed at `/ner` and `/summarize`. Each gated by a numeric floor in `eval_thresholds.yaml`.

Plus the supporting tables (`users`, `conversations`, `messages`, `widgets`) and the extended `audit_log`, all through a single Alembic migration `0003_chatbot.py`. Plus boot-checks (Vault auth key + Redis + `users` table). Plus three new redaction rules (JWT, 32-byte hex widget tokens, RFC-5322-shaped emails) wired BEFORE memory writes (not just before log emission).

This Part ships *primitives*. The chatbot agent loop (Part 2), the `/chat` router (Part 2), Streamlit (Part 3), the React widget (Part 3), and the demo host (Part 3) are explicitly out of scope.

## Technical Context

**Language/Version**: Python 3.12 (project pin, `pyproject.toml`).

**Primary Dependencies**:

- **Add** `fastapi-users[sqlalchemy]>=14` to base deps. Brings async SQLAlchemy 2.x; see research R1 for how this coexists with the existing sync engine.
- Reuse: FastAPI, SQLAlchemy 2.x, Pydantic v2, Alembic, `hvac`, `redis`, `httpx`, OpenTelemetry, `anthropic` (already pinned).
- No new ML dependencies. The embedding path for memory writes reuses the existing model-server `/embed` endpoint via `app/infra/embedding_client.py`.

**Storage**:

- Postgres 16 + pgvector: existing engine (sync, `app/infra/database.py`) plus a parallel async engine in `app/infra/database_async.py` scoped strictly to fastapi-users SQLAlchemy queries (research R1).
- Redis 7: short-term per-conversation message lists, key `convo:{conversation_id}`, value JSON-encoded message record.
- Vault: existing kv-v2 surface at `secret/maintainers-copilot`; one new key `auth_jwt_secret` (research R2).
- MinIO: not touched by this Part.

**Testing**: `pytest` + `pytest-asyncio` (already in dev deps); `httpx.MockTransport` for outbound HTTP shape; ephemeral Postgres for repository tests; ephemeral Redis for short-term-memory tests.

**Target Platform**: Same docker-compose stack as the rest of the project.

**Project Type**: Web service. No frontend in this Part.

**Performance Goals**: Registration → login → `/users/me` end-to-end ≤ 5 s on a dev laptop (SC-001). `write_memory` / `recall_memory` budget ≤ 1 s p95 absent embedding-service latency. NER / summarize p95 ≤ 5 s including Anthropic round-trip.

**Constraints**:

- Refuse-to-boot extensions (Rule 4): Vault `auth_jwt_secret` reachable; Redis reachable; `users` table present.
- `eval_thresholds.yaml` gains a non-zero `ner:` floor and a non-zero `summarize:` floor before merge (Rule 4).
- The agent loop is **not** built in this Part; the tool primitives MUST be callable directly (unit-testable) without an agent in the loop.

**Scale/Scope**: Tens of maintainer accounts; thousands of memories per account at most; ≤ 1 GB short-term memory in Redis (a memory window per active conversation, expiring on TTL).

**Decided up-front** (no longer operator-deferred; recorded in research.md):

1. Async-engine isolation strategy for fastapi-users (R1).
2. Vault key layout — single key under the existing kv-v2 secret, not a new path (R2).
3. Audit-log additive evolution (R3) — keep migration 0001's table, add columns, repurpose nothing destructively.
4. Embedding model + dimension — `BAAI/bge-base-en-v1.5` (768) identical to the RAG slice (R4).
5. Widget host-token shape — 32-byte hex, hashed with sha256 before persistence (R5).
6. Redaction layering — apply redaction at the *service* boundary before memory inserts AND at the log handler (R6).
7. NER output schema — strict JSON, 4 fixed entity-type buckets (R7).
8. Eval-judge for NER and summarize — frozen Claude Haiku via existing `anthropic_client.complete` (R8).

The remaining operator decisions (`eval_thresholds.yaml` floors for `ner:` and `summarize:`) surface only after a pilot run on the seeded golden sets — same pattern as the RAG slice did for `rag:`.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

Rule-by-rule application:

- **Rule 1 (Layered architecture).** Verbatim layering.
  - Routers (HTTP only): `app/api/routers/auth.py`, `app/api/routers/ner.py`, `app/api/routers/summarize.py`.
  - Services: `app/services/auth_service.py` (role checks), `app/services/short_term_memory_service.py`, `app/services/ner_service.py`, `app/services/summarize_service.py`, `app/services/tools/write_memory_tool.py`, `app/services/tools/recall_memory_tool.py`.
  - Repositories (SQL only): `app/repositories/memory_repository.py`, `app/repositories/conversation_repository.py`, `app/repositories/widget_repository.py`, `app/repositories/audit_repository.py`, `app/repositories/user_repository.py`.
  - Domain (Pydantic): `app/domain/auth.py`, `app/domain/memory.py`, `app/domain/conversation.py`, `app/domain/widget.py`, `app/domain/audit.py`, `app/domain/ner.py`, `app/domain/summarize.py`.
  - Infra (adapters): `app/infra/auth_backend.py` (fastapi-users JWT backend + UserManager), `app/infra/database_async.py` (async engine scoped to fastapi-users), extensions to `app/infra/log_redaction.py`. **PASS.**

- **Rule 2 (Secrets discipline).** One new Vault key (`auth_jwt_secret`) added to `vault_client.KEY_AUTH_JWT_SECRET`. The fastapi-users JWT signing key is read via `read_secrets([KEY_AUTH_JWT_SECRET])` inside `auth_backend.py` at app startup. No env-var fallback. `widgets.host_token_hash` stores a sha256 of the host-token plaintext; the plaintext is returned to the admin **once** at create time and never persisted. **PASS.**

- **Rule 3 (Storage discipline).** Postgres + pgvector + Redis are the only stores touched. Schema changes ship as Alembic migration `0003_chatbot.py` (additive: new tables + non-destructive ALTERs on `audit_log`; see R3). No "drop the volume" anywhere. **PASS.**

- **Rule 4 (Refuse to boot).** Three new fatal checks in `app/main.py`'s `lifespan`:
  - Vault: extend `REQUIRED_VAULT_KEYS` to include `KEY_AUTH_JWT_SECRET`. If missing, the existing `MissingVaultKeyError` path logs `REFUSE TO BOOT: Vault dependency failed: …` and exits non-zero.
  - Database: a new helper `_verify_chatbot_tables()` runs after `_verify_rag_corpus()` and confirms `users` and `chatbot_memories` tables exist; absence logs a specific `REFUSE TO BOOT: <table> missing` and exits non-zero.
  - Redis: the current lifespan WARNS on Redis-down. This Part promotes Redis-unreachable to a **fatal** check because the chatbot's short-term memory cannot degrade safely (a widget conversation with no short-term store loses turn-to-turn coherence). Wording: `REFUSE TO BOOT: Redis dependency failed: …`. **PASS.**

- **Rule 5 (Evals are the grade).** Two new golden sets, each in the existing pattern: `evals/ner/golden.jsonl` (10 examples) + `evals/ner/eval_ner.py`; `evals/summarize/golden.jsonl` (10 examples) + `evals/summarize/eval_summarize.py`. Floors recorded in `eval_thresholds.yaml`'s new `ner:` and `summarize:` sections. CI gate enforces both. The cross-conversation memory recall test (SC-002) ships as an integration test rather than a golden eval, since it exercises the same retrieval primitive the embedding service already covers via the RAG golden set. **PASS.**

- **Rule 6 (Decisions backed by numbers).** DECISIONS.md entry for the scope expansion (NER + summarize built in this Part rather than Part 2) cites the constraint cost on Part 2 if deferred (one additional service-creation cycle wedged into agent-loop work). Each of NER and summarize's choice of evaluation metric is recorded with the rationale (exact-set F1 for NER bounded labels; rubric-judge for summarize). The "redact at service boundary AND at log handler" choice is recorded as a defense against the existing handler-only approach leaking secrets into memory writes. **PASS.**

- **Rule 7 (Observability).** Every Anthropic call from `ner_service` / `summarize_service` runs through `anthropic_client.complete()` which is already auto-instrumented by `HTTPXClientInstrumentor`. Each `write_memory` / `recall_memory` call writes a Phoenix span (`memory.write`, `memory.recall`) with the actor id, the byte-size of the content (NOT the content itself), and the result count. Audit-log writes happen via `audit_repository.record()` and carry `request_id` + `trace_id` in their payloads. Log redaction extended (Rule 7 itself): two new patterns for JWT-shaped tokens and email addresses; the redaction test (`tests/infra/test_log_redaction.py`) gets new cases. **PASS.**

- **Rule 8 (Tooling).** `uv add fastapi-users[sqlalchemy]` updates `pyproject.toml` + `uv.lock`. No new docker-compose service. Fresh-clone + `cp .env.example .env` + `docker-compose up` still works — `.env.example` does not change (the new Vault key is added to the `vault-bootstrap` init script, not to `.env`). **PASS.**

- **Rule 9 (No vibe coding).** Every new file is named for what it holds: `auth_service.py`, `auth_backend.py`, `memory_repository.py`, `short_term_memory_service.py`, `write_memory_tool.py`, `recall_memory_tool.py`, `ner_service.py`, `summarize_service.py`, `audit_repository.py`, `conversation_repository.py`, `widget_repository.py`, `user_repository.py`. No `utils.py` / `helpers.py` / `misc.py`. **PASS.**

- **Rule 10 (CI discipline).** Existing workflow extended with `evals/ner/eval_ner.py` and `evals/summarize/eval_summarize.py` after the RAG eval step. Image builds + lint + mypy + redaction test + stack-up smoke all continue to run. No new compose service to wait on. **PASS.**

- **Rule 11 (Resilient tool use).** The two tool primitives (`write_memory`, `recall_memory`) define their failure modes explicitly: embedding-service unreachable → typed `MemoryToolError("embedding_unreachable")`, audit-log write failure → memory write rolled back via a single transaction. The two upstream services catch `AnthropicError` variants and return typed outcomes mapped to 503/504/502 per the existing `_KIND_TO_STATUS` pattern in `app/api/routers/retrieve.py`. The agent loop in Part 2 will compose these primitives; Rule 11's user-visible-message obligation kicks in there. This Part's responsibility is to make sure the primitives never raise opaque 500s. **PASS.**

**Constitution Check verdict (pre-Phase-0)**: All eleven rules pass. Complexity Tracking table empty — no justified deviations.

## Project Structure

### Documentation (this feature)

```text
specs/002-chatbot-part1-foundations/
├── spec.md
├── plan.md              # This file
├── research.md          # Phase 0 — 8 decisions (R1-R8)
├── data-model.md        # Phase 1 — table DDLs, indices, constraints
├── quickstart.md        # Phase 1 — bring-up + manual smoke
├── contracts/
│   ├── auth.openapi.yaml         # /auth/register, /auth/login, /auth/logout, /users/me
│   ├── ner.openapi.yaml          # POST /ner
│   ├── summarize.openapi.yaml    # POST /summarize
│   └── memory-tools.md           # write_memory / recall_memory internal-primitive contracts
├── checklists/
│   └── requirements.md
└── tasks.md             # Phase 2 (created by /speckit-tasks)
```

### Source Code (repository root)

```text
# Application code — extends the existing layered tree.
app/
├── api/
│   └── routers/
│       ├── auth.py                       # fastapi-users routers (register/login/logout/me)
│       ├── ner.py                        # POST /ner
│       └── summarize.py                  # POST /summarize
├── services/
│   ├── auth_service.py                   # require_admin dependency + role-check helpers
│   ├── short_term_memory_service.py      # append / get_window / TTL set
│   ├── ner_service.py                    # Anthropic structured extraction
│   ├── summarize_service.py              # Anthropic summarization
│   └── tools/
│       ├── __init__.py
│       ├── write_memory_tool.py          # embed + insert + audit; refuses for widget actors
│       └── recall_memory_tool.py         # embed + query_top_k; refuses for widget actors
├── repositories/
│   ├── user_repository.py                # fastapi-users SQLAlchemy adapter glue
│   ├── memory_repository.py              # ONLY place pgvector SQL for chatbot_memories lives
│   ├── conversation_repository.py        # CRUD on conversations + messages
│   ├── widget_repository.py              # CRUD on widgets (create / get_by_token_hash / revoke)
│   └── audit_repository.py               # append-only write helpers; raises on update/delete attempts
├── domain/
│   ├── auth.py                           # UserRead, UserCreate, UserUpdate, Role
│   ├── memory.py                         # Memory, MemoryWriteResult, MemoryRecallHit
│   ├── conversation.py                   # Conversation, Message, MessageRole, Actor
│   ├── widget.py                         # Widget, WidgetCreate, WidgetCreated (with one-time token)
│   ├── audit.py                          # AuditEntry, AuditAction enum
│   ├── ner.py                            # NerRequest, NerResponse, EntityBuckets
│   └── summarize.py                      # SummarizeRequest, SummarizeResponse
└── infra/
    ├── auth_backend.py                   # fastapi-users JWT backend + UserManager
    ├── database_async.py                 # async engine scoped to fastapi-users (R1)
    └── log_redaction.py                  # extended: JWT + email patterns + service-boundary helper

# Migration.
alembic/versions/0003_chatbot.py          # users + chatbot_memories + conversations + messages
                                          # + widgets + audit_log evolution (additive)

# Prompts (existing summarizer.md already in place; add ner prompt).
prompts/
└── ner.md                                # NER structured-extraction prompt (versioned)

# Evals.
evals/
├── ner/
│   ├── golden.jsonl                      # 10 examples with expected entity buckets
│   ├── eval_ner.py                       # live /ner; computes per-bucket F1; writes report
│   └── README.md                         # selection logic, expected metric ranges
└── summarize/
    ├── golden.jsonl                      # 10 issue-body inputs + reference summaries
    ├── eval_summarize.py                 # rubric judge via frozen Claude Haiku; writes report
    └── README.md                         # selection logic, judge prompt versioning

# Eval thresholds + CI.
eval_thresholds.yaml                      # +ner: f1_floor, +summarize: rubric_floor
.github/workflows/ci.yml                  # +ner + +summarize gate steps; redaction test extended

# Vault bootstrap.
docker-compose.yml / scripts/init_vault.sh # set new key auth_jwt_secret on stack bring-up

# Tests.
tests/
├── api/
│   ├── test_auth_router.py               # register/login/logout/me round-trip
│   ├── test_ner_router.py                # 200 happy + 503 anthropic-down
│   └── test_summarize_router.py          # 200 happy + 503 anthropic-down
├── services/
│   ├── test_short_term_memory_service.py
│   ├── test_ner_service.py
│   ├── test_summarize_service.py
│   └── tools/
│       ├── test_write_memory_tool.py
│       └── test_recall_memory_tool.py
├── repositories/
│   ├── test_user_repository.py
│   ├── test_memory_repository.py
│   ├── test_conversation_repository.py
│   ├── test_widget_repository.py
│   └── test_audit_repository.py
├── infra/
│   ├── test_log_redaction.py             # EXISTING — extended with JWT + email cases
│   └── test_auth_backend.py
└── integration/
    ├── test_cross_conversation_memory_recall.py   # SC-002, SC-003
    ├── test_widget_actor_refusal.py               # SC-004
    ├── test_audit_writes.py                       # SC-006
    └── test_boot_check_negative.py                # SC-008 — Vault auth key disabled => exit
```

**Structure Decision**: Strict adherence to the layered architecture already in place. No new top-level Python directories under `app/`; this Part adds files only inside the existing `app/api/routers/`, `app/services/`, `app/services/tools/` (new subpackage — named for what it holds, satisfies Rule 9), `app/repositories/`, `app/domain/`, and `app/infra/` layers. Migration `0003_chatbot.py` is additive against `audit_log` (R3) so the existing migration history stays intact.

## Complexity Tracking

> One justified deviation: the parallel async engine for fastapi-users.

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|--------------------------------------|
| Two SQLAlchemy engines (sync existing + async for fastapi-users) coexist in `app/infra/` | fastapi-users[sqlalchemy] requires an async engine. The rest of `app/` is sync and migrating it is out of scope for this Part. | Migrating every existing repository to async would inflate Part 1 well past its scope and risk regressions in the already-shipped RAG slice. The async engine is scoped strictly to fastapi-users SQLAlchemy queries (the `users` table); every other repository remains on the sync engine. |
