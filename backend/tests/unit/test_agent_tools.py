from app.modules.catalog.agent_tools import catalog_summary, list_vehicles, list_weather_presets, search_sites, site_section


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
        "vehicles": [
            {"id": "vehicle.tesla.model3", "base_type": "car", "length_m": 4.79, "width_m": 2.16},
            {"id": "vehicle.yamaha.yzf", "number_of_wheels": 2},
            {"id": "vehicle.vespa.zx125", "base_type": "motorcycle"},
            {"id": "vehicle.bh.crossbike", "base_type": "bicycle", "number_of_wheels": 2},
        ],
        "weather_presets": ["Default", "ClearNoon", "HardRainNight"],
    }


def test_search_filters_and_returns_the_longest_first():
    found = search_sites(document(), tags=["straight"], min_length_m=100)
    assert [item["site_id"] for item in found["sites"]] == ["b", "a"]
    # Where the ego starts, and the side the adjacent lane is on (site b: a vehicle moving left comes from the right).
    assert {key: found["sites"][0][key] for key in ("ego_road_id", "ego_lane_id", "adjacent_lane_side")} == {
        "ego_road_id": 7, "ego_lane_id": -2, "adjacent_lane_side": "right"}
    assert found["total_matching"] == 2 and "map_summary" not in found and "hint" not in found
    # Site answers stay small: no anchors, lanes or coordinates.
    assert "ego_anchor" not in found["sites"][0] and "ego_lane" not in found["sites"][0]


def test_seed_reorders_sites_stably():
    first = [item["site_id"] for item in search_sites(document(), seed=1)["sites"]]
    assert first == [item["site_id"] for item in search_sites(document(), seed=1)["sites"]]
    assert sorted(first) == ["a", "b", "c", "d"]
    assert any([item["site_id"] for item in search_sites(document(), seed=seed)["sites"]] != first for seed in range(2, 6))


def test_summary_counts_what_the_map_offers():
    summary = catalog_summary(document())
    assert summary["tags"] == {"straight": 3, "junction": 1, "curve": 1}
    assert summary["speed_limits_kmh"]["unknown"] == 1 and summary["max_length_m"] == 300.0


def test_search_speed_filter_skips_unknown_speeds_and_flags_them():
    assert {item["site_id"] for item in search_sites(document(), speed_kmh_max=100)["sites"]} == {"d"}
    unknown = search_sites(document(), near_junction=True)["sites"]
    assert [item["site_id"] for item in unknown] == ["c"] and unknown[0]["speed_limit_unknown_reason"]
    assert search_sites(document(), lane_change_allowed=False, limit=50)["sites"][0]["site_id"] == "c"


def test_near_junction_accepts_verified_location_tag_even_without_distance():
    doc = document()
    doc["cut_in_sites"].append(site("e", length=100, tags=["intersection_4way"], junction_m=None))
    assert {item["site_id"] for item in search_sites(doc, near_junction=True)["sites"]} == {"c", "e"}


def test_each_section_answers_only_its_part_in_corridor_order():
    geometry = site_section(document(), "a", "geometry")
    assert geometry["upstream_length_m"] == 20.0 and "traffic_lights" not in geometry
    speed = site_section(document(), "a", "speed")
    assert [sign["value"] for sign in speed["signs"]] == [90.0]
    assert speed["ego_road"] == [{"road_id": 7, "from_s": 0.0, "max_kmh": 104.6}]
    controls = site_section(document(), "a", "controls")
    assert [(light["opendrive_id"], light["controls_ego_road"]) for light in controls["traffic_lights"]] == [("11", True)]
    assert controls["stop_or_yield_signs"][0]["kind"] == "stop"
    assert controls["crosswalks"] == [{"along_m": 100.0, "lateral_m": 1.0}]
    assert [junction["junction_id"] for junction in site_section(document(), "a", "junctions")["junctions"]] == [5]
    assert site_section(document(), "missing", "speed") is None


def test_records_without_a_position_are_skipped():
    doc = document()
    # Town03 has junctions known only from the OpenDRIVE, without center/extent.
    doc["junctions"].append({"junction_id": 6, "incoming_road_count": 3, "four_way_candidate": False})
    doc["crosswalks"].append({"polygon": []})
    assert [junction["junction_id"] for junction in site_section(doc, "a", "junctions")["junctions"]] == [5]
    assert len(site_section(doc, "a", "controls")["crosswalks"]) == 1


def test_vehicles_and_weather_lists():
    cars = list_vehicles(document(), "car")
    assert cars["vehicles"] == [{"id": "vehicle.tesla.model3", "kind": "car", "length_m": 4.8, "width_m": 2.2}]
    assert {item["id"] for item in list_vehicles(document(), "motorcycle")["vehicles"]} == {"vehicle.yamaha.yzf", "vehicle.vespa.zx125"}
    everything = {item["id"]: item["kind"] for item in list_vehicles(document(), "all")["vehicles"]}
    assert everything["vehicle.bh.crossbike"] == "bicycle" and everything["vehicle.yamaha.yzf"] == "motorcycle"
    assert list_weather_presets(document()) == {"weather_presets": ["ClearNoon", "HardRainNight"]}


def test_no_match_returns_what_the_map_offers():
    found = search_sites(document(), tags=["intersection_4way"])
    assert found["total_matching"] == 0 and found["sites"] == []
    assert found["map_summary"]["tags"] == {"straight": 3, "junction": 1, "curve": 1} and found["hint"]
