from __future__ import annotations

import json

from app.graph import MAX_LLM_TURNS
from app.llm.tools import REJECT_TOOL, SUBMIT_TOOL
from app.llm.client import LLMCallError
from app.service import generate
from test_service import FAR, READS, VALID, FakeBackend, ScriptedLLM, request

SAVE_TO = {"project_id": 3, "user_id": 8, "prompt": "Xe máy tạt đầu ô tô", "request_hash": "h"}


def tool_answers(llm: ScriptedLLM, turn: int) -> list[dict]:
    """Tool results the LLM saw at the start of `turn` (1-based), parsed."""
    messages = llm.sent[turn - 1]["messages"]
    tail = []
    for message in reversed(messages):
        if message["role"] != "tool":
            break
        tail.insert(0, json.loads(message["content"]))
    return tail


def test_reads_in_parallel_then_submits_builds_and_saves() -> None:
    llm, backend = ScriptedLLM([READS, [(SUBMIT_TOOL, VALID)]]), FakeBackend()
    result = generate(request(save_to=SAVE_TO), llm, backend)
    assert result.status == "completed" and len(result.scenarios) == 1
    scenario = result.scenarios[0]
    assert scenario.generation_id == 99 and "<OpenSCENARIO>" in scenario.xosc
    assert scenario.plan.ego.blueprint_id == "vehicle.tesla.model3" and scenario.plan.environment.weather_preset == "ClearNoon"
    assert scenario.site_id == "1:-1:20" and scenario.plan.metrics["min_ttc_s"] <= 3
    # Prefetch read the inventories from the Backend; the two lane-graph reads of turn 1 needed no Backend call.
    assert sorted(name for name, _ in backend.calls) == ["summary", "vehicles", "weather"]
    assert [answer["road_id"] for answer in tool_answers(llm, 2)[1:]] == [1] and len(tool_answers(llm, 2)) == 2
    saved = backend.saved[0]
    assert saved["save_to"] == SAVE_TO and saved["snapshot_id"] == 1 and saved["model_name"] == "test-llm"
    assert saved["scenario"]["xosc_sha256"] == scenario.xosc_sha256


def test_without_save_to_the_scenario_is_only_returned() -> None:
    backend = FakeBackend()
    result = generate(request(), ScriptedLLM([READS, [(SUBMIT_TOOL, VALID)]]), backend)
    assert result.status == "completed" and result.scenarios[0].generation_id is None and backend.saved == []


def test_a_scenario_with_no_incident_goes_back_to_the_llm_with_the_numbers() -> None:
    llm = ScriptedLLM([READS, [(SUBMIT_TOOL, FAR)], [(SUBMIT_TOOL, VALID)]])
    result = generate(request(), llm, FakeBackend())
    assert result.status == "completed" and len(llm.sent) == 3
    [feedback] = tool_answers(llm, 3)
    # A cut-in prompt: merging 250 m ahead is reported as too far, with a start and time found by simulation.
    assert feedback["valid"] is False and [issue["code"] for issue in feedback["issues"]] == ["CUT_IN_TOO_FAR"]
    assert "đã thử bằng mô phỏng: đạt" in feedback["issues"][0]["message"]


def test_facts_missing_from_the_catalog_are_reported_back() -> None:
    wrong = {**VALID, "weather_preset": "Snow", "ego": {**VALID["ego"], "blueprint_id": "vehicle.audi.a2"},
             "actors": [{**VALID["actors"][0], "blueprint_id": "vehicle.made.up"}]}
    llm = ScriptedLLM([READS, [(SUBMIT_TOOL, wrong)], [(SUBMIT_TOOL, VALID)]])
    assert generate(request(), llm, FakeBackend()).status == "completed"
    codes = {issue["code"] for issue in tool_answers(llm, 3)[0]["issues"]}
    assert codes == {"UNKNOWN_BLUEPRINT", "WEATHER_NOT_AVAILABLE", "EGO_LOCKED"}


def test_the_run_fails_after_the_attempts() -> None:
    llm = ScriptedLLM([[(SUBMIT_TOOL, FAR)]] * 5)
    result = generate(request(), llm, FakeBackend())
    assert result.status == "failed" and result.map_failures[0].code == "NO_VALID_PLAN"
    assert "CUT_IN_TOO_FAR" in result.map_failures[0].message


def test_reject_request_ends_the_run_without_searching() -> None:
    llm, backend = ScriptedLLM([[(REJECT_TOOL, {"quote": "trời có tuyết", "reason": "tuyết ngoài miền hỗ trợ"})]]), FakeBackend()
    result = generate(request(prompt="Xe máy tạt đầu ô tô khi trời có tuyết"), llm, backend)
    assert result.status == "failed" and result.map_failures[0].code == "UNSUPPORTED_REQUIREMENT"
    assert "trời có tuyết" in result.map_failures[0].message and "search_sites" not in [name for name, _ in backend.calls]


def test_the_last_turn_must_submit() -> None:
    llm = ScriptedLLM([[READS[1]]] * (MAX_LLM_TURNS - 1))
    result = generate(request(), llm, FakeBackend())
    assert result.status == "completed" and len(llm.sent) == MAX_LLM_TURNS
    assert llm.sent[-1]["tool_choice"] == {"type": "function", "function": {"name": SUBMIT_TOOL}}
    assert all(item["tool_choice"] == "required" for item in llm.sent[:-1])


def test_llm_error_and_save_error_fail_the_run() -> None:
    class Broken(ScriptedLLM):
        def chat(self, messages, tools, tool_choice="required"):
            raise LLMCallError("LLM_EMPTY_RESPONSE", "Không có dữ liệu.")

    assert generate(request(), Broken([]), FakeBackend()).map_failures[0].code == "LLM_EMPTY_RESPONSE"
    result = generate(request(save_to=SAVE_TO), ScriptedLLM([[(SUBMIT_TOOL, VALID)]]), FakeBackend(save_error=True))
    assert result.status == "failed" and result.scenarios == [] and result.map_failures[0].code == "SAVE_FAILED"


def test_two_submits_in_one_turn_answer_every_call() -> None:
    llm = ScriptedLLM([[(SUBMIT_TOOL, VALID), (SUBMIT_TOOL, VALID)]])
    result = generate(request(), llm, FakeBackend())
    assert result.status == "completed"


def test_rejecting_the_supported_scenario_itself_is_sent_back_to_the_llm() -> None:
    # Seen live: filters the user never asked for found 0 sites, then the model rejected the whole prompt.
    give_up = (REJECT_TOOL, {"quote": "xe máy tạt đầu ô tô ego", "reason": "Không tìm thấy site tạt đầu nào phù hợp."})
    llm = ScriptedLLM([[give_up], READS, [(SUBMIT_TOOL, VALID)]])
    result = generate(request(prompt="xe máy tạt đầu ô tô ego"), llm, FakeBackend())
    assert result.status == "completed" and len(llm.sent) == 3
    [refused] = tool_answers(llm, 2)
    assert "không được từ chối" in refused["error"] and "tìm lại" in refused["error"]


def test_reject_problem_accepts_only_a_quoted_unsupported_requirement() -> None:
    from app.llm.tools import reject_problem

    prompt = "Xe máy tạt đầu ô tô trên cao tốc khi trời có tuyết"
    assert reject_problem("trời có tuyết", prompt) is None
    assert reject_problem("người đi bộ băng qua", "người đi bộ băng qua đường") is None
    assert reject_problem("Xe máy tạt đầu ô tô", prompt) is not None
    assert reject_problem("xe tải phanh gấp", "xe tải phanh gấp trước ô tô") is not None  # vehicles: supported
    # Seen live: the whole prompt quoted to give up; weather and light are buildable too.
    live = "xe máy và ô tô tạt đầu xe ego vào lúc trời bụi mù vào ban đêm"
    assert reject_problem(live, live) is not None
    assert reject_problem("trên cao tốc", prompt) is not None
    assert reject_problem("mưa đá", prompt) is not None  # not in the prompt


def test_prefetch_gives_whole_lists_in_a_neutral_order_and_picks_nothing() -> None:
    llm, backend = ScriptedLLM([READS, [(SUBMIT_TOOL, VALID)]]), FakeBackend()
    generate(request(), llm, backend)
    catalog = json.loads(llm.sent[0]["messages"][1]["content"])["catalog"]["Town01"]
    assert catalog["weather_presets"] == ["ClearNoon", "HardRainNight"]
    assert {item["kind"] for item in catalog["vehicles"]} == {"car", "motorcycle"}  # every kind, not a pick
    assert [item["id"] for item in catalog["ego"]] == ["vehicle.tesla.model3"]  # the locked ego only
    assert catalog["summary"]["tags"] == {"straight": 1} and "sites" not in catalog
    # No seed: the lists are not reordered per variant.
    assert all(args[1] is None for name, args in backend.calls if name == "vehicles")
    # Without a locked ego the whole car list is offered.
    llm = ScriptedLLM([READS, [(SUBMIT_TOOL, {**VALID, "ego": {**VALID["ego"], "blueprint_id": "vehicle.tesla.model3"}})]])
    generate(request(ego_blueprint_id=None), llm, FakeBackend())
    ego = json.loads(llm.sent[0]["messages"][1]["content"])["catalog"]["Town01"]["ego"]
    assert [item["id"] for item in ego] == ["vehicle.audi.a2", "vehicle.tesla.model3"]


def test_prefetch_failure_stops_before_the_llm() -> None:
    llm = ScriptedLLM([])
    result = generate(request(), llm, FakeBackend(down=True))
    assert result.status == "failed" and result.map_failures[0].code == "CATALOG_UNAVAILABLE" and llm.sent == []


def test_a_locked_pedestrian_is_refused_before_the_llm() -> None:
    llm = ScriptedLLM([])
    result = generate(request(actor_type="pedestrian"), llm, FakeBackend())
    assert result.status == "failed" and result.map_failures[0].code == "UNSUPPORTED_ACTOR" and llm.sent == []



def test_a_map_that_cannot_host_the_prompt_is_reported_for_the_builder_to_move_on() -> None:
    from world import snapshot

    give_up = (REJECT_TOOL, {"quote": "xe máy tạt đầu ô tô", "reason": "Map không có làn bên cạnh."})
    llm = ScriptedLLM([[give_up]])
    # Another snapshot id: lane graphs are cached by content hash.
    single = request(selected_snapshots=[snapshot("Town01", 9, presets=["ClearNoon"], single_lane=True)])
    result = generate(single, llm, FakeBackend())
    assert result.status == "failed" and result.map_failures[0].code == "LOCATION_NOT_AVAILABLE"
    # The same rejection on a map with adjacent lanes is sent back to the model instead.
    llm = ScriptedLLM([[give_up], [(SUBMIT_TOOL, VALID)]])
    assert generate(request(), llm, FakeBackend()).status == "completed"
    assert json.loads(llm.sent[0]["messages"][1]["content"])["catalog"]["Town01"]["can_host"]["multi_lane"] is True
