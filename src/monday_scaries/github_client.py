"""Read-only access to public GitHub repositories."""

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import httpx

logger = logging.getLogger(__name__)

API_URL = "https://api.github.com"
TIMEOUT_SECONDS = 10.0
PER_PAGE = 100
MAX_PAGES = 10
BODY_CHARS = 4000
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


class RepoNotFoundError(GitHubError):
    """GitHub returned 404: the repo does not exist or is private."""


class AuthError(GitHubError):
    """GitHub rejected GITHUB_TOKEN."""


class RateLimitError(GitHubError):
    """The GitHub API rate limit is used up."""


@dataclass
class Issue:
    """The parts of a GitHub issue that the pipeline reads.

    Attributes:
        number: Issue number.
        title: Issue title.
        body: Issue body, shortened to about BODY_CHARS characters.
        labels: Label names exactly as they appear on GitHub.
        created_at: When the issue was opened (UTC).
        updated_at: Last activity on the issue (UTC).
        url: Link to the issue on github.com.
        thumbs_up: Number of +1 reactions.
        is_pull_request: True if the item is a pull request.
    """

    number: int
    title: str
    body: str
    labels: list[str]
    created_at: datetime
    updated_at: datetime
    url: str
    thumbs_up: int = 0
    is_pull_request: bool = False


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


def shorten(text: str | None, limit: int = BODY_CHARS) -> str:
    """Shortens text to about limit characters, keeping start and end.

    The end of a bug report often holds the stack trace or the actual
    error, so this keeps the first three quarters and the last quarter.

    Args:
        text: The text to shorten. None counts as empty.
        limit: Maximum number of original characters to keep.

    Returns:
        The text unchanged if short enough, else head + marker + tail.
    """
    text = text or ""
    if len(text) <= limit:
        return text
    head = limit * 3 // 4
    return f"{text[:head]}\n[... truncated ...]\n{text[-(limit - head) :]}"


def _parse_time(value: str) -> datetime:
    """Parses a GitHub timestamp such as 2024-05-01T12:00:00Z."""
    # Python 3.10's fromisoformat() does not accept the "Z" suffix.
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _to_issue(item: dict[str, Any]) -> Issue:
    """Converts one item from the GitHub issues API to an Issue."""
    return Issue(
        number=item["number"],
        title=item["title"],
        body=shorten(item.get("body")),
        labels=[label["name"] for label in item.get("labels") or []],
        created_at=_parse_time(item["created_at"]),
        updated_at=_parse_time(item["updated_at"]),
        url=item["html_url"],
        thumbs_up=(item.get("reactions") or {}).get("+1", 0),
        is_pull_request="pull_request" in item,
    )


class GitHubClient:
    """Read-only client for the GitHub REST API (github.com only)."""

    def __init__(
        self,
        token: str | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        """Creates the client.

        Args:
            token: Optional GitHub token for a higher rate limit. Only
                ever sent to api.github.com.
            transport: Optional httpx transport. Tests pass a mock here.
        """
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "monday-scaries",
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self._authenticated = bool(token)
        self._http = httpx.Client(
            base_url=API_URL,
            headers=headers,
            timeout=TIMEOUT_SECONDS,
            transport=transport,
        )

    def close(self) -> None:
        """Closes the underlying HTTP connection pool."""
        self._http.close()

    def fetch_open_issues(
        self, owner: str, repo: str, since: datetime
    ) -> list[Issue]:
        """Fetches open issues and PRs updated since a given time.

        Pull requests come back too (the API mixes them in); they are
        marked with is_pull_request and removed by the filters.

        Args:
            owner: Repository owner.
            repo: Repository name.
            since: Only items updated at or after this time are returned.

        Returns:
            Items ordered by most recently updated first.

        Raises:
            RepoNotFoundError: The repo does not exist or is private.
            RateLimitError: The rate limit is used up.
            AuthError: GITHUB_TOKEN was rejected.
            GitHubError: Timeouts, network errors, or bad responses.
        """
        params: dict[str, Any] = {
            "state": "open",
            "since": since.astimezone(timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            ),
            "sort": "updated",
            "direction": "desc",
            "per_page": PER_PAGE,
        }
        items: list[Issue] = []
        for page in range(1, MAX_PAGES + 1):
            batch = self._get_list(
                f"/repos/{owner}/{repo}/issues", {**params, "page": page}
            )
            for raw in batch:
                try:
                    items.append(_to_issue(raw))
                except (KeyError, TypeError, ValueError, AttributeError):
                    logger.warning("Skipping one malformed issue record.")
            if len(batch) < PER_PAGE:
                return items
        logger.warning("Stopped after %d pages of issues.", MAX_PAGES)
        return items

    def _get_list(
        self, path: str, params: dict[str, Any]
    ) -> list[dict[str, Any]]:
        """Sends a GET request and returns the JSON list in the response.

        Args:
            path: API path, e.g. "/repos/psf/requests/issues".
            params: Query parameters.

        Returns:
            The decoded JSON list.

        Raises:
            GitHubError: Any failure; see fetch_open_issues.
        """
        try:
            response = self._http.get(path, params=params)
        except httpx.TimeoutException:
            raise GitHubError(
                f"GitHub did not respond within {TIMEOUT_SECONDS:.0f}s."
            ) from None
        except httpx.HTTPError as err:
            raise GitHubError(
                f"Could not reach GitHub ({type(err).__name__})."
            ) from None
        self._raise_for_status(response)
        try:
            data = response.json()
        except ValueError:
            raise GitHubError("GitHub returned invalid JSON.") from None
        if not isinstance(data, list):
            raise GitHubError("GitHub returned an unexpected response.")
        return data

    def _raise_for_status(self, response: httpx.Response) -> None:
        """Maps HTTP error statuses to GitHubError subclasses."""
        status = response.status_code
        if status < 400:
            return
        if status == 404:
            raise RepoNotFoundError("Repository not found or private.")
        if status == 401:
            raise AuthError("GitHub rejected GITHUB_TOKEN (401).")
        limited = response.headers.get("x-ratelimit-remaining") == "0"
        if status in (403, 429) and (
            limited or "retry-after" in response.headers
        ):
            message = "GitHub rate limit reached."
            if not self._authenticated:
                message += " Set GITHUB_TOKEN for 5000 requests/hour."
            raise RateLimitError(message)
        raise GitHubError(f"GitHub returned HTTP {status}.")
