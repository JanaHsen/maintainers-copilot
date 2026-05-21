"""Fetch resolved pandas issues with maintainer responses (offline corpus build).

Real mode: GraphQL fetch (same PAT-from-Vault pattern as
``scripts/dataset/fetch_issues_graphql.py``) but pulling comments +
``author_association`` so the maintainer-response filter has the
signal it needs.

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
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from app.infra.minio_client import DATA_BUCKET, get_client  # noqa: E402

logger = logging.getLogger("rag.fetch_issues_held_out")

MAINTAINER_ASSOCIATIONS: frozenset[str] = frozenset({"MEMBER", "OWNER", "COLLABORATOR"})


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


def fetch(
    *,
    dataset_run_id: str | None,
    fixture_dir: Path | None = None,
) -> FetchResult:
    """Main entry point — used by build_corpus.py."""
    excluded_set: set[int] = set()
    if dataset_run_id is not None:
        excluded_set = load_excluded_issue_numbers(dataset_run_id)
    if fixture_dir is not None:
        return fetch_from_fixture(fixture_dir, excluded_set=excluded_set)
    raise NotImplementedError(
        "Real-mode GraphQL fetch lives in T011 (build_corpus.py orchestrator). "
        "For now this script is fixture-mode only; the GraphQL pattern is "
        "transplanted from scripts/dataset/fetch_issues_graphql.py with the "
        "comments + authorAssociation query."
    )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    fixture = os.environ.get("FIXTURE_DIR")
    dataset_run_id = os.environ.get("DATASET_RUN_ID")
    result = fetch(
        dataset_run_id=dataset_run_id,
        fixture_dir=Path(fixture) if fixture else None,
    )
    print(
        f"sources={len(result.sources)} "
        f"excluded_overlap={len(result.excluded_issue_numbers)} "
        f"dropped_no_maintainer={len(result.dropped_no_maintainer)}"
    )
    for src in result.sources:
        print(f"  #{src.source_id} {src.title[:60]!r}  {len(src.comments)} comments")
