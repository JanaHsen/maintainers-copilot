# Quickstart: Day 1 Foundations

Validates the spec's primary user journeys end to end. This is the manual
acceptance script a reviewer runs; `RUNBOOK.md` will carry the canonical
copy for operators.

## Prerequisites

- Docker + docker-compose, `uv`, `curl`, a GitHub Personal Access Token (public repo read scope), and `ngrok` (only for the notebook step).

## 1. Bring the stack up (User Story 1 — P1)

```bash
git clone <repo> && cd maintainers-copilot
cp .env.example .env
# edit .env: set VAULT_DEV_ROOT_TOKEN_ID to any dev token string
docker-compose up -d
bash scripts/vault_seed.sh        # idempotent: writes dev secrets into Vault kv-v2
```

**Expect**: `postgres`, `redis`, `minio`, `vault`, `phoenix` reach healthy;
`migrate` runs `alembic upgrade head` and **exits 0**; `api` starts only
after `migrate` completed.

```bash
docker-compose ps                 # migrate = Exited (0); others = healthy/up
curl -s localhost:8000/health | jq
```

**Expect** (SC-002, SC-003, FR-006): HTTP 200, body `status: "ok"`, a
`dependencies[]` entry per upstream each `reachable: true`, plus
`request_id` and `trace_id`.

## 2. Honest degraded reporting (FR-007, SC-003)

```bash
docker-compose stop redis
curl -s localhost:8000/health | jq '.status, .dependencies[] | select(.name=="redis")'
```

**Expect**: still HTTP 200, `status: "degraded"`, redis `reachable: false`
with a redacted `detail`. Restart: `docker-compose start redis`.

## 3. Refuse-to-boot without Vault (FR-008, SC-004)

```bash
docker-compose stop vault
docker-compose restart api
docker-compose logs --tail=20 api
```

**Expect**: `api` exits non-zero; logs contain one specific line naming
Vault as the cause. Recover: `docker-compose start vault && docker-compose restart api`.

## 4. Tracing span tree (FR-010, SC-005)

```bash
curl -s localhost:8000/health >/dev/null
# open Phoenix UI (port from .env)
```

**Expect**: one connected trace for the request with a child span per
dependency check.

## 5. Dataset pipeline (User Story 2 — P2)

```bash
uv run python scripts/dataset/fetch_issues.py     # → raw/pandas/issues/{run_id}/page_*.jsonl
uv run python scripts/dataset/build_splits.py     # → processed/pandas/{run_id}/{train,val,test}.parquet + splits_report.json
```

**Expect** (SC-006, SC-007, contracts C2–C5): only the four classes appear;
`splits_report.json` time_boundary shows test min > train/val max; counts
sum to `total_mapped`. Re-run and confirm the prior `run_id` objects are
untouched (SC-008, C1).

## 6. Fine-tuning notebook (User Story 3 — P3)

Expose MinIO to Colab (RUNBOOK documents the `ngrok http <minio-port>`
step), open `notebooks/finetune_distilbert.ipynb`, set the train-split
`run_id`, run all.

**Expect** (SC-009, C6): `artifacts/classifier/distilbert/{run_id}/`
contains the state_dict, loss curve, and `model_card.json` whose
`weights_sha256` matches the state_dict. Not wired into `api` (FR-020).

## 7. Documentation (User Story 4 — P2, SC-010)

Confirm `DECISIONS.md` (dataset, label mapping + rationale, split sizes
citing `splits_report.json`, Phoenix justification), `ARCH.md` (layered
description + diagram), and `RUNBOOK.md` (up / down / re-run + ngrok)
exist and the runbook commands work as written.

## CI parity

`.github/workflows/ci.yml` reproduces steps 1 and the redaction test on
every push: ruff → mypy `app/` → image build → `docker-compose up -d` →
curl `/health` → `docker-compose down`. Eval suites present as stubs,
thresholds not enforced (Days 2–3).
