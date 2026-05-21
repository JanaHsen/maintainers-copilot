# Implementation Plan: Day 1 Foundations — Infrastructure, Health, Dataset Pipeline

**Branch**: `foundations` | **Date**: 2026-05-19 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/foundations/spec.md`

## Summary

Stand up the reproducible foundation the rest of the Maintainer's Copilot is
built on. A `docker-compose` stack (Postgres 16 + pgvector, Redis 7, MinIO,
Vault dev, Phoenix tracing, plus `api` and one-shot `migrate` services) comes
up cleanly from `cp .env.example .env` + `docker-compose up`. The FastAPI
`api` resolves every secret from Vault at startup and refuses to boot if
Vault, a required Vault key, Postgres, or MinIO is unreachable. `/health`
emits one OpenTelemetry span per upstream check so a single request is a
connected span tree in Phoenix. A standalone offline pipeline fetches
`pandas-dev/pandas` closed issues into MinIO as versioned JSONL, maps labels
to `{bug, feature, docs, question}` via a committed YAML, and writes a
stratified, time-ordered train/val/test split plus a splits report. A Colab
Pro notebook fine-tunes `distilbert-base-uncased` on the train split and
pushes a state_dict + model card to MinIO (not consumed today).
`DECISIONS.md`, `ARCH.md`, `RUNBOOK.md` are authored. CI runs ruff, mypy on
`app/`, and a compose `/health` smoke test; the two eval suites exist as
correctly-shaped stubs with thresholds not yet enforced.

## Technical Context

**Language/Version**: Python 3.12, managed with `uv` → `pyproject.toml` + `uv.lock`; `.venv` gitignored on first commit (Rule 8).

**Primary Dependencies**: FastAPI + uvicorn; SQLAlchemy 2.x; Alembic; Pydantic v2; `hvac` (Vault kv-v2); MinIO via `boto3`; `redis-py`; `httpx`; OpenTelemetry SDK + OTLP exporter + FastAPI/HTTPX auto-instrumentation; `arize-phoenix` (local container); `PyYAML`; `pandas` + `pyarrow` (pipeline only); HuggingFace `transformers`/`datasets`/`torch` (Colab notebook only — **not** in the `api` image).

**Storage**: Postgres 16 + pgvector (`pgvector/pgvector:pg16`) — only relational/vector store; MinIO (`minio/minio`) — only blob store; Redis 7 (`redis:7-alpine`) — only ephemeral store. All schema changes via Alembic (Rule 3).

**Testing**: `pytest`, `tests/` mirroring `app/` package-for-package; ruff + mypy on `app/` in CI (Rule 10).

**Target Platform**: Linux containers orchestrated by `docker-compose`; Colab Pro (GPU) for the notebook only.

**Project Type**: Web service (FastAPI) + offline data/ML scripts + ops docs.

**Performance Goals**: Correctness-first. `/health` returns well under 1s on a healthy stack. `fetch_issues.py` caps concurrency to respect GitHub REST rate limits.

**Constraints**: Refuse-to-boot (Rule 4) on: Vault unreachable, any required Vault key missing, Postgres unreachable after bounded retries, MinIO unreachable after bounded retries. Tracing wired from the first commit, never retrofitted (Rule 7). One-command bring-up from a clean clone (Rule 8). `.env` carries only `VAULT_DEV_ROOT_TOKEN_ID` + ports — no application secret (Rule 2).

**Scale/Scope**: Solo project; one bounded recent window of pandas closed issues sufficient for a stratified 4-class split; exact counts recorded in `splits_report.json` and `DECISIONS.md`.

## Constitution Check

*GATE: evaluated against `.specify/memory/constitution.md` v1.1.0 (Rules 1–11). Re-checked after Phase 1 — still PASS, no new violations.*

| Rule | Day 1? | How this plan satisfies it |
|---|---|---|
| **1 — Layered Architecture** | Yes | `app/api/routers/` (one file per resource + `routers/__init__.py` aggregating into one `APIRouter` from day one) → `app/services/` → `app/repositories/` (all SQL) ; `app/domain/` Pydantic models distinct from ORM; `app/infra/` one file per external system. `/health` flows api→service→infra only. |
| **2 — Secrets Discipline** | Yes | All secrets via `app/infra/vault_client.py` from kv-v2 `secret/maintainers-copilot/`. `.env.example` = `VAULT_DEV_ROOT_TOKEN_ID` + ports only. CI greps `app/` for `sk-ant-`/`password` → zero matches outside the Vault adapter. |
| **3 — Storage Discipline** | Yes | Postgres+pgvector / MinIO / Redis only; one initial Alembic migration enables pgvector and creates `audit_log` + `widget_configs` placeholder. No volume-reset workflow anywhere. |
| **4 — Refuse To Boot** | Yes | `app/main.py` lifespan raises → container exits non-zero with a specific log line on: Vault unreachable, missing required Vault key, Postgres unreachable after retries, MinIO unreachable after retries. |
| **5 — Evals Are The Grade** | Partial (deferred) | Golden sets/trained classifier do not exist until Days 2–3. Day 1 ships `evals/classification/` and `evals/rag/` as correctly-shaped stubs and `eval_thresholds.yaml` with placeholder values **not** enforced. Documented in DECISIONS.md + Complexity Tracking. |
| **6 — Decisions Backed By Numbers** | Yes | `DECISIONS.md` first entries: dataset choice, label mapping + rationale, split sizes (counts cite `splits_report.json`), tracing backend (Phoenix) justification. |
| **7 — Observability** | Yes | `app/infra/tracing.py` initializes OTel + Phoenix OTLP exporter from the first commit; FastAPI + HTTPX auto-instrumented; trace-id + request-id middleware; `/health` emits one span per dependency. `log_redaction.py` + a CI redaction test prove redaction (Rule 7). |
| **8 — Tooling** | Yes | `uv` (`pyproject.toml`+`uv.lock`), `.venv` gitignored, `docker-compose` orchestrates the full stack, clean clone → `cp .env.example .env` + `docker-compose up`. |
| **9 — No Vibe Coding** | Yes | One file per concern, descriptive names (`vault_client.py`, `build_splits.py`); no `utils.py`/`helpers.py`/`misc.py`. |
| **10 — CI Discipline** | Partial (scoped) | Day 1 `.github/workflows/ci.yml`: ruff → mypy `app/` → docker image build → `docker-compose up -d` → curl `/health` → `docker-compose down`; redaction test enforced. Eval gates filled Days 2–3. Scoped deferral documented. |
| **11 — Resilient Tool Use** | No | Chatbot/tool-calling is a later day; no Day 1 surface. |

**Gate result**: PASS. Rules 5 and 10 are intentionally partial because golden
sets and the trained classifier are explicitly out of Day 1 scope per spec
and the constitution's day-staging; this is a documented scoped deferral, not
an unjustified violation (see Complexity Tracking + DECISIONS.md).

## Project Structure

### Documentation (this feature)

```text
specs/foundations/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
├── contracts/           # Phase 1 output (health response + artifact schemas)
└── tasks.md             # Phase 2 (/speckit-tasks — NOT created here)
```

### Source Code (repository root)

```text
app/
├── main.py                      # FastAPI app + lifespan: Vault→DB engine→Redis pool→MinIO→tracing; refuse-to-boot; clean teardown
├── config.py                    # Pydantic settings: Vault addr/token + ports from env ONLY (no secrets)
├── api/
│   └── routers/
│       ├── __init__.py          # Aggregates all routers into one APIRouter (wired day one)
│       └── health.py            # GET /health — calls health service only
├── services/
│   └── health_service.py        # Orchestrates per-dependency checks; one span each
├── repositories/
│   └── health_repository.py     # SELECT 1 + pgvector presence probe (only SQL lives here)
├── domain/
│   └── health.py                # Pydantic: HealthReport, DependencyStatus
├── infra/
│   ├── vault_client.py          # hvac kv-v2 reader; raises on unreachable/missing key
│   ├── database.py              # SQLAlchemy 2.x engine + session factory
│   ├── redis_client.py          # Redis connection pool
│   ├── minio_client.py          # MinIO/boto3 client + bucket bootstrap
│   ├── tracing.py               # OTel init + Phoenix OTLP exporter + get_tracer()
│   ├── log_redaction.py         # Redacts sk-ant-*, password, token-shaped strings
│   ├── request_context.py       # trace-id + request-id middleware
│   ├── anthropic_client.py      # STUB today (Day 2+)
│   └── model_server_client.py   # STUB today (Day 2+)

alembic/
├── alembic.ini
├── env.py
└── versions/0001_baseline.py    # enable pgvector; create audit_log
scripts/
├── vault_seed.sh                # Idempotent: writes dev secrets (database_password, minio_root_password, github_pat) into kv-v2
└── dataset/
    ├── fetch_issues.py          # GitHub REST (PAT from Vault) → MinIO raw/pandas/issues/{run_id}/page_{n}.jsonl, concurrency-capped
    ├── label_map.yaml           # pandas labels → {bug,feature,docs,question} + precedence + drop rule
    └── build_splits.py          # raw→mapped→drop-unmappable→stratify→time-sort→train/val/test.parquet + splits_report.json

notebooks/
└── finetune_distilbert.ipynb   # boto3→MinIO(via ngrok), fine-tune distilbert-base-uncased, push state_dict + model_card.json

tests/                           # Mirrors app/
├── api/test_health.py
├── services/test_health_service.py
├── infra/test_vault_client.py
├── infra/test_log_redaction.py  # Rule 7: proves redaction works
└── infra/test_refuse_to_boot.py

evals/
├── classification/              # STUB suite (correct shape; thresholds not enforced — Day 2)
└── rag/                         # STUB suite (correct shape; thresholds not enforced — Day 3)

.github/workflows/ci.yml
docker-compose.yml               # full stack; model-server + chatbot have profiles:[later]
Dockerfile                       # api + migrate share image; entrypoint switches on command
.env.example                     # VAULT_DEV_ROOT_TOKEN_ID + ports ONLY
pyproject.toml / uv.lock
eval_thresholds.yaml             # present; placeholder values, not enforced Day 1
DECISIONS.md  ARCH.md  RUNBOOK.md
```

**Structure Decision**: Single-project layered FastAPI service (Rule 1) with
sibling offline `scripts/`, `notebooks/`, `evals/`, and ops docs; `tests/`
mirrors `app/` package-for-package (Rule 9). The compose file is the **final**
shape of the stack on Day 1 — `model-server` and `chatbot` are declared with
`profiles: [later]` so `docker-compose up` runs only Day 1 services while the
file already documents the destination. `api` and `migrate` share one
Dockerfile/image; the entrypoint switches on the container command
(`alembic upgrade head` for `migrate`, uvicorn for `api`), and `api`
`depends_on` `migrate` completing successfully.

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|---|---|---|
| Rule 5 partial (no enforced eval gate Day 1) | Golden sets + trained classifier do not exist until Days 2–3; a threshold on an empty suite fails every build | Stubbing with the correct file shape keeps CI honest and makes Days 2–3 a fill-in, not a rebuild |
| Rule 10 partial (eval gates deferred; redaction enforced) | Same day-staging dependency; only ruff/mypy/build/smoke/redaction are enforceable Day 1 | A CI referencing nonexistent suites would be a perpetually-red gate; CI grows by day per the documented plan |
| Two stub infra clients committed unused (`anthropic_client.py`, `model_server_client.py`) | Rule 1 requires the layer's final shape visible so Day 2+ imports without restructuring | Adding them later forces an `app/infra/` reshuffle, churning an audited layer boundary |
