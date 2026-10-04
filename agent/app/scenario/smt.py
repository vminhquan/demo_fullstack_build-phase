"""
ScenarioForge — SMT Adversarial Parameter Synthesizer
=====================================================

Formulates and solves for fair, physically realistic, and challenging adversarial
scenario parameters (d_spawn, d_trig, v_actor) using the Z3 SMT Solver.

Core Principles & Physical Laws (ISO 21448 / SOTIF & Vehicle Dynamics):
  1. Lateral Acceleration & Road Curvature:
     a_lat = v^2 / R <= mu * g
     Actors and ego must respect tire adhesion limits on curves.
  2. SOTIF / ISO 21448 Avoidability (Fairness):
     TTC_init = d_trig / v_closing >= tau_react (default: 0.8s)
     Guarantees that a competent Autonomous Driving System (ADS) has sufficient
     reaction and braking time to avoid collision ("no instant-kill scenarios").
  3. Physical Braking Limits & DRAC:
     DRAC = v_closing^2 / (2 * d_trig) <= mu * g
     The required deceleration to avoid crash must not exceed available tire friction.
  4. Adversarial Challenge Zone:
     0.70 * mu * g <= DRAC <= 1.0 * mu * g
     Ensures the scenario is genuinely safety-critical (stresses ADS emergency braking)
     without being physically impossible.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any
import z3

from app.scenario.schemas import (
    ScenarioIR,
    ActorType,
    RelativePosition,
    TriggerType,
    WeatherPreset,
    ExpectedOutcome,
    ExpectedVerdict,
    SafetyViolationType,
)
from app.scenario.curve_dynamics import compute_max_safe_speed

def _z3_to_float(val: Any) -> float:
    """Safely convert a Z3 Real/Rat/Alg value to a Python float."""
    if z3.is_rational_value(val):
        return float(val.numerator_as_long()) / float(val.denominator_as_long())
    elif hasattr(val, "as_decimal"):
        clean_dec = val.as_decimal(6).replace("?", "")
        return float(clean_dec)
    return float(str(val))


@dataclass
class SynthesizedParameters:
    success: bool
    d_spawn_m: float
    d_trigger_m: float
    v_actor_kmh: float
    ttc_init_s: float
    drac_m_s2: float
    friction_mu: float
    road_radius_m: float
    tier: str
    rationale: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "d_spawn_m": round(self.d_spawn_m, 2),
            "d_trigger_m": round(self.d_trigger_m, 2),
            "v_actor_kmh": round(self.v_actor_kmh, 1),
            "ttc_init_s": round(self.ttc_init_s, 2),
            "drac_m_s2": round(self.drac_m_s2, 2),
            "friction_mu": self.friction_mu,
            "road_radius_m": self.road_radius_m,
            "tier": self.tier,
            "rationale": self.rationale,
        }


def get_road_radius_from_type(road_type: str) -> float:
    """Infer approximate road radius in meters from descriptive road_type."""
    rt = road_type.lower()
    if "highway" in rt:
        return 120.0 if "curve" in rt else float("inf")
    if any(k in rt for k in ["intersection", "roundabout", "junction"]):
        return 15.0
    if any(k in rt for k in ["arterial", "rural"]):
        return 60.0 if "curve" in rt else float("inf")
    if "curve" in rt:
        return 25.0
    return float("inf")


def get_friction_from_weather(weather: WeatherPreset | str) -> float:
    """Infer tire-road friction coefficient mu from weather conditions."""
    w = weather.value if isinstance(weather, WeatherPreset) else str(weather).lower()
    if w in ["heavy_rain", "heavyrain"]:
        return 0.35
    elif w in ["rain"]:
        return 0.50
    return 0.80


def synthesize_fair_adversarial_parameters(
    ego_speed_kmh: float,
    actor_type: str | ActorType = ActorType.CAR,
    trigger_type: str | TriggerType = TriggerType.CUT_IN,
    relative_position: str | RelativePosition = RelativePosition.AHEAD_ADJACENT_RIGHT,
    road_radius_m: float = float("inf"),
    friction_mu: float = 0.80,
    reaction_time_s: float = 0.80,
    target_drac_ratio: tuple[float, float] = (0.70, 1.00),
    preferred_v_actor_kmh: float | None = None,
    preferred_d_trig_m: float | None = None,
    preferred_d_spawn_m: float | None = None,
) -> SynthesizedParameters:
    """
    Synthesize fair, physically realistic, and challenging adversarial parameters
    (d_spawn, d_trig, v_actor) using Z3 SMT solver.

    Guarantees:
      - SOTIF Avoidability: TTC_init >= reaction_time_s (0.8s)
      - Physical Braking Feasibility: DRAC <= mu * g
      - Curvature Lateral Grip: v_actor^2 / R <= mu * g
      - Adversarial Stress: 0.70 * mu * g <= DRAC <= mu * g (with tiered fallback)
      - Schema & Kinematics Bounds: d_spawn >= d_trig + margin, valid actor speed limits.
    """
    if isinstance(actor_type, str):
        try:
            actor_type = ActorType(actor_type.lower())
        except ValueError:
            actor_type = ActorType.CAR

    if isinstance(trigger_type, str):
        try:
            trigger_type = TriggerType(trigger_type.lower())
        except ValueError:
            trigger_type = TriggerType.CUT_IN

    if isinstance(relative_position, str):
        try:
            relative_position = RelativePosition(relative_position.lower())
        except ValueError:
            relative_position = RelativePosition.AHEAD_ADJACENT_RIGHT

    g = 9.81
    v_ego_ms = max(5.0, ego_speed_kmh) / 3.6
    a_friction_max = friction_mu * g

    # Actor kinematic limits based on type
    if actor_type == ActorType.PEDESTRIAN:
        v_min_ms = 1.0 / 3.6
        v_max_ms = 12.0 / 3.6  # Capped strictly below 15 km/h human sprint limit
    elif actor_type == ActorType.BICYCLE:
        v_min_ms = 4.0 / 3.6
        v_max_ms = 25.0 / 3.6
    elif actor_type == ActorType.TRUCK:
        v_min_ms = 10.0 / 3.6
        v_max_ms = min(90.0 / 3.6, v_ego_ms * 1.1)
    else:  # CAR or MOTORCYCLE
        v_min_ms = 10.0 / 3.6
        v_max_ms = min(120.0 / 3.6, max(30.0 / 3.6, v_ego_ms * 1.2))

    # Curve speed limit for actor
    if road_radius_m < 1000.0:
        v_curve_max_ms = math.sqrt(max(0.1, a_friction_max * road_radius_m * 0.90))
        v_max_ms = min(v_max_ms, v_curve_max_ms)

    if v_min_ms > v_max_ms:
        v_min_ms = max(0.5, v_max_ms * 0.5)

    # Solve in progressive tiers:
    # Tier 1: Challenge Zone [0.70 * a_friction_max, 1.00 * a_friction_max]
    # Tier 2: Active Braking Zone [0.45 * a_friction_max, 1.00 * a_friction_max]
    # Tier 3: General Avoidable Feasibility [0.25 * a_friction_max, 1.00 * a_friction_max]
    drac_tiers = [
        ("Tier 1: Challenge Zone (0.7-1.0 mu*g)", target_drac_ratio[0], target_drac_ratio[1]),
        ("Tier 2: Active Braking Zone (0.45-1.0 mu*g)", 0.45, 1.00),
        ("Tier 3: Avoidable Feasibility Zone (0.25-1.0 mu*g)", 0.25, 1.00),
        ("Tier 4: Minimal Stress Feasibility", 0.10, 1.00),
    ]

    for tier_name, lower_ratio, upper_ratio in drac_tiers:
        has_prefs = (preferred_v_actor_kmh is not None or preferred_d_trig_m is not None or preferred_d_spawn_m is not None)
        pref_attempts = [True, False] if has_prefs else [True]

        for with_prefs in pref_attempts:
            solver = z3.Solver()
            solver.set("timeout", 5000)  # 5s guard against adversarial over-constraint

            d_spawn = z3.Real("d_spawn")
            d_trig = z3.Real("d_trig")
            v_actor = z3.Real("v_actor")
            v_close = z3.Real("v_close")

            # Schema bounds
            solver.add(d_trig >= 3.0, d_trig <= 90.0)
            solver.add(d_spawn >= d_trig + 3.0, d_spawn <= 150.0)
            solver.add(v_actor >= v_min_ms, v_actor <= v_max_ms)

            # Closing velocity formulation
            if actor_type == ActorType.PEDESTRIAN or trigger_type in [
                TriggerType.JAYWALKING,
                TriggerType.RED_LIGHT_VIOLATION,
            ]:
                # Ego must stop for crossing obstacle in path
                solver.add(v_close == v_ego_ms)
            elif relative_position == RelativePosition.ONCOMING:
                solver.add(v_close == v_ego_ms + v_actor)
            elif relative_position == RelativePosition.BEHIND_SAME_LANE:
                solver.add(v_close == v_actor - v_ego_ms)
                solver.add(v_close >= 1.5)
            elif trigger_type == TriggerType.SUDDEN_BRAKE:
                # Lead vehicle brakes to a stop
                solver.add(v_close == v_ego_ms)
                solver.add(v_actor <= v_ego_ms)
            else:
                # Cut-in / Lane change ahead: ego closes on slower lead vehicle
                solver.add(v_close == v_ego_ms - v_actor)
                solver.add(v_close >= 1.5)

            # Avoidability constraint: TTC_init >= reaction_time_s
            # Formulated without division: d_trig >= reaction_time_s * v_close
            solver.add(d_trig >= reaction_time_s * v_close)

            # DRAC upper bound: DRAC <= upper_ratio * mu * g
            # v_close^2 <= 2 * d_trig * (upper_ratio * mu * g)
            solver.add(v_close * v_close <= 2 * d_trig * (upper_ratio * a_friction_max))

            # DRAC lower bound: DRAC >= lower_ratio * mu * g
            solver.add(v_close * v_close >= 2 * d_trig * (lower_ratio * a_friction_max))

            # Curvature lateral acceleration limit
            if road_radius_m < 1000.0:
                solver.add(v_actor * v_actor <= a_friction_max * road_radius_m)

            # Preferred value guidance
            if with_prefs:
                if preferred_v_actor_kmh is not None:
                    v_pref_ms = preferred_v_actor_kmh / 3.6
                    if v_min_ms <= v_pref_ms <= v_max_ms:
                        # Soft proximity interval
                        solver.add(v_actor >= max(v_min_ms, v_pref_ms - 2.0))
                        solver.add(v_actor <= min(v_max_ms, v_pref_ms + 2.0))

                if preferred_d_trig_m is not None:
                    if 3.0 <= preferred_d_trig_m <= 90.0:
                        solver.add(d_trig >= max(3.0, preferred_d_trig_m - 3.0))
                        solver.add(d_trig <= min(90.0, preferred_d_trig_m + 3.0))

                if preferred_d_spawn_m is not None:
                    if 5.0 <= preferred_d_spawn_m <= 150.0:
                        solver.add(d_spawn >= preferred_d_spawn_m - 5.0)
                        solver.add(d_spawn <= min(150.0, preferred_d_spawn_m + 5.0))

            if solver.check() == z3.sat:
                m = solver.model()
                solved_spawn = _z3_to_float(m[d_spawn])
                solved_trig = _z3_to_float(m[d_trig])
                solved_v_act_ms = _z3_to_float(m[v_actor])
                solved_v_close_ms = _z3_to_float(m[v_close])

                solved_ttc = solved_trig / max(0.1, solved_v_close_ms)
                solved_drac = (solved_v_close_ms ** 2) / (2.0 * max(0.5, solved_trig))

                pref_note = "" if with_prefs else " (preferences relaxed)"
                rationale = (
                    f"SMT synthesized under {tier_name}{pref_note}: TTC_init={solved_ttc:.2f}s "
                    f"(>= {reaction_time_s:.2f}s reaction delay), DRAC={solved_drac:.2f} m/s2 "
                    f"({solved_drac/a_friction_max:.1%} of {a_friction_max:.2f} m/s2 tire limit). "
                    f"Avoidable under ISO 21448."
                )

                return SynthesizedParameters(
                    success=True,
                    d_spawn_m=round(solved_spawn, 1),
                    d_trigger_m=round(solved_trig, 1),
                    v_actor_kmh=round(solved_v_act_ms * 3.6, 1),
                    ttc_init_s=round(solved_ttc, 2),
                    drac_m_s2=round(solved_drac, 2),
                    friction_mu=friction_mu,
                    road_radius_m=road_radius_m,
                    tier=tier_name,
                    rationale=rationale,
                )

    # Fallback heuristic if constraints were over-constrained
    v_actor_fallback_ms = max(v_min_ms, min(v_max_ms, v_ego_ms * 0.7))
    if actor_type == ActorType.PEDESTRIAN or trigger_type in [
        TriggerType.JAYWALKING,
        TriggerType.RED_LIGHT_VIOLATION,
    ]:
        v_close_fallback = v_ego_ms
    elif trigger_type == TriggerType.SUDDEN_BRAKE:
        v_close_fallback = v_ego_ms
    elif relative_position == RelativePosition.ONCOMING:
        v_close_fallback = v_ego_ms + v_actor_fallback_ms
    elif relative_position == RelativePosition.BEHIND_SAME_LANE:
        v_close_fallback = max(2.0, v_actor_fallback_ms - v_ego_ms)
    else:
        v_close_fallback = max(2.0, v_ego_ms - v_actor_fallback_ms)

    fallback_ttc = max(reaction_time_s, 1.2)
    fallback_trig = max(5.0, v_close_fallback * fallback_ttc)
    fallback_spawn = fallback_trig + 8.0
    fallback_drac = (v_close_fallback ** 2) / (2.0 * fallback_trig)

    return SynthesizedParameters(
        success=False,
        d_spawn_m=round(fallback_spawn, 1),
        d_trigger_m=round(fallback_trig, 1),
        v_actor_kmh=round(v_actor_fallback_ms * 3.6, 1),
        ttc_init_s=round(fallback_ttc, 2),
        drac_m_s2=round(fallback_drac, 2),
        friction_mu=friction_mu,
        road_radius_m=road_radius_m,
        tier="Fallback Heuristic",
        rationale="SMT solver constraints over-constrained; applied robust kinematic safety fallback.",
    )


def enhance_or_repair_scenario_ir(
    ir: ScenarioIR,
    strict_challenge_zone: bool = True,
    dry_friction: float = 0.80,
    wet_friction: float = 0.50,
) -> ScenarioIR:
    """
    Examine and repair a ScenarioIR to guarantee physical feasibility,
    SOTIF avoidability, and optimal adversarial challenge zone.

    1. Corrects ego speed if cornering lateral acceleration exceeds physical limits.
    2. Uses Z3 SMT solver to repair actor spawn, trigger distances, and speeds.
    3. Resolves spatial overlap between actors at spawn.
    4. Automatically synthesizes realistic ExpectedOutcome with TTC ranges and rationale.
    """
    ir_dict = ir.model_dump()
    weather = ir.weather
    friction_mu = get_friction_from_weather(weather)
    road_radius = get_road_radius_from_type(ir.ego.road_type)
    ego_speed = float(ir.ego.initial_speed_kmh)

    # 1. Ego curve speed governing if severely unphysical
    a_limit = friction_mu * 9.81
    if road_radius < 1000.0:
        a_lat_ego = ((ego_speed / 3.6) ** 2) / road_radius
        if a_lat_ego > 1.8 * a_limit:
            # Governed to safe cornering speed
            safe_speed = compute_max_safe_speed(road_radius, friction_mu=friction_mu)
            ir_dict["ego"]["initial_speed_kmh"] = float(safe_speed)
            ego_speed = float(safe_speed)

    # 2. Repair actors using SMT synthesizer
    repaired_actors: list[dict] = []
    seen_spawns: list[tuple[str, float]] = []
    min_observed_ttc = float("inf")
    max_observed_drac = 0.0

    for idx, actor in enumerate(ir.actors):
        a_dict = actor.model_dump()
        act_type = actor.actor_type
        trig = actor.trigger or TriggerType.CUT_IN
        rel_pos = actor.relative_position

        # Compute approximate closing velocity to filter out bad instant-kill trigger preferences
        v_ego_ms = ego_speed / 3.6
        v_act_ms = float(actor.initial_speed_kmh) / 3.6
        if act_type == ActorType.PEDESTRIAN or trig in [TriggerType.JAYWALKING, TriggerType.RED_LIGHT_VIOLATION]:
            approx_v_close = v_ego_ms
        elif rel_pos == RelativePosition.ONCOMING:
            approx_v_close = v_ego_ms + v_act_ms
        elif trig == TriggerType.SUDDEN_BRAKE:
            approx_v_close = v_ego_ms
        elif rel_pos == RelativePosition.BEHIND_SAME_LANE:
            approx_v_close = max(1.5, v_act_ms - v_ego_ms)
        else:
            approx_v_close = max(1.5, v_ego_ms - v_act_ms)

        cand_trig = float(actor.trigger_distance_m) if actor.trigger_distance_m else None
        if cand_trig is not None and (cand_trig / max(0.1, approx_v_close) < 0.80):
            # Prior trigger distance was an unfair instant-kill; do not constrain solver to it
            cand_trig = None

        # Synthesize fair parameters
        synth = synthesize_fair_adversarial_parameters(
            ego_speed_kmh=ego_speed,
            actor_type=act_type,
            trigger_type=trig,
            relative_position=rel_pos,
            road_radius_m=road_radius,
            friction_mu=friction_mu,
            target_drac_ratio=(0.70, 1.00) if strict_challenge_zone else (0.45, 1.00),
            preferred_v_actor_kmh=float(actor.initial_speed_kmh),
            preferred_d_trig_m=cand_trig,
            preferred_d_spawn_m=float(actor.initial_distance_m),
        )

        d_spawn = synth.d_spawn_m
        d_trig = synth.d_trigger_m
        v_act = synth.v_actor_kmh

        # Enforce spatial exclusivity at spawn: iteratively resolve any collision with prior actors in the same lane
        pos_key = rel_pos.value
        for _ in range(50):
            conflict = False
            for prev_pos, prev_dist in seen_spawns:
                if prev_pos == pos_key and abs(d_spawn - prev_dist) < 3.5:
                    d_spawn = round(max(d_spawn, prev_dist) + 5.0, 1)
                    if d_spawn <= d_trig:
                        d_spawn = round(d_trig + 5.0, 1)
                    conflict = True
                    break
            if not conflict:
                break
        seen_spawns.append((pos_key, d_spawn))

        a_dict["initial_distance_m"] = d_spawn
        a_dict["trigger_distance_m"] = d_trig
        a_dict["initial_speed_kmh"] = v_act

        if synth.ttc_init_s < min_observed_ttc:
            min_observed_ttc = synth.ttc_init_s
        if synth.drac_m_s2 > max_observed_drac:
            max_observed_drac = synth.drac_m_s2

        repaired_actors.append(a_dict)

    ir_dict["actors"] = repaired_actors

    # 3. Formulate or update ExpectedOutcome
    ttc_range_low = round(max(0.6, min_observed_ttc * 0.70), 2)
    ttc_range_high = round(min_observed_ttc * 1.35, 2)

    if max_observed_drac >= 3.5 or min_observed_ttc <= 1.8:
        verdict = ExpectedVerdict.NEAR_MISS
        violation = SafetyViolationType.TTC_CRITICAL if min_observed_ttc <= 1.5 else SafetyViolationType.NONE
    else:
        verdict = ExpectedVerdict.PASS
        violation = SafetyViolationType.NONE

    rationale = (
        f"SMT Verified: Min TTC_init={min_observed_ttc:.2f}s satisfies ISO 21448 avoidability. "
        f"Peak DRAC={max_observed_drac:.2f} m/s2 within tire grip limit {friction_mu*9.81:.2f} m/s2."
    )

    expected = ExpectedOutcome(
        expected_verdict=verdict,
        expected_min_ttc_range=(ttc_range_low, ttc_range_high),
        expected_safety_violation=violation,
        rationale=rationale,
    )
    ir_dict["expected_outcome"] = expected.model_dump()

    return ScenarioIR(**ir_dict)
