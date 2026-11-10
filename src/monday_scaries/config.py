"""Chooses the LLM provider, its key, and its model.

Keys come only from environment variables, never from a file in the
project folder. Precedence: --provider/--model flags, then
MONDAY_SCARIES_PROVIDER/MONDAY_SCARIES_MODEL, then the defaults below.
"""

import logging
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path

from monday_scaries.llm.base import LLMProvider, MissingKeyError

logger = logging.getLogger(__name__)

# The only key file the tool reads. Never a .env in the project folder.
KEY_FILE = Path.home() / ".monday_scaries" / ".env"

# Provider SDKs are imported inside make_provider(): the anthropic and
# openai packages are large and slow to import, so --help, bad input,
# and a missing key answer instantly, and only the chosen SDK is loaded.

DEFAULT_PROVIDER = "claude"
CUSTOM_URL_ENV = "MONDAY_SCARIES_CUSTOM_BASE_URL"
CUSTOM_KEY_ENV = "MONDAY_SCARIES_CUSTOM_API_KEY"


@dataclass(frozen=True)
class ProviderSpec:
    """How to reach one provider.

    Attributes:
        key_env: Environment variable that holds the API key.
        default_model: Model used when none is given.
        base_url: OpenAI-compatible endpoint; None means OpenAI itself.
    """

    key_env: str
    default_model: str
    base_url: str | None = None


PROVIDERS = {
    "claude": ProviderSpec("ANTHROPIC_API_KEY", "claude-haiku-5-5"),
    "openai": ProviderSpec("OPENAI_API_KEY", "gpt-4o-mini"),
    # Native endpoint: the OpenAI-compatible one rejects AQ. keys.
    "gemini": ProviderSpec("GEMINI_API_KEY", "gemini-flash-latest"),
    "groq": ProviderSpec(
        "GROQ_API_KEY",
        "llama-3.3-70b-versatile",
        "https://api.groq.com/openai/v1",
    ),
    "openrouter": ProviderSpec(
        "OPENROUTER_API_KEY",
        "openai/gpt-4o-mini",
        "https://openrouter.ai/api/v1",
    ),
    # Self-hosted, e.g. Ollama. URL from CUSTOM_URL_ENV; key optional.
    "custom": ProviderSpec(CUSTOM_KEY_ENV, "llama3.1"),
}


def allowed_variables() -> frozenset[str]:
    """Variable names the key file may set (the names in .env.example).

    Anything else in the file is ignored, so it cannot change PATH or
    point an SDK at another server (e.g. ANTHROPIC_BASE_URL).
    """
    names = {spec.key_env for spec in PROVIDERS.values()}
    names |= {
        "GITHUB_TOKEN",
        "MONDAY_SCARIES_PROVIDER",
        "MONDAY_SCARIES_MODEL",
        CUSTOM_URL_ENV,
    }
    return frozenset(names)


def parse_key_file(text: str) -> dict[str, str]:
    """Parses KEY=value lines. Never logs a value.

    Blank lines, comments, empty values, unknown names, and malformed
    lines are skipped. "export " prefixes and surrounding quotes are
    allowed.

    Args:
        text: The file's contents.

    Returns:
        The allowed, non-empty values by name.
    """
    allowed = allowed_variables()
    values: dict[str, str] = {}
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        line = line.removeprefix("export ").strip()
        name, sep, value = line.partition("=")
        name, value = name.strip(), value.strip().strip("\"'")
        if not sep or not name:
            logger.warning(
                "Key file line %d is not NAME=value; skipped.", number
            )
            continue
        if name not in allowed:
            if not re.fullmatch(r"[A-Z0-9_]{1,40}", name):
                name = "that name"  # might be a pasted key: don't echo it
            logger.warning(
                "Key file line %d: %s is not used; skipped.", number, name
            )
            continue
        if value:
            values[name] = value
    return values


def load_key_file(path: Path = KEY_FILE) -> dict[str, str]:
    """Fills in env variables from the key file in the home folder.

    A variable that is already set in the environment always wins. A
    missing file is fine: the environment alone is enough.

    Args:
        path: The key file. Defaults to ~/.monday_scaries/.env.

    Returns:
        The names (not values) that came from the file, mapped to "file".
    """
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    except OSError as err:
        logger.warning("Could not read %s (%s).", path, type(err).__name__)
        return {}
    if os.name != "nt" and path.stat().st_mode & (stat.S_IRWXG | stat.S_IRWXO):
        logger.warning(
            "%s can be read by other users; run: chmod 600 %s", path, path
        )
    loaded = {}
    for name, value in parse_key_file(text).items():
        if not os.environ.get(name, "").strip():
            os.environ[name] = value
            loaded[name] = "file"
    return loaded


def key_status(from_file: dict[str, str]) -> list[str]:
    """One line per key: where it came from, or missing. Never values."""
    lines = []
    names = [(n, s.key_env) for n, s in PROVIDERS.items() if n != "custom"]
    names += [("custom", CUSTOM_URL_ENV), ("github", "GITHUB_TOKEN")]
    for label, variable in names:
        if not os.environ.get(variable, "").strip():
            where = "missing"
        elif from_file.get(variable):
            where = f"set (from {KEY_FILE})"
        else:
            where = "set (environment)"
        lines.append(f"  {label:<11} {variable:<31} {where}")
    return lines


def providers_with_keys() -> list[str]:
    """Names of providers whose key (or custom URL) is set. No values."""
    names = [
        name
        for name, spec in PROVIDERS.items()
        if name != "custom" and os.environ.get(spec.key_env, "").strip()
    ]
    if os.environ.get(CUSTOM_URL_ENV, "").strip():
        names.append("custom")
    return names


def set_env_hint(variable: str) -> str:
    """Shows how to set an env var in the user's shell, without a value."""
    if os.name == "nt":
        return f"$env:{variable} = (Get-Clipboard).Trim()   # PowerShell"
    return f'export {variable}="..."'


def missing_key_message(name: str, variable: str) -> str:
    """Explains a missing key and how to fix it. Never shows a value.

    Args:
        name: The provider that was asked for.
        variable: The environment variable that is empty.

    Returns:
        A multi-line message for the user.
    """
    others = [p for p in providers_with_keys() if p != name]
    lines = [
        f"{variable} is not set, so the {name} provider cannot be used.",
        "  Copy your key, then set it in this terminal:",
        f"    {set_env_hint(variable)}",
        f"  Or save it once in {KEY_FILE} as {variable}=... "
        "(same format as .env.example).",
        "  A .env file in the project folder is never read.",
    ]
    if others:
        lines.append(
            f"  Or use a provider that already has a key: "
            f"--provider {others[0]}"
            + (f" (also set: {', '.join(others[1:])})" if others[1:] else "")
        )
    else:
        lines.append(f"  Other providers: {', '.join(PROVIDERS)}")
    return "\n".join(lines)


def default_provider_name() -> str:
    """Returns MONDAY_SCARIES_PROVIDER if set, else DEFAULT_PROVIDER."""
    return os.environ.get("MONDAY_SCARIES_PROVIDER", "").strip().lower() or (
        DEFAULT_PROVIDER
    )


def make_provider(name: str, model: str | None = None) -> LLMProvider:
    """Builds a provider from its name, reading the key from the env.

    Args:
        name: A key of PROVIDERS.
        model: Model name. None means MONDAY_SCARIES_MODEL or the
            provider's default.

    Returns:
        A ready provider. No API call has been made yet.

    Raises:
        ValueError: If name is not a known provider.
        MissingKeyError: If the key (or the custom URL) is not set.
    """
    if name not in PROVIDERS:
        raise ValueError(
            f"Unknown provider {name!r} (from MONDAY_SCARIES_PROVIDER or "
            f"--provider); choose one of {', '.join(PROVIDERS)}."
        )
    spec = PROVIDERS[name]
    model = model or os.environ.get("MONDAY_SCARIES_MODEL", "").strip()
    model = model or spec.default_model
    key = os.environ.get(spec.key_env, "").strip()

    if name == "custom":
        base_url = os.environ.get(CUSTOM_URL_ENV, "").strip()
        if not base_url:
            raise MissingKeyError(
                f"{CUSTOM_URL_ENV} is not set, so the custom provider has "
                "no endpoint.\n  Example for a local Ollama server:\n"
                f"    {set_env_hint(CUSTOM_URL_ENV)}\n"
                "    (value: http://localhost:11434/v1)"
            )
        from monday_scaries.llm.openai_compat import OpenAICompatProvider

        # Ollama needs no key, but the SDK requires a non-empty string.
        return OpenAICompatProvider(key or "none", model, base_url, name)
    if not key:
        raise MissingKeyError(missing_key_message(name, spec.key_env))
    if name == "claude":
        from monday_scaries.llm.anthropic import AnthropicProvider

        return AnthropicProvider(key, model)
    if name == "gemini":
        from monday_scaries.llm.gemini import GeminiProvider

        return GeminiProvider(key, model)
    from monday_scaries.llm.openai_compat import OpenAICompatProvider

    return OpenAICompatProvider(key, model, spec.base_url, name)
