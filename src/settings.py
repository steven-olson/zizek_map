from functools import lru_cache
from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", case_sensitive=False)

    llm_api_key: SecretStr
    llm_model: str = "anthropic:claude-opus-4-7"
    database_url: str
    books_dir: Path = Path("/app/books")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide Settings, instantiated once and cached.

    Centralizes config access so the rest of the codebase never re-reads .env or
    re-parses environment variables ad-hoc — call this whenever you need settings.
    """
    return Settings()
