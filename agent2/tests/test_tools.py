from __future__ import annotations

import json

import httpx
from app.llm.tools import CATALOG_TOOLS, REJECT_TOOL, SUBMIT_TOOL, CatalogApi, CatalogTools, tool_result
from app.scenario.lanes import LaneGraph
from world import snapshot


def backend(handler) -> CatalogApi:
    client = httpx.Client(base_url="http://backend", headers={"X-API-Key": "k"}, transport=httpx.MockTransport(handler))
    return CatalogApi("http://backend", "k", client=client)


def recording(seen: list, answer: dict | None = None, status: int = 200):
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(status, json=answer if answer is not None else {"ok": True})
    return handler


SEARCH = {"map_name": "Town04", "tags": ["straight"], "min_length_m": 200, "speed_kmh_min": None, "speed_kmh_max": None,
          "target_side": None, "lane_change_allowed": True, "near_junction": None, "limit": 5}


def test_search_goes_to_the_backend_with_the_run_seed():
    seen = []
    tools = CatalogTools(backend(recording(seen, {"total_matching": 1, "sites": [{"site_id": "s"}]})),
                         [snapshot("Town04", 108).ref], seed=7)
    assert json.loads(tool_result(tools, "search_cut_in_sites", json.dumps(SEARCH)))["total_matching"] == 1
    request = seen[0]
    assert request.url.path == "/api/v1/internal/agent/catalog/snapshots/108/cut-in-sites"
    assert request.url.params.get_list("tags") == ["straight"] and request.url.params["lane_change_allowed"] == "true"
    assert request.url.params["seed"] == "7" and "speed_kmh_min" not in request.url.params
    assert request.headers["X-API-Key"] == "k"


def test_a_map_that_was_not_selected_is_refused_without_a_request():
    seen = []
    tools = CatalogTools(backend(recording(seen)), [snapshot("Town04", 108).ref])
    error = json.loads(tool_result(tools, "search_cut_in_sites", json.dumps({**SEARCH, "map_name": "Town05"})))
    assert "Town04" in error["error"] and seen == []
    assert "Không có tool" in json.loads(tool_result(tools, "draw_map", "{}"))["error"]


def test_each_small_tool_reads_one_endpoint():
    seen = []
    tools = CatalogTools(backend(recording(seen)), [snapshot("Town04", 108).ref], seed=2)
    site = {"map_name": "Town04", "site_id": "a/b"}
    for name in ("get_site_speed", "get_site_controls", "get_site_junctions", "get_site_geometry"):
        tool_result(tools, name, json.dumps(site))
    base = "/api/v1/internal/agent/catalog/snapshots/108/cut-in-sites/a%2Fb"
    assert [request.url.raw_path.decode() for request in seen] == [
        f"{base}/speed", f"{base}/controls", f"{base}/junctions", f"{base}/geometry",
    ]


def test_prefetch_reads_whole_lists_without_a_seed():
    seen = []
    api = backend(recording(seen))
    api.summary(108), api.vehicles(108, "motorcycle"), api.weather_presets(108)
    base = "/api/v1/internal/agent/catalog/snapshots/108"
    assert [request.url.path for request in seen] == [f"{base}/summary", f"{base}/vehicles", f"{base}/weather-presets"]
    assert dict(seen[1].url.params) == {"kind": "motorcycle"}


def test_backend_errors_reach_the_model_as_a_message():
    tools = CatalogTools(backend(recording([], {}, 404)), [snapshot("Town04", 108).ref])
    answer = json.loads(tool_result(tools, "get_site_speed", json.dumps({"map_name": "Town04", "site_id": "x"})))
    assert "site_id 'x' không tồn tại" in answer["error"]
    assert "search_cut_in_sites" in answer["error"]


def test_save_posts_the_scenario_and_returns_the_id():
    seen = []
    assert backend(recording(seen, {"generation_id": 55}, 201)).save_scenario({"snapshot_id": 1}) == 55
    assert seen[0].method == "POST" and seen[0].url.path == "/api/v1/internal/agent/scenario-generations"


def test_the_llm_is_offered_the_reads_plus_submit_and_reject():
    names = [spec["function"]["name"] for spec in CatalogTools.specs()]
    assert names == [*CATALOG_TOOLS, SUBMIT_TOOL, REJECT_TOOL]
    assert all(spec["function"]["strict"] for spec in CatalogTools.specs())


def test_lane_tools_answer_from_the_snapshot_without_the_backend():
    seen = []
    selected = snapshot("Town04", 108)
    tools = CatalogTools(backend(recording(seen)), [selected.ref], seed=3, graphs={"Town04": LaneGraph(selected.catalog)})
    starts = json.loads(tool_result(tools, "find_ego_starts", json.dumps(
        {"map_name": "Town04", "tags": ["multi_lane"], "min_path_m": 100, "min_left_lanes": None, "min_right_lanes": None,
         "limit": 3})))
    assert starts["total_matching"] > 0 and len(starts["starts"]) == 3
    info = json.loads(tool_result(tools, "describe_position", json.dumps({"map_name": "Town04", "road_id": 1, "lane_id": -1, "s": 40})))
    assert info["right_lanes_same_direction"] == 1 and seen == []
    missing = json.loads(tool_result(tools, "describe_position", json.dumps({"map_name": "Town04", "road_id": 9, "lane_id": -1, "s": 0})))
    assert "Không có làn" in missing["error"]


def test_cut_in_search_ignores_constraints_the_prompt_did_not_request():
    seen = []
    selected = snapshot("Town04", 108)
    tools = CatalogTools(backend(recording(seen, {"total_matching": 1, "sites": [{"site_id": "s"}]})),
                         [selected.ref], graphs={"Town04": LaneGraph(selected.catalog)},
                         prompt="xe máy tạt đầu ô tô ở ngã tư")
    starts = json.loads(tool_result(tools, "find_ego_starts", json.dumps({
        "map_name": "Town04", "tags": ["intersection_4way_approach"], "min_path_m": 40,
        "min_left_lanes": 1, "min_right_lanes": 1, "limit": 5,
    })))
    assert starts["total_matching"] > 0 and starts["ignored_filters"] == ["min_left_lanes", "min_right_lanes"]
    assert all(start["left_lanes_same_direction"] + start["right_lanes_same_direction"] > 0 for start in starts["starts"])
    sites = json.loads(tool_result(tools, "search_cut_in_sites", json.dumps({
        "map_name": "Town04", "tags": ["junction", "intersection_4way", "multi_lane"], "min_length_m": 40,
        "speed_kmh_min": 0, "speed_kmh_max": 80, "target_side": "left", "lane_change_allowed": True,
        "near_junction": True, "limit": 10,
    })))
    params = seen[0].url.params
    assert params.get_list("tags") == ["intersection_4way"] and params["near_junction"] == "true"
    assert all(key not in params for key in ("speed_kmh_min", "speed_kmh_max", "target_side", "lane_change_allowed"))
    assert set(sites["ignored_filters"]) == {"speed_kmh_min", "speed_kmh_max", "target_side", "lane_change_allowed", "tags=multi_lane", "tags=junction (using near_junction)"}
