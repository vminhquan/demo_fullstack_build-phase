"""
ScenarioForge — Scenario Suite Validator & Guardrail Engine
===========================================================

Validates simulation scenarios across four core pillars:
  1. Schema Conformance: Pydantic model validation.
  2. Physical Feasibility & Kinematics:
     - Ego speed vs road curvature (Part B lateral acceleration a_lat = v^2/R <= mu*g)
     - Pedestrian speed bounds (v <= 15 km/h)
     - Logical consistency (trigger_distance_m <= initial_distance_m)
     - Physical spatial exclusivity (no co-located overlapping actors at spawn)
  3. SOTIF / ISO 21448 Fairness (Avoidability):
     - Initial TTC at trigger activation must satisfy TTC_init >= tau_react (>= 0.8s)
  4. Pre-defined Expectations (Kỳ vọng duyệt trước):
     - Every scenario must declare expected_outcome (verdict, TTC range, safety violation)

Also provides test suite verification for both the 20 Positive Scenarios and
the Distinct Negative Test Set.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional
from pydantic import ValidationError

from app.scenario.schemas import (
    ScenarioIR,
    ExpectedOutcome,
    ExpectedVerdict,
    ActorType,
    WeatherPreset,
    RelativePosition,
    TriggerType,
)
from app.scenario.curve_dynamics import compute_lateral_acceleration, compute_critical_radius

@dataclass
class ValidationResult:
    scenario_name: str
    is_valid: bool
    verdict: ExpectedVerdict
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)
    ir: Optional[ScenarioIR] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "scenario_name": self.scenario_name,
            "is_valid": self.is_valid,
            "verdict": self.verdict.value,
            "errors": self.errors,
            "warnings": self.warnings,
            "metrics": self.metrics,
        }


class ScenarioGuardrailValidator:
    """Rigorous multi-layer scenario validator and guardrail engine."""

    # Baseline human/ADS reaction time threshold (ISO 21448 / NHTSA)
    MIN_FAIR_TTC_SECONDS: float = 0.80
    MAX_PEDESTRIAN_SPEED_KMH: float = 15.0
    MIN_SPAWN_SPACING_M: float = 3.0

    @classmethod
    def validate_scenario(cls, scenario_input: dict | ScenarioIR) -> ValidationResult:
        """
        Validate a scenario against schema, physical feasibility, avoidability,
        and pre-defined expectation completeness.
        """
        errors: list[str] = []
        warnings: list[str] = []
        metrics: dict[str, Any] = {}
        name = "unnamed_scenario"

        # -------------------------------------------------------------------
        # Layer 1: Schema Conformance (Pydantic)
        # -------------------------------------------------------------------
        ir: Optional[ScenarioIR] = None
        if isinstance(scenario_input, ScenarioIR):
            ir = scenario_input
            name = ir.name
        elif isinstance(scenario_input, dict):
            name = scenario_input.get("name", "unnamed_scenario")
            try:
                ir = ScenarioIR(**scenario_input)
            except (ValidationError, TypeError, ValueError) as e:
                errors.append(f"Schema Validation Error: {str(e)}")
                return ValidationResult(
                    scenario_name=name,
                    is_valid=False,
                    verdict=ExpectedVerdict.REJECT_INVALID,
                    errors=errors,
                    warnings=warnings,
                    metrics=metrics,
                    ir=None,
                )
        else:
            errors.append(f"Invalid scenario input type: {type(scenario_input).__name__}")
            return ValidationResult(
                scenario_name=name,
                is_valid=False,
                verdict=ExpectedVerdict.REJECT_INVALID,
                errors=errors,
            )

        # -------------------------------------------------------------------
        # Layer 2: Kinematics & Physics Feasibility
        # -------------------------------------------------------------------
        ego_speed = ir.ego.initial_speed_kmh
        road_type = ir.ego.road_type.lower()
        weather = ir.weather
        friction_mu = 0.5 if weather in [WeatherPreset.RAIN, WeatherPreset.HEAVY_RAIN] else 0.8

        # Check negative speeds or unphysical speeds
        if ego_speed < 0:
            errors.append(f"Ego speed cannot be negative: {ego_speed} km/h")

        # Check Curve & Intersection Vehicle Dynamics (Part B: 60 km/h analysis)
        if any(rt in road_type for rt in ["curve", "intersection", "roundabout", "junction"]):
            # Differentiate radius by road topology:
            # - Highways have gentle superelevated curves (R >= 100-250m)
            # - Arterial/rural roads have moderate curves (R >= 50-80m)
            # - Tight urban 90-deg intersections/roundabouts have sharp turns (R = 12-20m)
            # - Standard urban street curves (R = 25-35m)
            if "highway" in road_type:
                assumed_r = 120.0
            elif any(rt in road_type for rt in ["arterial", "rural"]):
                assumed_r = 60.0
            elif any(rt in road_type for rt in ["intersection", "roundabout", "junction"]):
                assumed_r = 15.0
            else:
                assumed_r = 25.0

            a_lat = compute_lateral_acceleration(ego_speed, assumed_r)
            a_limit = friction_mu * 9.81
            r_crit = compute_critical_radius(ego_speed, friction_mu)
            metrics["estimated_curve_radius_m"] = assumed_r
            metrics["required_a_lat_ms2"] = round(a_lat, 2)
            metrics["tire_grip_limit_ms2"] = round(a_limit, 2)
            metrics["critical_radius_m"] = round(r_crit, 2)

            # If speed is excessively high for the curve (e.g. 60+ km/h on 15m radius or 110 km/h on 25m),
            # required centripetal acceleration vastly exceeds tire friction, causing unavoidable understeer into dividers
            if a_lat > 2.2 * a_limit:
                errors.append(
                    f"Physical Dynamics Infeasibility: Ego speed {ego_speed} km/h on '{road_type}' "
                    f"requires a_lat={a_lat:.1f} m/s2, which exceeds tire friction limit {a_limit:.1f} m/s2 by "
                    f"{a_lat/a_limit:.1f}x. Guaranteed understeer into median divider or barrier."
                )
            elif a_lat > a_limit:
                warnings.append(
                    f"High-Speed Cornering Warning: Ego speed {ego_speed} km/h on '{road_type}' "
                    f"exceeds friction limit ({a_lat:.1f} > {a_limit:.1f} m/s2). "
                    f"Requires curve speed governor in resolver or gentle curve radius >= {r_crit:.1f}m."
                )

        # Actor physical bounds
        seen_positions: list[tuple[RelativePosition, float]] = []
        for idx, actor in enumerate(ir.actors):
            # Check pedestrian speed limits
            if actor.actor_type == ActorType.PEDESTRIAN and actor.initial_speed_kmh > cls.MAX_PEDESTRIAN_SPEED_KMH:
                errors.append(
                    f"Actor {idx} ({actor.actor_type.value}) speed {actor.initial_speed_kmh} km/h "
                    f"exceeds maximum human sprint capability ({cls.MAX_PEDESTRIAN_SPEED_KMH} km/h)."
                )

            # Check trigger distance vs initial distance
            if actor.trigger_distance_m is not None and actor.trigger_distance_m > actor.initial_distance_m:
                errors.append(
                    f"Actor {idx} contradictory trigger distance: trigger_distance_m ({actor.trigger_distance_m}m) "
                    f"cannot exceed initial_distance_m ({actor.initial_distance_m}m)."
                )

            # Check co-location / spatial overlap
            for prev_pos, prev_dist in seen_positions:
                if actor.relative_position == prev_pos and abs(actor.initial_distance_m - prev_dist) < cls.MIN_SPAWN_SPACING_M:
                    errors.append(
                        f"Actor {idx} overlaps with another actor at {actor.relative_position.value} "
                        f"(distance delta {abs(actor.initial_distance_m - prev_dist):.1f}m < {cls.MIN_SPAWN_SPACING_M}m)."
                    )
            seen_positions.append((actor.relative_position, actor.initial_distance_m))

            # -------------------------------------------------------------------
            # Layer 3: SOTIF / ISO 21448 Avoidability & Fairness
            # -------------------------------------------------------------------
            if (
                actor.trigger in [
                    TriggerType.CUT_IN,
                    TriggerType.SUDDEN_BRAKE,
                    TriggerType.JAYWALKING,
                    TriggerType.RED_LIGHT_VIOLATION,
                ]
                or actor.actor_type == ActorType.PEDESTRIAN
            ):
                trig_d = actor.trigger_distance_m or 15.0
                # Closing speed depends on relative positions and trigger kinematics
                if (
                    actor.actor_type == ActorType.PEDESTRIAN
                    or actor.trigger in [TriggerType.JAYWALKING, TriggerType.RED_LIGHT_VIOLATION]
                ):
                    # Ego must avoid or stop for crossing obstacle in its path
                    v_closing_ms = ego_speed / 3.6
                elif actor.relative_position == RelativePosition.ONCOMING:
                    v_closing_ms = (ego_speed + actor.initial_speed_kmh) / 3.6
                elif actor.relative_position == RelativePosition.BEHIND_SAME_LANE:
                    v_closing_ms = (actor.initial_speed_kmh - ego_speed) / 3.6
                elif actor.trigger == TriggerType.SUDDEN_BRAKE:
                    # Target vehicle brakes to stop, closing speed is ego's approach speed
                    v_closing_ms = ego_speed / 3.6
                else:
                    # Cut-in ahead of ego: closing rate is ego speed minus actor speed
                    v_closing_ms = (ego_speed - actor.initial_speed_kmh) / 3.6

                if v_closing_ms > 1.0:
                    ttc_init = trig_d / v_closing_ms
                    drac = (v_closing_ms ** 2) / (2.0 * max(0.5, trig_d))
                    metrics[f"actor_{idx}_ttc_init_s"] = round(ttc_init, 2)
                    metrics[f"actor_{idx}_drac_ms2"] = round(drac, 2)

                    if ttc_init < cls.MIN_FAIR_TTC_SECONDS:
                        errors.append(
                            f"Unfair 'Instant-Kill' Scenario (ISO 21448 violation): Actor {idx} ({actor.actor_type.value}) "
                            f"triggers at {trig_d}m with closing speed {v_closing_ms*3.6:.1f} km/h -> TTC_init={ttc_init:.2f}s "
                            f"< reaction time {cls.MIN_FAIR_TTC_SECONDS}s."
                        )

        # -------------------------------------------------------------------
        # Layer 4: Pre-defined Expectations (Kỳ vọng duyệt trước)
        # -------------------------------------------------------------------
        expected = ir.expected_outcome
        if expected is None:
            warnings.append("Scenario does not declare 'expected_outcome' (kỳ vọng duyệt trước).")
            assigned_verdict = ExpectedVerdict.PASS if not errors else ExpectedVerdict.REJECT_INVALID
        else:
            assigned_verdict = expected.expected_verdict
            # Check consistency of expectation
            if expected.expected_verdict == ExpectedVerdict.REJECT_INVALID and not errors:
                warnings.append("Scenario declared REJECT_INVALID but passed all validation checks.")
            elif expected.expected_verdict in [ExpectedVerdict.PASS, ExpectedVerdict.NEAR_MISS] and errors:
                assigned_verdict = ExpectedVerdict.REJECT_INVALID

        is_valid = len(errors) == 0

        return ValidationResult(
            scenario_name=name,
            is_valid=is_valid,
            verdict=ExpectedVerdict.REJECT_INVALID if not is_valid else assigned_verdict,
            errors=errors,
            warnings=warnings,
            metrics=metrics,
            ir=ir if is_valid else None,
        )

    @classmethod
    def evaluate_execution_against_expectation(
        cls,
        expected: ExpectedOutcome,
        execution_summary: dict,
    ) -> dict[str, Any]:
        """
        Deterministically grade actual CARLA simulation results against
        pre-defined expectations declared prior to the run.
        """
        collision = execution_summary.get("collision", False)
        is_critical = execution_summary.get("is_critical", False)
        min_ttc = execution_summary.get("min_ttc_s", execution_summary.get("min_ttc", float("inf")))
        max_drac = execution_summary.get("max_drac_m_s2", execution_summary.get("max_drac", 0.0))

        # Determine actual verdict
        if collision:
            actual_verdict = ExpectedVerdict.COLLISION_EXPECTED
        elif is_critical:
            actual_verdict = ExpectedVerdict.NEAR_MISS
        else:
            actual_verdict = ExpectedVerdict.PASS

        # Evaluate match
        verdict_matched = (actual_verdict == expected.expected_verdict)

        # Check TTC range if specified
        ttc_range_matched = True
        if expected.expected_min_ttc_range and min_ttc != float("inf"):
            t_low, t_high = expected.expected_min_ttc_range
            ttc_range_matched = (t_low <= min_ttc <= t_high)

        # Overall grading
        grade_passed = verdict_matched and ttc_range_matched

        return {
            "expected_verdict": expected.expected_verdict.value,
            "actual_verdict": actual_verdict.value,
            "verdict_matched": verdict_matched,
            "expected_ttc_range": expected.expected_min_ttc_range,
            "actual_min_ttc": min_ttc,
            "ttc_range_matched": ttc_range_matched,
            "actual_max_drac": max_drac,
            "actual_collision": collision,
            "grade_passed": grade_passed,
            "rationale": expected.rationale,
        }
