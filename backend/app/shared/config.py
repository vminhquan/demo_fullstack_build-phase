from functools import lru_cache

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "development"
    api_prefix: str = "/api/v1"
    database_url: str = "postgresql+asyncpg://scenario:change-me@localhost:5432/scenario_forge"
    jwt_secret: str = Field(default="development-only-change-before-production-123456", min_length=32)
    jwt_access_ttl_minutes: int = 15
    jwt_refresh_ttl_days: int = 7
    worker_service_token: str = Field(default="development-worker-token-change-me", min_length=16)
    embedding_provider: str = "disabled"
    embedding_model: str = "text-embedding-3-small"
    embedding_dim: int = 768
    openai_api_key: str | None = None
    embedding_request_timeout_seconds: float = 15.0
    rag_data_source: str = "postgres"
    cors_origins: str = "http://localhost:3000"
    max_xosc_size_bytes: int = 10 * 1024 * 1024
    # Scenario-generation Agent (agent/ service). Empty URL disables generation endpoints with 503.
    agent_service_url: str = "http://localhost:8100"
    agent_api_key: str = ""
    agent_timeout_seconds: float = 180.0
    # USD per 1M tokens for the cost report (defaults: gpt-4o-mini list price, check before relying on it).
    llm_price_input_per_mtok: float = 0.15
    llm_price_output_per_mtok: float = 0.60
    max_catalog_upload_bytes: int = 50 * 1024 * 1024
    # Simulator Runner over the Bridge: per test case limit sent in run.assign; a RUNNING case with no result
    # after timeout + 60 s is failed with TIMEOUT.
    bridge_job_timeout_seconds: int = 300

    @field_validator("database_url")
    @classmethod
    def _use_asyncpg_driver(cls, value: str) -> str:
        # Hosted Postgres (Render, Heroku, ...) hands out postgres:// or postgresql:// URLs;
        # the app engine needs the asyncpg driver and Alembic strips it again for psycopg.
        for prefix in ("postgres://", "postgresql://"):
            if value.startswith(prefix):
                return "postgresql+asyncpg://" + value[len(prefix):]
        return value

    @model_validator(mode="after")
    def _reject_default_secrets_outside_development(self) -> "Settings":
        if self.app_env not in {"development", "test"}:
            if self.jwt_secret.startswith("development-") or self.worker_service_token.startswith("development-"):
                raise ValueError("JWT_SECRET and WORKER_SERVICE_TOKEN must be set outside development")
        if self.embedding_dim != 768:
            raise ValueError("EMBEDDING_DIM must be 768 to match the vector(768) column created by migrations")
        return self

    @property
    def cors_origin_list(self) -> list[str]:
        return [item.strip() for item in self.cors_origins.split(",") if item.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
