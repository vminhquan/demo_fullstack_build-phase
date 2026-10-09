from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from app.catalog.find_sites import find_cut_in_sites
from app.cut_in.model import EnvironmentSelection, SampledContext
from app.cut_in.sample import SampledVariant
from app.llm.client import LLMCallError, OpenAILLM
from app.llm.prompts import proposal_messages
from test_sample import snapshot


class FakeCompletions:
    def __init__(self, parsed, *, refusal=None, finish_reason="stop"):
        self.parsed = parsed
        self.refusal = refusal
        self.finish_reason = finish_reason
        self.calls = []

    def parse(self, **kwargs):
        self.calls.append(kwargs)
        message = SimpleNamespace(parsed=self.parsed, refusal=self.refusal)
        return SimpleNamespace(choices=[SimpleNamespace(message=message, finish_reason=self.finish_reason)])


def fake_client(completions: FakeCompletions):
    return SimpleNamespace(chat=SimpleNamespace(completions=completions))


def variant() -> SampledVariant:
    selected = snapshot("Town01", 1)
    site = find_cut_in_sites([selected]).sites[0]
    context = SampledContext(
        site_id=site.site_id, ego_blueprint_id="vehicle.tesla.model3", motorcycle_blueprint_id="vehicle.yamaha.yzf",
        ego_speed_kmh=37, motorcycle_speed_kmh=43,
        environment=EnvironmentSelection(profile_id="carla_preset:HardRainNight", weather_conditions=["rain"],
                                         weather_preset="HardRainNight", time_of_day_hour=22,
                                         road_surface="slippery", friction_scale_factor=0.6),
    )
    return SampledVariant(site=site, context=context)


def test_extract_constraints_keeps_unspecified_fields_empty() -> None:
    reply = {
        "location_tags": [], "weather_conditions": [], "lighting": None, "road_surface": None,
        "ego_speed_kmh": None, "motorcycle_speed_kmh": None,
        "ambiguities": [], "unsupported_requirements": [],
    }
    completions = FakeCompletions(reply)
    llm = OpenAILLM(sdk_client=fake_client(completions))
    constraints = llm.extract_constraints("Xe máy tạt đầu ô tô")
    assert constraints.location_tags == [] and constraints.weather_conditions == []
    assert constraints.ego_speed_kmh is None and constraints.road_surface is None
    assert completions.calls[0]["model"] == "gpt-4o-mini"
    assert completions.calls[0]["response_format"].__name__ == "_PromptExtraction"
    assert completions.calls[0]["messages"][1]["content"] == "Xe máy tạt đầu ô tô"


def test_extract_intersection_and_weather_without_downgrading() -> None:
    reply = {
        "location_tags": ["intersection_4way"], "weather_conditions": ["rain"],
        "lighting": "night", "road_surface": "slippery", "ego_speed_kmh": 37,
        "motorcycle_speed_kmh": 43, "ambiguities": [], "unsupported_requirements": [],
    }
    llm = OpenAILLM(sdk_client=fake_client(FakeCompletions(reply)))
    constraints = llm.extract_constraints("Xe máy tạt đầu ô tô ở ngã tư, trời mưa ban đêm, đường trơn")
    assert constraints.location_tags == ["intersection_4way"]
    assert constraints.weather_conditions == ["rain"] and constraints.road_surface == "slippery"


def test_proposal_only_sets_four_fields_and_preserves_sampled_facts() -> None:
    sampled = variant()
    reply = {"motorcycle_start_offset_m": 10, "trigger_time_s": 2,
             "lane_change_duration_s": 2.5, "desired_lead_gap_m": 5,
             "site_id": "invented-site", "ego_speed_kmh": 100}
    completions = FakeCompletions(reply)
    llm = OpenAILLM(sdk_client=fake_client(completions))
    plan = llm.propose_maneuver("Tạt đầu", sampled, feedback=["Khoảng cách trước xe cần lớn hơn."])
    assert plan.site_id == sampled.site.site_id
    assert plan.ego_blueprint_id == sampled.context.ego_blueprint_id
    assert plan.environment == sampled.context.environment
    assert plan.ego_speed_kmh == 37 and plan.motorcycle_speed_kmh == 43
    assert plan.motorcycle_start_offset_m == 10 and plan.desired_lead_gap_m == 5
    request = json.loads(completions.calls[0]["messages"][1]["content"])
    assert request["map_name"] == "Town01" and request["available_length_m"] == 65
    assert request["feedback"] == ["Khoảng cách trước xe cần lớn hơn."]
    assert "waypoints" not in request
    assert completions.calls[0]["response_format"].__name__ == "_ManeuverProposal"


def test_invalid_proposal_is_rejected_before_plan_output() -> None:
    reply = {"motorcycle_start_offset_m": -8, "trigger_time_s": 2,
             "lane_change_duration_s": -1, "desired_lead_gap_m": 5}
    llm = OpenAILLM(sdk_client=fake_client(FakeCompletions(reply)))
    with pytest.raises(LLMCallError) as error:
        llm.propose_maneuver("Tạt đầu", variant())
    assert error.value.code == "LLM_INVALID_PROPOSAL"


@pytest.mark.parametrize("completions,code", [
    (FakeCompletions(None, refusal="refused"), "LLM_REFUSAL"),
    (FakeCompletions(None, finish_reason="length"), "LLM_INCOMPLETE_RESPONSE"),
    (FakeCompletions(None), "LLM_UNPARSEABLE_RESPONSE"),
])
def test_refusal_truncation_and_missing_parsed_response(completions: FakeCompletions, code: str) -> None:
    llm = OpenAILLM(sdk_client=fake_client(completions))
    with pytest.raises(LLMCallError) as error:
        llm.extract_constraints("Xe máy tạt đầu ô tô")
    assert error.value.code == code


def test_missing_api_key_fails_before_sdk_import() -> None:
    with pytest.raises(LLMCallError) as error:
        OpenAILLM(api_key=None)
    assert error.value.code == "OPENAI_NOT_CONFIGURED"


def test_proposal_prompt_has_only_compact_site_facts() -> None:
    messages = proposal_messages("Tạt đầu", variant())
    facts = json.loads(messages[1]["content"])
    assert set(facts) == {"prompt", "map_name", "site_id", "ego_lane", "motorcycle_lane",
                          "available_length_m", "ego_speed_kmh", "motorcycle_speed_kmh",
                          "weather_preset", "road_surface", "feedback"}


def test_sampled_variant_rejects_context_from_another_site() -> None:
    sampled = variant()
    with pytest.raises(ValueError, match="site_id does not match"):
        SampledVariant(site=sampled.site, context=sampled.context.model_copy(update={"site_id": "other-site"}))


def test_only_quoted_requirements_block_and_missing_details_do_not() -> None:
    reply = {
        "location_tags": [], "weather_conditions": [], "lighting": None, "road_surface": None,
        "ego_speed_kmh": None, "motorcycle_speed_kmh": None, "ambiguities": [],
        "unsupported_requirements": [
            {"quote": "trời  có TUYẾT", "reason": "tuyết ngoài miền hỗ trợ"},
            {"quote": "", "reason": "Không có thông tin về tốc độ"},
            {"quote": "tốc độ và thời tiết", "reason": "Thiếu tốc độ và thời tiết"},
        ],
    }
    llm = OpenAILLM(sdk_client=fake_client(FakeCompletions(reply)))
    constraints = llm.extract_constraints("Xe máy tạt đầu ô tô khi trời có tuyết")
    assert constraints.unsupported_requirements == ['"trời  có TUYẾT": tuyết ngoài miền hỗ trợ']
