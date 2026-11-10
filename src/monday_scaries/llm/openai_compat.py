"""Provider for OpenAI and OpenAI-compatible endpoints."""

import openai

from monday_scaries.llm.base import (
    TIMEOUT_SECONDS,
    EmptyResponseError,
    LLMProvider,
    TruncatedResponseError,
)


class OpenAICompatProvider(LLMProvider):
    """Chat completions on OpenAI or any compatible base URL."""

    _sdk_error = openai.APIError

    def __init__(
        self,
        api_key: str,
        model: str,
        base_url: str | None = None,
        name: str = "openai",
    ) -> None:
        """Creates the provider.

        Args:
            api_key: API key for the endpoint.
            model: Model name.
            base_url: Endpoint URL. None means OpenAI itself.
            name: Provider name shown in the report.
        """
        self.name = name
        self.model = model
        self._api_key = api_key  # only used to redact error text
        self._client = openai.OpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=TIMEOUT_SECONDS,
            max_retries=0,
        )

    def _send(self, system: str, user: str, max_tokens: int) -> str:
        """See base class."""
        response = self._client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            max_tokens=max_tokens,
            temperature=0,
        )
        if not response.choices:
            raise EmptyResponseError("The provider returned no choices.")
        choice = response.choices[0]
        if choice.finish_reason == "length":
            raise TruncatedResponseError(
                f"Reply was cut off at max_tokens={max_tokens}."
            )
        return choice.message.content or ""
