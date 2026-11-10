"""Checks the LLM's reply. LLM output is untrusted.

Only the known fields are kept, and each must have an allowed value.
Anything else in the reply is dropped, so even a successful prompt
injection can only pick values from these lists.
"""

import json
import re
from dataclasses import dataclass

ALLOWED = {
    "kind": ("bug", "feature", "question", "docs", "other"),
    "area": ("installation", "input-output", "other"),
    "impact": (
        "security",
        "data_loss",
        "wrong_results",
        "crash",
        "broken_feature",
        "performance",
        "cosmetic",
        "unclear",
    ),
    "workaround": ("yes", "no", "unknown"),
    "scope": ("edge_case", "common", "unknown"),
    "regression": ("yes", "no", "unknown"),
}
MAX_SUMMARY_CHARS = 200

_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)


class InvalidOutputError(ValueError):
    """The reply is not valid JSON or breaks the allowed values."""


@dataclass(frozen=True)
class Facts:
    """Facts the LLM reported about one issue. Severity is not here.

    Attributes:
        kind: bug, feature, question, docs, or other.
        area: installation, input-output, or other.
        impact: What goes wrong, e.g. crash or wrong_results.
        workaround: yes, no, or unknown.
        scope: edge_case, common, or unknown.
        regression: yes, no, or unknown.
        summary: One sentence, at most MAX_SUMMARY_CHARS characters.
    """

    kind: str
    area: str
    impact: str
    workaround: str
    scope: str
    regression: str
    summary: str


def parse_facts(text: str) -> Facts:
    """Parses and checks one LLM reply.

    Args:
        text: The raw reply. A ```json fence around it is allowed.

    Returns:
        The checked facts.

    Raises:
        InvalidOutputError: If anything is missing, extra-typed, or not
            an allowed value. The message never echoes the reply.
    """
    fence = _FENCE_RE.fullmatch(text.strip())
    if fence:
        text = fence.group(1)
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        raise InvalidOutputError("reply is not valid JSON") from None
    if not isinstance(data, dict):
        raise InvalidOutputError("reply is not a JSON object")

    values: dict[str, str] = {}
    for field, allowed in ALLOWED.items():
        value = data.get(field)
        value = value.strip().lower() if isinstance(value, str) else None
        if value not in allowed:
            raise InvalidOutputError(
                f"{field} must be one of: {', '.join(allowed)}"
            )
        values[field] = value

    summary = data.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        raise InvalidOutputError("summary is missing")
    summary = " ".join(summary.split())[:MAX_SUMMARY_CHARS]
    return Facts(summary=summary, **values)
