from functools import lru_cache

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration, loaded from environment variables (and `.env` if present)."""

    # env_ignore_empty: `GEMINI_API_KEY=` (blank) counts as unset, not as an empty secret.
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", env_ignore_empty=True)

    database_url: str = "postgresql+psycopg://email:email@localhost:5433/email_automation"
    api_key: SecretStr = Field(min_length=16)

    gemini_api_key: SecretStr | None = None
    gemini_model: str = "gemini-flash-lite-latest"
    # Fallback when Gemini fails: any OpenAI-compatible API (Groq by default, or OpenRouter).
    fallback_llm_api_key: SecretStr | None = None
    fallback_llm_base_url: str = "https://api.groq.com/openai/v1"
    fallback_llm_model: str = "openai/gpt-oss-120b"
    llm_timeout_s: float = Field(default=30.0, gt=0)
    llm_max_retries: int = Field(default=3, ge=1, le=10)

    max_process_attempts: int = Field(default=3, ge=1)
    stale_processing_minutes: int = Field(default=15, ge=1)
    min_confidence: float = Field(default=0.6, ge=0, le=1)
    max_body_chars: int = Field(default=20_000, ge=500)
    history_lookup_limit: int = Field(default=5, ge=1, le=20)

    # MCP server (separate service, `python -m mcp_server`). Read-only access to records.
    mcp_api_key: SecretStr | None = Field(default=None, min_length=16)
    mcp_host: str = "127.0.0.1"
    mcp_port: int = Field(default=8001, ge=1, le=65535)
    mcp_max_results: int = Field(default=50, ge=1, le=200)
    # Extra Host headers the MCP server accepts besides localhost (comma-separated, e.g. "mcp:*").
    mcp_allowed_hosts: str = ""
    # When set, the agent's history tool calls the MCP server here instead of the DB directly.
    mcp_server_url: str | None = None
    mcp_timeout_s: float = Field(default=10.0, gt=0)

    log_level: str = "INFO"

    @property
    def mcp_extra_allowed_hosts(self) -> tuple[str, ...]:
        return tuple(host.strip() for host in self.mcp_allowed_hosts.split(",") if host.strip())

    @model_validator(mode="after")
    def _mcp_client_needs_key(self) -> "Settings":
        if self.mcp_server_url and self.mcp_api_key is None:
            raise ValueError("MCP_SERVER_URL is set but MCP_API_KEY is not.")
        # The MCP history lookup asks for one extra row (the email being analyzed).
        if self.mcp_server_url and self.history_lookup_limit + 1 > self.mcp_max_results:
            raise ValueError("MCP_MAX_RESULTS must exceed HISTORY_LOOKUP_LIMIT.")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
