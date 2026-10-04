from benchmark.scoring import fidelity_checks, placement_check

EGO = {"x": 0.0, "y": 0.0, "yaw_deg": 0.0}  # facing +x; CARLA +y is to the ego's right


def spec(position, distance=20.0):
    return {"relative_position": position, "initial_distance_m": distance}


def test_placement_rules_follow_carla_left_handed_frame():
    assert placement_check(EGO, {"x": 20, "y": 0, "yaw_deg": 0}, spec("ahead_same_lane"))["ok"]
    assert placement_check(EGO, {"x": 20, "y": 3.5, "yaw_deg": 0}, spec("ahead_adjacent_right"))["ok"]
    assert not placement_check(EGO, {"x": 20, "y": 3.5, "yaw_deg": 0}, spec("ahead_adjacent_left"))["ok"]
    assert placement_check(EGO, {"x": 40, "y": -3.5, "yaw_deg": 180}, spec("oncoming", 40))["ok"]
    assert placement_check(EGO, {"x": 20, "y": -12, "yaw_deg": 90}, spec("crossing_from_left"))["ok"]
    assert placement_check(EGO, {"x": 20, "y": 6, "yaw_deg": -90}, spec("crossing_from_right"))["ok"]
    assert placement_check(EGO, {"x": -15, "y": 0, "yaw_deg": 0}, spec("behind_same_lane", 15))["ok"]
    assert not placement_check(EGO, {"x": 60, "y": 0, "yaw_deg": 0}, spec("ahead_same_lane"))["ok"]


def ir(actors, **overrides):
    base = {"ego": {"road_type": "intersection_4way", "initial_speed_kmh": 40}, "weather": "rain", "time_of_day_hour": 14, "actors": actors}
    base.update(overrides)
    return base


def test_fidelity_flags_wrong_count_and_extra_actors():
    expect = {"road_types": ["intersection_4way"], "weather": ["rain"], "actors": [{"type": "motorcycle", "count": 1, "positions": ["ahead_adjacent_right"], "triggers": ["cut_in"]}]}
    exact = [{"actor_type": "motorcycle", "relative_position": "ahead_adjacent_right", "trigger": "cut_in"}]
    assert all(ok for _, ok, _ in fidelity_checks(ir(exact), expect))
    noisy = exact + [{"actor_type": "bicycle", "relative_position": "crossing_from_right", "trigger": None}]
    failed = {name for name, ok, _ in fidelity_checks(ir(noisy), expect) if not ok}
    assert failed == {"no_extra_actors"}


def test_fidelity_night_accepts_clear_late_hour():
    checks = fidelity_checks(ir([], weather="clear", time_of_day_hour=22), {"night": True})
    assert checks == [("night", True, "want night=True, got clear@22h")]
