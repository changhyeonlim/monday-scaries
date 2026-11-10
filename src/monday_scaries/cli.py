"""Command-line entry point for Monday Scaries."""

import argparse
import logging
import os
from datetime import datetime, timedelta, timezone

from monday_scaries.github_client import (
    GitHubClient,
    GitHubError,
    InvalidRepoError,
    RateLimitError,
    RepoNotFoundError,
    parse_repo,
)

logger = logging.getLogger(__name__)

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2
EXIT_NOT_FOUND = 3
EXIT_RATE_LIMITED = 4

MAX_DAYS = 365


def _days(text: str) -> int:
    """Argparse type for --days: a whole number from 1 to MAX_DAYS."""
    try:
        value = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError("must be a whole number") from None
    if not 1 <= value <= MAX_DAYS:
        raise argparse.ArgumentTypeError(f"must be 1 to {MAX_DAYS}")
    return value


def _setup_logging(verbose: bool) -> None:
    """Sends diagnostics to stderr and keeps HTTP libraries quiet."""
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.WARNING,
        format="%(levelname)s: %(message)s",
    )
    # HTTP library debug logs can include request headers.
    for name in ("httpx", "httpcore", "httpx2", "openai", "anthropic"):
        logging.getLogger(name).setLevel(logging.WARNING)


def build_parser() -> argparse.ArgumentParser:
    """Builds the argument parser."""
    parser = argparse.ArgumentParser(
        prog="monday-scaries",
        description="Triage a public GitHub repo's open issues with an LLM.",
    )
    parser.add_argument("repo", help="owner/repo or a GitHub URL")
    parser.add_argument(
        "--days",
        type=_days,
        default=7,
        help="activity window in days (default: 7)",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="show debug logs"
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Parses arguments and runs the triage pipeline.

    Args:
        argv: Command-line arguments without the program name. If None,
            argparse reads sys.argv.

    Returns:
        The process exit code. See the EXIT_* constants.
    """
    args = build_parser().parse_args(argv)
    _setup_logging(args.verbose)

    try:
        owner, repo = parse_repo(args.repo)
    except InvalidRepoError as err:
        logger.error("%s", err)
        return EXIT_USAGE

    since = datetime.now(timezone.utc) - timedelta(days=args.days)
    client = GitHubClient(token=os.environ.get("GITHUB_TOKEN") or None)
    try:
        items = client.fetch_open_issues(owner, repo, since)
    except RepoNotFoundError as err:
        logger.error("%s/%s: %s", owner, repo, err)
        return EXIT_NOT_FOUND
    except RateLimitError as err:
        logger.error("%s", err)
        return EXIT_RATE_LIMITED
    except GitHubError as err:
        logger.error("%s", err)
        return EXIT_ERROR
    finally:
        client.close()

    # Temporary output until filters and report.py exist.
    prs = sum(item.is_pull_request for item in items)
    print(
        f"{owner}/{repo}: {len(items)} open items updated in the last "
        f"{args.days} days ({len(items) - prs} issues, {prs} pull requests)"
    )
    for item in items:
        kind = "PR   " if item.is_pull_request else "issue"
        print(f"  {kind} #{item.number:<6} {item.title[:60]}")
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
