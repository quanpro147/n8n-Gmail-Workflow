from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration, loaded from environment variables (and `.env` if present)."""

    # env_ignore_empty: `GEMINI_API_KEY=` (blank) counts as unset, not as an empty secret.
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", env_ignore_empty=True)

    database_url: str = "postgresql+psycopg://email:email@localhost:5433/email_automation"
    api_key: SecretStr = Field(min_length=16)

    gemini_api_key: SecretStr | None = None
    gemini_model: str = "gemini-2.5-flash"
    llm_timeout_s: float = Field(default=30.0, gt=0)
    llm_max_retries: int = Field(default=3, ge=1, le=10)

    max_process_attempts: int = Field(default=3, ge=1)
    stale_processing_minutes: int = Field(default=15, ge=1)
    min_confidence: float = Field(default=0.6, ge=0, le=1)
    max_body_chars: int = Field(default=20_000, ge=500)
    history_lookup_limit: int = Field(default=5, ge=1, le=20)

    log_level: str = "INFO"


@lru_cache
def get_settings() -> Settings:
    return Settings()
