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


class RuledWaypoint(Waypoint):
    """Waypoint with the catalog.v2 attributes: lane rule, markings and predecessors."""

    def __init__(self, x: float, y: float, lane_id: int, *, rule: str = "LaneChange.Left"):
        super().__init__(x, y, lane_id)
        self.lane_change = rule
        self.left_lane_marking = SimpleNamespace(type="LaneMarkingType.Broken")
        self.predecessors = []

    def previous(self, distance):
        assert distance == 5.0
        return self.predecessors


def long_corridor(steps: int, *, anchor: int, curve_from: int | None = None, solid_at: int | None = None):
    sources = [RuledWaypoint(i * 5.0, 0.0, -1) for i in range(steps)]
    targets = [RuledWaypoint(i * 5.0, 3.5, -2) for i in range(steps)]
    for i, (source, target) in enumerate(zip(sources, targets, strict=True)):
        source.left = target
        if i + 1 < steps:
            source.successors, target.successors = [sources[i + 1]], [targets[i + 1]]
        if i > 0:
            source.predecessors, target.predecessors = [sources[i - 1]], [targets[i - 1]]
        if curve_from is not None and i >= curve_from:
            source.transform.rotation.yaw = target.transform.rotation.yaw = 30.0
    if solid_at is not None:
        sources[solid_at].lane_change = "LaneChange.NONE"
        sources[solid_at].left_lane_marking = SimpleNamespace(type="LaneMarkingType.Solid")
    return sources[anchor]


def test_corridor_reports_full_length_upstream_and_rules() -> None:
    [site] = extract_cut_in_sites([long_corridor(90, anchor=10, curve_from=40)])
    assert site["available_length_m"] == 300.0 and site["ends_at"] == "max_length"
    assert site["upstream_length_m"] == 50.0  # 10 verified steps behind the anchor
    # Tags keep describing the first 60 m (straight there), the turn further on is in max_heading_change_deg.
    assert site["location_tags"] == ["straight"] and site["max_heading_change_deg"] == 30.0
    assert site["lane_change_allowed_throughout"] is True and site["marking_between"] == ["Broken"]
    assert site["lane_width_m"] == {"ego": 3.5, "motorcycle": 3.5} and site["first_junction_m"] is None


def test_corridor_end_and_lane_rule_on_the_way() -> None:
    [site] = extract_cut_in_sites([long_corridor(20, anchor=0, solid_at=15)])
    assert site["available_length_m"] == 95.0 and site["ends_at"] == "branch_or_end"
    assert site["upstream_length_m"] == 0.0
    assert site["lane_change_allowed_throughout"] is False and site["marking_between"] == ["Broken", "Solid"]


def test_lane_rule_unknown_on_older_carla() -> None:
    sources, _ = corridor()
    [site] = extract_cut_in_sites([sources[0]])
    assert site["lane_change_allowed_throughout"] is None and site["marking_between"] == []


def ending_at_junction(steps: int, *, junction_id: int = 77):
    """A corridor whose lanes run into junction `junction_id` one step after the last verified waypoint."""
    anchor = long_corridor(steps, anchor=0)
    last = anchor
    while last.successors:
        last = last.successors[0]
    entry = RuledWaypoint(last.transform.location.x + 5.0, 0.0, -1)
    entry.is_junction, entry.junction_id = True, junction_id
    last.successors = [entry, RuledWaypoint(last.transform.location.x + 5.0, -3.0, -1)]  # the lane branches here
    return anchor


def test_corridor_running_into_a_junction_is_an_approach() -> None:
    [site] = extract_cut_in_sites([ending_at_junction(20)])
    assert site["ends_at"] == "branch_or_end"
    assert site["junction_ahead"] == {"junction_id": 77, "distance_m": 95.0}
    assert site["location_tags"] == ["straight", "junction_approach"]


def test_far_junction_is_reported_but_not_an_approach() -> None:
    [site] = extract_cut_in_sites([ending_at_junction(40)])
    assert site["junction_ahead"]["distance_m"] == 195.0 and site["location_tags"] == ["straight"]


def test_anchor_yaw_is_normalised() -> None:
    anchor = long_corridor(20, anchor=0)
    node = anchor
    while node is not None:
        node.transform.rotation.yaw = node.left.transform.rotation.yaw = -450.0
        node = node.successors[0] if node.successors else None
    [site] = extract_cut_in_sites([anchor])
    assert site["ego_anchor"]["yaw"] == -90.0 and site["junction_ahead"] is None
