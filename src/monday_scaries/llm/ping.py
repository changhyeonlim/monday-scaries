"""Checks that a provider and key work: python -m monday_scaries.llm.ping."""

import os
import sys

from monday_scaries.llm.anthropic import AnthropicProvider
from monday_scaries.llm.base import LLMError, LLMProvider, MissingKeyError
from monday_scaries.llm.gemini import GeminiProvider
from monday_scaries.llm.openai_compat import OpenAICompatProvider

KEY_ENV = {
    "openai": "OPENAI_API_KEY",
    "claude": "ANTHROPIC_API_KEY",
    "gemini": "GEMINI_API_KEY",
}

# OpenAI-compatible endpoints. None means OpenAI itself.
BASE_URLS = {
    "openai": None,
}


def _make(provider: str, model: str) -> LLMProvider:
    """Builds a provider from its name and its key's env var."""
    key = os.environ.get(KEY_ENV[provider], "").strip()
    if not key:
        raise MissingKeyError(f"{KEY_ENV[provider]} is not set.")
    if provider == "claude":
        return AnthropicProvider(key, model)
    if provider == "gemini":
        return GeminiProvider(key, model)
    return OpenAICompatProvider(
        key, model, base_url=BASE_URLS[provider], name=provider
    )


def main() -> int:
    """Asks the model to reply with OK and prints the answer."""
    if len(sys.argv) != 3 or sys.argv[1] not in KEY_ENV:
        names = "|".join(KEY_ENV)
        print(f"usage: python -m monday_scaries.llm.ping {names} MODEL")
        return 2
    provider, model = sys.argv[1], sys.argv[2]
    try:
        reply = _make(provider, model).complete(
            "You are a health check.", "Reply with OK.", max_tokens=1024
        )
    except LLMError as err:
        print(f"{provider} failed: {err}", file=sys.stderr)
        return 1
    print(f"{provider}/{model}: {reply!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
