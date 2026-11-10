"""Terminal output: colors and one-line views of LLM results."""

import os
import sys

from monday_scaries.classify import Classified

RESET = "\033[0m"
BOLD = "1"
DIM = "2"
RED = "31"
GREEN = "32"
YELLOW = "33"
BLUE = "34"
MAGENTA = "35"
CYAN = "36"

# Worst impacts are loudest. Matches the starting levels in NOTES.md.
IMPACT_STYLE = {
    "security": (BOLD, RED),
    "data_loss": (BOLD, RED),
    "wrong_results": (RED,),
    "crash": (RED,),
    "broken_feature": (RED,),
    "performance": (YELLOW,),
    "cosmetic": (GREEN,),
    "unclear": (MAGENTA,),
}
KIND_STYLE = {
    "bug": (BOLD,),
    "feature": (CYAN,),
    "question": (BLUE,),
    "docs": (BLUE,),
    "other": (DIM,),
}


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


def format_result(result: Classified, color: bool) -> str:
    """Formats one LLM result as two lines: facts, then the summary.

    Args:
        result: The classified (or unrated) issue.
        color: Whether to add ANSI colors.

    Returns:
        The text to print.
    """
    number = paint(f"#{result.issue.number:<7}", (BOLD,), color)
    if result.facts is None:
        line = paint("UNRATED", (DIM,), color)
        title = result.issue.title[:70]
        reason = paint(result.error, (DIM,), color)
        return f"{number} {line}  {title}\n          {reason}"

    facts = result.facts
    kind = paint(f"{facts.kind.upper():<8}", KIND_STYLE[facts.kind], color)
    impact = paint(f"{facts.impact:<14}", IMPACT_STYLE[facts.impact], color)
    extras = []
    if facts.regression == "yes":
        extras.append(paint("regression", (RED,), color))
    if facts.workaround == "yes":
        extras.append(paint("workaround", (GREEN,), color))
    if facts.scope == "edge_case":
        extras.append(paint("edge case", (DIM,), color))
    if result.issue.thumbs_up:
        extras.append(f"{result.issue.thumbs_up} 👍")
    details = f"{kind} {impact} {facts.area:<12} {', '.join(extras)}"
    return f"{number} {details.rstrip()}\n          {facts.summary}"
