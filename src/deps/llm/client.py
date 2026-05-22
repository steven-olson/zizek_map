import logging
from dataclasses import dataclass
from typing import Generic, Protocol, TypeVar

from pydantic import BaseModel, SecretStr
from pydantic_ai import Agent
from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.providers.anthropic import AnthropicProvider
from pydantic_ai.settings import ModelSettings

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


@dataclass(frozen=True)
class LlmCallResult(Generic[T]):
    output: T
    model: str
    input_tokens: int | None
    output_tokens: int | None


class LlmCaller(Protocol):
    """Structural interface for anything that can run a typed LLM call.

    Both `LlmClient` (raw) and `CachingLlmClient` (DB-cached wrapper) satisfy this
    protocol. Steps and tasks depend on `LlmCaller` so composition can swap in
    either implementation — or a fake — without touching consumer code.
    """

    async def call_structured(
        self,
        system: str,
        user: str,
        response_model: type[T],
        *,
        cache_key: str | None = None,
    ) -> LlmCallResult[T]: ...


class LlmClient:
    """Provider-agnostic structured-output client.

    Selects the concrete pydantic-ai Model from a 'provider:model-name' string at
    construction time. The only public capability is `call_structured` — a typed
    function `(system, user, schema) -> LlmCallResult[schema]`.
    """

    def __init__(self, model: str, api_key: SecretStr, max_tokens: int = 16000) -> None:
        """Resolve the configured `model` to a pydantic-ai Model and cache call settings."""
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
        self._model_settings: ModelSettings = {"max_tokens": max_tokens}

    async def call_structured(
        self,
        system: str,
        user: str,
        response_model: type[T],
        *,
        cache_key: str | None = None,
    ) -> LlmCallResult[T]:
        """Send one prompt and return the validated response plus usage info.

        `cache_key` is accepted for signature compatibility with CachingLlmClient and
        ignored here — this is the raw client, no cache layer.
        """
        del cache_key
        logger.info(
            "llm_tasks call_structured model=%s response_model=%s user_chars=%d",
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
            "llm_tasks returned model=%s in=%d out=%d total=%d",
            self._model_label,
            usage.input_tokens or 0,
            usage.output_tokens or 0,
            usage.total_tokens or 0,
        )
        return LlmCallResult(
            output=result.output,
            model=self._model_label,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
        )
