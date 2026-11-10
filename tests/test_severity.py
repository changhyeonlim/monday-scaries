"""Tests for severity: the policy from NOTES.md, in code."""

from datetime import datetime, timedelta, timezone

import pytest

from monday_scaries.classify import Classified
from monday_scaries.github_client import Issue
from monday_scaries.severity import assess, sort_key
from monday_scaries.validate import Facts

NOW = datetime(2026, 11, 9, tzinfo=timezone.utc)


def case(labels=(), thumbs=0, age=1, number=1, unrated=False, **facts):
    """A classified issue with default facts that can be overridden."""
    values = {
        "kind": "bug",
        "area": "other",
        "impact": "crash",
        "workaround": "unknown",
        "scope": "unknown",
        "regression": "unknown",
        "summary": "s",
    }
    values.update(facts)
    issue = Issue(
        number,
        "t",
        "",
        list(labels),
        NOW - timedelta(days=age),
        NOW,
        "",
        thumbs_up=thumbs,
    )
    if unrated:
        return Classified(issue, None, "Timed out after 30s.")
    return Classified(issue, Facts(**values))


@pytest.mark.parametrize(
    "impact, level",
    [
        ("security", "critical"),
        ("data_loss", "critical"),
        ("wrong_results", "high"),
        ("crash", "high"),
        ("broken_feature", "high"),
        ("performance", "medium"),
        ("cosmetic", "low"),
        ("unclear", "?"),
    ],
)
def test_impact_sets_the_starting_level(impact, level):
    """The table in NOTES.md."""
    assert assess(case(impact=impact)).severity == level


def test_adjustments_move_one_level_and_name_the_facts():
    """Regression moves up; the reason lists the facts used."""
    result = assess(case(impact="performance", regression="yes", thumbs=12))
    assert result.severity == "high"  # capped at one level, not two
    assert result.reason == "performance, regression, 12 👍"


def test_workaround_and_edge_case_move_down_one_level_total():
    """Two downward facts still move only one level."""
    result = assess(case(impact="crash", workaround="yes", scope="edge_case"))
    assert result.severity == "medium"


def test_critical_never_moves_down():
    """A security bug with a workaround is still critical."""
    assert assess(case(impact="security", workaround="yes")).severity == (
        "critical"
    )


def test_unclear_ignores_adjustments():
    """No starting level, so 50 thumbs up cannot rank it."""
    assert assess(case(impact="unclear", thumbs=50)).severity == "?"


def test_features_get_no_severity():
    """Only bugs are rated, even if the LLM says crash."""
    result = assess(case(kind="feature"))
    assert result.severity is None and result.group == "feature"


def test_maintainer_label_beats_the_llm():
    """A P0 label on a cosmetic bug is critical."""
    result = assess(case(labels=["P0"], impact="cosmetic"))
    assert result.severity == "critical"
    assert result.reason == "maintainer label: P0"


def test_bug_label_beats_llm_kind():
    """The LLM says feature, the maintainers say bug."""
    assert assess(case(labels=["Bug"], kind="feature")).kind == "bug"


def test_priority_label_on_a_feature_is_not_a_severity():
    """P1 on a feature request means priority, not severity."""
    result = assess(case(labels=["enhancement", "P1"], kind="feature"))
    assert result.severity is None


def test_similar_label_is_not_a_severity_label():
    """'high memory' is not 'high priority'."""
    assert assess(case(labels=["high memory"], impact="cosmetic")).severity == (
        "low"
    )


def test_unrated_bug_goes_to_review_not_the_bottom():
    """Missing a critical costs more than a false alarm."""
    assert assess(case(labels=["bug"], unrated=True)).severity == "?"
    assert assess(case(unrated=True)).severity == "unrated"


def test_sort_order_and_oldest_first():
    """critical, high, ?, medium, low, unrated, features, rest."""
    items = [
        assess(case(number=1, kind="question")),
        assess(case(number=2, kind="feature")),
        assess(case(number=3, unrated=True)),
        assess(case(number=4, impact="cosmetic")),
        assess(case(number=5, impact="performance")),
        assess(case(number=6, impact="unclear")),
        assess(case(number=7, impact="crash", age=1)),
        assess(case(number=8, impact="crash", age=30)),
        assess(case(number=9, impact="security")),
    ]
    order = [a.result.issue.number for a in sorted(items, key=sort_key)]
    assert order == [9, 8, 7, 6, 5, 4, 3, 2, 1]
