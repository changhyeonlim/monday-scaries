"""The one interface every LLM provider implements, and its errors.

Every SDK failure is turned into one LLMError subclass, so the rest of
the pipeline never sees provider-specific exceptions. Errors marked
fatal stop the whole run (every later call would fail the same way);
the others only lose the current issue.
"""

import logging
import time
from abc import ABC, abstractmethod

logger = logging.getLogger(__name__)

TIMEOUT_SECONDS = 30.0
MAX_RETRIES = 2
BACKOFF_SECONDS = 1.0
MAX_BACKOFF_SECONDS = 30.0


class LLMError(Exception):
    """Base class for provider errors reported to the user."""

    fatal = False


class MissingKeyError(LLMError):
    """The API key env var is empty or unset. Checked before any call."""

    fatal = True


class InvalidKeyError(LLMError):
    """The provider rejected the key (401, 403, or Gemini's 400)."""

    fatal = True


class OutOfCreditsError(LLMError):
    """The account has no credits or quota left. Never retried."""

    fatal = True


class ModelNotFoundError(LLMError):
    """404: the model name or the base URL is wrong."""

    fatal = True


class RateLimitedError(LLMError):
    """429 that persisted through every retry."""

    fatal = True


class ServerError(LLMError):
    """5xx or overloaded (529) that persisted through every retry."""


class LLMConnectionError(LLMError):
    """The provider could not be reached (DNS, offline, TLS)."""


class LLMTimeoutError(LLMError):
    """No response within TIMEOUT_SECONDS. Not retried: too slow."""


class InvalidRequestError(LLMError):
    """Any other 4xx: the request itself was wrong."""


class EmptyResponseError(LLMError):
    """HTTP 200 but no choices or content came back."""


class TruncatedResponseError(LLMError):
    """The reply hit max_tokens and was cut off."""


class RefusalError(LLMError):
    """The model declined to answer (Claude stop_reason "refusal")."""


_RETRYABLE = (RateLimitedError, ServerError, LLMConnectionError)

# Gemini reports a bad key as a 400 with one of these.
_BAD_KEY_PHRASES = ("api key", "auth key", "api_key_invalid")

# OpenAI (code), Anthropic (400 message), OpenRouter (402 message).
_NO_CREDIT_PHRASES = (
    "insufficient_quota",
    "credit balance",
    "insufficient credits",
)


def _body_message(body: object) -> str:
    """Pulls the human-readable message out of a provider error body.

    Handles OpenAI and Anthropic ({"error": {"message": ...}}) and Gemini,
    which wraps the same shape in a list.
    """
    if isinstance(body, list) and body:
        body = body[0]
    if not isinstance(body, dict):
        return ""
    inner = body.get("error", body)
    if isinstance(inner, dict):
        return str(inner.get("message", ""))
    return str(inner)


def describe_error(err: Exception, secret: str | None = None) -> str:
    """Describes an SDK error without echoing a possible API key.

    Provider 401 messages can contain part of the key, so only the error
    type and HTTP status are reported. 400 messages describe the request
    itself, so their reason is kept, with the key redacted just in case.
    Gemini reports an invalid key as a 400.

    Args:
        err: The exception raised by the provider SDK.
        secret: The API key in use, removed from any text that is kept.

    Returns:
        A short description that is safe to print or log.
    """
    status = getattr(err, "status_code", None)
    text = type(err).__name__
    if status:
        text += f" (HTTP {status})"
    if status == 400:
        reason = _body_message(getattr(err, "body", None))
        reason = reason or str(getattr(err, "message", ""))
        if secret:
            reason = reason.replace(secret, "[redacted]")
        text += f": {reason[:200]}"
    return text


def classify_error(err: Exception, secret: str | None = None) -> LLMError:
    """Maps an SDK exception to the matching LLMError subclass.

    Args:
        err: The exception raised by the provider SDK.
        secret: The API key in use, kept out of the message.

    Returns:
        An LLMError whose message is safe to print.
    """
    text = describe_error(err, secret)
    status = getattr(err, "status_code", None)
    # Only used for matching, never printed.
    blob = f"{getattr(err, 'body', '')} {getattr(err, 'message', '')}"
    blob = blob.lower()

    if "Timeout" in type(err).__name__:
        return LLMTimeoutError(f"Timed out after {TIMEOUT_SECONDS:.0f}s.")
    if status is None:
        return LLMConnectionError(f"Could not reach the provider ({text}).")
    if status in (401, 403) or (
        status == 400 and any(p in blob for p in _BAD_KEY_PHRASES)
    ):
        return InvalidKeyError(f"API key was rejected: {text}")
    # Not "billing": OpenAI's ordinary rate-limit message links to billing.
    if status == 402 or any(phrase in blob for phrase in _NO_CREDIT_PHRASES):
        return OutOfCreditsError(f"Out of credits or quota: {text}")
    if status == 404:
        return ModelNotFoundError(f"Model or endpoint not found: {text}")
    if status == 429:
        return RateLimitedError(f"Rate limited: {text}")
    if status >= 500:
        return ServerError(f"Provider server error: {text}")
    return InvalidRequestError(f"Request rejected: {text}")


def _retry_after(err: Exception) -> float | None:
    """Reads the retry-after header (seconds) if the provider sent one."""
    response = getattr(err, "response", None)
    if response is None:
        return None
    value = response.headers.get("retry-after")
    try:
        return float(value) if value else None
    except ValueError:
        return None


class LLMProvider(ABC):
    """Sends one prompt to a model and returns the text reply.

    Subclasses implement _send and set _sdk_error to the SDK's base
    exception. complete() adds error mapping and retries.
    """

    name: str
    model: str
    _api_key: str
    _sdk_error: type[Exception]
    tokens_used: int = 0  # input + output tokens across all calls

    def _add_usage(self, usage: object, *fields: str) -> None:
        """Adds token counts from a response's usage object, if any."""
        if usage is None:
            return
        counts = (getattr(usage, name, 0) for name in fields)
        self.tokens_used += sum(c for c in counts if isinstance(c, int))

    def complete(self, system: str, user: str, max_tokens: int = 300) -> str:
        """Returns the model's text reply.

        Rate limits, server errors, and connection errors are retried
        MAX_RETRIES times with exponential backoff (or the provider's
        retry-after). Everything else fails at once.

        Args:
            system: System prompt.
            user: User message.
            max_tokens: Cap on output tokens.

        Returns:
            The reply text.

        Raises:
            LLMError: A subclass describing what went wrong.
        """
        for attempt in range(MAX_RETRIES + 1):
            try:
                return self._send(system, user, max_tokens)
            except self._sdk_error as err:
                error = classify_error(err, self._api_key)
                wait = _retry_after(err)
            if attempt == MAX_RETRIES or not isinstance(error, _RETRYABLE):
                raise error
            if wait is None:
                wait = BACKOFF_SECONDS * 2**attempt
            wait = min(wait, MAX_BACKOFF_SECONDS)
            logger.warning(
                "%s: %s, retrying in %.0fs (%d/%d)",
                self.name,
                type(error).__name__,
                wait,
                attempt + 1,
                MAX_RETRIES,
            )
            time.sleep(wait)
        raise AssertionError("unreachable")

    @abstractmethod
    def _send(self, system: str, user: str, max_tokens: int) -> str:
        """Makes one API call and returns the reply text.

        Raises:
            self._sdk_error: Any SDK failure, mapped by complete().
            EmptyResponseError: No reply came back.
            TruncatedResponseError: The reply hit max_tokens.
            RefusalError: The model declined.
        """
