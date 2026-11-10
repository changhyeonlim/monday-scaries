"""Tests for the LLM provider layer. No network, no real keys.

Every failure an LLM call can hit is mapped to one LLMError subclass.
Each test runs against both SDKs (OpenAI-compatible and Anthropic).
"""

from dataclasses import dataclass
from types import ModuleType, SimpleNamespace
from typing import Any

import anthropic
import httpx2
import openai
import pytest

from monday_scaries.llm import base, ping
from monday_scaries.llm.anthropic import AnthropicProvider
from monday_scaries.llm.base import (
    EmptyResponseError,
    InvalidKeyError,
    InvalidRequestError,
    LLMConnectionError,
    LLMError,
    LLMProvider,
    LLMTimeoutError,
    MissingKeyError,
    ModelNotFoundError,
    OutOfCreditsError,
    RateLimitedError,
    RefusalError,
    ServerError,
    TruncatedResponseError,
    describe_error,
)
from monday_scaries.llm.openai_compat import OpenAICompatProvider

FAKE_KEY = "sk-test-SECRET-1234567890"
REQUEST = httpx2.Request("POST", "https://example.invalid/v1")


# Helpers


def status_error(
    sdk: ModuleType,
    cls_name: str,
    status: int,
    body: object = None,
    headers: dict[str, str] | None = None,
) -> Exception:
    """Builds an SDK HTTP error the way the SDK itself would."""
    response = httpx2.Response(status, request=REQUEST, headers=headers)
    cls = getattr(sdk, cls_name)
    return cls(f"Error code: {status}", response=response, body=body)


class FakeCall:
    """Stands in for an SDK create() call and plays back outcomes.

    Each call uses the next outcome; the last one repeats. An outcome
    that is an exception is raised, anything else is returned.
    """

    def __init__(self, *outcomes: Any) -> None:
        """Stores the outcomes to play back."""
        self.outcomes = list(outcomes)
        self.calls: list[dict[str, Any]] = []

    def __call__(self, **kwargs: Any) -> Any:
        """Records the call and plays back the next outcome."""
        self.calls.append(kwargs)
        index = min(len(self.calls), len(self.outcomes)) - 1
        outcome = self.outcomes[index]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def openai_reply(content="OK", finish_reason="stop", choices=True):
    """A minimal chat.completions response."""
    message = SimpleNamespace(content=content)
    choice = SimpleNamespace(message=message, finish_reason=finish_reason)
    return SimpleNamespace(choices=[choice] if choices else [])


def claude_reply(*texts, stop_reason="end_turn"):
    """A minimal messages response, with a thinking block first."""
    blocks = [SimpleNamespace(type="thinking", thinking="")]
    blocks += [SimpleNamespace(type="text", text=t) for t in texts]
    return SimpleNamespace(content=blocks, stop_reason=stop_reason)


@dataclass
class Setup:
    """A provider with its SDK and a way to fake its API call."""

    provider: LLMProvider
    sdk: ModuleType
    target: Any
    ok: Any
    monkeypatch: pytest.MonkeyPatch

    def fake(self, *outcomes: Any) -> FakeCall:
        """Replaces the SDK call with a FakeCall and returns it."""
        call = FakeCall(*outcomes)
        self.monkeypatch.setattr(self.target, "create", call)
        return call


@pytest.fixture(params=["openai", "claude"])
def llm(request, monkeypatch) -> Setup:
    """Runs a test once per SDK."""
    if request.param == "openai":
        provider = OpenAICompatProvider(FAKE_KEY, "test-model")
        target = provider._client.chat.completions
        return Setup(provider, openai, target, openai_reply(), monkeypatch)
    provider = AnthropicProvider(FAKE_KEY, "test-model")
    target = provider._client.messages
    return Setup(provider, anthropic, target, claude_reply("OK"), monkeypatch)


@pytest.fixture(autouse=True)
def sleeps(monkeypatch) -> list[float]:
    """Records retry waits instead of sleeping."""
    waits: list[float] = []
    monkeypatch.setattr(base.time, "sleep", waits.append)
    return waits


# HTTP status -> error class, for both SDKs.
# (id, SDK class, status, body, expected error, expected calls)
STATUS_CASES = [
    ("401-bad-key", "AuthenticationError", 401, None, InvalidKeyError, 1),
    ("403-no-access", "PermissionDeniedError", 403, None, InvalidKeyError, 1),
    ("404-model", "NotFoundError", 404, None, ModelNotFoundError, 1),
    (
        "402-openrouter-credits",
        "APIStatusError",
        402,
        {"error": {"message": "Insufficient credits"}},
        OutOfCreditsError,
        1,
    ),
    (
        "429-openai-quota",
        "RateLimitError",
        429,
        {"error": {"code": "insufficient_quota", "message": "quota"}},
        OutOfCreditsError,
        1,
    ),
    (
        "400-claude-credits",
        "BadRequestError",
        400,
        {"error": {"message": "Your credit balance is too low"}},
        OutOfCreditsError,
        1,
    ),
    (
        "400-bad-request",
        "BadRequestError",
        400,
        {"error": {"message": "max_tokens: too large"}},
        InvalidRequestError,
        1,
    ),
    ("422-unprocessable", "APIStatusError", 422, None, InvalidRequestError, 1),
    (
        "429-rate-limit-mentions-billing",
        "RateLimitError",
        429,
        {"error": {"message": "Rate limit reached. See .../billing"}},
        RateLimitedError,
        3,
    ),
    ("500-server", "InternalServerError", 500, None, ServerError, 3),
    ("529-overloaded", "APIStatusError", 529, None, ServerError, 3),
]


# Error mapping


@pytest.mark.parametrize(
    "cls_name, status, body, expected, calls",
    [case[1:] for case in STATUS_CASES],
    ids=[case[0] for case in STATUS_CASES],
)
def test_http_errors_map_to_the_right_class(
    llm, cls_name, status, body, expected, calls
):
    """Each HTTP failure becomes one specific LLMError subclass."""
    call = llm.fake(status_error(llm.sdk, cls_name, status, body))
    with pytest.raises(expected):
        llm.provider.complete("sys", "user")
    assert len(call.calls) == calls


def test_timeout_is_not_retried(llm):
    """A timeout fails at once: retrying would triple a slow call."""
    call = llm.fake(llm.sdk.APITimeoutError(request=REQUEST))
    with pytest.raises(LLMTimeoutError):
        llm.provider.complete("sys", "user")
    assert len(call.calls) == 1


def test_connection_error_is_retried_then_fails(llm, sleeps):
    """DNS or offline errors get the normal retries."""
    call = llm.fake(llm.sdk.APIConnectionError(request=REQUEST))
    with pytest.raises(LLMConnectionError):
        llm.provider.complete("sys", "user")
    assert len(call.calls) == 3
    assert sleeps == [1.0, 2.0]


@pytest.mark.parametrize(
    "message",
    ["Please pass a valid API key", "Invalid Auth key.", "API_KEY_INVALID"],
)
def test_gemini_invalid_key_400_is_an_invalid_key(message):
    """Gemini reports a bad key as 400 with a list-wrapped body.

    The first two came back from the live endpoint during setup;
    API_KEY_INVALID is the reason code Google documents.
    """
    provider = OpenAICompatProvider(FAKE_KEY, "gemini-test", name="gemini")
    body = [{"error": {"code": 400, "message": message}}]
    err = status_error(openai, "BadRequestError", 400, body)
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(provider._client.chat.completions, "create", FakeCall(err))
        with pytest.raises(InvalidKeyError):
            provider.complete("sys", "user")


def test_programming_errors_are_not_swallowed(llm):
    """A bug in our code must crash loudly, not look like an API error."""
    llm.fake(TypeError("bug"))
    with pytest.raises(TypeError):
        llm.provider.complete("sys", "user")


# Retries


def test_rate_limit_then_success_returns_reply(llm, sleeps):
    """One 429 followed by success: the reply is returned."""
    limited = status_error(llm.sdk, "RateLimitError", 429)
    call = llm.fake(limited, llm.ok)
    assert llm.provider.complete("sys", "user") == "OK"
    assert len(call.calls) == 2
    assert sleeps == [1.0]


def test_backoff_doubles_between_retries(llm, sleeps):
    """Without retry-after, waits are 1s then 2s."""
    llm.fake(status_error(llm.sdk, "InternalServerError", 500))
    with pytest.raises(ServerError):
        llm.provider.complete("sys", "user")
    assert sleeps == [1.0, 2.0]


@pytest.mark.parametrize(
    "header, waits",
    [("7", [7.0, 7.0]), ("999", [30.0, 30.0]), ("soon", [1.0, 2.0])],
    ids=["honored", "capped-at-30s", "unparseable-falls-back"],
)
def test_retry_after_header(llm, sleeps, header, waits):
    """The provider's retry-after is used, capped, or ignored if bad."""
    err = status_error(
        llm.sdk, "RateLimitError", 429, headers={"retry-after": header}
    )
    llm.fake(err)
    with pytest.raises(RateLimitedError):
        llm.provider.complete("sys", "user")
    assert sleeps == waits


def test_out_of_credits_is_never_retried(llm, sleeps):
    """Retrying cannot fix an empty account."""
    body = {"error": {"code": "insufficient_quota", "message": "quota"}}
    call = llm.fake(status_error(llm.sdk, "RateLimitError", 429, body))
    with pytest.raises(OutOfCreditsError):
        llm.provider.complete("sys", "user")
    assert len(call.calls) == 1
    assert sleeps == []


# Fatal vs per-issue errors


@pytest.mark.parametrize(
    "error_cls",
    [
        MissingKeyError,
        InvalidKeyError,
        OutOfCreditsError,
        ModelNotFoundError,
        RateLimitedError,
    ],
)
def test_account_level_errors_stop_the_run(error_cls):
    """Every later call would fail the same way, so stop."""
    assert error_cls.fatal


@pytest.mark.parametrize(
    "error_cls",
    [
        LLMTimeoutError,
        LLMConnectionError,
        ServerError,
        InvalidRequestError,
        EmptyResponseError,
        TruncatedResponseError,
        RefusalError,
    ],
)
def test_issue_level_errors_only_lose_one_issue(error_cls):
    """These mark one issue unclassified; the run continues."""
    assert not error_cls.fatal
    assert issubclass(error_cls, LLMError)


# The key never leaks


@pytest.mark.parametrize(
    "cls_name, status",
    [(case[1], case[2]) for case in STATUS_CASES],
    ids=[case[0] for case in STATUS_CASES],
)
def test_key_never_appears_in_any_error(llm, cls_name, status):
    """Even if the provider echoes the key, our message does not."""
    body = {"error": {"message": f"bad key {FAKE_KEY}"}}
    llm.fake(status_error(llm.sdk, cls_name, status, body))
    with pytest.raises(LLMError) as info:
        llm.provider.complete("sys", "user")
    assert FAKE_KEY not in str(info.value)
    # No chained SDK exception that could print the key in a traceback.
    assert info.value.__cause__ is None
    assert info.value.__context__ is None


# Response shapes (HTTP 200 but unusable)


def test_openai_no_choices_is_empty_response(monkeypatch):
    """Some compatible providers return 200 with an empty choices list."""
    provider = OpenAICompatProvider(FAKE_KEY, "m")
    fake = FakeCall(openai_reply(choices=False))
    monkeypatch.setattr(provider._client.chat.completions, "create", fake)
    with pytest.raises(EmptyResponseError):
        provider.complete("sys", "user")


def test_openai_cut_off_reply_is_truncated(monkeypatch):
    """finish_reason "length" means the JSON would be incomplete."""
    provider = OpenAICompatProvider(FAKE_KEY, "m")
    fake = FakeCall(openai_reply('{"kind": "bu', finish_reason="length"))
    monkeypatch.setattr(provider._client.chat.completions, "create", fake)
    with pytest.raises(TruncatedResponseError):
        provider.complete("sys", "user")


def test_openai_none_content_becomes_empty_string(monkeypatch):
    """A None content does not crash."""
    provider = OpenAICompatProvider(FAKE_KEY, "m")
    fake = FakeCall(openai_reply(content=None))
    monkeypatch.setattr(provider._client.chat.completions, "create", fake)
    assert provider.complete("sys", "user") == ""


def test_openai_sends_temperature_zero(monkeypatch):
    """The spec asks for temperature 0 where the provider allows it."""
    provider = OpenAICompatProvider(FAKE_KEY, "m")
    fake = FakeCall(openai_reply())
    monkeypatch.setattr(provider._client.chat.completions, "create", fake)
    provider.complete("sys", "user", max_tokens=50)
    assert fake.calls[0]["temperature"] == 0
    assert fake.calls[0]["max_tokens"] == 50


def test_claude_refusal(monkeypatch):
    """stop_reason "refusal" is reported, not returned as text."""
    provider = AnthropicProvider(FAKE_KEY, "m")
    fake = FakeCall(claude_reply(stop_reason="refusal"))
    monkeypatch.setattr(provider._client.messages, "create", fake)
    with pytest.raises(RefusalError):
        provider.complete("sys", "user")


def test_claude_cut_off_reply_is_truncated(monkeypatch):
    """stop_reason "max_tokens" means the JSON would be incomplete."""
    provider = AnthropicProvider(FAKE_KEY, "m")
    fake = FakeCall(claude_reply('{"kind"', stop_reason="max_tokens"))
    monkeypatch.setattr(provider._client.messages, "create", fake)
    with pytest.raises(TruncatedResponseError):
        provider.complete("sys", "user")


def test_claude_joins_only_text_blocks_and_sends_no_temperature(
    monkeypatch,
):
    """Thinking blocks are skipped; Claude rejects temperature."""
    provider = AnthropicProvider(FAKE_KEY, "m")
    fake = FakeCall(claude_reply("O", "K"))
    monkeypatch.setattr(provider._client.messages, "create", fake)
    assert provider.complete("sys", "user", max_tokens=50) == "OK"
    assert "temperature" not in fake.calls[0]
    assert fake.calls[0]["max_tokens"] == 50


# describe_error


def test_describe_error_401_drops_the_message():
    """A 401 message can echo part of the key, so it is dropped."""
    err = openai.AuthenticationError(
        f"Incorrect API key: {FAKE_KEY}",
        response=httpx2.Response(401, request=REQUEST),
        body=None,
    )
    text = describe_error(err)
    assert "HTTP 401" in text
    assert "SECRET" not in text


def test_describe_error_400_reads_anthropic_body():
    """Anthropic nests the reason under error.message."""
    body = {"type": "error", "error": {"message": "credit balance too low"}}
    err = status_error(anthropic, "BadRequestError", 400, body)
    assert "credit balance too low" in describe_error(err)


def test_describe_error_400_message_is_capped():
    """A huge 400 message is cut so logs stay readable."""
    body = {"error": {"message": "x" * 5000}}
    err = status_error(openai, "BadRequestError", 400, body)
    assert len(describe_error(err)) < 300


def test_describe_error_400_redacts_the_key():
    """If a 400 reason ever echoes the key, it is redacted."""
    body = {"error": {"message": f"bad key {FAKE_KEY}"}}
    err = status_error(openai, "BadRequestError", 400, body)
    text = describe_error(err, FAKE_KEY)
    assert FAKE_KEY not in text
    assert "[redacted]" in text


# ping: provider selection and empty keys


@pytest.mark.parametrize("value", ["", "   "])
def test_empty_key_is_rejected_before_any_call(monkeypatch, value):
    """An empty or blank key fails with the env var name, not a call."""
    monkeypatch.setenv("GEMINI_API_KEY", value)
    with pytest.raises(MissingKeyError, match="GEMINI_API_KEY is not set"):
        ping._make("gemini", "test-model")


def test_gemini_uses_its_base_url(monkeypatch):
    """Gemini goes through the OpenAI-compatible client with its URL."""
    monkeypatch.setenv("GEMINI_API_KEY", FAKE_KEY)
    provider = ping._make("gemini", "test-model")
    assert isinstance(provider, OpenAICompatProvider)
    assert provider.name == "gemini"
    assert "generativelanguage.googleapis.com" in str(provider._client.base_url)


def test_unknown_provider_prints_usage(monkeypatch, capsys):
    """An unknown provider exits 2 with a usage line."""
    monkeypatch.setattr("sys.argv", ["ping", "nope", "model"])
    assert ping.main() == 2
    assert "usage:" in capsys.readouterr().out
