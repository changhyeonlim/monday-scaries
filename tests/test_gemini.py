"""Tests for the native Gemini provider. No network, no real keys."""

import json

import httpx
import pytest

from monday_scaries.llm import base, ping
from monday_scaries.llm.base import (
    EmptyResponseError,
    InvalidKeyError,
    LLMConnectionError,
    LLMTimeoutError,
    ModelNotFoundError,
    RateLimitedError,
    RefusalError,
    ServerError,
    TruncatedResponseError,
)
from monday_scaries.llm.gemini import THINKING_HEADROOM, GeminiProvider

FAKE_KEY = "AQ.test-SECRET-1234567890"


def reply(text="OK", finish_reason="STOP", thought=None):
    """A minimal generateContent body, optionally with a thought part."""
    parts = []
    if thought:
        parts.append({"text": thought, "thought": True})
    if text is not None:
        parts.append({"text": text})
    return {
        "candidates": [
            {"content": {"parts": parts}, "finishReason": finish_reason}
        ]
    }


def error_body(status, message):
    """An error body the way Google sends it."""
    return {"error": {"code": status, "message": message}}


class Handler:
    """Plays back responses and records the requests it saw."""

    def __init__(self, *outcomes):
        """Stores (status, json) pairs or exceptions to play back."""
        self.outcomes = list(outcomes)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        """Records the request and plays back the next outcome."""
        self.requests.append(request)
        index = min(len(self.requests), len(self.outcomes)) - 1
        outcome = self.outcomes[index]
        if isinstance(outcome, Exception):
            raise outcome
        status, body = outcome
        return httpx.Response(status, json=body)


def make(*outcomes, model="gemini-test"):
    """Builds a provider wired to a Handler."""
    handler = Handler(*outcomes)
    provider = GeminiProvider(
        FAKE_KEY, model, transport=httpx.MockTransport(handler)
    )
    return provider, handler


@pytest.fixture(autouse=True)
def sleeps(monkeypatch) -> list[float]:
    """Records retry waits instead of sleeping."""
    waits: list[float] = []
    monkeypatch.setattr(base.time, "sleep", waits.append)
    return waits


# Request shape


def test_key_goes_in_the_goog_header_not_bearer_or_url():
    """AQ. keys only work in x-goog-api-key; Bearer gets them rejected."""
    provider, handler = make((200, reply()))
    assert provider.complete("sys", "user") == "OK"
    request = handler.requests[0]
    assert request.headers["x-goog-api-key"] == FAKE_KEY
    assert "authorization" not in request.headers
    assert FAKE_KEY not in str(request.url)


@pytest.mark.parametrize("model", ["gemini-test", "models/gemini-test"])
def test_calls_native_generate_content(model):
    """Both name forms hit the same native URL, not /openai/."""
    provider, handler = make((200, reply()), model=model)
    provider.complete("sys", "user")
    url = str(handler.requests[0].url)
    assert url == (
        "https://generativelanguage.googleapis.com/v1beta"
        "/models/gemini-test:generateContent"
    )


def test_payload_has_prompts_and_thinking_headroom():
    """Thinking counts against the cap, so headroom is added on top."""
    provider, handler = make((200, reply()))
    provider.complete("be brief", "hello", max_tokens=50)
    payload = json.loads(handler.requests[0].content)
    assert payload["systemInstruction"]["parts"][0]["text"] == "be brief"
    assert payload["contents"][0]["parts"][0]["text"] == "hello"
    config = payload["generationConfig"]
    assert config["maxOutputTokens"] == 50 + THINKING_HEADROOM
    # Allowed levels differ per model, so the model's default is used.
    assert "thinkingConfig" not in config


# Response shapes


def test_thought_parts_are_left_out_of_the_reply():
    """Only answer text is returned."""
    provider, _ = make((200, reply("OK", thought="let me think")))
    assert provider.complete("sys", "user") == "OK"


def test_cut_off_reply_is_truncated():
    """A MAX_TOKENS finish means the JSON would be incomplete."""
    provider, _ = make((200, reply('{"kind": "bu', "MAX_TOKENS")))
    with pytest.raises(TruncatedResponseError, match="max_tokens=300"):
        provider.complete("sys", "user")


def test_safety_stop_is_a_refusal():
    """A safety stop is reported, not returned as text."""
    provider, _ = make((200, reply(None, "SAFETY")))
    with pytest.raises(RefusalError):
        provider.complete("sys", "user")


def test_blocked_prompt_is_a_refusal():
    """A blocked prompt comes back with no candidates."""
    body = {"promptFeedback": {"blockReason": "SAFETY"}}
    provider, _ = make((200, body))
    with pytest.raises(RefusalError):
        provider.complete("sys", "user")


def test_no_candidates_is_empty_response():
    """HTTP 200 with nothing in it."""
    provider, _ = make((200, {}))
    with pytest.raises(EmptyResponseError):
        provider.complete("sys", "user")


# Errors


@pytest.mark.parametrize(
    "status, message",
    [
        (400, "API key not valid. Please pass a valid API key."),
        (401, "API key not valid. Please pass a valid API key."),
        (403, "Permission denied"),
    ],
)
def test_rejected_key_is_an_invalid_key(status, message):
    """Bad AIza keys come back as 400, bad AQ. keys as 401."""
    provider, handler = make((status, error_body(status, message)))
    with pytest.raises(InvalidKeyError) as info:
        provider.complete("sys", "user")
    assert len(handler.requests) == 1
    assert FAKE_KEY not in str(info.value)


def test_unknown_model_is_model_not_found():
    """404 means the model name is wrong."""
    provider, _ = make((404, error_body(404, "models/nope is not found")))
    with pytest.raises(ModelNotFoundError):
        provider.complete("sys", "user")


@pytest.mark.parametrize(
    "status, expected", [(429, RateLimitedError), (503, ServerError)]
)
def test_rate_limit_and_overload_are_retried(status, expected, sleeps):
    """Both get the normal retries before failing."""
    provider, handler = make((status, error_body(status, "try later")))
    with pytest.raises(expected):
        provider.complete("sys", "user")
    assert len(handler.requests) == 3
    assert sleeps == [1.0, 2.0]


def test_rate_limit_then_success_returns_reply():
    """One 429 does not lose the issue."""
    provider, handler = make((429, error_body(429, "slow")), (200, reply()))
    assert provider.complete("sys", "user") == "OK"
    assert len(handler.requests) == 2


def test_timeout_is_not_retried():
    """A timeout fails at once."""
    provider, handler = make(httpx.ReadTimeout("slow"))
    with pytest.raises(LLMTimeoutError):
        provider.complete("sys", "user")
    assert len(handler.requests) == 1


def test_connection_error_is_retried_then_fails():
    """DNS or offline errors get the normal retries."""
    provider, handler = make(httpx.ConnectError("offline"))
    with pytest.raises(LLMConnectionError) as info:
        provider.complete("sys", "user")
    assert len(handler.requests) == 3
    assert info.value.__cause__ is None
    assert info.value.__context__ is None


# ping


def test_ping_builds_the_native_gemini_provider(monkeypatch):
    """Gemini no longer goes through the OpenAI-compatible client."""
    monkeypatch.setenv("GEMINI_API_KEY", FAKE_KEY)
    provider = ping._make("gemini", "models/test-model")
    assert isinstance(provider, GeminiProvider)
    assert provider.name == "gemini"
    assert provider.model == "test-model"
