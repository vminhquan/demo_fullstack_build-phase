"""Pure scoring functions for one benchmark case. No I/O, so they are unit-testable."""
from __future__ import annotations

import math
from typing import Any

SIDE_MIN_M = 1.5  # an actor counts as left/right of the ego only beyond this lateral offset


def relative_pose(ego: dict[str, Any], actor: dict[str, Any]) -> dict[str, float]:
    """Actor pose in the ego frame (CARLA, left-handed: +lateral = right of the ego)."""
    yaw = math.radians(ego["yaw_deg"])
    fx, fy = math.cos(yaw), math.sin(yaw)
    rx, ry = -fy, fx
    dx, dy = actor["x"] - ego["x"], actor["y"] - ego["y"]
    return {
        "along": dx * fx + dy * fy,
        "lateral": dx * rx + dy * ry,
        "distance": math.hypot(dx, dy),
        "dyaw": (actor["yaw_deg"] - ego["yaw_deg"] + 180.0) % 360.0 - 180.0,
    }


def placement_check(ego: dict[str, Any], actor: dict[str, Any], spec: dict[str, Any]) -> dict[str, Any]:
    """Does the grounded pose agree with the IR's relative_position and distance?"""
    rel = relative_pose(ego, actor)
    want = float(spec["initial_distance_m"])
    tolerance = max(4.0, 0.25 * want)
    position = spec["relative_position"]
    along, lateral, dyaw, dist = rel["along"], rel["lateral"], rel["dyaw"], rel["distance"]
    close = abs(dist - want) <= tolerance
    rules: dict[str, tuple[bool, str]] = {
        "ahead_same_lane": (along > 0 and abs(dyaw) <= 35 and close, "ahead, same heading, at distance"),
        "behind_same_lane": (along < 0 and close, "behind, at distance"),
        "ahead_adjacent_left": (along > 0 and lateral < -SIDE_MIN_M and abs(dyaw) <= 35 and close, "ahead-left, same heading"),
        "ahead_adjacent_right": (along > 0 and lateral > SIDE_MIN_M and abs(dyaw) <= 35 and close, "ahead-right, same heading"),
        "oncoming": (along > 0 and lateral < -SIDE_MIN_M and abs(dyaw) >= 145, "ahead-left, opposite heading"),
        "crossing_from_left": (lateral < -SIDE_MIN_M and 50 <= dyaw <= 130, "left side, heading right"),
        "crossing_from_right": (lateral > SIDE_MIN_M and -130 <= dyaw <= -50, "right side, heading left"),
    }
    ok, rule = rules.get(position, (False, "unknown position"))
    return {
        "entity": actor.get("entity_name"),
        "position": position,
        "ok": bool(ok),
        "rule": rule,
        "placement": actor.get("placement"),
        **{key: round(value, 2) for key, value in rel.items()},
        "want_distance": want,
    }


def _actor_checks(actors: list[dict[str, Any]], expected: list[dict[str, Any]]) -> list[tuple[str, bool, str]]:
    checks: list[tuple[str, bool, str]] = []
    for item in expected:
        label = item.get("type") or "/".join(item.get("positions", ["actor"]))
        candidates = [a for a in actors if not item.get("type") or a["actor_type"] == item["type"]]
        if not item.get("type") and item.get("positions"):
            candidates = [a for a in candidates if a["relative_position"] in item["positions"]]
        if "count" in item:
            checks.append((f"count[{label}]", len(candidates) == item["count"], f"want {item['count']}, got {len(candidates)}"))
        if item.get("positions") and item.get("type"):
            got = sorted({a["relative_position"] for a in candidates})
            checks.append((f"position[{label}]", any(a["relative_position"] in item["positions"] for a in candidates), f"want {item['positions']}, got {got}"))
        if item.get("triggers"):
            got = sorted({str(a.get("trigger")) for a in candidates})
            checks.append((f"trigger[{label}]", any(a.get("trigger") in item["triggers"] for a in candidates), f"want {item['triggers']}, got {got}"))
    return checks


def fidelity_checks(ir: dict[str, Any], expect: dict[str, Any]) -> list[tuple[str, bool, str]]:
    """How faithfully the IR follows the prompt. Each check: (name, passed, detail)."""
    checks: list[tuple[str, bool, str]] = []
    road = ir["ego"]["road_type"]
    if expect.get("road_types"):
        checks.append(("road_type", road in expect["road_types"], f"want {expect['road_types']}, got {road}"))
    if expect.get("weather"):
        checks.append(("weather", ir["weather"] in expect["weather"], f"want {expect['weather']}, got {ir['weather']}"))
    if "night" in expect:
        hour = int(ir["time_of_day_hour"])
        is_night = ir["weather"] == "night" or hour >= 19 or hour < 6
        checks.append(("night", is_night == expect["night"], f"want night={expect['night']}, got {ir['weather']}@{hour}h"))
    if expect.get("ego_speed_kmh"):
        low, high = expect["ego_speed_kmh"]
        speed = float(ir["ego"]["initial_speed_kmh"])
        checks.append(("ego_speed", low <= speed <= high, f"want {low}-{high}, got {speed:g}"))
    expected_actors = expect.get("actors") or []
    checks.extend(_actor_checks(ir["actors"], expected_actors))
    if expected_actors and not expect.get("allow_extra_actors", False):
        wanted = sum(item.get("count", 1) for item in expected_actors)
        extra = len(ir["actors"]) - wanted
        checks.append(("no_extra_actors", extra <= 0, f"{max(extra, 0)} unrequested actor(s): {[a['actor_type'] for a in ir['actors']]}"))
    return checks


def score_case(case: dict[str, Any], status: int, body: dict[str, Any], latency_s: float) -> dict[str, Any]:
    expect = case.get("expect", {})
    result: dict[str, Any] = {"id": case["id"], "tags": case.get("tags", []), "status": status, "latency_s": round(latency_s, 2)}
    if expect.get("error_code"):
        code = (body.get("error") or {}).get("code")
        result.update(kind="negative", passed=code == expect["error_code"], detail=f"want {expect['error_code']}, got {status} {code}")
        return result
    if status != 200:
        error = body.get("error") or {}
        result.update(kind="scenario", passed=False, error_code=error.get("code"), detail=error.get("message"))
        return result

    ir, grounding = body["scenario_ir"], body["grounding"]
    placements = [placement_check(grounding["ego"], actor, spec) for actor, spec in zip(grounding["actors"], ir["actors"])]
    technical = {
        "guardrail_valid": bool(body["validation"].get("is_valid")),
        "xsd_valid": bool(body["xosc_validation"].get("schema_valid")),
        "on_lanes": bool(grounding.get("fully_on_lanes")),
        "placement_ok": all(item["ok"] for item in placements),
    }
    fidelity = fidelity_checks(ir, expect)
    fidelity_score = sum(ok for _, ok, _ in fidelity) / len(fidelity) if fidelity else 1.0
    result.update(
        kind="scenario",
        generation_mode=body.get("generation_mode"),
        technical=technical,
        fidelity_score=round(fidelity_score, 3),
        fidelity_failures=[f"{name}: {detail}" for name, ok, detail in fidelity if not ok],
        placements=placements,
        actor_count=len(ir["actors"]),
        threat=body.get("threat_score", {}).get("weighted_threat"),
        warnings=body.get("warnings", []),
        passed=all(technical.values()) and fidelity_score == 1.0,
        ir_summary={
            "road_type": ir["ego"]["road_type"],
            "ego_speed_kmh": ir["ego"]["initial_speed_kmh"],
            "weather": ir["weather"],
            "hour": ir["time_of_day_hour"],
            "actors": [f"{a['actor_type']}:{a['relative_position']}:{a.get('trigger')}" for a in ir["actors"]],
        },
    )
    return result
