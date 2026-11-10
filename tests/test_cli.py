"""Tests for the command line: exit codes and input checks. No network."""

import pytest

from monday_scaries import cli
from monday_scaries.github_client import RateLimitError, RepoNotFoundError


class FakeClient:
    """Stands in for GitHubClient; raises or returns what it is given."""

    result: object = []
    created = 0

    def __init__(self, token=None):
        """Counts how many clients were created."""
        FakeClient.created += 1

    def fetch_open_issues(self, owner, repo, since):
        """Returns or raises the configured result."""
        if isinstance(FakeClient.result, Exception):
            raise FakeClient.result
        return FakeClient.result

    def close(self):
        """Nothing to close."""


@pytest.fixture(autouse=True)
def fake_github(monkeypatch):
    """Replaces the real GitHub client for every test."""
    FakeClient.result, FakeClient.created = [], 0
    monkeypatch.setattr(cli, "GitHubClient", FakeClient)


def test_bad_repo_exits_2_before_any_api_call():
    """Invalid input never creates a GitHub client."""
    assert cli.main(["hello world"]) == cli.EXIT_USAGE
    assert FakeClient.created == 0


def test_private_or_missing_repo_exits_3(capsys):
    """A 404 is reported as not found or private."""
    FakeClient.result = RepoNotFoundError("Repository not found or private.")
    assert cli.main(["someone/secret"]) == cli.EXIT_NOT_FOUND


def test_rate_limit_exits_4():
    """A used-up rate limit has its own exit code."""
    FakeClient.result = RateLimitError("GitHub rate limit reached.")
    assert cli.main(["o/r"]) == cli.EXIT_RATE_LIMITED


def test_no_issues_exits_0(capsys):
    """An empty window is a normal result."""
    assert cli.main(["o/r"]) == cli.EXIT_OK
    assert "0 open items" in capsys.readouterr().out


@pytest.mark.parametrize("days", ["0", "abc", "9999"])
def test_bad_days_exits_2(days):
    """--days must be a whole number in range."""
    with pytest.raises(SystemExit) as info:
        cli.main(["o/r", "--days", days])
    assert info.value.code == 2
