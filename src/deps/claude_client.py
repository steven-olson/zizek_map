import json
import logging
from typing import TypeVar

from anthropic import AsyncAnthropic
from anthropic.types import Message, ToolUseBlock
from pydantic import BaseModel, SecretStr

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


class ClaudeStructuredOutputError(RuntimeError):
    pass


class ClaudeClient:
    """Thin async wrapper around the Anthropic SDK with one capability the rest of the
    codebase cares about: a structured-output call that returns a validated Pydantic
    model. Knows nothing about the domain."""

    def __init__(self, api_key: SecretStr, model: str, max_tokens: int = 16000) -> None:
        """Construct a reusable client bound to one model + max_tokens budget.

        Intent: instantiate once in `run()` and share — services receive the same
        client instance so connection pooling and credential handling stay centralized.
        """
        self._client = AsyncAnthropic(api_key=api_key.get_secret_value())
        self._model = model
        self._max_tokens = max_tokens

    async def call_structured(
        self,
        system: str,
        user: str,
        response_model: type[T],
    ) -> T:
        """Send a single message and return its response validated as `response_model`.

        Intent: give callers a typed Pydantic object back. Done by registering a
        single tool whose `input_schema` is the model's JSON schema and forcing the
        model to call it — the tool's `input` is the structured payload we want.
        """
        tool_name = self._tool_name(response_model)
        schema = response_model.model_json_schema()
        logger.info(
            "claude call_structured model=%s tool=%s user_chars=%d",
            self._model,
            tool_name,
            len(user),
        )
        message: Message = await self._client.messages.create(
            model=self._model,
            max_tokens=self._max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
            tools=[
                {
                    "name": tool_name,
                    "description": (
                        f"Return a {response_model.__name__} object matching the provided schema."
                    ),
                    "input_schema": schema,
                }
            ],
            tool_choice={"type": "tool", "name": tool_name},
        )
        for block in message.content:
            if isinstance(block, ToolUseBlock) and block.name == tool_name:
                logger.info(
                    "claude returned tool=%s stop_reason=%s usage_in=%d usage_out=%d",
                    tool_name,
                    message.stop_reason,
                    message.usage.input_tokens,
                    message.usage.output_tokens,
                )
                return response_model.model_validate(block.input)
        raise ClaudeStructuredOutputError(
            f"no tool_use block named {tool_name!r} in response: "
            f"{json.dumps([b.model_dump() for b in message.content])[:500]}"
        )

    @staticmethod
    def _tool_name(response_model: type[BaseModel]) -> str:
        """Derive a deterministic tool name from the response model class name.

        Intent: the same model class always maps to the same tool name so the call
        and the response-block lookup stay in lockstep.
        """
        return f"return_{response_model.__name__}"
