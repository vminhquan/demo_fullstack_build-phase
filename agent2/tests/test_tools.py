from __future__ import annotations

import json
from types import SimpleNamespace

import httpx
import pytest
from app.contracts import GenerationRequest
from app.graph import run_graph
from app.llm.client import MAX_TOOL_ROUNDS, LLMCallError, OpenAILLM
from app.llm.tools import CatalogApi, ExtractionTools, ProposalTools, tool_result
from test_llm import fake_client, variant
from test_sample import snapshot
from test_service import FakeLLM


def backend(handler) -> CatalogApi:
    client = httpx.Client(base_url="http://backend", headers={"X-API-Key": "k"}, transport=httpx.MockTransport(handler))
    return CatalogApi("http://backend", "k", client=client)


def call(name: str, arguments: dict, call_id: str = "c1"):
    return SimpleNamespace(id=call_id, function=SimpleNamespace(name=name, arguments=json.dumps(arguments)))


class ScriptedCompletions:
    """Returns the scripted tool calls first, then the parsed answer."""

    def __init__(self, rounds: list[list], answer):
        self.rounds, self.answer, self.calls = list(rounds), answer, []

    def parse(self, **kwargs):
        self.calls.append({**kwargs, "messages": list(kwargs["messages"])})
        if self.rounds:
            message = SimpleNamespace(content=None, tool_calls=self.rounds.pop(0), parsed=None, refusal=None)
            return SimpleNamespace(choices=[SimpleNamespace(message=message, finish_reason="tool_calls")])
        message = SimpleNamespace(content="{}", tool_calls=None, parsed=self.answer, refusal=None)
        return SimpleNamespace(choices=[SimpleNamespace(message=message, finish_reason="stop")])


def test_search_goes_to_the_backend_for_the_selected_snapshot():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"total_matching": 1, "sites": [{"site_id": "s"}]})

    tools = ExtractionTools(backend(handler), [snapshot("Town04", 108).ref])
    arguments = {"map_name": "Town04", "tags": ["straight"], "min_length_m": 200, "speed_kmh_min": None,
                 "speed_kmh_max": None, "target_side": None, "lane_change_allowed": True, "near_junction": None, "limit": 5}
    assert json.loads(tool_result(tools, "search_cut_in_sites", json.dumps(arguments)))["total_matching"] == 1
    request = seen[0]
    assert request.url.path == "/api/v1/internal/agent/catalog/snapshots/108/cut-in-sites"
    assert request.url.params.get_list("tags") == ["straight"] and request.url.params["lane_change_allowed"] == "true"
    assert "speed_kmh_min" not in request.url.params and request.headers["X-API-Key"] == "k"
    # A map that was not selected is refused without a request.
    error = json.loads(tool_result(tools, "search_cut_in_sites", json.dumps({**arguments, "map_name": "Town05"})))
    assert "Town04" in error["error"] and len(seen) == 1


def test_backend_failures_become_tool_errors_not_crashes():
    tools = ProposalTools(backend(lambda request: httpx.Response(404)), variant(), snapshot("Town01", 1))
    assert "error" in json.loads(tool_result(tools, "get_site_context", json.dumps({"site_id": "x:0:-1:-2:0.0"})))
    down = ProposalTools(backend(lambda request: (_ for _ in ()).throw(httpx.ConnectError("down"))), variant(), snapshot("Town01", 1))
    assert "Backend" in json.loads(tool_result(down, "get_site_context", json.dumps({"site_id": "a"})))["error"]


def test_site_context_reads_the_variant_snapshot_and_escapes_the_site_id():
    paths = []
    tools = ProposalTools(backend(lambda request: paths.append(request.url.raw_path) or httpx.Response(200, json={"ok": 1})),
                          variant(), snapshot("Town01", 1))
    tool_result(tools, "get_site_context", json.dumps({"site_id": "1072:0:-1:-2:0.0"}))
    assert paths == [b"/api/v1/internal/agent/catalog/snapshots/1/cut-in-sites/1072%3A0%3A-1%3A-2%3A0.0/context"]


def test_check_cut_in_runs_the_graph_validator():
    tools = ProposalTools(None, variant(), snapshot("Town01", 1))
    assert [spec["function"]["name"] for spec in tools.specs] == ["check_cut_in"]
    good = {"motorcycle_start_offset_m": 10, "trigger_time_s": 0.5, "lane_change_duration_s": 2, "desired_lead_gap_m": 5}
    assert json.loads(tool_result(tools, "check_cut_in", json.dumps(good))) == {"valid": True, "issues": []}
    bad = json.loads(tool_result(tools, "check_cut_in", json.dumps({**good, "motorcycle_start_offset_m": 0})))
    assert bad["valid"] is False and bad["issues"][0]["code"] == "INSUFFICIENT_LEAD_GAP"
    negative = json.loads(tool_result(tools, "check_cut_in", json.dumps({**good, "lane_change_duration_s": -1})))
    assert negative["valid"] is False and negative["issues"][0]["code"] == "INVALID_VALUE"


def test_client_feeds_tool_results_back_before_the_answer():
    answer = {"motorcycle_start_offset_m": 10, "trigger_time_s": 0.5, "lane_change_duration_s": 2, "desired_lead_gap_m": 5}
    completions = ScriptedCompletions([[call("check_cut_in", answer)]], answer)
    llm = OpenAILLM(sdk_client=fake_client(completions))
    plan = llm.propose_maneuver("Tạt đầu", variant(), tools=ProposalTools(None, variant(), snapshot("Town01", 1)))
    assert plan.motorcycle_start_offset_m == 10 and llm.tool_calls == ["check_cut_in"]
    second = completions.calls[1]["messages"]
    assert second[-2]["tool_calls"][0]["function"]["name"] == "check_cut_in"
    assert second[-1] == {"role": "tool", "tool_call_id": "c1", "content": '{"valid":true,"issues":[]}'}
    assert "check_cut_in" in completions.calls[0]["messages"][0]["content"]
    assert completions.calls[0]["tools"][0]["function"]["strict"] is True


def test_last_round_forbids_tools_and_a_model_that_keeps_calling_fails():
    good = {"motorcycle_start_offset_m": 10, "trigger_time_s": 0.5, "lane_change_duration_s": 2, "desired_lead_gap_m": 5}
    completions = ScriptedCompletions([[call("check_cut_in", good)]] * (MAX_TOOL_ROUNDS + 1), good)
    llm = OpenAILLM(sdk_client=fake_client(completions))
    with pytest.raises(LLMCallError) as error:
        llm.propose_maneuver("Tạt đầu", variant(), tools=ProposalTools(None, variant(), snapshot("Town01", 1)))
    assert error.value.code == "LLM_TOOL_LOOP"
    assert completions.calls[-1]["tool_choice"] == "none" and "tool_choice" not in completions.calls[0]


def test_graph_hands_tools_to_both_llm_steps():
    class RecordingLLM(FakeLLM):
        def __init__(self):
            self.seen = {}

        def extract_constraints(self, prompt, *, tools=None, map_names=None, environments=None):
            self.seen["extract"] = (type(tools).__name__, map_names)
            return super().extract_constraints(prompt)

        def propose_maneuver(self, prompt, variant, *, feedback=None, tools=None):
            self.seen["propose"] = sorted(spec["function"]["name"] for spec in tools.specs)
            return super().propose_maneuver(prompt, variant)

    body = GenerationRequest(session_id="tools", prompt="Xe máy tạt đầu ô tô", seed=1, target_count=1,
                             selected_snapshots=[snapshot("Town01", 1, presets=["ClearNoon"])], weather_preset="ClearNoon")
    llm = RecordingLLM()
    run_graph(body, llm, seed=1, catalog_api=backend(lambda request: httpx.Response(200, json={})))
    assert llm.seen == {"extract": ("ExtractionTools", ["Town01"]), "propose": ["check_cut_in", "get_site_context"]}
    run_graph(body, llm, seed=1)
    assert llm.seen["extract"][0] == "NoneType" and llm.seen["propose"] == ["check_cut_in"]
