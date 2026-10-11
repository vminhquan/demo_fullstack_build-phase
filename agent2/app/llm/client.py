"""OpenAI chat boundary for the central LLM node: one completion per graph turn, tool calls included."""

from __future__ import annotations

from typing import Any

from app.config import Settings, get_settings


class LLMCallError(RuntimeError):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


class OpenAILLM:
    def __init__(self, *, model_name: str = "gpt-4o-mini", api_key: str | None = None, sdk_client: Any | None = None):
        self.model_name = model_name
        if sdk_client is not None:
            self._client = sdk_client
        else:
            if not api_key:
                raise LLMCallError("OPENAI_NOT_CONFIGURED", "Thiếu OPENAI_API_KEY cho agent2.")
            from openai import OpenAI

            self._client = OpenAI(api_key=api_key, timeout=30.0, max_retries=2)
        # Token counts of the last chat() call, for the run log; one client serves one request.
        self.last_usage: dict[str, int] | None = None

    def chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]], tool_choice: Any = "required") -> dict[str, Any]:
        """The assistant message as a dict for the next request. `tool_choice="required"` makes every turn a tool
        call, so the run ends only through submit_scenario."""
        completion = self._client.chat.completions.create(
            model=self.model_name, messages=messages, tools=tools, tool_choice=tool_choice,
            parallel_tool_calls=True, temperature=0,
        )
        usage = getattr(completion, "usage", None)
        self.last_usage = ({"prompt_tokens": usage.prompt_tokens, "completion_tokens": usage.completion_tokens}
                           if usage is not None else None)
        if not completion.choices:
            raise LLMCallError("LLM_EMPTY_RESPONSE", "OpenAI không trả kết quả.")
        message = completion.choices[0].message
        if getattr(message, "refusal", None):
            raise LLMCallError("LLM_REFUSAL", "OpenAI từ chối yêu cầu.")
        calls = getattr(message, "tool_calls", None) or []
        if not calls:
            raise LLMCallError("LLM_NO_TOOL_CALL", "OpenAI trả lời mà không gọi tool.")
        return {"role": "assistant", "content": message.content, "tool_calls": [
            {"id": call.id, "type": "function", "function": {"name": call.function.name, "arguments": call.function.arguments}}
            for call in calls
        ]}


def from_settings(settings: Settings | None = None) -> OpenAILLM:
    config = settings or get_settings()
    key = config.openai_api_key.get_secret_value() if config.openai_api_key else None
    return OpenAILLM(model_name=config.model_name, api_key=key)
