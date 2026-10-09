from app.modules.catalog.agent_tools import search_sites, site_context


def site(site_id: str, *, length: float, tags: list[str], speed: float | None = 104.6, side: str = "right",
         allowed: bool = True, junction_m: float | None = None) -> dict:
    return {
        "site_id": site_id, "location_tags": tags, "available_length_m": length, "upstream_length_m": 20.0,
        "speed_limit_kmh": speed, "target_side": side, "lane_change_allowed_throughout": allowed,
        "marking_between": ["Broken"], "max_heading_change_deg": 0.0, "first_junction_m": junction_m,
        "ends_at": "max_length", "lane_width_m": {"ego": 3.5, "motorcycle": 3.5},
        "ego_lane": {"road_id": 7, "section_id": 0, "lane_id": -2},
        "motorcycle_lane": {"road_id": 7, "section_id": 0, "lane_id": -1},
        # Heading +x, yaw stored unnormalised as the Bridge sometimes sends it.
        "ego_anchor": {"x": 0.0, "y": 0.0, "z": 0.0, "yaw": 360.0},
        "motorcycle_anchor": {"x": 0.0, "y": -3.5, "z": 0.0, "yaw": 360.0},
    }


def document() -> dict:
    return {
        "map_name": "Town04",
        "cut_in_sites": [
            site("a", length=150.0, tags=["straight"]),
            site("b", length=300.0, tags=["straight"], side="left"),
            site("c", length=80.0, tags=["straight", "junction"], speed=None, allowed=False, junction_m=0.0),
            site("d", length=200.0, tags=["curve"], speed=88.5),
        ],
        "road_speeds": [{"road_id": 7, "from_s": 0.0, "max_kmh": 104.6}, {"road_id": 8, "from_s": 0.0, "max_kmh": 48.3}],
        "landmarks": [
            {"type": "274", "name": "Speed_90", "value": 90.0, "unit": "mph", "road_id": 7, "x": 60.0, "y": 2.0},
            {"type": "274", "name": "Speed_30", "value": 30.0, "unit": "mph", "road_id": 9, "x": 60.0, "y": 300.0},
            {"type": "206", "name": "Sign_Stop", "road_id": 7, "x": 140.0, "y": -5.0},
            {"type": "1000001", "name": "Signal_3Light_Post01", "road_id": 7, "x": 30.0, "y": 0.0},
        ],
        "traffic_lights": [
            {"actor_id": 1, "opendrive_id": "11", "x": 145.0, "y": 4.0, "green_s": 10.0, "yellow_s": 3.0, "red_s": 2.0,
             "affected_lanes": [{"road_id": 7, "section_id": 0, "lane_id": -2}]},
            {"actor_id": 2, "opendrive_id": "12", "x": -200.0, "y": 0.0, "affected_lanes": []},
        ],
        "crosswalks": [{"center": {"x": 100.0, "y": 1.0, "z": 0.0}, "polygon": []}],
        "junctions": [{"junction_id": 5, "center": {"x": 170.0, "y": 0.0, "z": 0.0}, "extent": {"x": 15.0, "y": 15.0},
                       "incoming_road_count": 4, "four_way_candidate": True}],
    }


def test_search_filters_and_returns_the_longest_first():
    found = search_sites(document(), tags=["straight"], min_length_m=100)
    assert [item["site_id"] for item in found["sites"]] == ["b", "a"]
    assert found["total_matching"] == 2 and found["total_sites"] == 4
    assert found["map_summary"]["tags"] == {"straight": 3, "junction": 1, "curve": 1}


def test_search_speed_filter_skips_unknown_speeds_and_flags_them():
    assert {item["site_id"] for item in search_sites(document(), speed_kmh_max=100)["sites"]} == {"d"}
    unknown = search_sites(document(), near_junction=True)["sites"]
    assert [item["site_id"] for item in unknown] == ["c"] and unknown[0]["speed_limit_unknown_reason"]
    assert search_sites(document(), lane_change_allowed=False, limit=50)["sites"][0]["site_id"] == "c"
    assert search_sites(document())["map_summary"]["speed_limits_kmh"]["unknown"] == 1


def test_site_context_lists_what_lies_on_the_corridor_in_order():
    context = site_context(document(), "a")
    assert context["site"]["ego_anchor"]["yaw"] == 0.0
    assert [sign["name"] for sign in context["speed_limit"]["signs"]] == ["Speed_90"]
    assert context["speed_limit"]["ego_road"] == [{"road_id": 7, "from_s": 0.0, "max_kmh": 104.6}]
    assert [(light["opendrive_id"], light["controls_ego_road"]) for light in context["traffic_lights"]] == [("11", True)]
    assert context["stop_or_yield_signs"][0]["kind"] == "stop"
    assert context["crosswalks"] == [{"along_m": 100.0, "lateral_m": 1.0}]
    assert [junction["junction_id"] for junction in context["junctions"]] == [5]
    assert site_context(document(), "missing") is None


def test_records_without_a_position_are_skipped():
    doc = document()
    # Town03 has junctions known only from the OpenDRIVE, without center/extent.
    doc["junctions"].append({"junction_id": 6, "incoming_road_count": 3, "four_way_candidate": False})
    doc["crosswalks"].append({"polygon": []})
    assert [junction["junction_id"] for junction in site_context(doc, "a")["junctions"]] == [5]
