"""OpenAI chat boundary for the central LLM node: one completion per graph turn, tool calls included."""

from __future__ import annotations

from typing import Any

from app.config import Settings, get_settings


# Failures of the LLM service itself (not of the scenario): the API answers 503 so they are not taken for a bad prompt.
SERVICE_CODES = {"LLM_QUOTA_EXHAUSTED", "LLM_RATE_LIMITED", "LLM_AUTH_FAILED", "LLM_UNAVAILABLE"}


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
        try:
            completion = self._client.chat.completions.create(
                model=self.model_name, messages=messages, tools=tools, tool_choice=tool_choice,
                parallel_tool_calls=True, temperature=0,
            )
        except Exception as exc:  # the SDK's own errors, mapped to codes the user can act on
            raise _service_error(exc) from exc
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


def _service_error(exc: Exception) -> Exception:
    """An OpenAI SDK error as an LLMCallError with a code; anything else is returned unchanged (a bug to see)."""
    import openai

    if isinstance(exc, openai.RateLimitError):
        body = exc.body if isinstance(exc.body, dict) else {}
        error = body.get("error", body) if isinstance(body.get("error", body), dict) else {}
        if error.get("code") in ("insufficient_quota", "credit_balance_exhausted") or error.get("type") == "insufficient_quota":
            return LLMCallError("LLM_QUOTA_EXHAUSTED", "Tài khoản OpenAI đã hết credit: nạp thêm hoặc đổi OPENAI_API_KEY.")
        return LLMCallError("LLM_RATE_LIMITED", "OpenAI đang giới hạn số request (429): thử lại sau ít phút.")
    if isinstance(exc, openai.AuthenticationError):
        return LLMCallError("LLM_AUTH_FAILED", "OPENAI_API_KEY không hợp lệ.")
    if isinstance(exc, (openai.APIConnectionError, openai.APITimeoutError)):
        return LLMCallError("LLM_UNAVAILABLE", "Không kết nối được OpenAI.")
    if isinstance(exc, openai.APIStatusError):
        return LLMCallError(f"LLM_API_ERROR_{exc.status_code}", f"OpenAI trả lỗi {exc.status_code}.")
    return exc


def from_settings(settings: Settings | None = None) -> OpenAILLM:
    config = settings or get_settings()
    key = config.openai_api_key.get_secret_value() if config.openai_api_key else None
    return OpenAILLM(model_name=config.model_name, api_key=key)
