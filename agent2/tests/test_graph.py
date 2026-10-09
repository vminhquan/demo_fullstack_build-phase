from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from app.contracts import GenerationRequest
from app.cut_in.model import CutInPlan, PromptConstraints
from app.graph import run_graph
from app.llm.client import LLMCallError
from app.service import generate
from test_sample import snapshot
from test_service import FakeLLM


def request(*, target_count: int = 1, max_proposal_attempts: int = 3) -> GenerationRequest:
    return GenerationRequest(
        session_id="graph-session", prompt="Xe máy tạt đầu ô tô", seed=17,
        selected_snapshots=[snapshot("Town01", 1, presets=["ClearNoon"])],
        target_count=target_count, max_proposal_attempts=max_proposal_attempts,
        weather_preset="ClearNoon",
    )


def test_graph_routes_recoverable_validation_back_to_llm() -> None:
    class RepairLLM(FakeLLM):
        def __init__(self):
            self.feedbacks = []

        def propose_maneuver(self, prompt, variant, *, feedback=None, tools=None):
            self.feedbacks.append(feedback or [])
            offset = 0 if len(self.feedbacks) == 1 else 10
            return CutInPlan.model_validate({**variant.context.model_dump(),
                                             "motorcycle_start_offset_m": offset,
                                             "trigger_time_s": 0.5,
                                             "lane_change_duration_s": 2,
                                             "desired_lead_gap_m": 5})

    llm = RepairLLM()
    result = generate(request(), llm)
    assert result.status == "completed"
    assert len(llm.feedbacks) == 2
    assert any("INSUFFICIENT_LEAD_GAP" in item for item in llm.feedbacks[1])


def test_graph_stops_after_configured_attempt_limit() -> None:
    class InvalidLLM(FakeLLM):
        calls = 0

        def propose_maneuver(self, prompt, variant, *, feedback=None, tools=None):
            self.calls += 1
            return CutInPlan.model_validate({**variant.context.model_dump(),
                                             "motorcycle_start_offset_m": 0,
                                             "trigger_time_s": 0.5,
                                             "lane_change_duration_s": 2,
                                             "desired_lead_gap_m": 5})

    llm = InvalidLLM()
    result = generate(request(max_proposal_attempts=2), llm)
    # Two LLM tries, then the numbers are repaired by computation instead of losing the variant.
    assert llm.calls == 2 and result.status == "completed"
    plan = result.scenarios[0].plan
    assert plan.motorcycle_start_offset_m > 0 and plan.desired_lead_gap_m == 5 and plan.trigger_time_s == 0.5


def test_graph_does_not_retry_site_error_with_llm() -> None:
    class CountingLLM(FakeLLM):
        calls = 0

        def propose_maneuver(self, prompt, variant, *, feedback=None, tools=None):
            self.calls += 1
            return super().propose_maneuver(prompt, variant, feedback=feedback)

    selected = snapshot("Town01", 1, presets=["ClearNoon"])
    selected.catalog.cut_in_sites[0].ego_s = None
    body = request().model_copy(update={"selected_snapshots": [selected]})
    llm = CountingLLM()
    result = generate(body, llm)
    assert result.status == "failed" and llm.calls == 1
    assert "SITE_POSITION_UNAVAILABLE" in result.map_failures[0].message


def test_graph_covers_selected_maps_and_keeps_catalog_out_of_state() -> None:
    body = request(target_count=2).model_copy(update={
        "selected_snapshots": [snapshot("Town01", 1, presets=["ClearNoon"]),
                               snapshot("Town03", 2, presets=["ClearNoon"])]
    })
    state = run_graph(body, FakeLLM(), seed=17)
    assert state["status"] == "completed"
    assert {item.snapshot.map_name for item in state["scenarios"]} == {"Town01", "Town03"}
    assert "selected_snapshots" not in state and "catalog" not in state


def test_unclear_prompt_still_generates_and_keeps_the_note() -> None:
    class AmbiguousLLM(FakeLLM):
        def extract_constraints(self, prompt, *, tools=None, map_names=None):
            return PromptConstraints(ambiguities=["Tốc độ này thuộc xe nào?"])

    result = generate(request(), AmbiguousLLM())
    assert result.status == "completed" and len(result.scenarios) == 1
    assert result.clarification_questions == ["Tốc độ này thuộc xe nào?"]


def test_sessions_are_isolated_and_multi_variant_run_exceeds_default_graph_limit() -> None:
    first = request(target_count=6)
    second = first.model_copy(update={"session_id": "another-session", "seed": 18})
    with ThreadPoolExecutor(max_workers=2) as pool:
        a = pool.submit(generate, first, FakeLLM())
        b = pool.submit(generate, second, FakeLLM())
        first_result, second_result = a.result(), b.result()
    assert first_result.status == second_result.status == "completed"
    assert len(first_result.scenarios) == len(second_result.scenarios) == 6
    assert {item.scenario_id for item in first_result.scenarios}.isdisjoint(
        item.scenario_id for item in second_result.scenarios
    )


def test_failure_on_one_map_keeps_valid_scenario_from_other_map() -> None:
    class OneMapFailsLLM(FakeLLM):
        def propose_maneuver(self, prompt, variant, *, feedback=None, tools=None):
            if variant.site.snapshot.map_name == "Town03":
                raise LLMCallError("LLM_REFUSAL", "Không có đề xuất cho map này.")
            return super().propose_maneuver(prompt, variant, feedback=feedback)

    body = request(target_count=2).model_copy(update={
        "selected_snapshots": [snapshot("Town01", 1, presets=["ClearNoon"]),
                               snapshot("Town03", 2, presets=["ClearNoon"])]
    })
    result = generate(body, OneMapFailsLLM())
    assert result.status == "partial"
    assert [item.snapshot.map_name for item in result.scenarios] == ["Town01"]
    assert [(item.snapshot.map_name, item.code) for item in result.map_failures] == [("Town03", "NO_VALID_PLAN")]


def test_prompt_llm_error_fails_all_selected_maps_before_site_search() -> None:
    class BrokenLLM(FakeLLM):
        def extract_constraints(self, prompt, *, tools=None, map_names=None):
            raise LLMCallError("LLM_EMPTY_RESPONSE", "Không có dữ liệu.")

    result = generate(request(), BrokenLLM())
    assert result.status == "failed" and not result.scenarios
    assert result.map_failures[0].code == "LLM_EMPTY_RESPONSE"
