"""Tests for config: provider choice and keys from env only."""

import pytest

from monday_scaries.config import make_provider
from monday_scaries.llm.anthropic import AnthropicProvider
from monday_scaries.llm.base import MissingKeyError

KEY = "sk-test-SECRET-123"


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    """Start every test with no provider settings."""
    for name in (
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
        "MONDAY_SCARIES_MODEL",
        "MONDAY_SCARIES_PROVIDER",
        "MONDAY_SCARIES_CUSTOM_BASE_URL",
        "MONDAY_SCARIES_CUSTOM_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)


@pytest.mark.parametrize("value", [None, "", "   "])
def test_missing_key_names_the_variable(monkeypatch, value):
    """Empty, blank, or unset key fails before any call."""
    if value is not None:
        monkeypatch.setenv("OPENAI_API_KEY", value)
    with pytest.raises(MissingKeyError, match="OPENAI_API_KEY"):
        make_provider("openai")


def test_missing_key_message_says_how_to_fix(monkeypatch):
    """The error explains how to set the key and offers a working provider."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", KEY)
    with pytest.raises(MissingKeyError) as info:
        make_provider("groq")
    message = str(info.value)
    assert "GROQ_API_KEY" in message
    assert "--provider claude" in message  # one that already has a key
    assert KEY not in message  # names only, never values


def test_unknown_provider_is_rejected():
    """A typo in the provider name is a clear error."""
    with pytest.raises(ValueError, match="choose one of"):
        make_provider("chatgpt")


def test_model_precedence(monkeypatch):
    """Flag beats env, env beats the default."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", KEY)
    assert make_provider("claude").model == "claude-haiku-5-5"
    monkeypatch.setenv("MONDAY_SCARIES_MODEL", "from-env")
    assert make_provider("claude").model == "from-env"
    assert make_provider("claude", "from-flag").model == "from-flag"
    assert isinstance(make_provider("claude"), AnthropicProvider)


def test_custom_needs_a_url_but_no_key(monkeypatch):
    """Ollama-style endpoints: URL required, key optional."""
    with pytest.raises(MissingKeyError, match="CUSTOM_BASE_URL"):
        make_provider("custom")
    monkeypatch.setenv(
        "MONDAY_SCARIES_CUSTOM_BASE_URL", "http://localhost:11434/v1"
    )
    assert make_provider("custom").name == "custom"
