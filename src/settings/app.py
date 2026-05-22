from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class AppSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="APP_", env_file=".env", case_sensitive=False)

    books_dir: Path = Path("/app/books")
    log_level: str = "INFO"
