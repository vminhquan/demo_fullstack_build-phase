"""A small synthetic CARLA snapshot for tests.

Road 1 (300 m) continues into road 2 (200 m) along +x. Each has two lanes running along +x (lane -1 nearest the
centre line, lane -2 to its right, i.e. larger y in CARLA's left-handed frame) and one lane running the other way
(lane 1). Waypoints every 2 m with neighbours, and the topology linking the roads.
Apart from them, road 10 (150 m) runs into junction 50 (road 11, 20 m, a four-way) and on into road 12 (100 m).
`single_lane=True` keeps only lanes -1 and 1: no same-direction neighbour anywhere.
"""

from __future__ import annotations

from app.catalog.models import SelectedSnapshot

LANE_Y = {-1: 1.75, -2: 5.25, 1: -1.75}
# road -> (x of s=0, length, y offset, junction id)
ROADS = {1: (0.0, 300.0, 0.0, None), 2: (300.0, 200.0, 0.0, None),
         10: (0.0, 150.0, 1000.0, None), 11: (150.0, 20.0, 1000.0, 50), 12: (170.0, 100.0, 1000.0, None)}


def _neighbor(road: int, lane: int, s: float, side: str, lanes) -> dict | None:
    # Left/right relative to the lane's travel direction.
    order = {-2: {"left": -1}, -1: {"left": 1, "right": -2}, 1: {"left": -1}}
    other = order[lane].get(side)
    if other is None or other not in lanes:
        return None
    return {"road_id": road, "section_id": 0, "lane_id": other, "s": s, "lane_type": "Driving"}


def waypoints(lanes=LANE_Y) -> list[dict]:
    points = []
    for road, (x0, length, y0, junction) in ROADS.items():
        for lane, y in lanes.items():
            for step in range(int(length // 2) + 1):
                s = step * 2.0
                points.append({
                    "x": x0 + s, "y": y0 + y, "z": 0.0, "yaw": 0.0 if lane < 0 else 180.0, "road_id": road, "section_id": 0,
                    "lane_id": lane, "s": s, "lane_width": 3.5, "lane_type": "Driving",
                    "is_junction": junction is not None, "junction_id": junction,
                    "left_lane": _neighbor(road, lane, s, "left", lanes), "right_lane": _neighbor(road, lane, s, "right", lanes),
                })
    return points


def snapshot(map_name: str = "Town01", snapshot_id: int = 1, *, presets: list[str] | None = None,
             single_lane: bool = False) -> SelectedSnapshot:
    lanes = {lane: y for lane, y in LANE_Y.items() if not single_lane or lane != -2}
    return SelectedSnapshot.model_validate({
        "ref": {"snapshot_id": snapshot_id, "map_name": map_name, "content_hash": f"{snapshot_id:064x}"},
        "catalog": {
            "format": "scenario-forge.catalog.v2", "carla_version": "0.9.16", "map_name": map_name,
            "vehicles": [
                {"id": "vehicle.tesla.model3", "base_type": "car", "number_of_wheels": 4, "length_m": 4.8, "width_m": 2.1},
                {"id": "vehicle.audi.a2", "base_type": "car", "number_of_wheels": 4},
                {"id": "vehicle.yamaha.yzf", "number_of_wheels": 2},
                {"id": "vehicle.carlamotors.carlacola", "base_type": "truck", "number_of_wheels": 4, "length_m": 5.2,
                 "width_m": 2.6},
                {"id": "vehicle.bh.crossbike", "base_type": "bicycle", "number_of_wheels": 2},
            ],
            "spawn_points": [{"x": 0, "y": 0}],
            "waypoints": waypoints(lanes),
            "weather_presets": presets if presets is not None else ["Default", "ClearNoon", "HardRainNight", "WetSunset"],
            "topology": [
                {"start": {"road_id": 1, "lane_id": -1, "s": 0}, "end": {"road_id": 2, "lane_id": -1, "s": 0}},
                {"start": {"road_id": 1, "lane_id": -2, "s": 0}, "end": {"road_id": 2, "lane_id": -2, "s": 0}},
                {"start": {"road_id": 2, "lane_id": 1, "s": 200}, "end": {"road_id": 1, "lane_id": 1, "s": 300}},
                *({"start": {"road_id": a, "lane_id": lane, "s": 0}, "end": {"road_id": b, "lane_id": lane, "s": 0}}
                  for a, b in ((10, 11), (11, 12)) for lane in lanes if lane < 0),
            ],
            "junctions": [{"junction_id": 50, "incoming_road_count": 4, "four_way_candidate": True}],
            "cut_in_sites": [],
        },
    })
