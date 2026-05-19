"""Fetch scikit-learn/scikit-learn closed issues via the GitHub GraphQL API.

The REST /issues endpoint caps deep pagination at 10k items. GraphQL uses
cursor pagination with no depth limit, so this script can pull the full
closed scikit-learn issue corpus in a single run.

The PAT is read from Vault (Rule 2). Output is JSONL to MinIO under
raw/scikit-learn/issues/{run_id}/gql_batch_NNNN.jsonl, REST-shaped so
build_splits.py reads it identically to the REST output.

Usage::

    VAULT_ADDR=http://localhost:8200 MINIO_HOST=localhost \\
        uv run python scripts/dataset/fetch_issues_graphql.py

Optional env: MAX_BATCHES (default 500 = ~50k issues), RUN_ID (auto if unset).
"""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import UTC, datetime

import httpx

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from app.infra.minio_client import DATA_BUCKET, ensure_bucket, get_client  # noqa: E402
from app.infra.vault_client import read_secrets  # noqa: E402

REPO_OWNER = "scikit-learn"
REPO_NAME = "scikit-learn"
GRAPHQL_URL = "https://api.github.com/graphql"
MAX_BATCHES = int(os.environ.get("MAX_BATCHES", "500"))
PER_BATCH = 100  # GraphQL hard max

QUERY = """
query($owner: String!, $name: String!, $cursor: String) {
  repository(owner: $owner, name: $name) {
    issues(first: 100, after: $cursor, states: CLOSED,
           orderBy: {field: CREATED_AT, direction: DESC}) {
      pageInfo { hasNextPage endCursor }
      nodes {
        number
        databaseId
        title
        body
        state
        createdAt
        closedAt
        labels(first: 20) { nodes { name } }
      }
    }
  }
  rateLimit { cost remaining resetAt }
}
"""


def _new_run_id() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def _normalize(node: dict) -> dict:
    """REST-shape a GraphQL issue node so build_splits.py needs no special-case."""
    return {
        "number": node["number"],
        "id": node["databaseId"],
        "title": node.get("title", "") or "",
        "body": node.get("body") or "",
        "state": node.get("state", "CLOSED").lower(),
        "created_at": node["createdAt"],
        "closed_at": node.get("closedAt"),
        "labels": [{"name": lbl["name"]} for lbl in node["labels"]["nodes"]],
    }


def _handle_rate_limit(rate_limit: dict | None) -> None:
    """Sleep until reset if the GraphQL points budget is nearly exhausted."""
    if not rate_limit:
        return
    remaining = int(rate_limit.get("remaining", 5000))
    if remaining > 50:
        return
    reset_at = rate_limit.get("resetAt")
    if not reset_at:
        return
    reset_dt = datetime.fromisoformat(reset_at.replace("Z", "+00:00"))
    sleep_for = max((reset_dt - datetime.now(UTC)).total_seconds(), 0) + 2
    print(f"rate limit at {remaining}; sleeping {int(sleep_for)}s until reset", flush=True)
    time.sleep(sleep_for)


def main() -> int:
    pat = read_secrets(["github_pat"])["github_pat"]
    ensure_bucket(DATA_BUCKET)
    s3 = get_client()
    run_id = os.environ.get("RUN_ID") or _new_run_id()
    prefix = f"raw/scikit-learn/issues/{run_id}"
    print(f"run_id={run_id} -> s3://{DATA_BUCKET}/{prefix}/", flush=True)

    headers = {
        "Authorization": f"Bearer {pat}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "maintainers-copilot-dataset",
    }
    cursor: str | None = None
    batch_idx = 0
    total = 0

    with httpx.Client(headers=headers, timeout=60.0) as client:
        for _ in range(MAX_BATCHES):
            payload = {
                "query": QUERY,
                "variables": {"owner": REPO_OWNER, "name": REPO_NAME, "cursor": cursor},
            }
            for attempt in range(5):
                resp = client.post(GRAPHQL_URL, json=payload)
                if resp.status_code == 401:
                    raise SystemExit(
                        "GitHub rejected the PAT (401). Reseed Vault with a "
                        "classic token (no scopes needed for public-repo read) "
                        "and rerun."
                    )
                if 500 <= resp.status_code < 600:
                    time.sleep(2 ** attempt)
                    continue
                resp.raise_for_status()
                body = resp.json()
                if "errors" in body:
                    raise SystemExit(f"GraphQL errors: {body['errors']}")
                break
            else:
                raise RuntimeError("exhausted retries against GitHub GraphQL")

            _handle_rate_limit(body["data"].get("rateLimit"))
            page = body["data"]["repository"]["issues"]
            nodes = page["nodes"]
            if not nodes:
                print(f"batch {batch_idx + 1} empty; stopping", flush=True)
                break

            normalized = [_normalize(n) for n in nodes]
            batch_idx += 1
            key = f"{prefix}/gql_batch_{batch_idx:04d}.jsonl"
            body_bytes = ("\n".join(json.dumps(issue) for issue in normalized) + "\n").encode()
            s3.put_object(Bucket=DATA_BUCKET, Key=key, Body=body_bytes)
            total += len(normalized)
            print(f"wrote {key} ({len(normalized)} issues, {total} total)", flush=True)

            page_info = page["pageInfo"]
            if not page_info["hasNextPage"]:
                print("hasNextPage=false; stopping", flush=True)
                break
            cursor = page_info["endCursor"]

    print(f"done: {total} issues across run_id={run_id}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())