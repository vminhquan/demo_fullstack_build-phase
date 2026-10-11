from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    openai_api_key: SecretStr | None = Field(
        default=None,
        validation_alias="OPENAI_API_KEY",
    )
    agent_api_key: SecretStr | None = Field(default=None, validation_alias="AGENT_API_KEY")
    # Backend base URL (e.g. http://backend:8000) for the catalog tool calls and the save; required to generate.
    backend_url: str | None = Field(default=None, validation_alias="BACKEND_URL")
    model_name: str = Field(
        default="gpt-4o",
        validation_alias="AGENT2_MODEL_NAME",
    )
    # Run log (app.logs): INFO shows every turn, tool call and result; DEBUG adds the system prompt and the XOSC.
    log_level: str = Field(default="INFO", validation_alias="AGENT2_LOG_LEVEL")
    # Longest text logged for one value; 0 logs tool results and arguments in full.
    log_max_chars: int = Field(default=4000, ge=0, validation_alias="AGENT2_LOG_MAX_CHARS")


@lru_cache
def get_settings() -> Settings:
    return Settings()
