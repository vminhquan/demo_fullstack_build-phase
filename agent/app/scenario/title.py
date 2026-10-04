"""Short session title from a scenario description, like a chat app names a new conversation."""
from __future__ import annotations

import re

from app.config import get_settings
from app.llm import chat_model

MAX_WORDS = 8
# The fallback counts syllables (Vietnamese words are space-separated syllables).
FALLBACK_SYLLABLES = 12
MAX_CHARS = 80

PROMPT = (
    "Đặt một tiêu đề ngắn gọn bằng tiếng Việt (tối đa {words} từ) cho tình huống kiểm thử xe tự lái dưới đây. "
    "Chỉ trả về tiêu đề: không dấu ngoặc kép, không dấu chấm cuối, không giải thích.\n\nTình huống: {description}"
)


def fallback_title(description: str) -> str:
    """First clause of the description, trimmed to a few words (used without an LLM or when it fails)."""
    clause = re.split(r"[.\n;:,!?]", description.strip(), maxsplit=1)[0].strip() or description.strip()
    words = clause.split()
    title = " ".join(words[:FALLBACK_SYLLABLES])
    return (title[:1].upper() + title[1:])[:MAX_CHARS] or "Kịch bản mới"


def clean(text: str) -> str:
    title = text.strip().splitlines()[0] if text.strip() else ""
    title = title.strip().strip("\"'“”‘’`*#").rstrip(".").strip()
    return title[:MAX_CHARS]


def summarize_title(description: str) -> tuple[str, str]:
    """Returns (title, mode): mode is "llm" or "fallback"."""
    if get_settings().llm_enabled:
        try:
            reply = chat_model(temperature=0.2).invoke(PROMPT.format(words=MAX_WORDS, description=description[:2000]))
            title = clean(str(reply.content))
            if title:
                return title, "llm"
        except Exception:  # noqa: BLE001 - a title is never worth failing the request
            pass
    return fallback_title(description), "fallback"
