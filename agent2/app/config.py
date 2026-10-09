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
    model_name: str = Field(
        default="gpt-4o-mini",
        validation_alias="AGENT2_MODEL_NAME",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
