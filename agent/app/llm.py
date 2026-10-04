from __future__ import annotations

from langchain_openai import ChatOpenAI

from app.config import get_settings


def chat_model(temperature: float | None = None) -> ChatOpenAI:
    """OpenAI chat model configured from settings (key is passed explicitly, never read from os.environ)."""
    settings = get_settings()
    options: dict = {
        "model": settings.model_name,
        "api_key": settings.openai_api_key,
        "temperature": settings.llm_temperature if temperature is None else temperature,
        "timeout": settings.llm_timeout_seconds,
        "max_retries": 1,
    }
    if settings.openai_base_url:
        options["base_url"] = settings.openai_base_url
    return ChatOpenAI(**options)
