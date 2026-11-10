"""Provider for Anthropic's Claude models."""

import anthropic

from monday_scaries.llm.base import (
    TIMEOUT_SECONDS,
    LLMError,
    LLMProvider,
    describe_error,
)


class AnthropicProvider(LLMProvider):
    """Messages API on Anthropic."""

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

    def complete(self, system: str, user: str, max_tokens: int = 300) -> str:
        """See base class."""
        try:
            response = self._client.messages.create(
                model=self.model,
                system=system,
                messages=[{"role": "user", "content": user}],
                max_tokens=max_tokens,
                output_config={"effort": "low"},
            )
        except anthropic.APIError as err:
            raise LLMError(describe_error(err, self._api_key)) from None
        return "".join(
            block.text for block in response.content if block.type == "text"
        )
