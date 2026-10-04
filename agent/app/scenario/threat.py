"""
ScenarioForge — Multi-Objective 5D Threat Scoring Engine (ScenGE Pattern)
=========================================================================

Implements the multi-dimensional adversarial `ThreatLoss` / Threat Scoring
framework inspired by ScenGE, evaluating both kinematic criticality and
perception difficulty across 5 normalized dimensions [0.0, 1.0]:

  1. `proximity`      : Inverse Euclidean distance hazard between ego and actor.
  2. `occlusion`      : Line-of-sight (LoS) angular blocking by intermediate
                        vehicles or roadside static obstructions.
  3. `time_pressure`  : Ratio of ego velocity to available Time-To-Collision (TTC).
  4. `angle_surprise` : Blind-spot (100°–160°) or perpendicular junction (70°–110°)
                        approach surprise factor.
  5. `env_stress`     : Weather, lighting (night/dusk/fog), and wet road friction
                        degradation penalty.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from app.scenario.schemas import ScenarioIR, RelativePosition, WeatherPreset

WEATHER_STRESS_MAP: dict[str, float] = {
    "clear": 0.10,
    "dusk": 0.45,
    "rain": 0.60,
    "night": 0.70,
    "fog": 0.85,
    "heavy_rain": 0.90,
}


@dataclass
class ThreatScoreBreakdown:
    """Detailed breakdown of the 5-dimensional threat score and regularization penalties."""

    proximity: float
    occlusion: float
    time_pressure: float
    angle_surprise: float
    env_stress: float
    weighted_threat: float
    smoothness_penalty: float = 0.0
    deviation_penalty: float = 0.0
    net_adversarial_objective: float = 0.0
    dominant_dimension: str = "proximity"
    weights: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "proximity": round(self.proximity, 4),
            "occlusion": round(self.occlusion, 4),
            "time_pressure": round(self.time_pressure, 4),
            "angle_surprise": round(self.angle_surprise, 4),
            "env_stress": round(self.env_stress, 4),
            "weighted_threat": round(self.weighted_threat, 4),
            "smoothness_penalty": round(self.smoothness_penalty, 4),
            "deviation_penalty": round(self.deviation_penalty, 4),
            "net_adversarial_objective": round(self.net_adversarial_objective, 4),
            "dominant_dimension": self.dominant_dimension,
            "weights": self.weights,
        }


def compute_occlusion_ratio(
    ego_xy: tuple[float, float],
    target_xy: tuple[float, float],
    blocker_xys: list[tuple[float, float]],
    blocker_radius_m: float = 2.2,
    static_occlusions: list[str] | None = None,
) -> float:
    """
    Compute line-of-sight occlusion ratio in [0.0, 1.0] between `ego_xy` and `target_xy`.
    """
    ex, ey = ego_xy
    tx, ty = target_xy
    dx = tx - ex
    dy = ty - ey
    seg_len = math.hypot(dx, dy)

    occ_score = 0.0
    if static_occlusions:
        occ_score = min(1.0, 0.55 + 0.15 * len(static_occlusions))

    if seg_len < 1e-3 or not blocker_xys:
        return round(occ_score, 4)

    for bx, by in blocker_xys:
        proj_t = ((bx - ex) * dx + (by - ey) * dy) / (seg_len * seg_len)
        if 0.05 < proj_t < 0.95:
            closest_x = ex + proj_t * dx
            closest_y = ey + proj_t * dy
            perp_dist = math.hypot(bx - closest_x, by - closest_y)
            if perp_dist < blocker_radius_m * 1.5:
                ray_occ = max(0.0, 1.0 - (perp_dist / (blocker_radius_m * 1.5)))
                occ_score = max(occ_score, ray_occ)

    return round(min(1.0, occ_score), 4)


def compute_blind_spot_score(
    relative_bearing_deg: float | None = None,
    relative_position: str | RelativePosition | None = None,
) -> float:
    """
    Compute angular surprise / blind-spot score in [0.0, 1.0].
    """
    if relative_bearing_deg is not None:
        ang = abs(relative_bearing_deg) % 180.0
        if 100.0 <= ang <= 165.0:
            return 0.95  # Rear-quarter blind spot
        if 65.0 <= ang < 100.0:
            return 0.80  # Perpendicular peripheral crossing
        if 25.0 <= ang < 65.0:
            return 0.65  # Adjacent lane cut-in cone
        return 0.30      # Direct forward field of view

    if relative_position is not None:
        pos_str = getattr(relative_position, "value", str(relative_position))
        pos_scores = {
            "crossing_from_left": 0.85,
            "crossing_from_right": 0.85,
            "ahead_adjacent_left": 0.70,
            "ahead_adjacent_right": 0.70,
            "oncoming": 0.60,
            "behind_same_lane": 0.90,
            "ahead_same_lane": 0.30,
        }
        return pos_scores.get(pos_str, 0.50)

    return 0.40


def compute_environment_stress(
    weather: str | WeatherPreset = "clear",
    time_of_day_hour: int = 12,
    friction_mu: float | None = None,
) -> float:
    """Compute normalized environmental & road friction stress score in [0.0, 1.0]."""
    w_str = getattr(weather, "value", str(weather)).lower()
    base = WEATHER_STRESS_MAP.get(w_str, 0.20)

    if time_of_day_hour <= 5 or time_of_day_hour >= 20:
        base = max(base, 0.65)
    elif 6 <= time_of_day_hour <= 7 or 17 <= time_of_day_hour <= 19:
        base = max(base, 0.40)

    if friction_mu is not None:
        friction_stress = max(0.0, min(1.0, (0.85 - friction_mu) / 0.55))
        base = 0.6 * base + 0.4 * friction_stress

    return round(min(1.0, max(0.0, base)), 4)


def compute_threat_score(
    *,
    distance_m: float,
    ego_speed_kmh: float,
    ttc_s: float | None = None,
    relative_bearing_deg: float | None = None,
    relative_position: str | RelativePosition | None = None,
    weather: str | WeatherPreset = "clear",
    time_of_day_hour: int = 12,
    friction_mu: float | None = None,
    ego_xy: tuple[float, float] = (0.0, 0.0),
    target_xy: tuple[float, float] | None = None,
    blocker_xys: list[tuple[float, float]] | None = None,
    static_occlusions: list[str] | None = None,
    smoothness_penalty: float = 0.0,
    deviation_penalty: float = 0.0,
    weights: dict[str, float] | None = None,
) -> ThreatScoreBreakdown:
    """
    Compute the 5-dimensional ThreatLoss score for a scenario state or tick.
    """
    w = {
        "proximity": 0.25,
        "occlusion": 0.15,
        "time_pressure": 0.25,
        "angle_surprise": 0.15,
        "env_stress": 0.20,
        "smooth": 0.10,
        "dev": 0.10,
    }
    if weights:
        w.update(weights)

    safe_d = max(0.5, float(distance_m))
    proximity_score = min(1.0, 12.0 / (safe_d + 8.0))

    t_xy = target_xy if target_xy is not None else (ego_xy[0] + safe_d, ego_xy[1])
    occlusion_score = compute_occlusion_ratio(
        ego_xy=ego_xy,
        target_xy=t_xy,
        blocker_xys=blocker_xys or [],
        static_occlusions=static_occlusions,
    )

    ego_v_ms = max(0.0, float(ego_speed_kmh) / 3.6)
    effective_ttc = ttc_s if (ttc_s is not None and ttc_s > 0) else (safe_d / max(ego_v_ms, 1.0))
    raw_pressure = ego_v_ms / max(effective_ttc, 0.25)
    time_pressure_score = min(1.0, raw_pressure / 25.0)

    angle_score = compute_blind_spot_score(
        relative_bearing_deg=relative_bearing_deg,
        relative_position=relative_position,
    )

    env_score = compute_environment_stress(
        weather=weather,
        time_of_day_hour=time_of_day_hour,
        friction_mu=friction_mu,
    )

    dims = {
        "proximity": proximity_score,
        "occlusion": occlusion_score,
        "time_pressure": time_pressure_score,
        "angle_surprise": angle_score,
        "env_stress": env_score,
    }
    dominant = max(dims.items(), key=lambda kv: kv[1])[0]

    weighted_threat = (
        w["proximity"] * proximity_score
        + w["occlusion"] * occlusion_score
        + w["time_pressure"] * time_pressure_score
        + w["angle_surprise"] * angle_score
        + w["env_stress"] * env_score
    )
    net_obj = weighted_threat - w["smooth"] * smoothness_penalty - w["dev"] * deviation_penalty

    return ThreatScoreBreakdown(
        proximity=proximity_score,
        occlusion=occlusion_score,
        time_pressure=time_pressure_score,
        angle_surprise=angle_score,
        env_stress=env_score,
        weighted_threat=weighted_threat,
        smoothness_penalty=smoothness_penalty,
        deviation_penalty=deviation_penalty,
        net_adversarial_objective=net_obj,
        dominant_dimension=dominant,
        weights=w,
    )


def evaluate_scenario_ir_threat(ir: ScenarioIR | dict[str, Any]) -> ThreatScoreBreakdown:
    """
    Evaluate the static 5D ThreatScore of a `ScenarioIR` specification across all actors.
    """
    if isinstance(ir, dict):
        ir_obj = ScenarioIR(**ir)
    else:
        ir_obj = ir

    ego_v = ir_obj.ego.initial_speed_kmh
    ego_v_ms = ego_v / 3.6

    actor_coords: list[tuple[float, float]] = []
    for act in ir_obj.actors:
        d = act.initial_distance_m
        pos = act.relative_position.value
        lat = 0.0
        if "left" in pos:
            lat = -3.5
        elif "right" in pos:
            lat = 3.5
        actor_coords.append((d, lat))

    best_score: ThreatScoreBreakdown | None = None
    for idx, act in enumerate(ir_obj.actors):
        trig_d = act.trigger_distance_m or act.initial_distance_m
        act_v_ms = act.initial_speed_kmh / 3.6
        if act.relative_position in (
            RelativePosition.CROSSING_FROM_LEFT,
            RelativePosition.CROSSING_FROM_RIGHT,
        ):
            closing_v = max(ego_v_ms, 1.0)
        elif act.relative_position == RelativePosition.ONCOMING:
            closing_v = max(ego_v_ms + act_v_ms, 1.0)
        else:
            closing_v = max(abs(ego_v_ms - act_v_ms), ego_v_ms * 0.5, 1.0)

        est_ttc = trig_d / closing_v
        other_coords = [c for j, c in enumerate(actor_coords) if j != idx]

        score = compute_threat_score(
            distance_m=trig_d,
            ego_speed_kmh=ego_v,
            ttc_s=est_ttc,
            relative_position=act.relative_position,
            weather=ir_obj.weather,
            time_of_day_hour=ir_obj.time_of_day_hour,
            ego_xy=(0.0, 0.0),
            target_xy=actor_coords[idx],
            blocker_xys=other_coords,
            static_occlusions=ir_obj.occlusions,
        )
        if best_score is None or score.weighted_threat > best_score.weighted_threat:
            best_score = score

    return best_score or compute_threat_score(distance_m=50.0, ego_speed_kmh=ego_v)
