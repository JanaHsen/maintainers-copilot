"""Cover the two filters in scripts/rag/fetch_issues_held_out.py."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from scripts.rag.fetch_issues_held_out import (
    IssueComment,
    IssueSource,
    _apply_filters,
    fetch_from_fixture,
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
