from __future__ import annotations

from types import SimpleNamespace

from scenario_forge_bridge.cut_in_sites import extract_cut_in_sites


class Waypoint:
    def __init__(self, x: float, y: float, lane_id: int, *, junction: bool = False):
        self.transform = SimpleNamespace(
            location=SimpleNamespace(x=x, y=y, z=0.0),
            rotation=SimpleNamespace(yaw=0.0),
        )
        self.road_id = 1
        self.section_id = 0
        self.lane_id = lane_id
        self.s = x
        self.lane_width = 3.5
        self.is_junction = junction
        self.lane_type = "LaneType.Driving"
        self.left = None
        self.successors = []

    def get_left_lane(self):
        return self.left

    def get_right_lane(self):
        return None

    def next(self, distance):
        assert distance == 5.0
        return self.successors


def corridor(steps: int = 14, *, target_lane_id: int = -2, junction: bool = False):
    sources = [Waypoint(i * 5.0, 0.0, -1, junction=junction) for i in range(steps)]
    targets = [Waypoint(i * 5.0, 3.5, target_lane_id, junction=junction) for i in range(steps)]
    for i, (source, target) in enumerate(zip(sources, targets, strict=True)):
        source.left = target
        if i + 1 < steps:
            source.successors = [sources[i + 1]]
            target.successors = [targets[i + 1]]
    return sources, targets


def test_extracts_only_verified_adjacent_corridor() -> None:
    sources, _ = corridor(junction=True)
    sites = extract_cut_in_sites([sources[0]])
    assert len(sites) == 1
    assert sites[0]["motorcycle_lane"]["lane_id"] == -1
    assert sites[0]["ego_lane"]["lane_id"] == -2
    assert sites[0]["available_length_m"] >= 60.0
    assert sites[0]["verification_source"] == "carla_topology"
    assert (sites[0]["ego_s"], sites[0]["motorcycle_s"], sites[0]["s_direction"], sites[0]["target_side"]) == (0, 0, 1, "left")
    assert "junction" in sites[0]["location_tags"]
    assert "intersection_4way" not in sites[0]["location_tags"]


def test_rejects_opposite_direction_branch_and_short_corridor() -> None:
    opposite, _ = corridor(target_lane_id=2)
    branched, _ = corridor()
    branched[4].successors.append(branched[6])
    short, _ = corridor(10)
    assert extract_cut_in_sites([opposite[0]]) == []
    assert extract_cut_in_sites([branched[0]]) == []
    assert extract_cut_in_sites([short[0]]) == []


def test_rejects_discontinuous_waypoint_step() -> None:
    sources, targets = corridor()
    sources[6].transform.location.x += 50.0
    targets[6].transform.location.x += 50.0
    assert extract_cut_in_sites([sources[0]]) == []


def test_rejects_opendrive_s_reversal_inside_corridor() -> None:
    sources, targets = corridor()
    for source, target in zip(sources[7:], targets[7:], strict=True):
        source.s -= 10
        target.s -= 10
    assert extract_cut_in_sites([sources[0]]) == []
