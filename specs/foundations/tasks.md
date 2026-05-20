---
description: "Task list for Day 1 Foundations implementation"
---

# Tasks: Day 1 Foundations — Infrastructure, Health, Dataset Pipeline

**Input**: Design documents from `/specs/foundations/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/, quickstart.md

**Tests**: Rule 7 mandates a redaction test and the spec's refuse-to-boot
behavior must be proven; those specific tests are included. Broad unit-test
coverage is not requested for Day 1 and is not generated.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependency on an incomplete task)
- **[Story]**: US1 (P1 stack/health), US2 (P2 dataset), US3 (P3 notebook), US4 (P2 docs)
- Every task names its files, an acceptance check (command + expected result), and the constitution Rule numbers it respects.
- Ordering is deliberately a build-up chain: infra+Vault → api skeleton → tracing → `/health` → Alembic scaffold → baseline tables → dataset pipeline → notebook → docs/CI → the three committed deliverables. Run top to bottom; one task = one commit.

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Reproducible toolchain before any service exists.

- [ ] T001 Initialize the `uv` Python 3.12 project: create `pyproject.toml` (FastAPI, uvicorn, SQLAlchemy 2.x, Alembic, Pydantic v2, hvac, redis, boto3, httpx, opentelemetry-sdk + otlp exporter + fastapi/httpx instrumentation, arize-phoenix, PyYAML; dev group: ruff, mypy, pytest) and generate `uv.lock` via `uv lock`. Acceptance: `uv sync && uv run python -c "import fastapi, sqlalchemy, hvac"` exits 0. Rules: 8.
- [ ] T002 [P] verify the existing .gitignore contains .venv/, __pycache__/, .env, *.parquet, model weights; add anything missing. Acceptance unchanged.
- [ ] T003 [P] Add ruff + mypy config to `pyproject.toml` (`[tool.ruff]`, `[tool.mypy]` targeting `app/`). Acceptance: `uv run ruff check . && uv run mypy app/` runs (clean tree → exit 0; no config error). Rules: 8, 9, 10.
- [ ] T004 [P] Create `.env.example` containing ONLY `VAULT_DEV_ROOT_TOKEN_ID=` and the port numbers (`API_PORT`, `POSTGRES_PORT`, `REDIS_PORT`, `MINIO_PORT`, `MINIO_CONSOLE_PORT`, `VAULT_PORT`, `PHOENIX_PORT`) — no application secret. Acceptance: `grep -Ei 'sk-ant-|password|secret' .env.example` returns nothing. Rules: 2, 8.
- [ ] T005 Create the layered package skeleton with empty `__init__.py` files: `app/{api/routers,services,repositories,domain,infra,db/versions}/`, `tests/{api,services,infra}/`, `scripts/dataset/`, `notebooks/`, `evals/{classification,rag}/`. Acceptance: `find app -name __init__.py | wc -l` ≥ 6; `python -c "import app"` exits 0. Rules: 1, 9.

---

## Phase 2: Foundational (Blocking Prerequisites) — Infra & Vault before the api

**⚠️ CRITICAL**: The api service cannot be built or run until this phase is complete.

- [ ] T006 Create `docker-compose.yml` with all five infrastructure services only: `postgres` (`pgvector/pgvector:pg16`), `redis` (`redis:7-alpine`), `minio` (`minio/minio`, healthcheck on `/minio/health/live`), `vault` (`hashicorp/vault` dev mode, `VAULT_DEV_ROOT_TOKEN_ID` from `.env`), `phoenix` (arize phoenix); each with a healthcheck; declare `model-server` and `chatbot` with `profiles: [later]`. No `api`/`migrate` yet. Acceptance: `cp .env.example .env` (set a dev token) then `docker-compose up -d postgres redis minio vault phoenix && docker-compose ps` shows all five healthy; `model-server`/`chatbot` absent. Rules: 3, 7, 8.
- [ ] T007 Create `Dockerfile` (Python 3.12, `uv sync --frozen`, copies `app/`) and an entrypoint that switches on container command (`alembic upgrade head` vs `uvicorn app.main:app`). Acceptance: `docker build -t mcp-api .` succeeds; `docker run --rm mcp-api python -c "import app"` exits 0. Rules: 8, 9.
- [ ] T008 Create `scripts/vault_seed.sh`: idempotent, writes kv-v2 mount `secret/maintainers-copilot/` keys `database_password`, `minio_root_password`, `github_pat` using the dev root token. Acceptance: with `vault` up, `bash scripts/vault_seed.sh && bash scripts/vault_seed.sh` both exit 0; `vault kv get secret/maintainers-copilot` lists the 3 keys. Rules: 2.

**Checkpoint**: Infrastructure + Vault are healthy and seeded.

---

## Phase 3: User Story 1 — Healthy stack with honest /health (Priority: P1) 🎯 MVP

**Goal**: api boots only when its dependencies (esp. Vault) are reachable, `/health` reports per-dependency status, and a request is a connected span tree in Phoenix.

**Independent Test**: quickstart.md steps 1–4.

- [ ] T009 [US1] Create `app/config.py`: Pydantic settings reading ONLY Vault address/token and ports from env (no secrets). Acceptance: `uv run python -c "from app.config import settings; print(settings.vault_addr)"` prints the address; settings has no secret field. Rules: 2.
- [X] T010 [US1] Create the api skeleton `app/main.py` with a bare FastAPI app + empty async lifespan (no deps wired yet). Acceptance: `uv run uvicorn app.main:app --port 8000 &` then `curl -s localhost:8000/docs` returns 200. Rules: 1.
- [X] T011 [US1] Create `app/infra/tracing.py`: initialize OpenTelemetry, Phoenix OTLP exporter, auto-instrument FastAPI + HTTPX, expose `get_tracer()`; call it from `app/main.py` lifespan. Acceptance: start api, `curl localhost:8000/docs`, then the Phoenix UI shows a trace for the request. Rules: 7.
- [X] T012 [US1] Create `app/infra/request_context.py`: middleware assigning a request id + binding the OTel trace id; register it in `app/main.py`. Acceptance: `curl -i localhost:8000/docs` returns `X-Request-Id` and `X-Trace-Id` headers. Rules: 7.
- [X] T013 [US1] Create `app/infra/log_redaction.py` redacting `sk-ant-*`, `password`, and token-shaped strings, plus `tests/infra/test_log_redaction.py` proving it. Acceptance: `uv run pytest tests/infra/test_log_redaction.py` passes. Rules: 7, 2.
- [X] T014 [US1] Create `app/infra/vault_client.py` (hvac kv-v2 reader for `secret/maintainers-copilot/`; raises a specific error on unreachable Vault or a missing required key). Acceptance: `uv run python -c "from app.infra.vault_client import read_secrets; print(list(read_secrets(['database_password','minio_root_password'])))"` prints the values with Vault up. Rules: 2, 4.
- [X] T015 [P] [US1] Create `app/infra/database.py` (SQLAlchemy 2.x engine/session factory using the Vault-supplied password; bounded-retry connect). Acceptance: `uv run python -c "from app.infra.database import ping; ping()"` exits 0 with Postgres up. Rules: 1, 3, 4.
- [X] T016 [P] [US1] Create `app/infra/redis_client.py` (connection pool; ping helper). Acceptance: `uv run python -c "from app.infra.redis_client import ping; ping()"` exits 0 with Redis up. Rules: 1, 3.
- [X] T017 [P] [US1] Create `app/infra/minio_client.py` (boto3 client using Vault-supplied root password; bucket bootstrap; bounded-retry; liveness probe). Acceptance: `uv run python -c "from app.infra.minio_client import ping; ping()"` exits 0 with MinIO up. Rules: 1, 3, 4.
- [X] T018 [P] [US1] Create stub `app/infra/anthropic_client.py` and `app/infra/model_server_client.py` (typed interfaces, `raise NotImplementedError`, documented "Day 2+"). Acceptance: `uv run python -c "import app.infra.anthropic_client, app.infra.model_server_client"` exits 0. Rules: 1, 9.
- [X] T019 [US1] Wire the full bootstrap into `app/main.py` lifespan in order Vault → DB → Redis → MinIO → tracing, with clean teardown on shutdown, raising a specific log line and exiting non-zero on any failure (Vault unreachable, missing Vault key, Postgres unreachable after retries, MinIO unreachable after retries). Acceptance: with all infra up the api starts; `docker-compose stop vault` then restart api → container exits non-zero and logs one Vault-specific line. Rules: 4, 1, 7.
- [X] T020 [US1] Add `api` + `migrate` services to `docker-compose.yml` (shared image; `migrate` runs `alembic upgrade head` then exits; `api` `depends_on` `migrate` completed + infra healthy; api on `API_PORT`). Acceptance: `docker-compose up -d` → `docker-compose ps` shows `migrate` `Exited (0)` and `api` healthy. Rules: 3, 4, 8.
- [X] T021 [US1] Create `app/domain/health.py` (`DependencyStatus`, `HealthReport` per data-model.md) and `app/repositories/health_repository.py` (`SELECT 1` + pgvector-presence probe — the only SQL). Acceptance: `uv run pytest tests/services` collects; `python -c "from app.domain.health import HealthReport"` exits 0. Rules: 1.
- [X] T022 [US1] Create `app/services/health_service.py` (probes each dependency, one OTel child span per check) and `app/api/routers/health.py` (`GET /health`) and wire `app/api/routers/__init__.py` to aggregate routers into one `APIRouter`, included by `app/main.py`. Acceptance: `curl -s localhost:8000/health | jq` returns 200 with a `dependencies[]` entry per upstream, `request_id`, `trace_id`; Phoenix shows one trace with a child span per dependency; `docker-compose stop redis` → `/health` still 200 with `status:"degraded"` and redis `reachable:false`. Rules: 1, 4, 6, 7.
- [X] T023 [US1] Add `tests/infra/test_refuse_to_boot.py` asserting the lifespan raises on Vault-unreachable and on a missing required key. Acceptance: `uv run pytest tests/infra/test_refuse_to_boot.py` passes. Rules: 4.

**Checkpoint**: Stack comes up healthy; `/health` is honest; api refuses to boot without Vault; tracing visible. (MVP demonstrable.)

---

## Phase 4: User Story 1 (cont.) — Alembic baseline & schema

**Goal**: Every schema change is migration-backed; `audit_log` and `widget_configs` exist via the baseline migration.

- [X] T024 [US1] Create the Alembic scaffold at the repo root: `alembic/alembic.ini`, `alembic/env.py` (uses the Vault-supplied DB password via `app/infra/database.py`), empty `alembic/versions/`. Acceptance: `uv run alembic current` runs without error (no migrations yet). Rules: 3.

- [X] T025 [US1] Create baseline migration `alembic/versions/0001_baseline.py`: `CREATE EXTENSION IF NOT EXISTS vector`; create `audit_log` (id bigint PK identity, actor_id text nullable, action text not null, target text nullable, timestamp timestamptz not null default now(), payload jsonb nullable) with index `ix_audit_log_timestamp` on timestamp; reversible `downgrade` (drops `audit_log`; `DROP EXTENSION vector` guarded by `IF EXISTS`). Acceptance: `uv run alembic upgrade head` then `psql -c "\dt"` lists `audit_log`; `\dx` lists `vector`; re-running `upgrade head` is a no-op success. Rules: 3.

- [X] T026 [US1] Confirm the `migrate` compose service applies `0001_baseline` end-to-end from a clean volume. Acceptance: `docker-compose down -v && docker-compose up -d` → `docker-compose logs migrate` shows `0001_baseline` applied and `Exited (0)`; `api` becomes healthy after. Rules: 3, 4, 8.

**Checkpoint**: User Story 1 fully complete and migration-backed.

---

## Phase 5: User Story 2 — Offline dataset pipeline (Priority: P2)

**Goal**: Versioned raw issues + label mapping + stratified time-ordered split + splits report in MinIO.

**Independent Test**: quickstart.md step 5; contracts C1–C5.


- [ ] T028 [US2] Create `scripts/dataset/fetch_issues.py`: GitHub REST closed issues for `pandas-dev/pandas`, PAT from Vault, concurrency-capped/rate-limit aware, writes verbatim JSONL to MinIO `raw/pandas/issues/{run_id}/page_{n}.jsonl`; never overwrites a prior `run_id`. Acceptance: `uv run python scripts/dataset/fetch_issues.py` then MinIO console shows `raw/pandas/issues/<run_id>/page_1.jsonl`; a second run creates a new `run_id` leaving the first byte-unchanged. Rules: 2, 3, 9.
- [ ] T027a [US2] After T028 has produced raw JSONL, create `scripts/dataset/inventory_labels.py` that reads every issue's `labels` from `raw/pandas/issues/{run_id}/page_*.jsonl` in MinIO, computes a `Counter` of unique label names, and writes `scripts/dataset/observed_labels.txt` (one line per label: `<count>\t<label>`, sorted descending). Acceptance: `uv run python scripts/dataset/inventory_labels.py --run-id <id>` produces `observed_labels.txt` listing every unique pandas label and its frequency. Rules: 6, 9.

- [X] T027b [US2] Commit `scripts/dataset/label_map.yaml` grounded in `observed_labels.txt`: precedence list `[bug, feature, docs, question]`, per-class lists drawn from the actual top-frequency pandas labels, `drop_if_unmapped: true`. Add a `DECISIONS.md` entry citing the per-label counts used. Acceptance: `uv run python -c "import yaml; d=yaml.safe_load(open('scripts/dataset/label_map.yaml')); assert set(d['classes'])=={'bug','feature','docs','question'}"` exits 0; every label in `label_map.yaml` appears in `observed_labels.txt`. Rules: 6, 9.

- [ ] T029 [US2] Create `scripts/dataset/build_splits.py`: read raw JSONL, apply `label_map.yaml`, drop unmappable, stratify by class, time-sort so test = most recent 15% (train/val ≈70/15), write `train/val/test.parquet` + `splits_report.json` under `processed/pandas/{run_id}/`. Acceptance: `uv run python scripts/dataset/build_splits.py` produces the four objects in MinIO; `splits_report.json` shows `test_min_closed_at > train_val_max_closed_at`, only the four classes present, and counts summing to `total_mapped` (contracts C2–C5). Rules: 3, 6, 9.


**Checkpoint**: Dataset + report reproducible in MinIO.

---

## Phase 6: User Story 3 — Fine-tuning notebook (Priority: P3)

**Goal**: A Colab Pro notebook that fine-tunes DistilBERT on the train split and pushes weights + model card to MinIO (not consumed today).

**Independent Test**: quickstart.md step 6; contract C6.

- [X] T030 [US3] Create `notebooks/finetune_distilbert.ipynb`: pull `processed/pandas/{run_id}` via boto3 against MinIO (ngrok endpoint), fine-tune `distilbert-base-uncased` with HF Trainer, push `state_dict`, loss curve, and `model_card.json` (architecture, hyperparams, training-data hash, final val metrics, weights SHA-256) to `artifacts/classifier/distilbert/{run_id}/`. Acceptance: notebook runs top-to-bottom in Colab; MinIO shows the three artifacts; `model_card.json.weights_sha256` equals the sha256 of the pushed state_dict (contract C6); nothing in `app/` imports it (FR-020). Rules: 4 (pre-positions weights-integrity check), 6, 9.

**Checkpoint**: Training artifact exists for Day 2 pickup.

---

## Phase 7: User Story 4 — Docs, eval stubs & CI (Priority: P2)

**Goal**: Decisions/architecture/runbook authored; eval suites stubbed; CI green on every push.

**Independent Test**: quickstart.md step 7; CI parity.

- [X] T031 [P] [US4] Create `DECISIONS.md` with first entries (each one-line-justified, citing numbers/`splits_report.json` where applicable): dataset = `pandas-dev/pandas`; label mapping + rationale; train/val/test split sizes; tracing backend = Phoenix; plus the Rule 5/10 scoped-deferral note. Acceptance: `grep -c '^##' DECISIONS.md` ≥ 4. Rules: 6, 5, 10.
- [X] T032 [P] [US4] Create `ARCH.md`: short layered-architecture description + a diagram (mermaid) of api→service→repository→infra and the compose topology. Acceptance: `ARCH.md` renders a mermaid diagram and names all five layers. Rules: 1, 9.
- [X] T033 [P] [US4] Create `RUNBOOK.md`: exact commands to bring the stack up (`cp .env.example .env`, `docker-compose up`, `vault_seed.sh`), tear it down (`docker-compose down [-v]`), re-run the pipeline, and the Colab ngrok step. Acceptance: a clean clone followed only by RUNBOOK commands reaches a healthy `/health` (SC-001). Rules: 8.
- [X] T034 [P] [US4] Create `eval_thresholds.yaml` (placeholder values, not enforced Day 1) and correctly-shaped stub suites under `evals/classification/` and `evals/rag/` (Day 2/3 fill them). Acceptance: `uv run python -c "import yaml; yaml.safe_load(open('eval_thresholds.yaml'))"` exits 0; stub files importable. Rules: 5, 10.
- [X] T035 [US4] Create `.github/workflows/ci.yml`: on every push run ruff → `mypy app/` → grep `app/` for `sk-ant-`/`password` (zero matches outside vault adapter) → redaction test → docker image build → `docker-compose up -d` → `curl /health` (expect 200) → `docker-compose down`. Acceptance: `act` or a pushed branch run completes green. Rules: 10, 7, 2, 8.

**Checkpoint**: Repository is reviewable; CI gates every push.

---

## Phase 8: Day 1 Committed Deliverables

**Purpose**: The three artifacts that land on the `001-day1-foundations`/`foundations` branch as proof of Day 1.

- [X] T036 Push the branch and confirm a **green CI run**: ruff, mypy, redaction test, image build, and the compose `/health` smoke all pass. Acceptance: the GitHub Actions run for the branch is green; link recorded in `DECISIONS.md`. Rules: 10, 7, 8.
- [ ] T037 Run the dataset pipeline end-to-end and confirm **`splits_report.json` is visible in MinIO** under `processed/pandas/{run_id}/`, satisfying contracts C2–C5. Acceptance: MinIO console screenshot/object path recorded; counts sum to `total_mapped` and test is strictly newer. Rules: 3, 6.
- [ ] T038 Kick off the Colab training run and confirm a **run config + partial training logs (and, when finished, weights + model card) in MinIO** under `artifacts/classifier/distilbert/{run_id}/`. Acceptance: MinIO shows `model_card.json`/run config and loss-curve/log object; artifact intentionally NOT wired into `api` (FR-020). Rules: 4, 6.

---

## Dependencies & Execution Order

### Build-up chain (sequential — run top to bottom)

Setup (T001–T005) → Infra+Vault (T006–T008) → api skeleton (T009–T010) → tracing/context/redaction (T011–T013) → infra clients (T014–T018) → refuse-to-boot lifespan (T019) → api/migrate compose (T020) → /health (T021–T023) → Alembic scaffold (T024) → baseline tables (T025–T026) → fetch (T028) → label inventory (T027a) → label map (T027b) → build splits (T029) → notebook (T030) → docs/eval/CI (T031–T035) → deliverables (T036–T038).

### Story mapping

- **US1 (P1)**: T009–T026 — the MVP slice (stack + health + refuse-to-boot + tracing + migration).
- **US2 (P2)**: T027–T029 — depends on MinIO (T006) + Vault PAT (T008).
- **US3 (P3)**: T030 — depends on US2's processed split.
- **US4 (P2)**: T031–T035 — docs cite US1/US2 results; CI exercises US1.

### Parallel Opportunities

- T002–T004 [P] (independent setup files).
- T015–T018 [P] (separate `app/infra/*` files, no inter-dependency) after T014.
- T031–T034 [P] (independent doc/stub files) after the work they document exists.

## Implementation Strategy

### MVP First

1. Phase 1–2 (Setup + Infra/Vault).
2. Phase 3–4 (User Story 1: stack + `/health` + refuse-to-boot + tracing + baseline migration).
3. **STOP and VALIDATE** with quickstart.md steps 1–4 → demonstrable MVP.

### Incremental Delivery

US1 (MVP) → US2 (dataset) → US3 (notebook) → US4 (docs/CI) → Phase 8
deliverables committed. Each phase is a self-contained, independently
verifiable increment; commit after every task.

## Notes

- One task = one commit with a single clear scope; tasks never bundle unrelated work.
- Every task lists the Rule numbers it respects (constitution requirement for `tasks.md`).
- Rules 5 and 10 are partial-by-design Day 1 (eval gates deferred to Days 2–3); recorded in `DECISIONS.md` and the plan's Complexity Tracking.
