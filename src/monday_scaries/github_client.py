"""Read-only access to public GitHub repositories."""

import re

MAX_INPUT_CHARS = 200

# GitHub usernames: letters, digits, hyphens; 1-39 chars; no leading hyphen.
_OWNER = r"[A-Za-z0-9][A-Za-z0-9-]{0,38}"
# Repo names: letters, digits, ".", "_", "-"; up to 100 chars.
_REPO = r"[A-Za-z0-9._-]{1,100}"

_SHORT_RE = re.compile(rf"(?P<owner>{_OWNER})/(?P<repo>{_REPO})")
_URL_RE = re.compile(
    r"https://(?:www\.)?github\.com/"
    rf"(?P<owner>{_OWNER})/(?P<repo>{_REPO})/?"
)


class GitHubError(Exception):
    """Base class for GitHub problems reported to the user."""


class InvalidRepoError(GitHubError):
    """The repo argument is not owner/repo or a GitHub URL."""


def parse_repo(text: str) -> tuple[str, str]:
    """Extracts the owner and repo name from user input.

    Accepts exactly two forms: "owner/repo" and
    "https://github.com/owner/repo" (optional "www.", trailing "/" or
    ".git"). Anything else is rejected, so no request is ever sent to a
    host the user did not mean.

    Args:
        text: The repo argument as typed by the user.

    Returns:
        An (owner, repo) tuple.

    Raises:
        InvalidRepoError: If the input has any other form.
    """
    text = text.strip()
    if len(text) > MAX_INPUT_CHARS:
        raise InvalidRepoError("Repo argument is too long.")
    match = _SHORT_RE.fullmatch(text) or _URL_RE.fullmatch(text)
    repo = match["repo"].removesuffix(".git") if match else ""
    if match is None or repo in {"", ".", ".."}:
        raise InvalidRepoError(
            "Expected owner/repo or https://github.com/owner/repo, "
            f"got {text!r}."
        )
    return match["owner"], repo
