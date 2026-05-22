import logging
from typing import TypeVar

from pydantic import BaseModel, SecretStr
from pydantic_ai import Agent
from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.providers.anthropic import AnthropicProvider

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


class LlmClient:
    """Thin async wrapper around pydantic-ai with a single capability the rest of the
    codebase cares about: a structured-output call that returns a validated Pydantic
    model. Provider-agnostic — the concrete model is selected via a 'provider:model-name'
    string passed in at construction."""

    def __init__(self, model: str, api_key: SecretStr, max_tokens: int = 16000) -> None:
        """Resolve the configured `model` to a pydantic-ai Model and cache call settings.

        Intent: instantiate once in `run()` and share — services receive the same
        client instance so connection pooling and credential handling stay centralized,
        and swapping providers is a config change (the model string), not a code change.
        """
        provider_prefix, _, model_name = model.partition(":")
        if not model_name:
            raise ValueError(
                f"model must be in 'provider:model-name' form (e.g. 'anthropic:claude-opus-4-7'), "
                f"got {model!r}"
            )
        if provider_prefix == "anthropic":
            self._model = AnthropicModel(
                model_name,
                provider=AnthropicProvider(api_key=api_key.get_secret_value()),
            )
        else:
            raise NotImplementedError(
                f"LLM provider {provider_prefix!r} is not yet supported; "
                "add a branch here when needed."
            )
        self._model_label = model
        self._model_settings = {"max_tokens": max_tokens}

    async def call_structured(self, system: str, user: str, response_model: type[T]) -> T:
        """Send one prompt and return its response validated as `response_model`.

        Intent: keep the call surface tiny so prompt classes can treat the LLM as a
        pure function `(system, user, schema) -> typed_object`. pydantic-ai owns the
        tool-use plumbing under the hood; we just unwrap the typed output and log usage.
        """
        logger.info(
            "llm call_structured model=%s response_model=%s user_chars=%d",
            self._model_label,
            response_model.__name__,
            len(user),
        )
        agent: Agent[None, T] = Agent(
            model=self._model,
            output_type=response_model,
            system_prompt=system,
            model_settings=self._model_settings,
        )
        result = await agent.run(user)
        usage = result.usage
        logger.info(
            "llm returned model=%s in=%d out=%d total=%d",
            self._model_label,
            usage.input_tokens or 0,
            usage.output_tokens or 0,
            usage.total_tokens or 0,
        )
        return result.output
