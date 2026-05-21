#!/usr/bin/env bash
# Seed the CI MinIO with the canonical Day 2 classifier artifact + splits.
#
# The model server's boot check refuses to boot without state_dict.pt,
# model_card.json, and train.parquet present at their canonical MinIO keys.
# CI's MinIO is empty on every run, so this script downloads the artifact
# from a public GitHub Release (pinned by CLASSIFIER_RELEASE_TAG), verifies
# state_dict.pt's SHA-256 against the model card, and uploads everything
# to local MinIO at the keys the model server reads at boot.
#
# Why a GitHub Release: pandas issues are public, the trained weights
# carry no proprietary data, and a release attachment is the simplest
# S3-shaped public mirror available without extra infra (Approach A in
# the Day 2 brief).
#
# Required env:
#   CLASSIFIER_RELEASE_TAG  e.g. classifier-v1-20260520T193153Z
#   MODEL_RUN_ID            e.g. 20260520T193153Z
#   DATASET_RUN_ID          e.g. 20260519T133455Z
#
# Optional env (defaults are CI-correct):
#   GITHUB_REPOSITORY  owner/repo (auto-set in GitHub Actions)
#   MINIO_ENDPOINT     http://localhost:9000
#   MINIO_BUCKET       maintainers-copilot
#   MINIO_ROOT_USER    minioadmin
#   MINIO_ROOT_PASSWORD  dev_minio_password (matches docker-compose.yml dev creds)
#
# Local dev: requires `aws` CLI (v2; preinstalled on ubuntu-latest GitHub
# Actions runners). Install via `pip install awscli` or `apt-get install
# awscli` if running outside CI.
set -euo pipefail

: "${CLASSIFIER_RELEASE_TAG:?must be set, e.g. classifier-v1-20260520T193153Z}"
: "${MODEL_RUN_ID:?must be set, e.g. 20260520T193153Z}"
: "${DATASET_RUN_ID:?must be set, e.g. 20260519T133455Z}"

REPO="${GITHUB_REPOSITORY:-JanaHsen/maintainers-copilot}"
BASE_URL="https://github.com/${REPO}/releases/download/${CLASSIFIER_RELEASE_TAG}"
MINIO_ENDPOINT="${MINIO_ENDPOINT:-http://localhost:9000}"
MINIO_BUCKET="${MINIO_BUCKET:-maintainers-copilot}"

# Dev MinIO creds. Safe to bake here: these only apply to dev/CI MinIO that
# holds no real data; production reads creds from Vault (Rule 2).
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

# Ensure the bucket exists. "BucketAlreadyOwnedByYou" is non-fatal so let
# `aws s3 mb` fail silently on the re-run case.
aws --endpoint-url "${MINIO_ENDPOINT}" s3 mb "s3://${MINIO_BUCKET}" 2>/dev/null || true

download model_card.json
download state_dict.pt
download train.parquet
download val.parquet
download test.parquet

# Verify state_dict.pt SHA-256 matches model_card.weights.weights_sha256.
# This is the same integrity check model_server/boot_check.py runs at
# startup, but failing here gives a much clearer error than a refuse-to-boot
# log buried in a docker compose stream.
expected=$(python3 -c "
import json
with open('${WORKDIR}/model_card.json') as fh:
    print(json.load(fh)['weights']['weights_sha256'])
")
actual=$(sha256sum "${WORKDIR}/state_dict.pt" | awk '{print $1}')
if [ "${expected}" != "${actual}" ]; then
    echo "::error::state_dict.pt SHA-256 mismatch (expected ${expected}, got ${actual})" >&2
    exit 1
fi
echo "verified state_dict.pt SHA-256 = ${actual}"

ARTIFACT_PREFIX="artifacts/classifier/distilbert/${MODEL_RUN_ID}"
DATASET_PREFIX="processed/pandas/${DATASET_RUN_ID}"

upload "${WORKDIR}/model_card.json"  "${ARTIFACT_PREFIX}/model_card.json"
upload "${WORKDIR}/state_dict.pt"    "${ARTIFACT_PREFIX}/state_dict.pt"
upload "${WORKDIR}/train.parquet"    "${DATASET_PREFIX}/train.parquet"
upload "${WORKDIR}/val.parquet"      "${DATASET_PREFIX}/val.parquet"
upload "${WORKDIR}/test.parquet"     "${DATASET_PREFIX}/test.parquet"

echo "classifier artifact seeded into MinIO at s3://${MINIO_BUCKET}/${ARTIFACT_PREFIX}/"
