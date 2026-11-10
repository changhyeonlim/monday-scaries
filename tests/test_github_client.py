"""Tests for github_client. No network."""

from datetime import datetime, timezone

import httpx
import pytest

from monday_scaries.github_client import (
    GitHubClient,
    GitHubError,
    InvalidRepoError,
    RateLimitError,
    RepoNotFoundError,
    parse_repo,
    shorten,
)

TOKEN = "ghp_FAKE_TOKEN_1234567890"
SINCE = datetime(2026, 11, 1, tzinfo=timezone.utc)


def raw_issue(number: int, **extra) -> dict:
    """A minimal item as the GitHub issues API returns it."""
    item = {
        "number": number,
        "title": f"Issue {number}",
        "body": "body",
        "labels": [],
        "created_at": "2026-11-02T10:00:00Z",
        "updated_at": "2026-11-03T10:00:00Z",
        "html_url": f"https://github.com/o/r/issues/{number}",
        "reactions": {"+1": 0},
    }
    item.update(extra)
    return item


def client_returning(handler, token=None) -> GitHubClient:
    """A client whose HTTP calls go to handler instead of the network."""
    return GitHubClient(token=token, transport=httpx.MockTransport(handler))


def test_404_means_not_found_or_private():
    """Private and missing repos look the same: a 404."""
    client = client_returning(lambda request: httpx.Response(404))
    with pytest.raises(RepoNotFoundError, match="not found or private"):
        client.fetch_open_issues("someone", "secret-repo", SINCE)


def test_no_open_issues_returns_empty_list():
    """A repo with nothing open is not an error."""
    client = client_returning(lambda request: httpx.Response(200, json=[]))
    assert client.fetch_open_issues("o", "r", SINCE) == []


def test_pull_requests_are_marked_and_fields_parsed():
    """PRs come back from the API too; they are flagged for the filter."""
    items = [
        raw_issue(1, labels=[{"name": "bug"}], reactions={"+1": 12}),
        raw_issue(2, pull_request={"url": "..."}),
    ]
    client = client_returning(lambda request: httpx.Response(200, json=items))
    issue, pr = client.fetch_open_issues("o", "r", SINCE)
    assert not issue.is_pull_request and pr.is_pull_request
    assert issue.labels == ["bug"] and issue.thumbs_up == 12
    assert issue.created_at.tzinfo is not None  # "Z" parsed on 3.10


def test_request_asks_github_for_open_items_in_the_window():
    """state=open and since= are sent, so GitHub does the filtering."""
    seen = {}

    def handler(request):
        seen.update(request.url.params)
        return httpx.Response(200, json=[])

    client_returning(handler).fetch_open_issues("o", "r", SINCE)
    assert seen["state"] == "open"
    assert seen["since"] == "2026-11-01T00:00:00Z"


def test_malformed_record_is_skipped_not_fatal():
    """One broken item does not lose the whole page."""
    items = [{"number": 1}, raw_issue(2)]
    client = client_returning(lambda request: httpx.Response(200, json=items))
    assert [i.number for i in client.fetch_open_issues("o", "r", SINCE)] == [2]


def test_rate_limit_is_clear_and_suggests_a_token():
    """403 with no requests left is a rate limit, not a permission error."""
    client = client_returning(
        lambda request: httpx.Response(
            403, headers={"x-ratelimit-remaining": "0"}
        )
    )
    with pytest.raises(RateLimitError, match="GITHUB_TOKEN"):
        client.fetch_open_issues("o", "r", SINCE)


def test_timeout_is_a_clear_error():
    """A hung connection fails with a message, not a traceback."""

    def handler(request):
        raise httpx.ReadTimeout("slow", request=request)

    with pytest.raises(GitHubError, match="did not respond"):
        client_returning(handler).fetch_open_issues("o", "r", SINCE)


@pytest.mark.parametrize("status", [401, 403, 404, 500])
def test_token_never_appears_in_errors(status):
    """The token is sent as a header but never echoed in an error."""
    client = client_returning(
        lambda request: httpx.Response(status, text=TOKEN), token=TOKEN
    )
    with pytest.raises(GitHubError) as info:
        client.fetch_open_issues("o", "r", SINCE)
    assert TOKEN not in str(info.value)
    assert info.value.__cause__ is None


def test_long_body_keeps_start_and_end():
    """Stack traces sit at the end of reports, so the end is kept."""
    body = "START" + "x" * 10_000 + "TRACEBACK"
    short = shorten(body, 4000)
    assert short.startswith("START") and short.endswith("TRACEBACK")
    assert "[... truncated ...]" in short
    assert len(short) < 4100


@pytest.mark.parametrize(
    "text",
    [
        "psf/requests",
        "https://github.com/psf/requests",
        "https://www.github.com/psf/requests/",
        "https://github.com/psf/requests.git",
        "  psf/requests  ",
    ],
)
def test_accepted_forms(text):
    """Both allowed forms, with harmless variations, parse the same."""
    assert parse_repo(text) == ("psf", "requests")


@pytest.mark.parametrize(
    "url, expected",
    [
        ("https://github.com/pandas-dev/pandas", ("pandas-dev", "pandas")),
        ("https://github.com/morluto/rea", ("morluto", "rea")),
        ("https://github.com/opencv/opencv", ("opencv", "opencv")),
        ("https://github.com/pytorch/pytorch", ("pytorch", "pytorch")),
        ("https://github.com/changhyeonlim/PENN", ("changhyeonlim", "PENN")),
    ],
)
def test_real_public_repo_links(url, expected):
    """Links as users will paste them: hyphens and case are kept."""
    assert parse_repo(url) == expected


NOT_A_LINK = [
    "",
    "   ",
    "requests",
    "hello world",
    "pandas dev/pandas",
    "owner/",
    "/repo",
    "owner//repo",
    "a/b/c",
    "psf/requests?x=1",
    "-psf/requests",
    "psf/..",
    "pandas-dev/pandas 🐼",
]

NOT_GITHUB = [
    "http://github.com/psf/requests",  # not https
    "https://gitlab.com/psf/requests",
    "https://bitbucket.org/psf/requests",
    "https://evil.example.com/psf/requests",
    "https://github.com.evil.com/psf/requests",  # lookalike suffix
    "https://notgithub.com/psf/requests",  # lookalike prefix
    "https://github.com@evil.com/psf/requests",  # userinfo trick
    "https://gіthub.com/psf/requests",  # Cyrillic "i"
    "https://gist.github.com/psf/requests",
    "https://api.github.com/repos/psf/requests",
    "git@github.com:psf/requests.git",  # SSH clone address
    "github.com/psf/requests",  # no scheme
    "https://github.com/psf",  # owner page, no repo
    "https://github.com/psf/requests/issues",  # sub-page
    "https://github.com/psf/requests/pull/1",
]


@pytest.mark.parametrize("text", NOT_A_LINK)
def test_rejects_input_that_is_not_a_repo(text):
    """Plain words, broken paths, and odd characters are rejected."""
    with pytest.raises(InvalidRepoError, match="Expected owner/repo"):
        parse_repo(text)


@pytest.mark.parametrize("text", NOT_GITHUB)
def test_rejects_links_that_are_not_a_github_repo(text):
    """Other hosts, lookalikes, and GitHub sub-pages are rejected.

    Nothing here may reach the network: a lookalike host would receive
    GITHUB_TOKEN.
    """
    with pytest.raises(InvalidRepoError):
        parse_repo(text)


def test_too_long_input_is_rejected_without_echo():
    """A huge argument is rejected and not echoed back in the error."""
    text = "a" * 5000 + "/repo"
    with pytest.raises(InvalidRepoError) as info:
        parse_repo(text)
    assert "a" * 100 not in str(info.value)
