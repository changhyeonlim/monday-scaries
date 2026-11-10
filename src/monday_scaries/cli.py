"""Command-line entry point for Monday Scaries."""

import argparse


def main(argv: list[str] | None = None) -> int:
    """Parses arguments and runs the triage pipeline.

    Args:
        argv: Command-line arguments without the program name. If None,
            argparse reads sys.argv.

    Returns:
        The process exit code. 0 means success.
    """
    parser = argparse.ArgumentParser(
        prog="monday-scaries",
        description="Triage a public GitHub repo's open issues with an LLM.",
    )
    parser.add_argument("repo", help="owner/repo or a GitHub URL")
    parser.parse_args(argv)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
