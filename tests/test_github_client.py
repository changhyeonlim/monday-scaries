"""Tests for github_client. No network."""

import pytest

from monday_scaries.github_client import InvalidRepoError, parse_repo


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
