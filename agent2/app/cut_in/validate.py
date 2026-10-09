"""Deterministic preflight checks for a proposed motorcycle cut-in.

The checked corridor is the forward interval starting at the paired anchors.
Motion checks use constant initial speeds and a centre-gap proxy. A successful
result is necessary for XOSC export, not proof of ScenarioRunner execution.
"""

from __future__ import annotations

import math

from app.catalog.models import SelectedSnapshot
from app.cut_in.model import CutInPlan, SampledContext, ValidationIssue, ValidationResult
from app.cut_in.sample import SampledVariant, is_motorcycle_blueprint

MIN_CORRIDOR_M = 60.0
END_BUFFER_M = 5.0
VEHICLE_LENGTH_ALLOWANCE_M = 4.0
MAX_LATERAL_SPEED_MPS = 2.5


def _issue(code: str, message: str, field: str | None = None, *, recoverable: bool = False) -> ValidationIssue:
    return ValidationIssue(code=code, message=message, field=field, recoverable=recoverable)


def validate_cut_in(plan: CutInPlan, variant: SampledVariant, snapshot: SelectedSnapshot) -> ValidationResult:
    """Check grounding, corridor occupancy, and a rough lead-gap estimate.

    Returns coded issues for an LLM repair loop. The corridor length is only
    verified forward from each anchor; no negative motorcycle start offset is
    allowed without an independently verified upstream segment.
    """

    issues: list[ValidationIssue] = []
    site, context = variant.site, variant.context
    if site.snapshot != snapshot.ref:
        issues.append(_issue("SNAPSHOT_MISMATCH", "Site không thuộc snapshot map đang kiểm tra.", "snapshot"))
    records = snapshot.catalog.cut_in_sites or []
    matching = [record for record in records if record.site_id == site.site_id]
    if not matching or all(record.model_dump() != site.model_dump(exclude={"snapshot"}) for record in matching):
        issues.append(_issue("SITE_NOT_IN_CATALOG", "Site không khớp với chỉ mục đã lưu trong catalog.", "site_id"))
    for field in SampledContext.model_fields:
        if getattr(plan, field) != getattr(context, field):
            issues.append(_issue("CONTEXT_CHANGED", f"Plan đã thay đổi dữ liệu được khóa: {field}.", field))
    vehicles = {vehicle.id: vehicle for vehicle in snapshot.catalog.vehicles}
    ego = vehicles.get(plan.ego_blueprint_id)
    motorcycle = vehicles.get(plan.motorcycle_blueprint_id)
    if ego is None or (ego.base_type or "").lower() != "car":
        issues.append(_issue("EGO_NOT_CAR", "Blueprint ô tô không có trong catalog hoặc không phải ô tô.", "ego_blueprint_id"))
    if motorcycle is None or not is_motorcycle_blueprint(motorcycle):
        issues.append(_issue("ACTOR_NOT_MOTORCYCLE", "Blueprint xe máy không có trong catalog hoặc không phải xe máy.", "motorcycle_blueprint_id"))
    if plan.environment.weather_preset not in snapshot.catalog.weather_presets:
        issues.append(_issue("WEATHER_NOT_IN_CATALOG", "Weather preset không thuộc catalog của map.", "environment.weather_preset"))
    if site.available_length_m < MIN_CORRIDOR_M:
        issues.append(_issue("SITE_TOO_SHORT", "Site không đủ 60 m hành lang đã xác minh.", "site.available_length_m"))
    if (site.ego_s is None or site.motorcycle_s is None
            or site.s_direction is None or site.target_side is None):
        issues.append(_issue("SITE_POSITION_UNAVAILABLE", "Site thiếu tọa độ OpenDRIVE hoặc hướng đổi làn; cần sync lại từ Bridge.", "site"))
    elif not math.isfinite(site.ego_s) or not math.isfinite(site.motorcycle_s):
        issues.append(_issue("NON_FINITE_SITE_POSITION", "Tọa độ OpenDRIVE của site không hữu hạn.", "site"))

    numeric_fields = (
        "motorcycle_start_offset_m", "trigger_time_s", "lane_change_duration_s", "desired_lead_gap_m",
        "ego_speed_kmh", "motorcycle_speed_kmh",
    )
    if not math.isfinite(site.available_length_m):
        issues.append(_issue("NON_FINITE_SITE_LENGTH", "Chiều dài site không phải số hữu hạn.", "site.available_length_m"))
    if (any(not math.isfinite(getattr(plan, field)) for field in numeric_fields)
            or not math.isfinite(plan.environment.time_of_day_hour)
            or not math.isfinite(plan.environment.friction_scale_factor)):
        issues.append(_issue("NON_FINITE_PARAMETER", "Plan chứa giá trị số không hữu hạn.", recoverable=True))
    if issues:
        return ValidationResult(valid=False, issues=issues)

    offset = plan.motorcycle_start_offset_m
    usable_end = site.available_length_m - END_BUFFER_M
    if offset < 0 or offset > usable_end:
        issues.append(_issue(
            "START_OUTSIDE_CORRIDOR",
            "Xe máy phải bắt đầu trong hành lang đã xác minh phía trước pose mốc.",
            "motorcycle_start_offset_m", recoverable=True,
        ))
    duration = plan.lane_change_duration_s
    lateral_distance = math.hypot(
        site.ego_anchor.x - site.motorcycle_anchor.x,
        site.ego_anchor.y - site.motorcycle_anchor.y,
    )
    heading_difference = abs((site.ego_anchor.yaw - site.motorcycle_anchor.yaw + 180) % 360 - 180)
    same_road_section = (site.ego_lane.road_id, site.ego_lane.section_id) == (
        site.motorcycle_lane.road_id, site.motorcycle_lane.section_id,
    )
    same_direction = site.ego_lane.lane_id * site.motorcycle_lane.lane_id > 0 and heading_difference <= 20
    if (not same_road_section or not same_direction or
            abs(site.ego_anchor.z - site.motorcycle_anchor.z) > 1 or not 2.0 <= lateral_distance <= 6.0):
        issues.append(_issue("INVALID_LANE_GEOMETRY", "Cặp làn tại site không đủ điều kiện hình học/cùng chiều.", "site"))
    elif lateral_distance / duration > MAX_LATERAL_SPEED_MPS:
        issues.append(_issue(
            "LANE_CHANGE_TOO_FAST", "Thời lượng đổi làn quá ngắn so với khoảng cách giữa hai làn.",
            "lane_change_duration_s", recoverable=True,
        ))

    completion_time = plan.trigger_time_s + duration
    ego_at_completion = plan.ego_speed_kmh / 3.6 * completion_time
    motorcycle_at_completion = offset + plan.motorcycle_speed_kmh / 3.6 * completion_time
    if ego_at_completion > usable_end or motorcycle_at_completion > usable_end:
        issues.append(_issue(
            "TRAVEL_OUTSIDE_CORRIDOR", "Một trong hai xe đi quá hành lang đã xác minh trước khi đổi làn xong.",
            "trigger_time_s", recoverable=True,
        ))
    centre_gap = motorcycle_at_completion - ego_at_completion
    required_centre_gap = plan.desired_lead_gap_m + VEHICLE_LENGTH_ALLOWANCE_M
    if centre_gap < required_centre_gap:
        issues.append(_issue(
            "INSUFFICIENT_LEAD_GAP", "Xe máy chưa ở đủ xa phía trước ô tô khi hoàn thành đổi làn.",
            "desired_lead_gap_m", recoverable=True,
        ))
    return ValidationResult(valid=not issues, issues=issues)


def repair_maneuver(plan: CutInPlan, variant: SampledVariant) -> CutInPlan | None:
    """Closest numbers to `plan` that satisfy the motion checks above, or None when the corridor cannot fit
    any cut-in at the locked speeds. Used after the LLM ran out of attempts: the checks are plain kinematics."""
    site = variant.site
    usable_end = site.available_length_m - END_BUFFER_M
    lateral = math.hypot(site.ego_anchor.x - site.motorcycle_anchor.x, site.ego_anchor.y - site.motorcycle_anchor.y)
    ego, motorcycle = plan.ego_speed_kmh / 3.6, plan.motorcycle_speed_kmh / 3.6
    duration = max(plan.lane_change_duration_s, round(lateral / MAX_LATERAL_SPEED_MPS + 0.2, 1))
    margin = 0.5
    for trigger in dict.fromkeys((max(plan.trigger_time_s, 0.0), 0.0)):
        completion = trigger + duration
        if ego * completion > usable_end - margin:
            continue
        highest = usable_end - motorcycle * completion - margin  # motorcycle still inside the corridor
        for gap in dict.fromkeys((plan.desired_lead_gap_m, 2.0)):
            lowest = max(0.0, gap + VEHICLE_LENGTH_ALLOWANCE_M + margin - (motorcycle - ego) * completion)
            if lowest <= highest:
                offset = min(max(plan.motorcycle_start_offset_m, lowest), highest)
                return plan.model_copy(update={
                    "motorcycle_start_offset_m": round(offset, 1), "trigger_time_s": trigger,
                    "lane_change_duration_s": duration, "desired_lead_gap_m": gap,
                })
    return None
