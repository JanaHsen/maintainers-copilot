"""Cover the two filters in scripts/rag/fetch_issues_held_out.py."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import httpx
import pytest

from scripts.rag import fetch_issues_held_out as mod
from scripts.rag.fetch_issues_held_out import (
    GithubPatMissingError,
    IssueComment,
    IssueSource,
    _apply_filters,
    fetch,
    fetch_from_fixture,
    fetch_from_github,
    has_maintainer_response,
)

FIXTURE = Path(__file__).resolve().parents[1].parent / "tests" / "fixtures" / "rag_smoke"


def _comment(association: str) -> IssueComment:
    return IssueComment(
        body="...",
        created_at=datetime(2024, 1, 1, tzinfo=UTC),
        author_association=association,
    )


class TestHasMaintainerResponse:
    def test_member_kept(self) -> None:
        assert has_maintainer_response([_comment("MEMBER")]) is True

    def test_owner_kept(self) -> None:
        assert has_maintainer_response([_comment("OWNER")]) is True

    def test_collaborator_kept(self) -> None:
        assert has_maintainer_response([_comment("COLLABORATOR")]) is True

    def test_contributor_alone_dropped(self) -> None:
        assert has_maintainer_response([_comment("CONTRIBUTOR")]) is False

    def test_none_alone_dropped(self) -> None:
        assert has_maintainer_response([_comment("NONE")]) is False

    def test_no_comments_dropped(self) -> None:
        assert has_maintainer_response([]) is False

    def test_mixed_kept_if_one_maintainer(self) -> None:
        assert (
            has_maintainer_response(
                [_comment("NONE"), _comment("CONTRIBUTOR"), _comment("MEMBER")]
            )
            is True
        )


class TestFixtureFilters:
    def test_smoke_fixture_drops_9004_keeps_the_other_four(self) -> None:
        # 9001 MEMBER, 9002 OWNER, 9003 COLLABORATOR, 9005 MEMBER+others -> kept
        # 9004 only CONTRIBUTOR + NONE -> dropped by maintainer filter
        result = fetch_from_fixture(FIXTURE)
        kept = {int(s.source_id) for s in result.sources}
        assert kept == {9001, 9002, 9003, 9005}
        assert result.dropped_no_maintainer == [9004]
        assert result.excluded_issue_numbers == []

    def test_classifier_split_exclusion_drops_listed_issue(self) -> None:
        # Pretend 9002 was in a classifier split; expect it dropped.
        result = fetch_from_fixture(FIXTURE, excluded_set={9002})
        kept = {int(s.source_id) for s in result.sources}
        assert kept == {9001, 9003, 9005}
        assert result.excluded_issue_numbers == [9002]
        # 9004 still dropped by maintainer filter regardless.
        assert result.dropped_no_maintainer == [9004]

    def test_split_exclusion_takes_priority_over_maintainer_filter(self) -> None:
        # If an issue is in the splits AND has no maintainer comment, it's
        # counted in excluded_overlap, NOT dropped_no_maintainer
        # (exclusion runs first).
        result = fetch_from_fixture(FIXTURE, excluded_set={9004})
        assert result.excluded_issue_numbers == [9004]
        assert 9004 not in result.dropped_no_maintainer


class TestApplyFilters:
    """Unit tests against _apply_filters with synthetic IssueSource objects."""

    def _issue(self, number: int, *, assoc: str) -> IssueSource:
        return IssueSource(
            source_id=str(number),
            source_timestamp=datetime(2024, 1, 1, tzinfo=UTC),
            title="t",
            body="b",
            comments=[_comment(assoc)],
        )

    def test_keeps_issue_with_maintainer_comment(self) -> None:
        result = _apply_filters([(1, self._issue(1, assoc="MEMBER"))], excluded_set=set())
        assert len(result.sources) == 1
        assert result.dropped_no_maintainer == []

    def test_drops_issue_in_excluded_set(self) -> None:
        result = _apply_filters([(1, self._issue(1, assoc="MEMBER"))], excluded_set={1})
        assert result.sources == []
        assert result.excluded_issue_numbers == [1]


# --- real-mode (GraphQL) tests --------------------------------------------


def _gql_issue(
    number: int,
    *,
    closed_at: str = "2024-08-12T15:30:00Z",
    comments: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    return {
        "number": number,
        "title": f"issue {number}",
        "body": f"body {number}",
        "closedAt": closed_at,
        "comments": {
            "nodes": comments
            or [
                {
                    "body": "answered",
                    "createdAt": closed_at,
                    "authorAssociation": "MEMBER",
                }
            ]
        },
    }


def _gql_envelope(nodes: list[dict[str, Any]], *, has_next: bool = False) -> dict[str, Any]:
    return {
        "data": {
            "repository": {
                "issues": {
                    "pageInfo": {"hasNextPage": has_next, "endCursor": None},
                    "nodes": nodes,
                }
            },
            "rateLimit": {"cost": 1, "remaining": 4999, "resetAt": None},
        }
    }


class _FakeS3:
    """Minimal in-memory stand-in for boto3 S3 client used by fetch_from_github."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def put_object(self, *, Bucket: str, Key: str, Body: bytes) -> None:  # noqa: N803
        self.objects[Key] = Body

    def get_object(self, *, Bucket: str, Key: str) -> dict[str, Any]:  # noqa: N803
        if Key not in self.objects:
            raise KeyError(f"no such key: {Key}")
        body = self.objects[Key]
        return {"Body": MagicMock(read=lambda b=body: b)}


@pytest.fixture
def _fake_minio(monkeypatch: pytest.MonkeyPatch) -> _FakeS3:
    fake = _FakeS3()
    monkeypatch.setattr(mod, "get_client", lambda: fake)
    return fake


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mod.time, "sleep", lambda _s: None)


@pytest.fixture
def _vault_pat(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mod, "read_secrets", lambda _keys: {"github_pat": "ghp_test"})


def _client_factory_returning(pages: list[dict[str, Any]]) -> Any:
    """Build an httpx.Client factory whose MockTransport replays `pages` in order."""
    queue = list(pages)

    def handler(_request: httpx.Request) -> httpx.Response:
        if not queue:
            raise AssertionError("MockTransport exhausted before fetch stopped")
        return httpx.Response(200, json=queue.pop(0))

    transport = httpx.MockTransport(handler)

    def factory(**kwargs: Any) -> httpx.Client:
        kwargs["transport"] = transport
        return httpx.Client(**kwargs)

    return factory


class TestFetchFromGithub:
    def test_filters_in_flight_and_caches_survivors(
        self,
        _vault_pat: None,
        _fake_minio: _FakeS3,
    ) -> None:
        nodes = [
            _gql_issue(1),
            _gql_issue(
                2,
                comments=[
                    {
                        "body": "user reply",
                        "createdAt": "2024-08-12T16:00:00Z",
                        "authorAssociation": "NONE",
                    }
                ],
            ),
            _gql_issue(3),
        ]
        factory = _client_factory_returning([_gql_envelope(nodes)])
        result = fetch_from_github(
            corpus_run_id="test-run-1",
            excluded_set={1},
            client_factory=factory,
        )
        assert {int(s.source_id) for s in result.sources} == {3}
        assert result.excluded_issue_numbers == [1]
        assert result.dropped_no_maintainer == [2]
        assert any(k.endswith("batch_0001.jsonl") for k in _fake_minio.objects)
        assert any(k.endswith("cache_meta.json") for k in _fake_minio.objects)

    def test_cache_hit_short_circuits_graphql(
        self,
        _vault_pat: None,
        _fake_minio: _FakeS3,
    ) -> None:
        factory = _client_factory_returning([_gql_envelope([_gql_issue(42)])])
        first = fetch_from_github(
            corpus_run_id="test-run-cache",
            excluded_set=set(),
            client_factory=factory,
        )
        assert len(first.sources) == 1

        def boom(**_kwargs: Any) -> httpx.Client:
            raise AssertionError("cache should have prevented GraphQL call")

        second = fetch_from_github(
            corpus_run_id="test-run-cache",
            excluded_set=set(),
            client_factory=boom,
        )
        assert len(second.sources) == 1
        assert int(second.sources[0].source_id) == 42

    def test_missing_pat_raises_typed_error(
        self,
        monkeypatch: pytest.MonkeyPatch,
        _fake_minio: _FakeS3,
    ) -> None:
        monkeypatch.setattr(mod, "read_secrets", lambda _keys: {"github_pat": "n/a"})
        with pytest.raises(GithubPatMissingError):
            fetch_from_github(
                corpus_run_id="test-run-pat",
                excluded_set=set(),
                client_factory=_client_factory_returning([]),
            )

    def test_real_mode_requires_corpus_run_id(self) -> None:
        with pytest.raises(ValueError, match="corpus_run_id"):
            fetch(dataset_run_id=None, fixture_dir=None, corpus_run_id=None)
