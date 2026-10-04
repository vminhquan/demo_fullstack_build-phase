from __future__ import annotations

from functools import lru_cache

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "development"
    # Shared secret the product Backend sends as X-API-Key. Empty disables the check (development only).
    agent_api_key: str = ""

    openai_api_key: str = ""
    openai_base_url: str | None = None
    model_name: str = "gpt-4o-mini"
    llm_temperature: float = 0.2
    llm_timeout_seconds: float = 25.0

    # RAG lives in memory and is seeded at startup; generated scenarios are never indexed back.
    rag_enabled: bool = True

    # ScenarioRunner reads OpenSCENARIO world coordinates (right-handed) and converts them to CARLA
    # (left-handed) by negating y and heading. Disable only for consumers that expect raw CARLA poses.
    xosc_flip_y: bool = True
    max_catalog_waypoints: int = Field(default=20000, ge=100)

    @model_validator(mode="after")
    def _require_api_key_outside_development(self) -> "Settings":
        if self.app_env not in {"development", "test"} and not self.agent_api_key:
            raise ValueError("AGENT_API_KEY must be set outside development")
        return self

    @property
    def llm_enabled(self) -> bool:
        return bool(self.openai_api_key)


@lru_cache
def get_settings() -> Settings:
    return Settings()
