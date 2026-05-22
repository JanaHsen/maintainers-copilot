# Phase 0 — Research: Chatbot Part 1 Foundations

Eight decisions, each cited from the plan's "Decided up-front" list. Format per `.specify/templates/plan-template.md`: Decision → Rationale → Alternatives.

---

## R1 — Async-engine isolation strategy for fastapi-users

**Decision**: Add `app/infra/database_async.py` exposing an `AsyncEngine` and `async_sessionmaker` connected to the same Postgres database via `postgresql+psycopg_async://…` (psycopg 3 async driver, already a transitive dep via `psycopg[binary]`). Use this engine **only** inside `app/infra/auth_backend.py`'s `SQLAlchemyUserDatabase` and inside `app/repositories/user_repository.py`. Every other repository continues to use the existing sync engine in `app/infra/database.py`.

**Rationale**: `fastapi-users[sqlalchemy]` requires `AsyncSession` for its `SQLAlchemyUserDatabase` adapter — there is no sync mode. Migrating every existing sync repository to async would expand Part 1's scope by ~1-2 days and risks regressions in the already-shipped RAG slice (Rule 9 / Rule 1). Running both engines against the same database is safe because they operate on disjoint tables (`users` only, vs. everything else), and SQLAlchemy 2.x supports both shapes in one process. The choice is explicitly logged in the plan's Complexity Tracking table as a justified deviation.

**Alternatives considered**:

- **Migrate everything to async**. Rejected: large surface change for one feature; contradicts the project's preference for narrow Parts. Would touch `chunk_repository.py`, `retrieve_service.py`, `health_repository.py`, every test fixture.
- **Use a synchronous fastapi-users shim** (e.g., wrap sync calls in `run_in_threadpool`). Rejected: fastapi-users' `BaseUserManager`, `SQLAlchemyUserDatabase`, and `JWTStrategy` interlock with `AsyncSession` lifecycle — wrapping each call point is brittle and undocumented.
- **Switch the project to async wholesale**. Rejected: same as #1 with even broader blast radius (Rules 8/10 — CI pipeline expects existing sync test fixtures).

---

## R2 — Vault key layout for the auth JWT signing key

**Decision**: Add a single key `auth_jwt_secret` to the **existing** kv-v2 secret at `secret/maintainers-copilot` (the same secret that already holds `database_password`, `minio_root_password`, `github_pat`, `anthropic_api_key`). Add `KEY_AUTH_JWT_SECRET = "auth_jwt_secret"` to `app/infra/vault_client.py` next to the existing key constants. Vault path bootstrapping happens in the existing init-Vault path (`scripts/init_vault.sh` or the compose `vault-init` service).

**Rationale**: The brief calls this Vault path `secret/data/maintainers-copilot/auth-jwt` but the existing project layout uses a flat single-secret structure with named keys at `secret/maintainers-copilot`. Matching the existing pattern is mandatory — Rule 2 calls for "a single Vault adapter" and the redaction-grep test (Rule 7) enforces the constant lives in `vault_client.py`. The semantic intent (one secret per logical purpose, signed by Vault) is preserved.

**Alternatives considered**:

- **Standalone Vault path** (`secret/maintainers-copilot/auth-jwt` as a separate kv-v2 secret). Rejected: forces `_read_all()` in `vault_client.py` to fan out across multiple secret paths, doubles the boot-time read count, and complicates the `MissingVaultKeyError` semantics. The redaction-grep test treats the single-secret pattern as canonical.
- **Read JWT secret from env** (with Vault as a fallback). Rejected: explicit Rule 2 violation. The plan's Rule 2 PASS depends on this not happening.
- **Generate the JWT secret at boot if absent**. Rejected: breaks reproducibility across container restarts (every restart invalidates all live sessions) and silently bypasses Rule 4 (an absent secret should refuse-to-boot, not paper over).

The brief's path description is treated as the operator's intent rather than literal; the substantive requirement (the JWT signing key lives in Vault and refuses-to-boot if absent) is satisfied.

---

## R3 — Audit-log additive evolution vs. recreate

**Decision**: Migration `0003_chatbot.py` evolves the existing `audit_log` table additively:

- ADD COLUMN `actor_user_id UUID NULL` with FK to `users(id)` ON DELETE SET NULL.
- ADD COLUMN `actor_widget_id UUID NULL` with FK to `widgets(id)` ON DELETE SET NULL.
- ADD COLUMN `target_type TEXT NULL`.
- ADD COLUMN `target_id TEXT NULL` (separate from the existing `target` column; new code writes `target_id`, old `target` left in place but never written to by Part 1 code).
- ADD constraint CHECK: `(actor_user_id IS NOT NULL) <> (actor_widget_id IS NOT NULL)` only enforced via the application layer — a SQL CHECK would reject the legacy `actor_id`-only rows. Application validation lives in `audit_repository.record()`.
- ADD index `ix_audit_log_actor_user_id` and `ix_audit_log_actor_widget_id`.
- Revoke `UPDATE` and `DELETE` on `audit_log` from the application role; only `INSERT` and `SELECT` remain. Migration also grants this.

**Rationale**: Migration 0001 already created `audit_log (id BIGINT identity, actor_id TEXT, action TEXT, target TEXT, timestamp timestamptz default now(), payload JSONB)`. It has no live consumers — no code under `app/` reads or writes to it yet — but it is the migration baseline, so dropping the table is unnecessary churn. Additive evolution preserves Rule 3's "every schema change through Alembic" without contradicting the existing 0001 contract.

The existing `actor_id` column (Text) becomes the deprecated legacy column. Part 1 code reads/writes only `actor_user_id` / `actor_widget_id`. A future cleanup can drop `actor_id` once any out-of-tree consumer is verified absent.

**Alternatives considered**:

- **Drop and recreate** `audit_log` in 0003 with the brief's exact shape. Rejected: contradicts the "migration is not a volume-drop" spirit of Rule 3 even though the table is empty. Cleaner-looking schema, but it forecloses an easy back-port to 0001 if the spec changes again.
- **Add a NEW table** `audit_log_v2` and ignore the existing one. Rejected: two audit-log surfaces is a Rule 9 violation by name.
- **Use SQL-level CHECK constraint** for the actor-exclusivity rule. Rejected (for now): would reject legacy `actor_id`-only rows; the application-layer check in `audit_repository.record()` is enforceable and tested in a unit test, and the rule is preserved.

---

## R4 — Embedding model + dimension for memory

**Decision**: Use `BAAI/bge-base-en-v1.5` at 768 dimensions, the same model and dimensionality the RAG slice committed to in `0002_rag_chunks`. Memory embeddings travel through the existing model-server `/embed` endpoint via `app/infra/embedding_client.py` (the same client `retrieve_service` uses).

**Rationale**: Two consumers (RAG corpus and chatbot memories) of one embedding service with one weight set keeps inference simple and CI deterministic. Switching memory to a different model (e.g., `nomic-embed-text-v1.5`, also 768-D) would require either loading a second model on the model server (RAM cost: ~+700 MB on a WSL machine already tight on memory per CLAUDE.md operator notes) or running two embedding clients (Rule 9 hazard). The 768-dim choice is also what the RAG eval gate validated against, so the embedding quality is already benchmarked on a 25-example golden set.

**Alternatives considered**:

- **`text-embedding-3-small`** via OpenAI. Rejected: project's only LLM provider is Anthropic (constitution "Project Scope" — Anthropic-only); OpenAI adds a new credentials surface and a new failure mode.
- **A larger model** (`BAAI/bge-large-en-v1.5`, 1024-D). Rejected: would require schema change (vector(1024)), a 1024-d migration on the RAG side later, and ~3× more RAM. No evidence the gain is needed for short personal memories.
- **A smaller model** (`BAAI/bge-small-en-v1.5`, 384-D). Rejected: would require a separate vector column or a separate table; the cost saving on tens of thousands of memories is negligible.

---

## R5 — Widget host-token shape

**Decision**: 32-byte URL-safe random token (256 bits). Generated with `secrets.token_urlsafe(32)` (yields a 43-char base64-url string). Plaintext returned to the admin **once** at create time. Stored as `sha256(plaintext).hexdigest()` (64-char hex) in `widgets.host_token_hash`. Lookup by hashing the inbound header and matching on `host_token_hash`.

**Rationale**: 256-bit entropy matches industry norms for opaque API keys. URL-safe base64 makes the token easy to embed in a `data-` HTML attribute (Part 3). Sha256-of-plaintext means the server never holds the raw token after creation — if the database leaks, the host tokens do not, only their hashes. Hex-encoded hash (64 chars) is friendly to indexing.

**Alternatives considered**:

- **JWT-shaped host token**. Rejected: needs a separate signing key, complicates revocation (would need a deny list), and adds parsing overhead. Opaque random tokens are simpler and revocation is just deleting / time-stamping the row.
- **bcrypt-hashed token**. Rejected: bcrypt's slow-by-design property is wrong for a lookup-on-every-request path. Sha256 is appropriate because the token is high-entropy random — no need for brute-force resistance.
- **Smaller token (16 bytes)**. Rejected: 128 bits is fine cryptographically but offers no operational gain and the longer token is barely noticeable in a `data-widget-token="…"` attribute.

---

## R6 — Redaction layering

**Decision**: Two-layer redaction:

1. **Service-boundary layer** — extend `app/infra/log_redaction.py` to export a new `redact_for_persistence(text: str) -> str` function. Called inside `write_memory_tool.write_memory(...)` *before* the content reaches `memory_repository.insert(...)`. Called inside `short_term_memory_service.append(...)` *before* the JSON-encoded record reaches Redis.
2. **Log-handler layer** — the existing `RedactingFilter` on the root logger stays in place, extended with two new patterns (R6.a, R6.b).

R6.a. JWT-shaped tokens: `\beyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\b` (header.payload.signature). The existing 40+-char generic rule catches *most* JWTs but explicit recognition keeps the redacted log readable (`[REDACTED_JWT]`).

R6.b. Email addresses: a conservative RFC-5322-shaped regex `\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b`. Replacement `[REDACTED_EMAIL]`. This rule applies to log output AND to memory persistence (R6 layer 1).

**Rationale**: The existing redaction layer applies only at log emission. A maintainer who pastes `sk-ant-…` into a message that becomes a `write_memory` call would persist the secret unredacted into Postgres even though the log line was redacted. That is a Rule 2 / Rule 7 hole. Promoting redaction to the persistence boundary closes the hole. Layer 2 stays because not all redactable text passes through layer 1 (e.g., uncaught exception messages).

Persistence-time redaction is **deliberately conservative** — it only redacts strings that match known secret/PII shapes, never free-form content the user might find useful. The test suite asserts that a benign technical phrase like "the package `requests` raised a `ConnectionError`" is left untouched.

**Alternatives considered**:

- **Only handler-layer redaction** (status quo). Rejected: leaks secrets into memory storage.
- **Only service-boundary redaction**. Rejected: would let exception messages from libraries we don't control bypass the filter on the log path.
- **Encrypt memory content at rest instead of redacting**. Rejected for Part 1: cryptographic key management is its own can of worms (would need Vault-managed key rotation, decrypt-on-recall handling, performance considerations on bulk recall). Redaction is sufficient for the threat model — "secret accidentally pasted into a chat message" — without introducing a new key-management surface.

---

## R7 — NER output schema

**Decision**: Strict JSON with exactly four entity buckets. Each bucket is an array of strings.

```json
{
  "repo_names": ["acme/widget", "pandas-dev/pandas"],
  "file_paths": ["src/foo.py", "docs/CHANGELOG.md"],
  "error_types": ["ConnectionError", "ValueError"],
  "package_names": ["requests", "numpy"]
}
```

The Anthropic call uses Sonnet 4 (`claude-sonnet-4-5-20250929` — verified at implementation; if a newer Sonnet-4 minor is available at implementation time, prefer it and document the swap in `prompts/ner.md` version header). The system prompt requires JSON-only output with no surrounding text. `ner_service.extract(...)` parses the response with `json.loads`; a parse failure returns `NerError(kind="bad_format", detail=…)`. The four buckets are fixed — no `"other"` bucket, no dynamic typing.

Empty buckets are returned as empty arrays, not omitted. The eval gate measures per-bucket micro-F1 against the golden set and reports both per-bucket and aggregated scores. The aggregate is the metric tracked against `eval_thresholds.yaml`'s `ner.f1_floor`.

**Rationale**: Strict JSON keeps `ner_service` deterministic on the server side — the agent in Part 2 will consume the response shape directly as a tool result. Open-ended NER schemas (free-form entity types) hand the agent's tool a Pandora's box of types to interpret. Four named buckets are tight enough to test against ground truth and broad enough to cover what an OSS maintainer needs (which repo, which file, which kind of error, which dependency).

**Alternatives considered**:

- **General NER taxonomy** (PERSON, ORG, LOCATION, …). Rejected: doesn't match the issue-triage domain.
- **Dynamic schema** (the model invents entity types). Rejected: untestable against ground truth at fixed F1.
- **Anthropic `tool_use` mode** for structured output (forced schema via tool definition). Considered. Postponed to Part 2 where the chatbot is already in tool-use mode. For an isolated `/ner` endpoint, the JSON-only system prompt is simpler.

---

## R8 — Eval-judge for NER and summarize

**Decision**:

- **NER**: programmatic micro-F1 against the golden set. **No** LLM judge.
- **Summarize**: LLM judge via frozen Claude Haiku (`claude-haiku-4-5-20251001`, the same model already used by the RAG generation judge). Judge prompt in `prompts/summarize_judge.md`, versioned and checked into the repo (Rule 9). Rubric: 1-5 scale on faithfulness, conciseness, intent-capture. The `summarize.rubric_floor` in `eval_thresholds.yaml` is the average across the three dimensions.

**Rationale**:

- NER has objective ground truth — the expected entity sets are enumerable in the golden examples. Programmatic comparison is faster, cheaper, deterministic, and constitutionally simpler (no judge prompt to version-control, no judge model to refuse-to-boot on).
- Summarization does not have objective ground truth — reasonable summaries differ. A rubric-based LLM judge with a frozen model and a versioned prompt is the established RAG-slice pattern (`prompts/rag_judge.md`) and produces a stable metric.

**Alternatives considered**:

- **LLM judge for NER**. Rejected: programmatic F1 is unambiguously superior when ground truth is enumerable. The LLM judge would add cost and stochasticity without information gain.
- **No judge, just BLEU/ROUGE for summarization**. Rejected: these metrics correlate weakly with summary quality in domains with vocabulary diversity. The RAG slice already paid the cost of building the judge harness; reusing it is cheap.
- **Anthropic API outage in CI**: same mitigation pattern as RAG — the CI eval uses the seeded fixture pattern (`scripts/ci/seed_*.sh`) so most CI runs do not burn real API calls. Real-API smoke runs happen via the `--real-api` flag on a separate slower job (TBD in CI work — bundled with the RAG real-API path).

---

## Cross-cutting note: trace propagation

All eight decisions assume the existing `RequestContextMiddleware` propagates `request_id` and `trace_id` through every code path. The new redaction layer's `redact_for_persistence` is pure and does not need access to context. The audit-log writes embed `request_id` + `trace_id` into the `payload` JSONB so the audit row is self-contained for downstream analysis.

No NEEDS CLARIFICATION markers remain.
