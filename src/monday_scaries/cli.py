"""Command-line entry point for Monday Scaries."""

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timedelta, timezone

from monday_scaries.classify import classify_all
from monday_scaries.config import (
    KEY_FILE,
    PROVIDERS,
    default_provider_name,
    key_status,
    load_key_file,
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
    ReportData,
    color_enabled,
    format_progress,
    render_json,
    render_text,
)
from monday_scaries.severity import assess, sort_key

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

setup (environment variables, or the same NAME=value lines saved once in
~/.monday_scaries/.env; the environment wins; a project .env is never read):
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
  check:       monday-scaries --show-keys

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
    parser.add_argument("repo", nargs="?", help="owner/repo or a GitHub URL")
    parser.add_argument(
        "--show-keys",
        action="store_true",
        help="list which keys are set and where from (never the values)",
    )
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
        "--top",
        type=_limit,
        default=10,
        help="rows in the report table (default: 10)",
    )
    parser.add_argument(
        "--json", action="store_true", help="print the report as JSON"
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
    parser = build_parser()
    args = parser.parse_args(argv)
    _setup_logging(args.verbose)
    color = color_enabled(args.no_color)
    from_file = load_key_file(KEY_FILE)

    if args.show_keys:
        print(f"Keys (environment wins over {KEY_FILE}):")
        print("\n".join(key_status(from_file)))
        return EXIT_OK
    if not args.repo:
        parser.error("the following arguments are required: repo")

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
        message = f"No open issues in {owner}/{repo} matched the filters."
        if args.json:
            print(json.dumps({"repo": f"{owner}/{repo}", "message": message}))
        else:
            print(message)
        return EXIT_OK

    # Progress goes to stderr so stdout holds only the report.
    total = len(filtered.kept)
    print(
        f"Classifying {total} issues with {provider.name}/{provider.model}...",
        file=sys.stderr,
    )
    done = iter(range(1, total + 1))
    run = classify_all(
        provider,
        filtered.kept,
        on_result=lambda result: print(
            format_progress(next(done), total, result), file=sys.stderr
        ),
    )

    assessments = sorted((assess(r) for r in run.results), key=sort_key)
    data = ReportData(
        repo=f"{owner}/{repo}",
        days=args.days,
        new_only=args.new_only,
        since=since,
        now=datetime.now(timezone.utc),
        in_window=filtered.in_window,
        new=filtered.new,
        assessments=assessments,
        top=args.top,
        provider=provider.name,
        model=provider.model,
        tokens=provider.tokens_used,
        stopped=run.stopped,
        not_attempted=run.not_attempted,
    )
    print(render_json(data) if args.json else render_text(data, color))
    return EXIT_PARTIAL if run.stopped else EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
