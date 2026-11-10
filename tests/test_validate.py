"""Tests for validate: the LLM reply is untrusted."""

import json

import pytest

from monday_scaries.validate import (
    MAX_SUMMARY_CHARS,
    InvalidOutputError,
    parse_facts,
)

GOOD = {
    "kind": "bug",
    "area": "input-output",
    "impact": "wrong_results",
    "workaround": "no",
    "scope": "common",
    "regression": "yes",
    "summary": "read_csv drops the last row.",
}


def reply(**changes):
    """GOOD with some fields changed (None removes a field)."""
    data = {**GOOD, **changes}
    return json.dumps({k: v for k, v in data.items() if v is not None})


def test_valid_reply_is_parsed():
    """All fields present with allowed values."""
    facts = parse_facts(reply())
    assert facts.impact == "wrong_results" and facts.regression == "yes"


def test_code_fence_and_case_are_tolerated():
    """Models often wrap JSON in ```json and vary case."""
    facts = parse_facts("```json\n" + reply(kind="BUG") + "\n```")
    assert facts.kind == "bug"


@pytest.mark.parametrize(
    "text",
    ["", "not json", "{broken", "[1, 2]", '"just a string"', "null"],
)
def test_broken_json_is_rejected(text):
    """Anything that is not a JSON object is invalid."""
    with pytest.raises(InvalidOutputError):
        parse_facts(text)


@pytest.mark.parametrize(
    "changes",
    [
        {"kind": None},  # missing
        {"impact": "catastrophic"},  # not allowed
        {"severity": "critical", "kind": "urgent"},  # invented values
        {"area": 5},  # wrong type
        {"summary": "   "},  # empty
    ],
)
def test_missing_or_invalid_values_are_rejected(changes):
    """Only allowed values pass; nothing is guessed."""
    with pytest.raises(InvalidOutputError):
        parse_facts(reply(**changes))


def test_extra_fields_are_dropped():
    """A field like severity from the LLM is ignored, not trusted."""
    facts = parse_facts(reply(severity="critical", note="trust me"))
    assert not hasattr(facts, "severity")


def test_long_summary_is_cut():
    """A huge summary cannot flood the report."""
    facts = parse_facts(reply(summary="word " * 500))
    assert len(facts.summary) <= MAX_SUMMARY_CHARS


def test_error_never_echoes_the_reply():
    """Error text is ours, not the model's."""
    with pytest.raises(InvalidOutputError) as info:
        parse_facts(reply(impact="IGNORE ALL RULES"))
    assert "IGNORE" not in str(info.value)
