"""Provider for Anthropic's Claude models."""

import anthropic

from monday_scaries.llm.base import (
    TIMEOUT_SECONDS,
    LLMProvider,
    RefusalError,
    TruncatedResponseError,
)


class AnthropicProvider(LLMProvider):
    """Messages API on Anthropic.

    Current Claude models reject temperature, so none is sent. Low
    effort keeps the model's thinking short for classification.
    """

    _sdk_error = anthropic.APIError

    def __init__(self, api_key: str, model: str) -> None:
        """Creates the provider.

        Args:
            api_key: Anthropic API key.
            model: Model name, e.g. "claude-haiku-5-5".
        """
        self.name = "claude"
        self.model = model
        self._api_key = api_key  # only used to redact error text
        self._client = anthropic.Anthropic(
            api_key=api_key, timeout=TIMEOUT_SECONDS, max_retries=0
        )

    def _send(self, system: str, user: str, max_tokens: int) -> str:
        """See base class."""
        response = self._client.messages.create(
            model=self.model,
            system=system,
            messages=[{"role": "user", "content": user}],
            max_tokens=max_tokens,
            output_config={"effort": "low"},
        )
        self._add_usage(
            getattr(response, "usage", None), "input_tokens", "output_tokens"
        )
        if response.stop_reason == "refusal":
            raise RefusalError("The model declined to answer.")
        if response.stop_reason == "max_tokens":
            raise TruncatedResponseError(
                f"Reply was cut off at max_tokens={max_tokens}."
            )
        return "".join(
            block.text for block in response.content if block.type == "text"
        )
