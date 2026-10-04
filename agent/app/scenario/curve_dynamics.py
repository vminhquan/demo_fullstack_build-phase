"""
ScenarioForge — Vehicle Dynamics & Curve Negotiation Analysis
=============================================================

Analyzes vehicle cornering physics, lateral acceleration, tire friction limits,
and median divider / curb collision risks at 60 km/h (16.67 m/s) in CARLA.

Formulas & Principles (ISO 21448 / Vehicle Dynamics):
  - v [m/s] = speed_kmh / 3.6
  - a_lat = v^2 / R  (required centripetal acceleration)
  - a_lat_max = mu * g  (maximum available lateral tire grip)
  - R_crit = v^2 / (mu * g)  (minimum radius to negotiate curve without skidding)
  - v_safe = sqrt(mu * g * R * safety_factor) * 3.6  (safe cornering speed)
"""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass
class CurveAnalysisResult:
    speed_kmh: float
    speed_ms: float
    radius_m: float
    friction_mu: float
    lateral_accel_ms2: float
    friction_limit_ms2: float
    accel_g: float
    margin_ratio: float  # a_lat / (mu * g)
    is_negotiable: bool
    risk_verdict: str  # "SAFE", "MARGINAL", "SEVERE_UNDERSTEER_CRASH"
    max_safe_speed_kmh: float
    skid_phenomenon: str
    detailed_explanation: str


def compute_lateral_acceleration(speed_kmh: float, radius_m: float) -> float:
    """Calculate required lateral acceleration a_lat = v^2 / R in m/s^2."""
    if radius_m <= 0:
        raise ValueError("Radius must be strictly positive.")
    v_ms = speed_kmh / 3.6
    return (v_ms ** 2) / radius_m


def compute_critical_radius(speed_kmh: float, friction_mu: float = 0.8, g: float = 9.81) -> float:
    """Calculate critical radius R_crit = v^2 / (mu * g) in meters."""
    if friction_mu <= 0:
        raise ValueError("Friction coefficient must be strictly positive.")
    v_ms = speed_kmh / 3.6
    return (v_ms ** 2) / (friction_mu * g)


def compute_max_safe_speed(
    radius_m: float,
    friction_mu: float = 0.8,
    safety_factor: float = 0.85,
    g: float = 9.81,
) -> float:
    """Calculate maximum safe cornering speed in km/h: v_safe = sqrt(mu * g * R * safety_factor) * 3.6."""
    if radius_m <= 0 or friction_mu <= 0:
        return 0.0
    v_ms = math.sqrt(friction_mu * g * radius_m * safety_factor)
    return round(v_ms * 3.6, 1)


def estimate_radius_from_points(
    p1: tuple[float, float],
    p2: tuple[float, float],
    p3: tuple[float, float],
) -> float:
    """
    Estimate curve radius from three 2D points (x, y) using Menger curvature.
    Returns radius in meters, or float('inf') if collinear.
    """
    d12 = math.hypot(p2[0] - p1[0], p2[1] - p1[1])
    d23 = math.hypot(p3[0] - p2[0], p3[1] - p2[1])
    d31 = math.hypot(p1[0] - p3[0], p1[1] - p3[1])

    s = (d12 + d23 + d31) / 2.0
    area_sq = s * (s - d12) * (s - d23) * (s - d31)
    if area_sq <= 1e-6:
        return float("inf")  # Straight line

    area = math.sqrt(area_sq)
    radius = (d12 * d23 * d31) / (4.0 * area)
    return max(0.1, radius)


def analyze_curve_at_speed(
    speed_kmh: float = 60.0,
    radius_m: float = 15.0,
    friction_mu: float = 0.8,
    g: float = 9.81,
) -> CurveAnalysisResult:
    """
    Perform a complete vehicle dynamics analysis for a vehicle negotiating
    a curve at a given speed and friction coefficient.
    """
    v_ms = speed_kmh / 3.6
    a_lat = compute_lateral_acceleration(speed_kmh, radius_m)
    a_limit = friction_mu * g
    margin = a_lat / a_limit
    accel_g = a_lat / g
    v_safe = compute_max_safe_speed(radius_m, friction_mu=friction_mu, g=g)

    if margin <= 0.85:
        verdict = "SAFE"
        phenomenon = "Stable cornering within tire adhesion limits."
        explanation = (
            f"At {speed_kmh:.1f} km/h (v={v_ms:.2f} m/s) on radius R={radius_m:.1f}m, "
            f"required a_lat={a_lat:.2f} m/s2 ({accel_g:.2f}g) is well below tire grip limit "
            f"{a_limit:.2f} m/s2 (margin={margin:.2f}x). The vehicle safely follows the lane."
        )
        negotiable = True
    elif margin <= 1.0:
        verdict = "MARGINAL"
        phenomenon = "Impending tire saturation and slight lane drift."
        explanation = (
            f"At {speed_kmh:.1f} km/h on radius R={radius_m:.1f}m, required a_lat={a_lat:.2f} m/s2 "
            f"is near the physical limit {a_limit:.2f} m/s2 (margin={margin:.2f}x). "
            f"Tire slip angles approach saturation. High risk of crossing lane dividers if steering is abrupt."
        )
        negotiable = True
    else:
        verdict = "SEVERE_UNDERSTEER_CRASH"
        phenomenon = (
            "Total tire saturation understeer. Vehicle slides tangentially forward "
            "and crashes into the median barrier (dải phân cách) or road boundary."
        )
        explanation = (
            f"At {speed_kmh:.1f} km/h (v={v_ms:.2f} m/s) on radius R={radius_m:.1f}m, "
            f"required lateral acceleration a_lat={a_lat:.2f} m/s2 ({accel_g:.2f}g) exceeds tire grip "
            f"limit {a_limit:.2f} m/s2 by {margin:.2f}x! In CARLA, Traffic Manager maintains target speed, "
            f"causing front tire slip angles to blow past saturation. The vehicle cannot generate enough "
            f"centripetal force, understeers off the road, and collides with the central median divider or curb. "
            f"Safe cornering speed for this curve is {v_safe:.1f} km/h."
        )
        negotiable = False

    return CurveAnalysisResult(
        speed_kmh=speed_kmh,
        speed_ms=v_ms,
        radius_m=radius_m,
        friction_mu=friction_mu,
        lateral_accel_ms2=round(a_lat, 2),
        friction_limit_ms2=round(a_limit, 2),
        accel_g=round(accel_g, 2),
        margin_ratio=round(margin, 2),
        is_negotiable=negotiable,
        risk_verdict=verdict,
        max_safe_speed_kmh=v_safe,
        skid_phenomenon=phenomenon,
        detailed_explanation=explanation,
    )


def generate_60kmh_dynamics_report() -> str:
    """Generate a comprehensive technical analysis report answering the user query."""
    test_cases = [
        ("Tight 90° Intersection Turn", 12.0, 0.8, "Dry City Intersection"),
        ("Standard City Curve (R=20m)", 20.0, 0.8, "Dry Urban Street"),
        ("Standard City Curve in Rain (R=20m)", 20.0, 0.5, "Wet Urban Street (Rain)"),
        ("Moderate Urban Bend (R=35m)", 35.0, 0.8, "Dry Arterial Road"),
        ("Moderate Urban Bend in Rain (R=35m)", 35.0, 0.5, "Wet Arterial Road (Rain)"),
        ("Arterial Road Curve (R=60m)", 60.0, 0.8, "Dry Arterial Curve"),
        ("Gentle Highway Curve (R=100m)", 100.0, 0.8, "Dry Highway Curve"),
        ("Gentle Highway Curve in Heavy Rain (R=100m)", 100.0, 0.35, "Highway in Heavy Rain"),
    ]

    lines = [
        "=" * 80,
        "CARLA VEHICLE DYNAMICS AT 60 KM/H (16.67 M/S) — CURVE & MEDIAN COLLISION REPORT",
        "=" * 80,
        "Physical Law: a_lat = v^2 / R   |   a_lat_max = mu * g (g = 9.81 m/s^2)",
        "At v = 60.0 km/h = 16.67 m/s: v^2 = 277.78 m^2/s^2",
        f"Critical Radius on Dry Road (mu=0.8):  R_crit = {compute_critical_radius(60, 0.8):.1f} m",
        f"Critical Radius on Wet Road (mu=0.5):  R_crit = {compute_critical_radius(60, 0.5):.1f} m",
        f"Critical Radius in Heavy Rain (mu=0.35): R_crit = {compute_critical_radius(60, 0.35):.1f} m",
        "-" * 80,
        f"{'Geometry / Condition':<35} | {'Radius':<7} | {'mu':<4} | {'a_lat':<8} | {'Limit':<6} | {'Margin':<6} | {'Verdict':<18} | {'Safe V'}",
        "-" * 80,
    ]

    for name, r, mu, _ in test_cases:
        res = analyze_curve_at_speed(60.0, r, mu)
        row = (
            f"{name:<35} | {r:>5.1f}m | {mu:>4.2f} | {res.lateral_accel_ms2:>6.2f}ms2 | "
            f"{res.friction_limit_ms2:>5.2f} | {res.margin_ratio:>5.2f}x | {res.risk_verdict:<18} | {res.max_safe_speed_kmh:.0f} km/h"
        )
        lines.append(row)

    lines.append("=" * 80)
    lines.append("\nCONCLUSIONS & TRAFFIC MANAGER FINDINGS:")
    lines.append("1. CAN IT NEGOTIATE TIGHT TURNS AT 60 KM/H?")
    lines.append("   - NO. At R=12-20m, required lateral acceleration is 13.9 - 23.1 m/s^2 (1.4g - 2.4g).")
    lines.append("   - Maximum tire friction is only 7.85 m/s^2 on dry asphalt, 4.91 m/s^2 in rain.")
    lines.append("   - PhysX tire slip angle saturates immediately -> Severe understeer -> Vehicle slides straight")
    lines.append("     into the concrete median barrier (dải phân cách) or sidewalk curb!")
    lines.append("2. CARLA TRAFFIC MANAGER BEHAVIOR:")
    lines.append("   - tm.set_desired_speed(ego, 60.0) commands TM to cruise at 60 km/h.")
    lines.append("   - CARLA Traffic Manager DOES NOT automatically reduce cruising speed for road curvature.")
    lines.append("   - Without anticipatory speed governing, ego will plow into barriers on any curve with R < 35.4m.")
    lines.append("3. GENTLE CURVES & HIGHWAYS (R >= 60-100m):")
    lines.append("   - Yes, safe. At R=60m, a_lat = 4.63 m/s^2 <= mu*g (dry). At R=100m, a_lat = 2.78 m/s^2.")
    lines.append("4. RESOLVER & GENERATOR SAFEGUARDS IMPLEMENTED:")
    lines.append("   - Filter spawn points to prevent spawning 60 km/h vehicles in front of sharp R < 35m bends.")
    lines.append("   - Embed dynamic curvature speed governor in generated scenarios to slow to v_safe before turns.")
    lines.append("   - LLM generator prompt forbids assigning 60+ km/h to tight urban intersection turns.")
    lines.append("=" * 80)

    return "\n".join(lines)


if __name__ == "__main__":
    print(generate_60kmh_dynamics_report())
