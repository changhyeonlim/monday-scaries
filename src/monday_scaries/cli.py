"""Command-line entry point for Monday Scaries."""

import argparse
import logging
import os
from datetime import datetime, timedelta, timezone

from monday_scaries.classify import classify_all
from monday_scaries.config import (
    PROVIDERS,
    default_provider_name,
    make_provider,
)
from monday_scaries.filters import apply_filters
from monday_scaries.github_client import (
    GitHubClient,
    GitHubError,
    InvalidRepoError,
    RateLimitError,
    RepoNotFoundError,
    parse_repo,
)
from monday_scaries.llm.base import LLMError, LLMProvider
from monday_scaries.report import (
    BOLD,
    YELLOW,
    color_enabled,
    format_result,
    paint,
)

logger = logging.getLogger(__name__)

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2
EXIT_NOT_FOUND = 3
EXIT_RATE_LIMITED = 4
EXIT_LLM = 5  # missing or rejected key, no credits, unknown model
EXIT_PARTIAL = 6  # the LLM failed partway; partial results shown

MAX_DAYS = 365
MAX_LIMIT = 200


def _whole_number(text: str, high: int) -> int:
    """Parses a whole number from 1 to high, for argparse."""
    try:
        value = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError("must be a whole number") from None
    if not 1 <= value <= high:
        raise argparse.ArgumentTypeError(f"must be 1 to {high}")
    return value


def _days(text: str) -> int:
    """Argparse type for --days."""
    return _whole_number(text, MAX_DAYS)


def _limit(text: str) -> int:
    """Argparse type for --limit."""
    return _whole_number(text, MAX_LIMIT)


def _setup_logging(verbose: bool) -> None:
    """Sends diagnostics to stderr and keeps HTTP libraries quiet."""
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.WARNING,
        format="%(levelname)s: %(message)s",
    )
    # HTTP library debug logs can include request headers.
    for name in ("httpx", "httpcore", "httpx2", "openai", "anthropic"):
        logging.getLogger(name).setLevel(logging.WARNING)


HELP_EPILOG = """\
examples:
  monday-scaries pandas-dev/pandas
  monday-scaries https://github.com/pytorch/pytorch --days 1 --limit 10
  monday-scaries pandas-dev/pandas --new-only --provider openai

setup (keys are read from environment variables only):
  ANTHROPIC_API_KEY     key for --provider claude (the default)
  OPENAI_API_KEY        key for --provider openai
  GEMINI_API_KEY        key for --provider gemini (AI Studio key)
  GROQ_API_KEY          key for --provider groq
  OPENROUTER_API_KEY    key for --provider openrouter
  MONDAY_SCARIES_CUSTOM_BASE_URL   endpoint for --provider custom (Ollama)
  MONDAY_SCARIES_PROVIDER / MONDAY_SCARIES_MODEL   defaults for the flags
  GITHUB_TOKEN          optional: 5000 GitHub requests/hour instead of 60

  PowerShell:  $env:ANTHROPIC_API_KEY = (Get-Clipboard).Trim()
  bash/zsh:    export ANTHROPIC_API_KEY="..."

exit codes:
  0 ok   1 GitHub error   2 bad input   3 repo not found or private
  4 GitHub rate limit   5 LLM key/credits/model problem   6 partial results
"""


def build_parser() -> argparse.ArgumentParser:
    """Builds the argument parser."""
    parser = argparse.ArgumentParser(
        prog="monday-scaries",
        description="Triage a public GitHub repo's open issues with an LLM.",
        epilog=HELP_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("repo", help="owner/repo or a GitHub URL")
    parser.add_argument(
        "--days",
        type=_days,
        default=7,
        help="activity window in days (default: 7)",
    )
    parser.add_argument(
        "--new-only",
        action="store_true",
        help="only issues created within the window",
    )
    parser.add_argument(
        "--limit",
        type=_limit,
        default=50,
        help=f"max issues sent to the LLM, 1-{MAX_LIMIT} (default: 50)",
    )
    parser.add_argument(
        "--provider",
        choices=sorted(PROVIDERS),
        default=None,
        help="LLM provider (default: $MONDAY_SCARIES_PROVIDER or claude)",
    )
    parser.add_argument(
        "--model", default=None, help="model name (default: per provider)"
    )
    parser.add_argument(
        "--no-color", action="store_true", help="plain text output"
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="show debug logs"
    )
    return parser


def _ready_provider(args: argparse.Namespace) -> LLMProvider:
    """Builds the provider and checks its key with one tiny call.

    Raises:
        LLMError: If the key is missing, rejected, or out of credits.
        ValueError: If MONDAY_SCARIES_PROVIDER names no known provider.
    """
    provider = make_provider(
        args.provider or default_provider_name(), args.model
    )
    try:
        provider.complete("You are a health check.", "Reply with OK.", 1024)
    except LLMError as err:
        if err.fatal:
            raise
        logger.warning("Credential check was inconclusive: %s", err)
    return provider


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
    color = color_enabled(args.no_color)

    try:
        owner, repo = parse_repo(args.repo)
    except InvalidRepoError as err:
        logger.error("%s", err)
        return EXIT_USAGE

    # Check the LLM key before any other work.
    try:
        provider = _ready_provider(args)
    except (LLMError, ValueError) as err:
        logger.error("%s", err)
        return EXIT_LLM

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

    filtered = apply_filters(
        items, since, new_only=args.new_only, limit=args.limit
    )
    if not filtered.kept:
        print(f"No open issues in {owner}/{repo} matched the filters.")
        return EXIT_OK

    print(
        paint(f"{owner}/{repo}", (BOLD,), color)
        + f": classifying {len(filtered.kept)} of {filtered.in_window} open "
        f"issues updated in the last {args.days} days with "
        f"{provider.name}/{provider.model}\n"
    )
    run = classify_all(
        provider,
        filtered.kept,
        on_result=lambda result: print(format_result(result, color)),
    )
    if run.stopped:
        print(
            paint(
                f"\nWARNING: stopped early, results are partial. {run.stopped}"
                f" ({run.not_attempted} issues not classified)",
                (BOLD, YELLOW),
                color,
            )
        )
        return EXIT_PARTIAL
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
