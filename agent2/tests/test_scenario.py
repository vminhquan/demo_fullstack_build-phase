from __future__ import annotations

import shutil
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
from app.contracts import GenerationRequest
from app.scenario.check import check, map_cannot_host
from app.scenario.explore import describe, find_starts
from app.scenario.lanes import LaneGraph
from app.scenario.render import render_xosc
from app.scenario.spec import ScenarioPlan, ScenarioSpec, StartLane, ValidationResult
from world import snapshot

SCHEMA = Path(__file__).resolve().parents[1] / "knowledge/schemas/OpenSCENARIO_1_0.xsd"
SNAPSHOT = snapshot()
GRAPHS = {"Town01": LaneGraph(SNAPSHOT.catalog)}


def request(prompt: str = "Tình huống nguy hiểm cho ô tô ego", **changes) -> GenerationRequest:
    return GenerationRequest.model_validate({"session_id": "s", "prompt": prompt, "selected_snapshots": [SNAPSHOT],
                                             "ego_blueprint_id": "vehicle.tesla.model3", **changes})


def action(actor: str, at: float, kind: str, **values) -> dict:
    return {"actor": actor, "at_time_s": at, "type": kind, "target_speed_kmh": None, "acceleration_mps2": None,
            "direction": None, "duration_s": None, **values}


def spec(actors: list[dict], actions: list[dict], *, ego_kmh: float = 50, critical: str | None = None,
         expected: str = "near_miss", preset: str = "ClearNoon", duration: float = 8, start_s: float = 20) -> ScenarioSpec:
    return ScenarioSpec.model_validate({
        "map_name": "Town01", "title": "test", "ego": {"blueprint_id": None, "start": {"road_id": 1, "lane_id": -1, "s": start_s},
                                                        "speed_kmh": ego_kmh},
        "actors": actors, "actions": actions, "critical_actor": critical or actors[0]["id"], "expected": expected,
        "weather_preset": preset, "lighting": None, "road_surface": None, "duration_s": duration,
    })


def actor(name: str, blueprint: str, lane_offset: int, ds: float, kmh: float) -> dict:
    return {"id": name, "blueprint_id": blueprint, "lane_offset": lane_offset, "ds_m": ds, "speed_kmh": kmh}


MOTO = "vehicle.yamaha.yzf"
TRUCK = "vehicle.carlamotors.carlacola"


def cut_in(**changes) -> ScenarioSpec:
    """Motorcycle from the right lane, 12 m ahead, merges into the ego's lane then slows down."""
    values = {"ds": 12, "moto_kmh": 50, "ego_kmh": 50, "at": 1, "brake_to": 30}
    values.update(changes)
    return spec([actor("moto1", MOTO, -1, values["ds"], values["moto_kmh"])],
                [action("moto1", values["at"], "lane_change", direction="left", duration_s=2.0),
                 action("moto1", values["at"] + 2, "speed", target_speed_kmh=values["brake_to"], acceleration_mps2=3)],
                ego_kmh=values["ego_kmh"])


def codes(result) -> set[str]:
    assert isinstance(result, ValidationResult), result
    return {issue.code for issue in result.issues}


def test_lane_graph_follows_topology_and_same_direction_neighbours():
    graph = GRAPHS["Town01"]
    path = graph.path(1, -1, 250, ahead=150, behind=40)
    assert {point.road_id for point in path.points} == {1, 2} and round(path.origin) == 40
    assert path.length == pytest.approx(190, abs=2.5)
    start = graph.locate(1, -1, 51).point
    assert graph.neighbor(start, "right").lane_id == -2
    assert graph.neighbor(start, "left") is None  # lane 1 runs the other way
    assert graph.offset_lane(graph.locate(1, -2, 50).point, 1).lane_id == -1


def test_a_motorcycle_cut_in_passes_and_exports_valid_open_scenario(tmp_path: Path):
    plan = check(cut_in(), request(), [SNAPSHOT], GRAPHS)
    assert isinstance(plan, ScenarioPlan), plan
    assert plan.actors[0].kind == "motorcycle" and plan.actors[0].lane_id == -2 and plan.actors[0].s == 32
    assert plan.metrics["min_ttc_s"] is not None and plan.metrics["min_ttc_s"] <= 3
    xml, _ = render_xosc(plan, "Town01")
    root = ET.fromstring(xml.split("\n", 1)[1])
    assert root.find(".//ScenarioObject[@name='moto1']/Vehicle").get("vehicleCategory") == "motorbike"
    assert root.find(".//RelativeTargetLane").attrib == {"entityRef": "moto1", "value": "1"}
    assert root.find(".//Private[@entityRef='moto1']//LanePosition").attrib == {"roadId": "1", "laneId": "-2", "s": "32",
                                                                             "offset": "0"}
    if shutil.which("xmllint"):
        file = tmp_path / "scenario.xosc"
        file.write_text(xml, encoding="utf-8")
        result = subprocess.run(["xmllint", "--noout", "--schema", str(SCHEMA), str(file)], capture_output=True, text=True)
        assert result.returncode == 0, result.stderr


def test_other_road_users_and_manoeuvres_need_no_special_code():
    truck = spec([actor("truck1", TRUCK, -1, 15, 45)], [action("truck1", 0.5, "lane_change", direction="left", duration_s=3.0)],
                 ego_kmh=55)
    assert isinstance(check(truck, request("xe tải tạt đầu"), [SNAPSHOT], GRAPHS), ScenarioPlan)
    braking = spec([actor("lead", "vehicle.audi.a2", 0, 30, 50)], [action("lead", 2, "brake_to_stop", acceleration_mps2=6)])
    plan = check(braking, request("xe phía trước phanh gấp"), [SNAPSHOT], GRAPHS)
    assert isinstance(plan, ScenarioPlan) and plan.metrics["min_ttc_s"] <= 3


def test_a_motorcycle_merging_far_ahead_is_no_incident():
    # Seen live: 104.6 km/h ego, 65 km/h motorcycle 250 m ahead; it passed the old checks and was never seen.
    far = spec([actor("moto1", MOTO, -1, 250, 65)], [action("moto1", 1, "lane_change", direction="left", duration_s=1.6)],
               ego_kmh=104.6, duration=5)
    result = check(far, request(), [SNAPSHOT], GRAPHS)
    [issue] = [item for item in result.issues if item.code == "INCIDENT_TOO_MILD"]
    assert "TTC nhỏ nhất" in issue.message and "cần khoảng cách" in issue.message


def test_a_motorcycle_pulling_away_is_no_incident():
    result = check(cut_in(moto_kmh=70, brake_to=70), request(), [SNAPSHOT], GRAPHS)
    assert codes(result) == {"INCIDENT_TOO_MILD"} and "không chậm hơn ego" in result.issues[0].message


def test_a_cut_in_with_no_room_is_unavoidable():
    result = check(cut_in(ds=5, ego_kmh=60, moto_kmh=40, at=0), request(), [SNAPSHOT], GRAPHS)
    assert "INCIDENT_UNAVOIDABLE" in codes(result)


def test_the_kind_limits_apply_to_every_actor():
    quick_truck = spec([actor("truck1", TRUCK, -1, 15, 45)], [action("truck1", 0.5, "lane_change", direction="left", duration_s=1.6)])
    assert "LANE_CHANGE_TOO_FAST" in codes(check(quick_truck, request(), [SNAPSHOT], GRAPHS))
    fast_bike = spec([actor("bike", "vehicle.bh.crossbike", -1, 10, 60)], [])
    assert "SPEED_LIMIT" in codes(check(fast_bike, request(), [SNAPSHOT], GRAPHS))


def test_what_the_prompt_says_must_show():
    assert "PROMPT_ACTOR_MISSING" in codes(check(cut_in(), request("xe tải tạt đầu ô tô"), [SNAPSHOT], GRAPHS))
    assert "PROMPT_WEATHER_MISMATCH" in codes(check(cut_in(), request("xe máy tạt đầu khi trời mưa"), [SNAPSHOT], GRAPHS))
    assert "PROMPT_ROAD_MISMATCH" in codes(check(cut_in(), request("xe máy tạt đầu ở khúc cua"), [SNAPSHOT], GRAPHS))
    assert "ACTOR_LOCKED" in codes(check(cut_in(), request(actor_type="truck"), [SNAPSHOT], GRAPHS))
    rain = cut_in().model_copy(update={"weather_preset": "HardRainNight"})
    assert isinstance(check(rain, request("xe máy tạt đầu ban đêm trời mưa"), [SNAPSHOT], GRAPHS), ScenarioPlan)


def test_placement_and_road_problems_are_reported():
    assert "NO_SUCH_LANE" in codes(check(spec([actor("x", MOTO, 1, 10, 50)], []), request(), [SNAPSHOT], GRAPHS))
    overlap = spec([actor("x", "vehicle.audi.a2", 0, 2, 50)], [])
    assert "INITIAL_OVERLAP" in codes(check(overlap, request(), [SNAPSHOT], GRAPHS))
    # Road 2 ends 80 m after the ego's start; the only other actor is behind it.
    behind = spec([actor("x", MOTO, -1, -20, 50)], [])
    end_of_road = behind.model_copy(update={"ego": behind.ego.model_copy(update={"start": StartLane(road_id=2, lane_id=-1, s=120)})})
    assert "EGO_RUNS_OUT_OF_ROAD" in codes(check(end_of_road, request(), [SNAPSHOT], GRAPHS))
    locked = cut_in().model_copy(update={"ego": cut_in().ego.model_copy(update={"blueprint_id": "vehicle.audi.a2"})})
    assert "EGO_LOCKED" in codes(check(locked, request(), [SNAPSHOT], GRAPHS))


def test_map_questions_answer_from_the_lane_graph():
    found = find_starts(GRAPHS["Town01"], tags=["multi_lane"], min_path_m=200, min_left_lanes=None, min_right_lanes=1,
                        seed=1, limit=5)
    assert found["total_matching"] > 0 and all(start["lane_id"] == -1 for start in found["starts"])
    assert find_starts(GRAPHS["Town01"], tags=["curve"], min_path_m=None, min_left_lanes=None, min_right_lanes=None,
                       seed=1, limit=5)["total_matching"] == 0
    info = describe(GRAPHS["Town01"], 1, -2, 100).as_dict()
    assert info["left_lanes_same_direction"] == 1 and info["right_lanes_same_direction"] == 0 and "straight" in info["tags"]


def test_the_three_live_submissions_get_feedback_naming_the_real_lanes():
    # Seen live (Town04 road 22 lane -2: only a right-hand same-direction lane). Here: ego on lane -1, lane -2 on its right.
    on_left = spec([actor("moto1", MOTO, 1, -10, 40)], [action("moto1", 2, "speed", target_speed_kmh=40, acceleration_mps2=2)])
    [issue] = check(on_left, request(), [SNAPSHOT], GRAPHS).issues
    assert issue.code == "NO_SUCH_LANE" and "-1 (phải)" in issue.message and "không có làn nào khác" in issue.message
    no_change = spec([actor("moto1", MOTO, -1, -10, 40)], [action("moto1", 2, "speed", target_speed_kmh=40, acceleration_mps2=2)])
    [issue] = check(no_change, request(), [SNAPSHOT], GRAPHS).issues
    assert issue.code == "NO_INCIDENT" and "into_ego_lane" in issue.message
    same_lane = spec([actor("moto1", MOTO, 0, -10, 40)], [action("moto1", 2, "lane_change", direction="left", duration_s=2)])
    [issue] = check(same_lane, request(), [SNAPSHOT], GRAPHS).issues
    assert issue.code == "NO_TARGET_LANE" and "-1 (phải)" in issue.message and "into_ego_lane" in issue.message


def test_into_ego_lane_finds_the_side_and_behind_needs_to_be_ahead_first():
    behind = spec([actor("moto1", MOTO, -1, -10, 40)], [action("moto1", 2, "lane_change", direction="into_ego_lane", duration_s=2)])
    [issue] = check(behind, request(), [SNAPSHOT], GRAPHS).issues
    assert issue.code == "INCIDENT_TOO_MILD" and "PHÍA SAU" in issue.message
    plan = check(spec([actor("moto1", MOTO, -1, 12, 50)],
                      [action("moto1", 1, "lane_change", direction="into_ego_lane", duration_s=2.0),
                       action("moto1", 3, "speed", target_speed_kmh=30, acceleration_mps2=3)]), request(), [SNAPSHOT], GRAPHS)
    assert isinstance(plan, ScenarioPlan) and plan.actions[0].resolved_direction == "left"
    root = ET.fromstring(render_xosc(plan, "Town01")[0].split("\n", 1)[1])
    assert root.find(".//RelativeTargetLane").get("value") == "1"


def test_the_speed_hint_is_actionable():
    # Seen live: 104.6 km/h ego, motorcycle 10 m ahead at 110 km/h merging at 2 s: it pulls away, no incident.
    import re

    fast = spec([actor("moto1", MOTO, -1, 10, 110)], [action("moto1", 2, "lane_change", direction="into_ego_lane", duration_s=1.5)],
                ego_kmh=104.6, duration=10)
    [issue] = check(fast, request(), [SNAPSHOT], GRAPHS).issues
    assert issue.code == "INCIDENT_TOO_MILD" and "không chậm hơn ego" in issue.message
    at, target = re.search(r"at_time_s=([\d.]+), target_speed_kmh=(\d+)", issue.message).groups()
    # Doing what the hint says gives a valid scenario.
    fixed = fast.model_copy(update={"actions": [*fast.actions, ScenarioSpec.model_validate(
        {**fast.model_dump(), "actions": [action("moto1", float(at), "speed", target_speed_kmh=float(target), acceleration_mps2=4)]}
    ).actions[0]]})
    assert isinstance(check(fixed, request(), [SNAPSHOT], GRAPHS), ScenarioPlan)


def test_the_overtake_hint_gives_numbers():
    slow_behind = spec([actor("moto1", MOTO, -1, -10, 80)], [action("moto1", 2, "lane_change", direction="into_ego_lane", duration_s=1.5)],
                       ego_kmh=104.6, duration=10)
    [issue] = check(slow_behind, request(), [SNAPSHOT], GRAPHS).issues
    assert issue.code == "INCIDENT_TOO_MILD" and "PHÍA SAU" in issue.message
    assert "ds_m >=" in issue.message and "km/h để vượt lên" in issue.message



def test_the_live_rear_end_is_not_a_cut_in():
    # Seen live (Town01, single lane): motorcycle 10 m behind in the ego's lane, faster: it hits the ego from behind.
    rear = spec([actor("moto1", MOTO, 0, -10, 35)], [action("moto1", 5, "speed", target_speed_kmh=20, acceleration_mps2=2)],
                ego_kmh=30, duration=20)
    found = codes(check(rear, request("xe máy tạt đầu ô tô vào ban đêm"), [SNAPSHOT], GRAPHS))
    assert {"PROMPT_BEHAVIOUR_MISMATCH", "REAR_INCIDENT"} <= found


def at_junction(start_s: float) -> ScenarioSpec:
    base = cut_in()
    return base.model_copy(update={"ego": base.ego.model_copy(update={"start": StartLane(road_id=10, lane_id=-1, s=start_s)})})


def test_a_junction_prompt_needs_the_incident_at_the_junction():
    # Junction 50 is 150 m after road 10 starts; the cut-in's incident happens about 77 m after the ego's start.
    prompt = "xe máy tạt đầu ô tô ở ngã tư"
    assert isinstance(check(at_junction(60), request(prompt), [SNAPSHOT], GRAPHS), ScenarioPlan)
    far = check(at_junction(0), request(prompt), [SNAPSHOT], GRAPHS)
    assert codes(far) == {"INCIDENT_NOT_AT_JUNCTION"} and "sự cố phải xảy ra trong khoảng 11" in far.issues[0].message
    assert "PROMPT_ROAD_MISMATCH" in codes(check(cut_in(), request(prompt), [SNAPSHOT], GRAPHS))  # road 1: no junction


def test_four_way_approach_tag_and_map_capabilities():
    from app.scenario.explore import capabilities

    info = describe(GRAPHS["Town01"], 10, -1, 60).as_dict()
    assert "intersection_4way_approach" in info["tags"] and info["junction_ahead_four_way"] is True
    assert capabilities(GRAPHS["Town01"]) == {"multi_lane": True, "junction": True, "four_way": True,
                                              "multi_lane_before_junction": True, "multi_lane_before_four_way": True}
    single = {"Town01": LaneGraph(snapshot(single_lane=True).catalog)}
    assert capabilities(single["Town01"])["multi_lane"] is False and capabilities(single["Town01"])["four_way"] is True
    assert "làn bên cạnh" in map_cannot_host("xe máy tạt đầu ô tô", single)
    # Seen live on Town04: four-ways exist and so do multi-lane roads, but never at the same place.
    assert "ngã tư" in map_cannot_host("xe máy tạt đầu ô tô ở ngã tư", single)
    assert map_cannot_host("ô tô phanh gấp ở ngã tư", single) is None
    assert map_cannot_host("xe máy tạt đầu ô tô ở ngã tư", GRAPHS) is None



def test_dust_at_night_takes_the_preset_weather_and_the_asked_light():
    # Seen live: "trời bụi mù vào ban đêm"; CARLA's only dust preset (DustStorm) is daytime.
    snap = snapshot("Town01", 1, presets=["DustStorm", "ClearNight"])
    graphs = {"Town01": LaneGraph(snap.catalog)}
    prompt = "xe máy tạt đầu xe ego lúc trời bụi mù vào ban đêm"
    day = cut_in().model_copy(update={"weather_preset": "DustStorm"})
    found = check(day, request(prompt, selected_snapshots=[snap]), [snap], graphs)
    assert codes(found) == {"PROMPT_LIGHT_MISMATCH"} and "lighting='night'" in found.issues[0].message
    night = day.model_copy(update={"lighting": "night"})
    plan = check(night, request(prompt, selected_snapshots=[snap]), [snap], graphs)
    assert isinstance(plan, ScenarioPlan)
    assert plan.environment.time_of_day_hour == 22 and plan.environment.weather_preset is None
    assert plan.environment.base_preset == "DustStorm" and "dust" in plan.environment.weather_conditions
    root = ET.fromstring(render_xosc(plan, "Town01")[0].split("\n", 1)[1])
    assert root.find(".//TimeOfDay").get("dateTime").endswith("T22:00:00") and root.find(".//Fog").get("visualRange") == "80"



def test_a_cut_in_must_merge_close_in_front():
    # Seen live: the motorcycle merged 31 m ahead of the ego: it looked early and far.
    import re

    far = cut_in(ds=35, moto_kmh=50)
    found = check(far, request("xe máy tạt đầu ô tô"), [SNAPSHOT], GRAPHS)
    assert codes(found) == {"CUT_IN_TOO_FAR"} and "tối đa 14 m" in found.issues[0].message
    ds, at = re.search(r"ds_m=(-?[\d.]+) và lane_change at_time_s=([\d.]+)", found.issues[0].message).groups()
    fixed = far.model_copy(update={"actors": [far.actors[0].model_copy(update={"ds_m": float(ds)})],
                                   "actions": [far.actions[0].model_copy(update={"at_time_s": float(at)}), *far.actions[1:]]})
    plan = check(fixed, request("xe máy tạt đầu ô tô"), [SNAPSHOT], GRAPHS)
    assert isinstance(plan, ScenarioPlan) and plan.metrics["entry_ttc_s"] >= 0.5
    # A prompt not about a cut-in is not held to the close-merge rule.
    assert "CUT_IN_TOO_FAR" not in codes(check(far, request("xe máy chuyển làn phía trước"), [SNAPSHOT], GRAPHS))


def test_tuning_moves_the_start_so_the_incident_is_at_the_junction():
    # Variant #3 seen live: the incident happened past the junction window. Only the start was wrong.
    from app.scenario.check import tune

    plan = tune(at_junction(0), request("xe máy tạt đầu ô tô ở ngã tư"), [SNAPSHOT], GRAPHS)
    assert isinstance(plan, ScenarioPlan)
    # Too early (77 m into a 150 m road): the ego starts further along.
    assert any(note.startswith("INCIDENT_NOT_AT_JUNCTION: ego xuất phát dời +") for note in plan.tuning)
    forward = tune(at_junction(120), request("xe máy tạt đầu ô tô ở ngã tư"), [SNAPSHOT], GRAPHS)
    assert codes(check(at_junction(120), request("xe máy tạt đầu ô tô ở ngã tư"), [SNAPSHOT], GRAPHS)) != set()
    assert isinstance(forward, ScenarioPlan) and any("ego xuất phát dời -" in note for note in forward.tuning)
    # A structural mistake is not tuned away.
    wrong = spec([actor("x", MOTO, -1, 10, 50)], [])  # never changes lanes: no incident
    result = tune(wrong, request(), [SNAPSHOT], GRAPHS)
    assert isinstance(result, ValidationResult) and "TUNING_TRIED" not in codes(result)



def test_tuning_moves_an_actor_to_the_side_that_has_a_lane_and_backs_off_an_unavoidable_merge():
    # Seen live (#2, #10): both actors put on the left, where only a right-hand lane exists.
    from app.scenario.check import tune

    left = spec([actor("moto1", MOTO, 1, 12, 50), actor("car1", "vehicle.audi.a2", 1, 40, 50)],
                [action("moto1", 1, "lane_change", direction="into_ego_lane", duration_s=2.0),
                 action("moto1", 3, "speed", target_speed_kmh=30, acceleration_mps2=3)])
    plan = tune(left, request("xe máy và ô tô tạt đầu"), [SNAPSHOT], GRAPHS)
    assert isinstance(plan, ScenarioPlan), plan
    assert {actor.lane_id for actor in plan.actors} == {-2}
    assert sum(note.startswith("NO_SUCH_LANE") for note in plan.tuning) == 2
    # Seen live (#1): the merge leaves no time to react; a nearby start/time that passes is found.
    tight = cut_in(ds=5, ego_kmh=60, moto_kmh=40, at=0)
    plan = tune(tight, request(), [SNAPSHOT], GRAPHS)
    assert isinstance(plan, ScenarioPlan) and plan.tuning[0].startswith("INCIDENT_UNAVOIDABLE")
