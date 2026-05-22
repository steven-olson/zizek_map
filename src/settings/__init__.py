from dataclasses import dataclass
from functools import lru_cache

from src.settings.app import AppSettings
from src.settings.database import DatabaseSettings
from src.settings.llm import LlmSettings


@dataclass(frozen=True)
class Settings:
    app: AppSettings
    database: DatabaseSettings
    llm: LlmSettings


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide Settings bundle, instantiated once and cached.

    Each component loads its own env vars (LLM_*, DATABASE_*, APP_*) so adding a
    new concern never has to grow a god-config — just add a new BaseSettings class
    and expose it on the bundle.
    """
    return Settings(
        app=AppSettings(),
        database=DatabaseSettings(),  # type: ignore[call-arg]  # env-driven
        llm=LlmSettings(),  # type: ignore[call-arg]  # env-driven
    )
