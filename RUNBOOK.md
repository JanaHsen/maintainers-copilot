# Runbook

Canonical operator commands. A clean clone followed only by the "Bring the
stack up" block reaches a healthy `/health` (SC-001).

## Prerequisites

Docker + docker-compose, `uv`, `curl`; a GitHub PAT (public-repo read) and
`ngrok` only for the dataset / notebook steps.

## Bring the stack up

```bash
cp .env.example .env
# edit .env: set VAULT_DEV_ROOT_TOKEN_ID to any dev string (e.g. root)
docker-compose up -d            # builds the image; vault-seed -> migrate -> api
```

`vault-seed` (one-shot) seeds Vault, `migrate` runs `alembic upgrade head`
and exits 0, then `api` starts once every dependency is healthy.

```bash
docker-compose ps                       # vault-seed/migrate Exited(0); rest healthy
curl -s localhost:8000/health | jq      # 200, status "ok", 5 deps, request_id, trace_id
```

Open Phoenix at `http://localhost:6006` to see the request span tree.

### Degraded / refuse-to-boot demos

```bash
docker-compose stop redis    # /health -> 200, status "degraded", redis unreachable
docker-compose start redis
docker-compose stop vault && docker-compose restart api
docker-compose logs --tail=20 api   # one "REFUSE TO BOOT: Vault ..." line; api exits non-zero
docker-compose start vault && docker-compose restart api
```

## Tear down

```bash
docker-compose down       # keep volumes
docker-compose down -v    # also drop pg/minio data (clean slate)
```

## Dataset pipeline (host)

The scripts read host-facing endpoints, so override the in-container
defaults. The GitHub PAT must be a **classic** token (no scopes needed for
public read) or a fine-grained token with **Public Repositories (read-only)**.

```bash
# 1. put a real PAT into Vault (idempotent, non-clobbering)
GITHUB_PAT='ghp_xxx' VAULT_ADDR=http://localhost:8200 bash scripts/vault_seed.sh

# 2. fetch -> raw/scikit-learn/issues/{run_id}/page_*.jsonl
VAULT_ADDR=http://localhost:8200 MINIO_HOST=localhost \
  uv run python scripts/dataset/fetch_issues.py        # note the printed run_id

# 3. inventory labels -> scripts/dataset/observed_labels.txt
MINIO_HOST=localhost \
  uv run python scripts/dataset/inventory_labels.py --run-id <run_id>

# 4. build splits -> processed/scikit-learn/{run_id}/{train,val,test}.parquet + splits_report.json
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
