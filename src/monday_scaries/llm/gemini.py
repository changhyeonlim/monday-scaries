"""Provider for Gemini's native generateContent endpoint.

Google's newer auth keys (they start with "AQ.") are rejected by the
OpenAI-compatible endpoint with "Invalid Auth key.", so Gemini is called
on its native endpoint, which takes the key in x-goog-api-key.
"""

import httpx

from monday_scaries.llm.base import (
    TIMEOUT_SECONDS,
    EmptyResponseError,
    LLMProvider,
    RefusalError,
    TruncatedResponseError,
)

BASE_URL = "https://generativelanguage.googleapis.com"

# Gemini 3 always thinks, and thinking tokens count against
# maxOutputTokens, so this much is added on top of the caller's cap.
# No thinking level is sent: the allowed levels differ per model.
THINKING_HEADROOM = 8192

_REFUSAL_REASONS = ("SAFETY", "PROHIBITED_CONTENT", "BLOCKLIST", "RECITATION")


def _json(response: httpx.Response | None) -> object:
    """Returns the response's JSON body, or None if there is none."""
    if response is None:
        return None
    try:
        return response.json()
    except ValueError:
        return None


class GeminiAPIError(Exception):
    """An HTTP or network failure, shaped like the SDKs' errors."""

    def __init__(
        self, message: str, response: httpx.Response | None = None
    ) -> None:
        """Stores what classify_error reads: status_code, body, response."""
        super().__init__(message)
        self.message = message
        self.response = response
        self.status_code = None if response is None else response.status_code
        self.body = _json(response)


class GeminiTimeoutError(GeminiAPIError):
    """No reply in time. classify_error matches "Timeout" in the name."""


class GeminiProvider(LLMProvider):
    """generateContent on the Gemini API."""

    _sdk_error = GeminiAPIError

    def __init__(
        self,
        api_key: str,
        model: str,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        """Creates the provider.

        Args:
            api_key: Gemini API key, either kind (AIza... or AQ....).
            model: Model name, with or without the "models/" prefix.
            transport: Optional httpx transport. Tests pass a mock here.
        """
        self.name = "gemini"
        self.model = model.removeprefix("models/")
        self._api_key = api_key  # only used to redact error text
        # The key goes in a header, never in the URL.
        self._http = httpx.Client(
            base_url=BASE_URL,
            headers={"x-goog-api-key": api_key},
            timeout=TIMEOUT_SECONDS,
            transport=transport,
        )

    def _send(self, system: str, user: str, max_tokens: int) -> str:
        """See base class."""
        payload = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": {
                "maxOutputTokens": max_tokens + THINKING_HEADROOM,
            },
        }
        try:
            response = self._http.post(
                f"/v1beta/models/{self.model}:generateContent", json=payload
            )
        except httpx.TimeoutException:
            raise GeminiTimeoutError("Request timed out.") from None
        except httpx.HTTPError:
            raise GeminiAPIError("Could not reach Gemini.") from None
        if response.status_code >= 400:
            raise GeminiAPIError(
                f"Error code: {response.status_code}", response
            )

        data = _json(response)
        if not isinstance(data, dict):
            raise EmptyResponseError("Gemini returned invalid JSON.")
        candidates = data.get("candidates") or []
        if not candidates:
            blocked = (data.get("promptFeedback") or {}).get("blockReason")
            if blocked:
                raise RefusalError(f"The prompt was blocked ({blocked}).")
            raise EmptyResponseError("The provider returned no candidates.")
        candidate = candidates[0]
        reason = candidate.get("finishReason")
        if reason == "MAX_TOKENS":
            raise TruncatedResponseError(
                f"Reply was cut off at max_tokens={max_tokens}."
            )
        if reason in _REFUSAL_REASONS:
            raise RefusalError(f"The model declined to answer ({reason}).")
        parts = (candidate.get("content") or {}).get("parts") or []
        return "".join(
            part.get("text", "") for part in parts if not part.get("thought")
        )
