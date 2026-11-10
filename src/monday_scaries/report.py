"""The triage report: colored cards for people, JSON for scripts."""

import json
import os
import shutil
import sys
import textwrap
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
GROUP_TITLE = {
    "critical": "CRITICAL",
    "high": "HIGH",
    "?": "? NEEDS REVIEW",
    "medium": "MEDIUM",
    "low": "LOW",
    "unrated": "UNRATED",
    "feature": "FEATURE REQUESTS",
    "other": "QUESTIONS, DOCS, OTHER",
}
# Severity levels always shown in the summary line, even when zero.
ALWAYS_COUNTED = ("critical", "high", "?", "medium", "low")
CATEGORIES = ("installation", "input-output", "other")
INDENT = " " * 9  # card lines start under the title
MIN_WIDTH, MAX_WIDTH = 60, 100


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
        top: How many issues the report shows.
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


def _width() -> int:
    """Report width: the terminal width, kept between 60 and 100."""
    columns = shutil.get_terminal_size((MAX_WIDTH, 24)).columns
    return max(MIN_WIDTH, min(MAX_WIDTH, columns))


def _card(
    assessment: Assessment, data: ReportData, width: int, color: bool
) -> list[str]:
    """One issue as a short card: title, facts, why, summary."""
    issue, facts = assessment.result.issue, assessment.result.facts
    style = SEVERITY_STYLE.get(assessment.group, ())
    new = _is_new(assessment, data.since)

    title_room = width - len(INDENT) - (5 if new else 0)
    title = issue.title
    if len(title) > title_room:
        title = title[: title_room - 1] + "…"
    number = paint(f" #{issue.number:<6}", (BOLD,), color)
    first = f"{number}  {title:<{title_room}}"
    if new:
        first += " " + paint("NEW", (BOLD, GREEN), color)

    days = _days_open(assessment, data.now)
    meta = (
        f"{assessment.category} · opened {issue.created_at:%b %d} · "
        f"{days} day{'s' if days != 1 else ''}"
    )
    lines = [
        first.rstrip(),
        paint(INDENT + meta, (DIM,), color),
        paint(INDENT + assessment.reason, style, color),
    ]
    if facts:
        lines += textwrap.wrap(
            facts.summary,
            width=width,
            initial_indent=INDENT,
            subsequent_indent=INDENT,
        )
    return lines


def render_text(data: ReportData, color: bool) -> str:
    """Builds the human-readable report as grouped cards.

    Args:
        data: The run's results.
        color: Whether to add ANSI colors.

    Returns:
        The report text.
    """
    width = _width()
    window = "created" if data.new_only else "active"
    lines = [
        paint(data.repo, (BOLD,), color)
        + f" · issues {window} in the last {data.days} days "
        f"(since {data.since:%Y-%m-%d})",
        f"Classified {len(data.assessments)} of {data.in_window} open "
        f"issues · {data.new} new since the baseline",
    ]
    if data.stopped:
        lines.append(_partial_warning(data, color))

    groups, categories = _counts(data)
    chips = [
        paint(
            f"{GROUP_TITLE[name].split()[0]} {groups[name]}",
            SEVERITY_STYLE[name],
            color,
        )
        for name in GROUP_ORDER
        if name in ALWAYS_COUNTED or groups[name]
    ]
    lines += [
        "",
        " " + "   ".join(chips),
        " "
        + " · ".join(f"{c} {categories[c]}" for c in CATEGORIES)
        + (f" · unrated {categories['-']}" if categories["-"] else ""),
    ]

    shown = data.assessments[: data.top]
    for name in GROUP_ORDER:
        members = [a for a in shown if a.group == name]
        if not members:
            continue
        heading = f"━━ {GROUP_TITLE[name]} ({len(members)}) "
        lines += [
            "",
            paint(heading.ljust(width, "━"), SEVERITY_STYLE[name], color),
        ]
        for index, assessment in enumerate(members):
            if index:
                lines.append("")
            lines += _card(assessment, data, width, color)

    if len(data.assessments) > len(shown):
        hidden = len(data.assessments) - len(shown)
        lines += ["", paint(f" … {hidden} more (use --top)", (DIM,), color)]

    unrated = [a for a in data.assessments if a.result.facts is None]
    footer = "Unclassified: " + (
        ", ".join(
            f"#{a.result.issue.number} ({a.result.error})" for a in unrated
        )
        or "none"
    )
    lines += ["", footer]
    if data.not_attempted:
        lines.append(f"Not attempted: {data.not_attempted} (run stopped)")
    lines.append(f"Tokens: {data.tokens:,} · {data.provider}/{data.model}")
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
