from pydantic_settings import BaseSettings, SettingsConfigDict


class DatabaseSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="DATABASE_", env_file=".env", case_sensitive=False)

    url: str
    echo: bool = False
