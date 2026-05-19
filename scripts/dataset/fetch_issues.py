"""Fetch scikit-learn/scikit-learn closed issues into MinIO as verbatim JSONL.

Offline pipeline (not part of the api). The GitHub PAT is read from Vault,
never from env (Rule 2). Pages are fetched sequentially with rate-limit
awareness (concurrency capped at 1 — auditable and gentle on the REST API,
Rule 9). Each run gets a fresh UTC ``run_id`` and never overwrites a prior
run's objects (FR-013, SC-008).

Usage (host): set host-facing endpoints, then run::

    VAULT_ADDR=http://localhost:8200 MINIO_HOST=localhost \\
        uv run python scripts/dataset/fetch_issues.py

Optional env: MAX_PAGES (default 25), PER_PAGE (default 100).
"""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import UTC, datetime

import httpx

# The script lives outside the app package; make repo root importable.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from app.infra.minio_client import DATA_BUCKET, ensure_bucket, get_client  # noqa: E402
from app.infra.vault_client import read_secrets  # noqa: E402

REPO = "scikit-learn/scikit-learn"
API_URL = f"https://api.github.com/repos/{REPO}/issues"
MAX_PAGES = int(os.environ.get("MAX_PAGES", "25"))
PER_PAGE = int(os.environ.get("PER_PAGE", "100"))


def _new_run_id() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def _handle_rate_limit(response: httpx.Response) -> bool:
    """Return True if the caller should retry the same page after sleeping."""
    remaining = response.headers.get("X-RateLimit-Remaining")
    retry_after = response.headers.get("Retry-After")
    if response.status_code in (403, 429) and retry_after is not None:
        time.sleep(int(retry_after) + 1)
        return True
    if response.status_code == 403 and remaining == "0":
        reset = int(response.headers.get("X-RateLimit-Reset", "0"))
        sleep_for = max(reset - int(time.time()), 0) + 1
        print(f"rate limited; sleeping {sleep_for}s until reset", flush=True)
        time.sleep(sleep_for)
        return True
    return False


def _get_page(client: httpx.Client, page: int) -> list[dict[str, object]]:
    params = {
        "state": "closed",
        "per_page": PER_PAGE,
        "page": page,
        "sort": "created",
        "direction": os.environ.get("DIRECTION", "desc"),
    }
    for _ in range(5):
        resp = client.get(API_URL, params=params)
        if _handle_rate_limit(resp):
            continue
        if resp.status_code == 401:
            raise SystemExit(
                "GitHub rejected the PAT (401 Unauthorized). The token read "
                "from Vault is invalid, expired, or revoked. Generate a new "
                "token (classic with `public_repo`, or a fine-grained token "
                "with public-repository read), reseed Vault "
                "(GITHUB_PAT=... bash scripts/vault_seed.sh), and rerun. The "
                "token value itself is never logged (Rule 2/7)."
            )
        resp.raise_for_status()
        data: list[dict[str, object]] = resp.json()
        return data
    raise RuntimeError(f"page {page}: exhausted retries against GitHub")


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
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "maintainers-copilot-dataset",
    }
    total = 0
    with httpx.Client(headers=headers, timeout=30.0) as client:
        for page in range(1, MAX_PAGES + 1):
            issues = _get_page(client, page)
            if not issues:
                print(f"page {page} empty; stopping", flush=True)
                break
            body = "\n".join(json.dumps(issue) for issue in issues) + "\n"
            direction = os.environ.get("DIRECTION", "desc")
            key = f"{prefix}/page_{direction}_{page}.jsonl"
            s3.put_object(Bucket=DATA_BUCKET, Key=key, Body=body.encode("utf-8"))
            total += len(issues)
            print(f"wrote {key} ({len(issues)} issues, {total} total)", flush=True)

    print(f"done: {total} issues across run_id={run_id}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
