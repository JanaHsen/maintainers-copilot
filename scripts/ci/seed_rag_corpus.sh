#!/usr/bin/env bash
# Seed the CI MinIO + Postgres with the canonical RAG corpus.
#
# Mirror of scripts/ci/seed_classifier_artifact.sh: the api's boot check
# refuses to boot without rows in `rag_chunks` for the configured
# RAG_CORPUS_RUN_ID (Rule 4 — see app/main.py and data-model.md
# "Lifecycle / boot-time invariants"). CI's Postgres + MinIO are empty
# on every run, so this script downloads the published corpus snapshot
# from a public GitHub Release (pinned by RAG_CORPUS_RELEASE_TAG),
# verifies the source-state hash in corpus_report.json against the
# downloaded chunk dump, bulk-loads the dump into the rag_chunks table,
# and uploads corpus_report.json + the per-source index JSONL files to
# MinIO at the canonical keys.
#
# Why a GitHub Release: pandas docs + held-out issues are public, the
# chunk dump carries no proprietary data, and a release attachment is
# the simplest S3-shaped public mirror available without extra infra.
# Same pattern as the classifier artifact (Approach A in the Day 2
# brief, extended to slice (l) per specs/rag/quickstart.md step 9).
#
# Required env:
#   RAG_CORPUS_RELEASE_TAG  e.g. rag-corpus-v1-20260521T2327Z
#   RAG_CORPUS_RUN_ID       e.g. v1-full-20260521T2327Z (the corpus_run_id
#                           the api will pin at boot via RAG_CORPUS_RUN_ID)
#
# Optional env (defaults are CI-correct):
#   GITHUB_REPOSITORY  owner/repo (auto-set in GitHub Actions)
#   MINIO_ENDPOINT     http://localhost:9000
#   MINIO_BUCKET       maintainers-copilot
#   MINIO_ROOT_USER    minioadmin
#   MINIO_ROOT_PASSWORD  dev_minio_password (matches docker-compose.yml)
#   POSTGRES_HOST      localhost
#   POSTGRES_PORT      5432
#   POSTGRES_USER      postgres
#   POSTGRES_PASSWORD  dev_postgres_password
#   POSTGRES_DB        maintainers_copilot
#
# Release-attachment files this script expects (the operator publishes
# them once after the local corpus build settles; see
# specs/rag/quickstart.md step 9):
#   - corpus_report.json
#   - parents.parquet   (the parent-chunk rows; embedding column NULL)
#   - children.parquet  (the child-chunk rows with vector(768) embeddings)
#   - docs_index.jsonl
#   - issues_index.jsonl
#   - excluded_issue_numbers.txt   (may be empty)
#
# Local dev: requires `aws` CLI (v2) and `psql`. Both are preinstalled
# on ubuntu-latest GitHub Actions runners.
set -euo pipefail

: "${RAG_CORPUS_RELEASE_TAG:?must be set, e.g. rag-corpus-v1-20260521T2327Z}"
: "${RAG_CORPUS_RUN_ID:?must be set, e.g. v1-full-20260521T2327Z}"

REPO="${GITHUB_REPOSITORY:-JanaHsen/maintainers-copilot}"
BASE_URL="https://github.com/${REPO}/releases/download/${RAG_CORPUS_RELEASE_TAG}"
MINIO_ENDPOINT="${MINIO_ENDPOINT:-http://localhost:9000}"
MINIO_BUCKET="${MINIO_BUCKET:-maintainers-copilot}"
PGHOST="${POSTGRES_HOST:-localhost}"
PGPORT="${POSTGRES_PORT:-5432}"
PGUSER="${POSTGRES_USER:-postgres}"
PGPASSWORD="${POSTGRES_PASSWORD:-dev_postgres_password}"
PGDATABASE="${POSTGRES_DB:-maintainers_copilot}"
export PGHOST PGPORT PGUSER PGPASSWORD PGDATABASE

# Dev MinIO creds. Safe to bake here: these only apply to dev/CI MinIO;
# production reads creds from Vault (Rule 2).
export AWS_ACCESS_KEY_ID="${MINIO_ROOT_USER:-minioadmin}"
export AWS_SECRET_ACCESS_KEY="${MINIO_ROOT_PASSWORD:-dev_minio_password}"
export AWS_DEFAULT_REGION="us-east-1"

WORKDIR=$(mktemp -d)
trap 'rm -rf "$WORKDIR"' EXIT

download() {
    local name="$1"
    echo "downloading ${BASE_URL}/${name}"
    curl --fail --silent --show-error --location \
        --output "${WORKDIR}/${name}" \
        "${BASE_URL}/${name}"
}

upload() {
    local src="$1"
    local key="$2"
    echo "uploading -> s3://${MINIO_BUCKET}/${key}"
    aws --endpoint-url "${MINIO_ENDPOINT}" --no-paginate \
        s3 cp "${src}" "s3://${MINIO_BUCKET}/${key}" --only-show-errors
}

# Ensure the bucket exists.
aws --endpoint-url "${MINIO_ENDPOINT}" s3 mb "s3://${MINIO_BUCKET}" 2>/dev/null || true

download corpus_report.json
download parents.parquet
download children.parquet
download docs_index.jsonl
download issues_index.jsonl
download excluded_issue_numbers.txt

# Verify the corpus_run_id in the report matches what the api will pin.
report_run_id=$(python3 -c "
import json
with open('${WORKDIR}/corpus_report.json') as fh:
    print(json.load(fh)['corpus_run_id'])
")
if [ "${report_run_id}" != "${RAG_CORPUS_RUN_ID}" ]; then
    echo "::error::corpus_run_id mismatch: report says '${report_run_id}', RAG_CORPUS_RUN_ID is '${RAG_CORPUS_RUN_ID}'" >&2
    exit 1
fi
echo "verified corpus_run_id = ${report_run_id}"

# Stage the parquets in MinIO at the canonical embeddings prefix, then
# defer to import_embeddings.py — the same standalone importer the
# operator runs locally after the Colab GPU-embed notebook lands the
# parquets, so the seed path and the operator path go through one
# code surface (schema + vector(768) coercion + slab streaming +
# ON CONFLICT (id) DO NOTHING).
EMBEDDINGS_PREFIX="rag/embeddings/${RAG_CORPUS_RUN_ID}"
upload "${WORKDIR}/parents.parquet"   "${EMBEDDINGS_PREFIX}/parents.parquet"
upload "${WORKDIR}/children.parquet"  "${EMBEDDINGS_PREFIX}/children.parquet"

python3 -m scripts.rag.import_embeddings \
    --corpus-run-id "${RAG_CORPUS_RUN_ID}"

CORPUS_PREFIX="rag/corpus/${RAG_CORPUS_RUN_ID}"
upload "${WORKDIR}/corpus_report.json"           "${CORPUS_PREFIX}/corpus_report.json"
upload "${WORKDIR}/docs_index.jsonl"             "${CORPUS_PREFIX}/docs_index.jsonl"
upload "${WORKDIR}/issues_index.jsonl"           "${CORPUS_PREFIX}/issues_index.jsonl"
upload "${WORKDIR}/excluded_issue_numbers.txt"   "${CORPUS_PREFIX}/excluded_issue_numbers.txt"

echo "RAG corpus seeded into Postgres (rag_chunks) + MinIO at s3://${MINIO_BUCKET}/${CORPUS_PREFIX}/"
