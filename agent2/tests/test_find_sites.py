from __future__ import annotations

from app.catalog.find_sites import find_cut_in_sites
from app.catalog.models import SelectedSnapshot


def snapshot(map_name: str, snapshot_id: int, sites: list[dict] | None) -> SelectedSnapshot:
    return SelectedSnapshot.model_validate({
        "ref": {"snapshot_id": snapshot_id, "map_name": map_name, "content_hash": f"{snapshot_id:064x}"},
        "catalog": {
            "carla_version": "0.9.16", "map_name": map_name,
            "vehicles": [{"id": "vehicle.tesla.model3"}, {"id": "vehicle.yamaha.yzf"}],
            "spawn_points": [{"x": 0, "y": 0}],
            "waypoints": [{"x": 0, "y": 0, "road_id": 1, "lane_id": -1}],
            "cut_in_sites": sites,
        },
    })


def site(site_id: str, tags: list[str], length: float = 65.0) -> dict:
    return {
        "site_id": site_id,
        "ego_lane": {"road_id": 1, "section_id": 0, "lane_id": -1},
        "motorcycle_lane": {"road_id": 1, "section_id": 0, "lane_id": -2},
        "ego_anchor": {"x": 0, "y": 0},
        "motorcycle_anchor": {"x": 0, "y": 3.5},
        "available_length_m": length,
        "location_tags": tags,
        "verification_source": "carla_topology",
    }


def test_filters_selected_maps_and_attaches_snapshot_identity() -> None:
    town01 = snapshot("Town01", 1, [site("a", ["straight"]), site("b", ["curve"])])
    town03 = snapshot("Town03", 2, [site("c", ["straight", "junction_approach"])])
    result = find_cut_in_sites([town01, town03], [" STRAIGHT "])
    assert [(item.snapshot.map_name, item.snapshot.snapshot_id, item.site_id) for item in result.sites] == [
        ("Town01", 1, "a"), ("Town03", 2, "c"),
    ]
    assert result.map_failures == []


def test_legacy_index_and_checked_empty_index_have_distinct_failures() -> None:
    result = find_cut_in_sites([snapshot("Town01", 1, None), snapshot("Town03", 2, [])])
    assert [failure.code for failure in result.map_failures] == ["SITE_INDEX_MISSING", "NO_VERIFIED_SITES"]
    assert result.sites == []


def test_junction_is_not_misclassified_as_four_way_intersection() -> None:
    selected = snapshot("Town01", 1, [site("a", ["junction", "straight"])])
    result = find_cut_in_sites([selected], ["intersection_4way"])
    assert result.sites == []
    assert result.map_failures[0].code == "LOCATION_NOT_AVAILABLE"


def test_length_filter_and_unconstrained_location() -> None:
    selected = snapshot("Town01", 1, [site("short", ["curve"], 45), site("long", ["straight"], 75)])
    result = find_cut_in_sites([selected])
    assert [item.site_id for item in result.sites] == ["long"]
    assert find_cut_in_sites([selected], ["curve"]).map_failures[0].code == "INSUFFICIENT_SITE_LENGTH"


def test_catalog_v2_document_is_accepted_and_its_extra_facts_ignored() -> None:
    v2_site = {**site("a", ["straight"], 184.5), "upstream_length_m": 40.0, "lane_change_allowed_throughout": True,
               "marking_between": ["Broken"], "speed_limit_kmh": 64.4}
    base = snapshot("Town10HD", 3, [v2_site]).model_dump(mode="json")
    catalog = {**base["catalog"], "format": "scenario-forge.catalog.v2", "cut_in_sites": [v2_site],
               "road_speeds": [{"road_id": 1, "from_s": 0.0, "max_kmh": 64.4}], "junctions": [], "extraction_errors": []}
    selected = SelectedSnapshot.model_validate({**base, "catalog": catalog})
    [found] = find_cut_in_sites([selected]).sites
    assert selected.catalog.format == "scenario-forge.catalog.v2" and found.available_length_m == 184.5
