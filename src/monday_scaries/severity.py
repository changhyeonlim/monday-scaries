"""Turns the LLM's facts and the maintainers' labels into a severity.

Starting policy (not validated; see NOTES.md):
1. Not a bug -> no severity.
2. A maintainer severity or priority label on a bug wins.
3. Otherwise the impact sets the starting level, and the facts move it
   by at most one level in total. Critical never moves down; an unclear
   impact stays "?" (needs review).
"""

from dataclasses import dataclass

from monday_scaries.classify import Classified
from monday_scaries.filters import has_label, normalize_label
from monday_scaries.github_client import Issue
from monday_scaries.validate import Facts

LEVELS = ("critical", "high", "medium", "low")
NEEDS_REVIEW = "?"
UNRATED = "unrated"
THUMBS_UP_BUMP = 10  # arbitrary; see NOTES.md

IMPACT_LEVEL = {
    "security": "critical",
    "data_loss": "critical",
    "wrong_results": "high",
    "crash": "high",
    "broken_feature": "high",
    "performance": "medium",
    "cosmetic": "low",
    "unclear": NEEDS_REVIEW,
}

# Maintainer labels, matched as whole words after normalize_label().
BUG_LABELS = frozenset({"bug", "regression", "crash", "defect"})
KIND_LABELS = {
    "feature": frozenset({"enhancement", "feature", "feature request"}),
    "question": frozenset({"question", "usage question"}),
    "docs": frozenset({"docs", "documentation"}),
}
# Severity labels must match the whole normalized label exactly, so a
# label like "high memory" is not read as "high".
SEVERITY_LABELS = {
    "critical": {"critical", "blocker", "p0", "security"},
    "high": {"high priority", "priority high", "severity high", "p1"},
    "medium": {"medium priority", "priority medium", "severity medium", "p2"},
    "low": {"low priority", "priority low", "severity low", "p3", "minor"},
}

# Sort order of the report's groups.
GROUP_ORDER = (
    "critical",
    "high",
    NEEDS_REVIEW,
    "medium",
    "low",
    UNRATED,
    "feature",
    "other",
)


@dataclass
class Assessment:
    """Everything the report shows about one issue.

    Attributes:
        result: The LLM result (facts may be None).
        kind: bug, feature, question, docs, other, or "unknown".
        severity: A level, "?" or "unrated" for bugs and unknowns; None
            for non-bugs.
        reason: The labels or facts that decided the severity.
    """

    result: Classified
    kind: str
    severity: str | None
    reason: str

    @property
    def group(self) -> str:
        """The report group: a severity, "feature", or "other"."""
        if self.severity is not None:
            return self.severity
        return "feature" if self.kind == "feature" else "other"

    @property
    def category(self) -> str:
        """installation, input-output, other, or "-" if unrated."""
        facts = self.result.facts
        return facts.area if facts else "-"


def _label_kind(issue: Issue) -> str | None:
    """Returns the kind the maintainers' labels say, if any."""
    if has_label(issue, BUG_LABELS):
        return "bug"
    for kind, terms in KIND_LABELS.items():
        if has_label(issue, terms):
            return kind
    return None


def _label_severity(labels: list[str]) -> tuple[str, str] | None:
    """Returns (level, label) for the first severity label found."""
    for label in labels:
        normalized = normalize_label(label)
        for level, names in SEVERITY_LABELS.items():
            if normalized in names:
                return level, label
    return None


def _from_facts(facts: Facts, thumbs_up: int) -> tuple[str, str]:
    """Starting level from impact, then at most one level of adjustment."""
    start = IMPACT_LEVEL[facts.impact]
    reasons = [facts.impact.replace("_", " ")]
    if start == NEEDS_REVIEW:
        return NEEDS_REVIEW, "impact unclear, needs review"

    up = down = 0
    if facts.regression == "yes":
        up, reasons = up + 1, [*reasons, "regression"]
    if thumbs_up >= THUMBS_UP_BUMP:
        up, reasons = up + 1, [*reasons, f"{thumbs_up} 👍"]
    if facts.workaround == "yes":
        down, reasons = down + 1, [*reasons, "workaround"]
    if facts.scope == "edge_case":
        down, reasons = down + 1, [*reasons, "edge case"]

    shift = max(-1, min(1, up - down))  # net move capped at one level
    if start == "critical" and shift < 0:
        shift = 0  # critical never moves down
    index = max(0, min(len(LEVELS) - 1, LEVELS.index(start) - shift))
    return LEVELS[index], ", ".join(reasons)


def assess(result: Classified) -> Assessment:
    """Decides kind, severity, and reason for one classified issue.

    Args:
        result: The LLM result for one issue.

    Returns:
        The assessment shown in the report.
    """
    issue, facts = result.issue, result.facts
    kind = _label_kind(issue) or (facts.kind if facts else None)

    if kind is None:  # no usable LLM output and no kind label
        return Assessment(result, "unknown", UNRATED, result.error)
    if kind != "bug":
        return Assessment(result, kind, None, kind)

    labelled = _label_severity(issue.labels)
    if labelled:
        level, label = labelled
        return Assessment(result, kind, level, f"maintainer label: {label}")
    if facts is None:
        # A labelled bug we could not read: missing a critical costs more
        # than a false alarm, so it goes to review, not the bottom.
        return Assessment(
            result, kind, NEEDS_REVIEW, f"bug label, {result.error}"
        )
    level, reason = _from_facts(facts, issue.thumbs_up)
    return Assessment(result, kind, level, reason)


def sort_key(assessment: Assessment) -> tuple[int, float]:
    """Group order first, then oldest first within a group."""
    created = assessment.result.issue.created_at.timestamp()
    return GROUP_ORDER.index(assessment.group), created
