"""Tests for the command line: exit codes and input checks. No network."""

import pytest

from monday_scaries import cli
from monday_scaries.github_client import RateLimitError, RepoNotFoundError

REAL_READY_PROVIDER = cli._ready_provider


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


class FakeLLM:
    """Stands in for an LLM provider: valid facts for every issue."""

    name, model = "fake", "fake-model"
    tokens_used = 42

    def complete(self, system, user, max_tokens=300):
        """Returns a valid facts reply."""
        return (
            '{"kind": "bug", "area": "other", "impact": "crash", '
            '"workaround": "no", "scope": "common", "regression": "no", '
            '"summary": "It crashes."}'
        )


@pytest.fixture(autouse=True)
def fake_github(monkeypatch):
    """Replaces the real GitHub client and LLM for every test."""
    FakeClient.result, FakeClient.created = [], 0
    monkeypatch.setattr(cli, "GitHubClient", FakeClient)
    monkeypatch.setattr(cli, "_ready_provider", lambda args: FakeLLM())


def test_missing_llm_key_exits_5_before_github(monkeypatch):
    """No key: stop before any GitHub request."""
    # Use the real provider check; keep every other guard in place.
    monkeypatch.setattr(cli, "_ready_provider", REAL_READY_PROVIDER)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("MONDAY_SCARIES_PROVIDER", raising=False)
    assert cli.main(["o/r"]) == cli.EXIT_LLM
    assert FakeClient.created == 0


def test_issues_are_classified_and_printed(capsys):
    """Fetched issues go through the LLM and are printed."""
    from datetime import datetime, timezone

    from monday_scaries.github_client import Issue

    now = datetime.now(timezone.utc)
    FakeClient.result = [Issue(7, "Boom", "", [], now, now, "")]
    assert cli.main(["o/r", "--no-color"]) == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "Classified 1 of 1 open issues" in out
    assert "━━ HIGH (1) " in out  # grouped under its severity
    assert "HIGH 1" in out and "," not in out.splitlines()[3]  # chips
    assert "#7" in out and "crash" in out and "It crashes." in out
    assert "Tokens: 42 · fake/fake-model" in out
    assert "\033[" not in out  # --no-color means no ANSI codes


def test_json_output_is_valid_and_has_the_same_data(capsys):
    """--json prints one JSON document on stdout, nothing else."""
    import json
    from datetime import datetime, timezone

    from monday_scaries.github_client import Issue

    now = datetime.now(timezone.utc)
    FakeClient.result = [Issue(7, "Boom", "", [], now, now, "")]
    assert cli.main(["o/r", "--json"]) == cli.EXIT_OK
    report = json.loads(capsys.readouterr().out)
    assert report["top"][0]["number"] == 7
    assert report["top"][0]["severity"] == "high"
    assert report["tokens"] == 42 and report["model"] == "fake-model"


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
    """An empty window is a normal result with a clear message."""
    assert cli.main(["o/r"]) == cli.EXIT_OK
    assert "No open issues" in capsys.readouterr().out


@pytest.mark.parametrize("limit", ["0", "abc", "201"])
def test_bad_limit_exits_2(limit):
    """--limit must be a whole number from 1 to 200."""
    with pytest.raises(SystemExit) as info:
        cli.main(["o/r", "--limit", limit])
    assert info.value.code == 2


@pytest.mark.parametrize("days", ["0", "abc", "9999"])
def test_bad_days_exits_2(days):
    """--days must be a whole number in range."""
    with pytest.raises(SystemExit) as info:
        cli.main(["o/r", "--days", days])
    assert info.value.code == 2
