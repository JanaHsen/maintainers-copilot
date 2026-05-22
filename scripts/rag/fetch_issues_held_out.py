"""Fetch resolved pandas issues with maintainer responses (offline corpus build).

Real mode: GraphQL fetch (same PAT-from-Vault pattern as
``scripts/dataset/fetch_issues_graphql.py``) but pulling comments +
``author_association`` so the maintainer-response filter has the
signal it needs. Surviving issues are cached to MinIO under
``rag/held_out_issues/{corpus_run_id}/`` so a re-run against the
same ``corpus_run_id`` short-circuits the GraphQL fetch entirely.

Fixture mode (``fixture_dir`` provided): walks JSON files matching the
GraphQL response shape under ``<fixture_dir>/issues/*.json`` and
applies the same two filters offline. Used by the corpus-build smoke.

Two filters are applied to every fetched issue, in this order:

  1. **Classifier-split exclusion (FR-009)**. The set of issue numbers
     in the canonical ``processed/pandas/{dataset_run_id}/{train,val,test}.parquet``
     is the authoritative exclusion list. Any issue in any of the three
     splits is dropped and the dropped number is appended to
     ``excluded_issue_numbers`` so the corpus_report can record the
     overlap (expected: 0 in steady state).

  2. **Maintainer-response filter (T007)**. Drop any issue that has
     zero comments with ``author_association`` in
     ``{MEMBER, OWNER, COLLABORATOR}``. The signal we want in the
     corpus is maintainer-authored answers; threads with only
     user-side discussion or `NONE`/`CONTRIBUTOR` comments are
     dropped. The GraphQL query already returns
     ``authorAssociation`` on each comment node — this filter runs
     against the fetched data, no schema change.
"""

from __future__ import annotations

import io
import json
import logging
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pandas as pd

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from app.infra.minio_client import DATA_BUCKET, get_client  # noqa: E402
from app.infra.vault_client import read_secrets  # noqa: E402

logger = logging.getLogger("rag.fetch_issues_held_out")

MAINTAINER_ASSOCIATIONS: frozenset[str] = frozenset({"MEMBER", "OWNER", "COLLABORATOR"})

# GitHub GraphQL config — mirrors scripts/dataset/fetch_issues_graphql.py.
REPO_OWNER = "pandas-dev"
REPO_NAME = "pandas"
GRAPHQL_URL = "https://api.github.com/graphql"
MAX_BATCHES = int(os.environ.get("MAX_BATCHES", "500"))  # 500 * 100 issues = 50k cap
COMMENTS_PER_ISSUE = 100  # GraphQL hard max

GRAPHQL_QUERY = """
query($owner: String!, $name: String!, $cursor: String) {
  repository(owner: $owner, name: $name) {
    issues(first: 100, after: $cursor, states: CLOSED,
           orderBy: {field: CREATED_AT, direction: DESC}) {
      pageInfo { hasNextPage endCursor }
      nodes {
        number
        title
        body
        closedAt
        comments(first: 100) {
          nodes {
            body
            createdAt
            authorAssociation
          }
        }
      }
    }
  }
  rateLimit { cost remaining resetAt }
}
"""


@dataclass(frozen=True)
class IssueComment:
    body: str
    created_at: datetime
    author_association: str


@dataclass(frozen=True)
class IssueSource:
    source_id: str  # issue_number as a string (matches rag_chunks.source_id convention)
    source_timestamp: datetime  # issue.closedAt
    title: str
    body: str
    comments: list[IssueComment] = field(default_factory=list)


@dataclass
class FetchResult:
    sources: list[IssueSource]
    excluded_issue_numbers: list[int]    # appeared in classifier splits, dropped
    dropped_no_maintainer: list[int]     # had no MEMBER/OWNER/COLLABORATOR comment


# --- classifier-split exclusion -------------------------------------------


class ClassifierSplitMissingError(RuntimeError):
    """At least one of train/val/test.parquet is missing — refuse to start."""


def load_excluded_issue_numbers(dataset_run_id: str) -> set[int]:
    """Union of issue_numbers across train+val+test for the given run."""
    excluded: set[int] = set()
    for split in ("train", "val", "test"):
        key = f"processed/pandas/{dataset_run_id}/{split}.parquet"
        try:
            obj = get_client().get_object(Bucket=DATA_BUCKET, Key=key)
        except Exception as exc:  # noqa: BLE001 — refuse-to-start contract
            raise ClassifierSplitMissingError(
                f"required classifier split missing at s3://{DATA_BUCKET}/{key}: {exc}"
            ) from exc
        body = obj["Body"].read()
        df = pd.read_parquet(io.BytesIO(body), columns=["issue_number"])
        excluded.update(int(n) for n in df["issue_number"].tolist())
    logger.info("classifier-split exclusion set: %d issue numbers", len(excluded))
    return excluded


# --- maintainer-response filter -------------------------------------------


def has_maintainer_response(comments: list[IssueComment]) -> bool:
    """True iff at least one comment has author_association in {MEMBER, OWNER, COLLABORATOR}."""
    return any(c.author_association in MAINTAINER_ASSOCIATIONS for c in comments)


# --- raw → IssueSource shaping --------------------------------------------


def _parse_dt(s: str | None) -> datetime | None:
    if not s:
        return None
    # GraphQL emits e.g. "2024-09-04T10:18:00Z" — replace Z for fromisoformat.
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def _to_issue_source(raw: dict[str, Any]) -> IssueSource | None:
    """Shape a GraphQL-style or fixture-style JSON issue into IssueSource."""
    number = int(raw["number"])
    closed_at = _parse_dt(raw.get("closedAt"))
    if closed_at is None:
        return None  # only resolved issues land in the corpus
    raw_comments = raw.get("comments")
    nodes = raw_comments["nodes"] if isinstance(raw_comments, dict) else (raw_comments or [])
    comments = [
        IssueComment(
            body=str(c.get("body") or ""),
            created_at=_parse_dt(c.get("createdAt")) or closed_at,
            author_association=str(c.get("authorAssociation", "NONE")),
        )
        for c in nodes
    ]
    return IssueSource(
        source_id=str(number),
        source_timestamp=closed_at,
        title=str(raw.get("title") or ""),
        body=str(raw.get("body") or ""),
        comments=comments,
    )


def _apply_filters(
    candidates: list[tuple[int, IssueSource]],
    excluded_set: set[int],
) -> FetchResult:
    """Run the two filters in order: split-exclusion, then maintainer-response."""
    sources: list[IssueSource] = []
    excluded_overlap: list[int] = []
    dropped_no_maintainer: list[int] = []
    for number, issue in candidates:
        if number in excluded_set:
            excluded_overlap.append(number)
            continue
        if not has_maintainer_response(issue.comments):
            dropped_no_maintainer.append(number)
            continue
        sources.append(issue)
    return FetchResult(
        sources=sources,
        excluded_issue_numbers=sorted(excluded_overlap),
        dropped_no_maintainer=sorted(dropped_no_maintainer),
    )


# --- top-level entry points ----------------------------------------------


def fetch_from_fixture(
    fixture_dir: Path,
    *,
    excluded_set: set[int] | None = None,
) -> FetchResult:
    """Read every issue JSON under fixture_dir/issues/ and apply both filters."""
    excluded_set = excluded_set or set()
    candidates: list[tuple[int, IssueSource]] = []
    for path in sorted((fixture_dir / "issues").glob("*.json")):
        raw = json.loads(path.read_text(encoding="utf-8"))
        issue = _to_issue_source(raw)
        if issue is None:
            continue
        candidates.append((int(raw["number"]), issue))
    return _apply_filters(candidates, excluded_set)


# --- real-mode GraphQL fetch ----------------------------------------------


class GithubPatMissingError(RuntimeError):
    """github_pat is missing or sentinel-valued in Vault — refuse to fetch."""


def _read_github_pat() -> str:
    pat = read_secrets(["github_pat"]).get("github_pat") or ""
    # Vault is seeded with "n/a" as a placeholder before an operator reseeds
    # it with a real classic token; treat the sentinel as missing so the
    # error message points at the right remediation.
    if not pat or pat == "n/a":
        raise GithubPatMissingError(
            "github_pat is missing or 'n/a' in Vault. Reseed with a classic "
            "PAT (no scopes needed for public-repo read) before running the "
            "real-mode held-out issue fetch."
        )
    return pat


def _handle_rate_limit(rate_limit: dict[str, Any] | None) -> None:
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
    logger.info("rate limit at %d; sleeping %ds until reset", remaining, int(sleep_for))
    time.sleep(sleep_for)


def _post_graphql(client: httpx.Client, payload: dict[str, Any]) -> dict[str, Any]:
    """Bounded-retry POST against the GitHub GraphQL endpoint."""
    for attempt in range(5):
        resp = client.post(GRAPHQL_URL, json=payload)
        if resp.status_code == 401:
            raise GithubPatMissingError(
                "GitHub rejected the PAT (401). Reseed Vault with a classic "
                "token (no scopes needed for public-repo read) and rerun."
            )
        if 500 <= resp.status_code < 600:
            time.sleep(2**attempt)
            continue
        resp.raise_for_status()
        body: dict[str, Any] = resp.json()
        if "errors" in body:
            raise RuntimeError(f"GraphQL errors: {body['errors']}")
        return body
    raise RuntimeError("exhausted retries against GitHub GraphQL")


def _node_to_issue_source(node: dict[str, Any]) -> IssueSource | None:
    """Shape a GraphQL repository.issues node into IssueSource."""
    return _to_issue_source(node)


def _issue_source_to_dict(src: IssueSource) -> dict[str, Any]:
    """Serialize IssueSource as a GraphQL-shaped JSON object (round-trippable)."""
    return {
        "number": int(src.source_id),
        "title": src.title,
        "body": src.body,
        "closedAt": src.source_timestamp.isoformat().replace("+00:00", "Z"),
        "comments": [
            {
                "body": c.body,
                "createdAt": c.created_at.isoformat().replace("+00:00", "Z"),
                "authorAssociation": c.author_association,
            }
            for c in src.comments
        ],
    }


def _cache_prefix(corpus_run_id: str) -> str:
    return f"rag/held_out_issues/{corpus_run_id}"


def _try_load_cache(corpus_run_id: str) -> FetchResult | None:
    """Return the cached FetchResult for `corpus_run_id`, or None if absent."""
    s3 = get_client()
    meta_key = f"{_cache_prefix(corpus_run_id)}/cache_meta.json"
    try:
        obj = s3.get_object(Bucket=DATA_BUCKET, Key=meta_key)
    except Exception:  # noqa: BLE001 — cache miss is the expected branch
        return None
    meta = json.loads(obj["Body"].read())
    sources: list[IssueSource] = []
    for batch_key in meta.get("batches", []):
        body = s3.get_object(Bucket=DATA_BUCKET, Key=batch_key)["Body"].read().decode()
        for line in body.splitlines():
            if not line.strip():
                continue
            issue = _to_issue_source(json.loads(line))
            if issue is not None:
                sources.append(issue)
    logger.info(
        "loaded held-out cache for corpus_run_id=%s: %d issues",
        corpus_run_id,
        len(sources),
    )
    return FetchResult(
        sources=sources,
        excluded_issue_numbers=list(meta.get("excluded_issue_numbers", [])),
        dropped_no_maintainer=list(meta.get("dropped_no_maintainer", [])),
    )


def _write_cache_meta(
    corpus_run_id: str,
    batch_keys: list[str],
    excluded_overlap: list[int],
    dropped_no_maintainer: list[int],
) -> None:
    """Land the cache_meta.json LAST so partial runs leave no complete marker."""
    s3 = get_client()
    meta = {
        "corpus_run_id": corpus_run_id,
        "batches": batch_keys,
        "excluded_issue_numbers": sorted(excluded_overlap),
        "dropped_no_maintainer": sorted(dropped_no_maintainer),
        "fetched_at": datetime.now(UTC).isoformat(),
    }
    s3.put_object(
        Bucket=DATA_BUCKET,
        Key=f"{_cache_prefix(corpus_run_id)}/cache_meta.json",
        Body=json.dumps(meta, indent=2).encode(),
    )


def fetch_from_github(
    *,
    corpus_run_id: str,
    excluded_set: set[int],
    client_factory: Any = httpx.Client,
) -> FetchResult:
    """Real-mode GraphQL fetch with MinIO caching.

    A re-run with the same ``corpus_run_id`` short-circuits the fetch and
    returns the cached result. Surviving issues are streamed to MinIO as
    they are filtered so a partial run doesn't lose work — the
    ``cache_meta.json`` marker is written last to keep cache-hit detection
    honest.

    ``client_factory`` is exposed so tests can inject an ``httpx.Client``
    pre-loaded with an ``httpx.MockTransport``.
    """
    cached = _try_load_cache(corpus_run_id)
    if cached is not None:
        return cached

    pat = _read_github_pat()
    s3 = get_client()
    prefix = _cache_prefix(corpus_run_id)
    headers = {
        "Authorization": f"Bearer {pat}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "maintainers-copilot-rag",
    }
    cursor: str | None = None
    sources: list[IssueSource] = []
    excluded_overlap: list[int] = []
    dropped_no_maintainer: list[int] = []
    batch_keys: list[str] = []

    logger.info(
        "starting GraphQL fetch (corpus_run_id=%s, excluded=%d, max_batches=%d)",
        corpus_run_id,
        len(excluded_set),
        MAX_BATCHES,
    )
    with client_factory(headers=headers, timeout=60.0) as client:
        for batch_idx in range(1, MAX_BATCHES + 1):
            payload = {
                "query": GRAPHQL_QUERY,
                "variables": {"owner": REPO_OWNER, "name": REPO_NAME, "cursor": cursor},
            }
            body = _post_graphql(client, payload)
            _handle_rate_limit(body["data"].get("rateLimit"))
            page = body["data"]["repository"]["issues"]
            nodes = page["nodes"]
            if not nodes:
                logger.info("batch %d empty; stopping", batch_idx)
                break

            survivors: list[IssueSource] = []
            for node in nodes:
                number = int(node["number"])
                if number in excluded_set:
                    excluded_overlap.append(number)
                    continue
                issue = _node_to_issue_source(node)
                if issue is None:
                    continue
                if not has_maintainer_response(issue.comments):
                    dropped_no_maintainer.append(number)
                    continue
                survivors.append(issue)

            if survivors:
                key = f"{prefix}/batch_{batch_idx:04d}.jsonl"
                payload_bytes = (
                    "\n".join(json.dumps(_issue_source_to_dict(s)) for s in survivors)
                    + "\n"
                ).encode()
                s3.put_object(Bucket=DATA_BUCKET, Key=key, Body=payload_bytes)
                batch_keys.append(key)
                sources.extend(survivors)

            logger.info(
                "batch %d: page=%d survivors=%d (total surv=%d, excluded=%d, dropped_no_maint=%d)",
                batch_idx,
                len(nodes),
                len(survivors),
                len(sources),
                len(excluded_overlap),
                len(dropped_no_maintainer),
            )

            page_info = page["pageInfo"]
            if not page_info["hasNextPage"]:
                logger.info("hasNextPage=false; stopping")
                break
            cursor = page_info["endCursor"]

    _write_cache_meta(corpus_run_id, batch_keys, excluded_overlap, dropped_no_maintainer)
    return FetchResult(
        sources=sources,
        excluded_issue_numbers=sorted(excluded_overlap),
        dropped_no_maintainer=sorted(dropped_no_maintainer),
    )


def fetch(
    *,
    dataset_run_id: str | None,
    fixture_dir: Path | None = None,
    corpus_run_id: str | None = None,
) -> FetchResult:
    """Main entry point — used by build_corpus.py.

    Fixture mode: ``fixture_dir`` provided. Reads JSON files offline.
    Real mode: ``fixture_dir`` is ``None`` and ``corpus_run_id`` is set.
      GraphQL-fetches pandas-dev/pandas closed issues with comments, applies
      both filters, caches survivors to MinIO under
      ``rag/held_out_issues/{corpus_run_id}/``. Re-runs against the same
      ``corpus_run_id`` short-circuit to the cache.
    """
    excluded_set: set[int] = set()
    if dataset_run_id is not None:
        excluded_set = load_excluded_issue_numbers(dataset_run_id)
    if fixture_dir is not None:
        return fetch_from_fixture(fixture_dir, excluded_set=excluded_set)
    if corpus_run_id is None:
        raise ValueError(
            "real-mode fetch requires corpus_run_id (used as the MinIO cache key)"
        )
    return fetch_from_github(corpus_run_id=corpus_run_id, excluded_set=excluded_set)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    fixture = os.environ.get("FIXTURE_DIR")
    dataset_run_id = os.environ.get("DATASET_RUN_ID")
    corpus_run_id = os.environ.get("CORPUS_RUN_ID")
    result = fetch(
        dataset_run_id=dataset_run_id,
        fixture_dir=Path(fixture) if fixture else None,
        corpus_run_id=corpus_run_id,
    )
    print(
        f"sources={len(result.sources)} "
        f"excluded_overlap={len(result.excluded_issue_numbers)} "
        f"dropped_no_maintainer={len(result.dropped_no_maintainer)}"
    )
    for src in result.sources:
        print(f"  #{src.source_id} {src.title[:60]!r}  {len(src.comments)} comments")
