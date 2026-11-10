"""The triage report: colored text for people, JSON for scripts."""

import json
import os
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import datetime

from monday_scaries.classify import Classified
from monday_scaries.severity import GROUP_ORDER, Assessment

RESET = "\033[0m"
BOLD = "1"
DIM = "2"
RED = "31"
GREEN = "32"
YELLOW = "33"
BLUE = "34"
MAGENTA = "35"
CYAN = "36"

# Worst first, loudest first.
SEVERITY_STYLE = {
    "critical": (BOLD, RED),
    "high": (RED,),
    "?": (BOLD, MAGENTA),
    "medium": (YELLOW,),
    "low": (GREEN,),
    "unrated": (DIM,),
    "feature": (CYAN,),
    "other": (BLUE,),
}
CATEGORIES = ("installation", "input-output", "other")
TITLE_WIDTH = 44


@dataclass
class ReportData:
    """Everything one run produced, for the text and JSON reports.

    Attributes:
        repo: "owner/repo".
        days: Size of the activity window.
        new_only: Whether --new-only was used.
        since: Start of the window (the "new" baseline).
        now: When the report was made.
        in_window: Open issues active in the window (before --limit).
        new: Of those, issues created in the window.
        assessments: One per classified or unrated issue, sorted.
        top: How many rows the table shows.
        provider: Provider name.
        model: Model name.
        tokens: Tokens used across all LLM calls.
        stopped: Why the run stopped early, or "".
        not_attempted: Issues skipped because the run stopped.
    """

    repo: str
    days: int
    new_only: bool
    since: datetime
    now: datetime
    in_window: int
    new: int
    assessments: list[Assessment]
    top: int
    provider: str
    model: str
    tokens: int
    stopped: str = ""
    not_attempted: int = 0


def _enable_windows_ansi() -> bool:
    """Turns on ANSI color handling in a Windows console, if possible."""
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)  # stdout
        mode = ctypes.c_uint32()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return True  # not a classic console (e.g. a pty): assume ANSI
        return bool(kernel32.SetConsoleMode(handle, mode.value | 0x0004))
    except (AttributeError, OSError):
        return False


def color_enabled(no_color_flag: bool) -> bool:
    """Decides whether to print colors.

    Colors are off for --no-color, the NO_COLOR convention, or when
    output is piped to a file or another program.

    Args:
        no_color_flag: True if --no-color was given.

    Returns:
        True if ANSI colors should be printed.
    """
    if no_color_flag or os.environ.get("NO_COLOR") or not sys.stdout.isatty():
        return False
    if os.name == "nt":
        return _enable_windows_ansi()
    return True


def paint(text: str, codes: tuple[str, ...], color: bool) -> str:
    """Wraps text in ANSI codes when color is on."""
    if not color or not codes:
        return text
    return f"\033[{';'.join(codes)}m{text}{RESET}"


def format_progress(index: int, total: int, result: Classified) -> str:
    """One short progress line, printed to stderr while the LLM works."""
    if result.facts is None:
        status = f"unrated ({result.error})"
    else:
        status = f"{result.facts.kind}, {result.facts.impact}"
    return f"  [{index}/{total}] #{result.issue.number} {status}"


def _label(assessment: Assessment) -> str:
    """The text in the severity column: a level or the kind."""
    if assessment.severity is not None:
        return assessment.severity
    return assessment.kind


def _days_open(assessment: Assessment, now: datetime) -> int:
    """Whole days since the issue was opened."""
    return (now - assessment.result.issue.created_at).days


def _is_new(assessment: Assessment, since: datetime) -> bool:
    """True if the issue was created inside the window."""
    return assessment.result.issue.created_at >= since


def _counts(data: ReportData) -> tuple[Counter, Counter]:
    """Counts by report group and by category."""
    groups = Counter(a.group for a in data.assessments)
    categories = Counter(a.category for a in data.assessments)
    return groups, categories


def render_text(data: ReportData, color: bool) -> str:
    """Builds the human-readable report.

    Args:
        data: The run's results.
        color: Whether to add ANSI colors.

    Returns:
        The report text.
    """
    lines: list[str] = []
    classified = len(data.assessments)
    window = "created" if data.new_only else "active"
    lines.append(paint(data.repo, (BOLD,), color))
    lines.append(
        f"Baseline: issues {window} in the last {data.days} days "
        f"(since {data.since:%Y-%m-%d})"
    )
    lines.append(
        f"Classified {classified} of {data.in_window} open issues; "
        f"{data.new} new since the baseline"
    )
    if data.stopped:
        lines.append(_partial_warning(data, color))

    groups, categories = _counts(data)
    severity_parts = [
        paint(f"{name} {groups[name]}", SEVERITY_STYLE[name], color)
        for name in GROUP_ORDER
        if groups[name]
    ]
    lines.append("")
    lines.append("By severity: " + (" · ".join(severity_parts) or "none"))
    lines.append(
        "By category: "
        + " · ".join(f"{c} {categories[c]}" for c in CATEGORIES)
        + (f" · unrated {categories['-']}" if categories["-"] else "")
    )

    rows = data.assessments[: data.top]
    lines.append("")
    lines.append(paint(f"Top {len(rows)}", (BOLD,), color))
    header = (
        f"  {'#':<7} {'Title':<{TITLE_WIDTH}} {'Severity':<9} "
        f"{'Category':<13} {'Opened':<10} {'Days':>5}  New"
    )
    lines.append(paint(header, (DIM,), color))
    for assessment in rows:
        issue = assessment.result.issue
        title = issue.title
        if len(title) > TITLE_WIDTH:
            title = title[: TITLE_WIDTH - 1] + "…"
        label = _label(assessment)
        severity = paint(
            f"{label:<9}", SEVERITY_STYLE.get(assessment.group, ()), color
        )
        new = (
            paint("NEW", (BOLD,), color)
            if _is_new(assessment, data.since)
            else ""
        )
        lines.append(
            f"  {issue.number:<7} {title:<{TITLE_WIDTH}} {severity} "
            f"{assessment.category:<13} {issue.created_at:%Y-%m-%d} "
            f"{_days_open(assessment, data.now):>5}  {new}".rstrip()
        )
        summary = (
            assessment.result.facts.summary if assessment.result.facts else ""
        )
        why = f"why: {assessment.reason}"
        lines.append(
            paint(
                f"          {why}" + (f" — {summary}" if summary else ""),
                (DIM,),
                color,
            )
        )

    unrated = [a for a in data.assessments if a.result.facts is None]
    lines.append("")
    if unrated:
        listed = ", ".join(
            f"#{a.result.issue.number} ({a.result.error})" for a in unrated
        )
        lines.append(f"Unclassified: {listed}")
    else:
        lines.append("Unclassified: none")
    if data.not_attempted:
        lines.append(f"Not attempted: {data.not_attempted} (run stopped)")
    lines.append(
        f"Tokens: {data.tokens:,} · Model: {data.provider}/{data.model}"
    )
    if data.stopped:
        lines.append(_partial_warning(data, color))
    return "\n".join(lines)


def _partial_warning(data: ReportData, color: bool) -> str:
    """The warning shown when the LLM failed partway."""
    return paint(
        f"WARNING: results are partial. {data.stopped} "
        f"({data.not_attempted} issues not classified)",
        (BOLD, YELLOW),
        color,
    )


def render_json(data: ReportData) -> str:
    """Builds the same report as JSON.

    Args:
        data: The run's results.

    Returns:
        A JSON document (pretty-printed).
    """
    groups, categories = _counts(data)
    issues = []
    for assessment in data.assessments[: data.top]:
        issue, facts = assessment.result.issue, assessment.result.facts
        issues.append(
            {
                "number": issue.number,
                "title": issue.title,
                "url": issue.url,
                "severity": assessment.severity,
                "kind": assessment.kind,
                "category": facts.area if facts else None,
                "opened": issue.created_at.date().isoformat(),
                "days_open": _days_open(assessment, data.now),
                "new_since_baseline": _is_new(assessment, data.since),
                "reason": assessment.reason,
                "summary": facts.summary if facts else None,
                "labels": issue.labels,
            }
        )
    document = {
        "repo": data.repo,
        "baseline": {
            "type": "created_in_window"
            if data.new_only
            else "active_in_window",
            "days": data.days,
            "since": data.since.isoformat(),
        },
        "classified": len(data.assessments),
        "open_issues_in_window": data.in_window,
        "new_since_baseline": data.new,
        "partial": bool(data.stopped),
        "stopped_reason": data.stopped or None,
        "not_attempted": data.not_attempted,
        "counts": {
            "severity": {name: groups[name] for name in GROUP_ORDER},
            "category": {c: categories[c] for c in CATEGORIES},
        },
        "top": issues,
        "unclassified": [
            {"number": a.result.issue.number, "error": a.result.error}
            for a in data.assessments
            if a.result.facts is None
        ],
        "tokens": data.tokens,
        "provider": data.provider,
        "model": data.model,
    }
    return json.dumps(document, indent=2, ensure_ascii=False)
