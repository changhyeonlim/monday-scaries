"""Decides which fetched issues are sent to the LLM.

Order: pull requests and the activity window, excluded labels,
--new-only, and finally the --limit cap on LLM calls.
"""

import re
from dataclasses import dataclass
from datetime import datetime

from monday_scaries.github_client import Issue

# Labels that mark an issue as resolved, rejected, or abandoned.
EXCLUDED_LABELS = frozenset(
    {"duplicate", "invalid", "wontfix", "wont fix", "stale", "needs more info"}
)


@dataclass
class FilterResult:
    """Issues to classify, plus counts for the report header.

    Attributes:
        kept: Issues to send to the LLM, most recently updated first.
        in_window: Open issues (not PRs) updated within the window.
        excluded: Issues dropped by excluded labels or --new-only.
        capped: Issues dropped by the --limit cap.
        pull_requests: Pull requests that were removed.
    """

    kept: list[Issue]
    in_window: int
    excluded: int
    capped: int
    pull_requests: int


def normalize_label(name: str) -> str:
    """Normalizes a label so that differently written labels match.

    "Won't-Fix" becomes "wont fix" and "status: stale" becomes
    "status stale".

    Args:
        name: The label as it appears on GitHub.

    Returns:
        The label lowercased, without apostrophes, with every run of
        other symbols turned into one space.
    """
    name = name.lower().replace("'", "").replace("’", "")
    return re.sub(r"[^a-z0-9]+", " ", name).strip()


def has_label(issue: Issue, terms: frozenset[str]) -> bool:
    """Checks whether any label of the issue contains one of the terms.

    Terms match whole words only: "stale" matches "status: stale" but
    not "stalemate".

    Args:
        issue: The issue to check.
        terms: Normalized terms, e.g. {"wont fix", "stale"}.

    Returns:
        True if at least one label contains at least one term.
    """
    for label in issue.labels:
        padded = f" {normalize_label(label)} "
        if any(f" {term} " in padded for term in terms):
            return True
    return False


def apply_filters(
    items: list[Issue],
    since: datetime,
    new_only: bool = False,
    limit: int = 50,
) -> FilterResult:
    """Applies all filters in order.

    Args:
        items: Issues and PRs from GitHubClient.fetch_open_issues.
        since: Start of the activity window (timezone-aware).
        new_only: If True, keep only issues created within the window.
        limit: Maximum number of issues to keep.

    Returns:
        The kept issues and the counts behind them.
    """
    pull_requests = sum(item.is_pull_request for item in items)
    in_window = [
        item
        for item in items
        if not item.is_pull_request and item.updated_at >= since
    ]
    kept = [i for i in in_window if not has_label(i, EXCLUDED_LABELS)]
    if new_only:
        kept = [i for i in kept if i.created_at >= since]
    return FilterResult(
        kept=kept[:limit],
        in_window=len(in_window),
        excluded=len(in_window) - len(kept),
        capped=max(0, len(kept) - limit),
        pull_requests=pull_requests,
    )
