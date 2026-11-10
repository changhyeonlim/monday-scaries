"""Tests for filters. Pure functions, no network."""

from datetime import datetime, timedelta, timezone

import pytest

from monday_scaries.filters import apply_filters, normalize_label
from monday_scaries.github_client import Issue

NOW = datetime(2026, 11, 9, tzinfo=timezone.utc)
SINCE = NOW - timedelta(days=7)


def issue(number, labels=(), created_days_ago=1, updated_days_ago=1, pr=False):
    """Builds an Issue relative to NOW."""
    return Issue(
        number=number,
        title=f"Issue {number}",
        body="",
        labels=list(labels),
        created_at=NOW - timedelta(days=created_days_ago),
        updated_at=NOW - timedelta(days=updated_days_ago),
        url="",
        is_pull_request=pr,
    )


def numbers(result):
    """Issue numbers that were kept."""
    return [i.number for i in result.kept]


def test_pull_requests_are_removed_and_counted():
    """PRs never reach the LLM."""
    result = apply_filters([issue(1), issue(2, pr=True)], SINCE)
    assert numbers(result) == [1]
    assert result.pull_requests == 1


@pytest.mark.parametrize(
    "label", ["duplicate", "Won't Fix", "wontfix", "status: stale", "Invalid"]
)
def test_closing_candidate_labels_are_excluded(label):
    """Differently written closing labels are all caught."""
    result = apply_filters([issue(1, labels=[label]), issue(2)], SINCE)
    assert numbers(result) == [2]
    assert result.excluded == 1


def test_label_match_is_whole_word():
    """A label "stalemate" does not count as "stale"."""
    assert numbers(apply_filters([issue(1, ["stalemate"])], SINCE)) == [1]


def test_old_issue_with_new_activity_is_kept_unless_new_only():
    """Default window keeps old issues with recent activity."""
    old_but_active = issue(1, created_days_ago=400, updated_days_ago=1)
    assert numbers(apply_filters([old_but_active], SINCE)) == [1]
    assert numbers(apply_filters([old_but_active], SINCE, new_only=True)) == []


def test_activity_outside_window_is_dropped():
    """Defensive: items older than the window are dropped here too."""
    assert apply_filters([issue(1, updated_days_ago=30)], SINCE).kept == []


def test_limit_caps_llm_calls_and_counts_the_rest():
    """--limit keeps the first N and reports how many were cut."""
    result = apply_filters([issue(n) for n in range(10)], SINCE, limit=3)
    assert numbers(result) == [0, 1, 2]
    assert result.capped == 7 and result.in_window == 10


def test_nothing_left_is_an_empty_result():
    """All filtered out: empty kept list, not an error."""
    result = apply_filters([issue(1, pr=True)], SINCE)
    assert result.kept == [] and result.in_window == 0


def test_normalize_label_examples():
    """Case, apostrophes, and separators do not matter."""
    assert normalize_label("Won't-Fix") == "wont fix"
    assert normalize_label("status: stale") == "status stale"
    assert normalize_label("🐛 bug") == "bug"
