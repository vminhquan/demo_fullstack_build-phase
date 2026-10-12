from functools import lru_cache
from pathlib import Path
from typing import Literal

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
        default="gpt-4o-mini",
        validation_alias="AGENT2_MODEL_NAME",
    )
    # Run log (app.logs): INFO shows every turn, tool call and result; DEBUG adds the system prompt and the XOSC.
    log_level: str = Field(default="INFO", validation_alias="AGENT2_LOG_LEVEL")
    # Longest text logged for one value; 0 logs tool results and arguments in full.
    log_max_chars: int = Field(default=4000, ge=0, validation_alias="AGENT2_LOG_MAX_CHARS")
    # Bridges dial /v1/bridge/ws with a token the Backend signs with this secret; empty: no Bridge can connect.
    bridge_agent_secret: SecretStr | None = Field(default=None, validation_alias="BRIDGE_AGENT_SECRET")
    # A valid scenario also runs on the project's Bridge CARLA with the no-brain ego, which must collide.
    # off: never; optional: skipped when no verdict comes back (Bridge away, CARLA down); required: then the run fails.
    carla_validation: Literal["off", "optional", "required"] = Field(default="optional", validation_alias="AGENT2_CARLA_VALIDATION")
    # Limit of one ScenarioRunner run on the Bridge (seconds); agent2 waits a minute longer for the queue and map load.
    carla_validation_timeout_s: float = Field(default=120, gt=0, validation_alias="AGENT2_CARLA_VALIDATION_TIMEOUT_S")


@lru_cache
def get_settings() -> Settings:
    return Settings()
