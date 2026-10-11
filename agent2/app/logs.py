"""Readable logs of one generation run: every LLM turn, tool call, check and save, prefixed by the session id.

Uvicorn configures only its own loggers, so the "agent2" logger gets its own stream handler here.
"""

from __future__ import annotations

import json
import logging
import sys
from typing import Any

logger = logging.getLogger("agent2")
# Longest text logged for one value (tool result, arguments...); 0 logs everything.
_max_chars = 4000


def setup(level: str = "INFO", max_chars: int = 4000) -> None:
    global _max_chars
    _max_chars = max_chars
    if not any(getattr(handler, "_agent2", False) for handler in logger.handlers):
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%H:%M:%S"))
        handler._agent2 = True  # type: ignore[attr-defined]
        logger.addHandler(handler)
    logger.setLevel(level.upper())
    logger.propagate = False


def short(value: Any) -> str:
    """JSON text of `value` (strings are parsed first), cut to the configured length."""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            pass
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)
    if _max_chars and len(text) > _max_chars:
        return f"{text[:_max_chars]}… (+{len(text) - _max_chars} ký tự)"
    return text


def log(session_id: str, message: str, *args: Any, level: int = logging.INFO) -> None:
    logger.log(level, f"[{session_id}] {message}", *args)
