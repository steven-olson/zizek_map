from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class LlmSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="LLM_", env_file=".env", case_sensitive=False)

    api_key: SecretStr
    model: str = "anthropic:claude-opus-4-7"
    max_tokens: int = 16000
    max_concurrent_calls: int = 4
    cache_enabled: bool = True
