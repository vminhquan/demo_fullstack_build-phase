from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from app.contracts import GenerationRequest
from app.llm.client import LLMCallError, OpenAILLM
from app.llm.prompts import initial_messages
from app.llm.tools import CatalogTools
from world import snapshot


def tool_call(name: str, arguments: dict, call_id: str = "c1"):
    return SimpleNamespace(id=call_id, function=SimpleNamespace(name=name, arguments=json.dumps(arguments)))


class FakeCompletions:
    def __init__(self, message):
        self.message, self.calls = message, []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(choices=[SimpleNamespace(message=self.message, finish_reason="tool_calls")])


def fake_client(completions: FakeCompletions):
    return SimpleNamespace(chat=SimpleNamespace(completions=completions))


def test_chat_returns_the_tool_calls_as_a_message_and_requires_a_tool() -> None:
    message = SimpleNamespace(content=None, refusal=None, tool_calls=[
        tool_call("search_cut_in_sites", {"map_name": "Town01"}, "a"), tool_call("find_ego_starts", {"map_name": "Town01"}, "b"),
    ])
    completions = FakeCompletions(message)
    reply = OpenAILLM(sdk_client=fake_client(completions)).chat([{"role": "user", "content": "x"}], CatalogTools.specs())
    assert [call["function"]["name"] for call in reply["tool_calls"]] == ["search_cut_in_sites", "find_ego_starts"]
    assert reply["role"] == "assistant" and reply["tool_calls"][0]["id"] == "a"
    sent = completions.calls[0]
    assert sent["tool_choice"] == "required" and sent["parallel_tool_calls"] is True and sent["model"] == "gpt-4o-mini"


@pytest.mark.parametrize(("message", "code"), [
    (SimpleNamespace(content="xin chào", refusal=None, tool_calls=None), "LLM_NO_TOOL_CALL"),
    (SimpleNamespace(content=None, refusal="không", tool_calls=None), "LLM_REFUSAL"),
])
def test_answers_without_a_tool_call_fail(message, code: str) -> None:
    with pytest.raises(LLMCallError) as error:
        OpenAILLM(sdk_client=fake_client(FakeCompletions(message))).chat([], CatalogTools.specs())
    assert error.value.code == code


def test_missing_api_key_fails_before_sdk_import() -> None:
    with pytest.raises(LLMCallError) as error:
        OpenAILLM(api_key=None)
    assert error.value.code == "OPENAI_NOT_CONFIGURED"


def test_first_message_has_only_the_request_facts() -> None:
    request = GenerationRequest(session_id="s", prompt="Xe máy tạt đầu ô tô", selected_snapshots=[snapshot("Town01", 1)],
                                ego_blueprint_id="vehicle.tesla.model3", weather_preset="ClearNoon", actor_type="truck")
    system, user = initial_messages(request, seed=3)
    assert "submit_scenario" in system["content"]
    facts = json.loads(user["content"])
    assert facts == {"prompt": "Xe máy tạt đầu ô tô", "maps": ["Town01"], "ego_blueprint_id": "vehicle.tesla.model3",
                     "variant_seed": 3, "picked_environment": {"weather_preset": "ClearNoon"}, "locked_actor_type": "truck"}



def test_openai_service_errors_become_codes_the_user_can_act_on() -> None:
    import httpx
    import openai

    request = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")

    def failing(error):
        class Completions:
            def create(self, **kwargs):
                raise error
        return SimpleNamespace(chat=SimpleNamespace(completions=Completions()))

    quota = openai.RateLimitError("no credits", response=httpx.Response(429, request=request),
                                  body={"error": {"code": "credit_balance_exhausted", "type": "insufficient_quota"}})
    busy = openai.RateLimitError("slow down", response=httpx.Response(429, request=request), body={"error": {"code": "rate_limit"}})
    down = openai.APIConnectionError(request=request)
    for error, code in ((quota, "LLM_QUOTA_EXHAUSTED"), (busy, "LLM_RATE_LIMITED"), (down, "LLM_UNAVAILABLE")):
        with pytest.raises(LLMCallError) as caught:
            OpenAILLM(sdk_client=failing(error)).chat([], CatalogTools.specs())
        assert caught.value.code == code
