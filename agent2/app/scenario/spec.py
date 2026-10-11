"""The scenario the LLM composes, and the checked plan built from it.

Nothing here names a scenario type: a cut-in, a braking lead vehicle or a slow truck are all a few actors placed
relative to the ego plus timed actions. Fields are all required with null meaning "not set": the API's strict
tool schema does not allow defaults.
"""

from __future__ import annotations

from typing import Any, Literal

from app.scenario.environment import EnvironmentSelection
from pydantic import BaseModel, Field


class StartLane(BaseModel):
    road_id: int
    lane_id: int
    s: float


class EgoSpec(BaseModel):
    blueprint_id: str | None = Field(description="Null khi ô tô ego đã khóa trong yêu cầu.")
    start: StartLane = Field(description="Vị trí ô tô ego, lấy từ find_ego_starts hoặc search_cut_in_sites.")
    speed_kmh: float


class ActorSpec(BaseModel):
    id: str = Field(description="Tên ngắn duy nhất (chữ, số, gạch dưới), ví dụ moto1, truck1.")
    blueprint_id: str = Field(description="id trong catalog.vehicles.")
    lane_offset: int = Field(description="Làn lúc bắt đầu so với làn ego: 0 cùng làn, +1 làn bên trái, -1 làn bên phải "
                                         "(theo chiều xe chạy). Chỉ làn cùng chiều.")
    ds_m: float = Field(description="Vị trí dọc so với ego theo tâm xe (m): dương là phía trước, âm là phía sau.")
    speed_kmh: float


class ActionSpec(BaseModel):
    actor: str = Field(description="id của tác nhân (không phải ego).")
    at_time_s: float = Field(description="Thời điểm bắt đầu hành động (giây mô phỏng).")
    type: Literal["speed", "lane_change", "brake_to_stop"]
    target_speed_kmh: float | None = Field(description="type=speed: tốc độ đích.")
    acceleration_mps2: float | None = Field(description="type=speed/brake_to_stop: độ lớn gia/giảm tốc (m/s²).")
    direction: Literal["into_ego_lane", "left", "right"] | None = Field(
        description="type=lane_change: into_ego_lane (hệ thống tự tính trái/phải để vào làn của ego), hoặc left/right "
                    "theo chính tác nhân.")
    duration_s: float | None = Field(description="type=lane_change: thời gian đổi làn (s).")


class ScenarioSpec(BaseModel):
    """Nộp kịch bản khi đã đủ thông tin. Hệ thống kiểm tra bằng mô phỏng và tạo XOSC; nếu chưa đạt sẽ trả lỗi kèm số liệu."""

    map_name: str
    title: str = Field(description="Mô tả ngắn kịch bản bằng tiếng Việt.")
    ego: EgoSpec
    actors: list[ActorSpec]
    actions: list[ActionSpec]
    critical_actor: str = Field(description="id tác nhân gây ra sự cố cho ego.")
    expected: Literal["near_miss", "collision"] = Field(
        description="near_miss: nguy hiểm nhưng ego còn cơ hội xử lý; collision: chỉ khi prompt muốn va chạm không tránh được.")
    weather_preset: str = Field(description="Tên trong catalog.weather_presets (cho thời tiết).")
    lighting: Literal["day", "sunset", "night"] | None = Field(
        description="Null: ánh sáng của preset. Đặt khi prompt cần ánh sáng khác preset, ví dụ bão bụi (DustStorm) ban đêm.")
    road_surface: Literal["dry", "wet", "slippery"] | None = Field(description="Null: suy ra từ thời tiết.")
    duration_s: float = Field(description="Thời lượng kịch bản (s), 5-30.")


class ResolvedActor(BaseModel):
    """An actor placed on a real lane: what the exporter writes."""

    id: str
    blueprint_id: str
    kind: str
    length_m: float
    width_m: float
    height_m: float
    road_id: int
    lane_id: int
    s: float
    speed_kmh: float


class ResolvedAction(ActionSpec):
    # Speed when the action starts, from the simulation: the exporter turns rates into durations and distances with it.
    speed_at_start_kmh: float
    # The side a lane change actually goes ("into_ego_lane" resolved to left/right by the simulation).
    resolved_direction: Literal["left", "right"] | None = None


class ScenarioPlan(BaseModel):
    title: str
    ego: ResolvedActor
    actors: list[ResolvedActor]
    actions: list[ResolvedAction]
    critical_actor: str
    expected: str
    duration_s: float
    environment: EnvironmentSelection
    # Critical interaction measured by the simulation (non-reacting ego): min TTC, when, gap, distance...
    metrics: dict[str, float | str | bool | None] = Field(default_factory=dict)
    # Numbers the system tuned by simulation after the LLM's submission (structure stays the LLM's).
    tuning: list[str] = Field(default_factory=list)

    @property
    def start_id(self) -> str:
        return f"{self.ego.road_id}:{self.ego.lane_id}:{self.ego.s:g}"


class ValidationIssue(BaseModel):
    code: str = Field(min_length=1)
    message: str = Field(min_length=1)
    field: str | None = None
    # The same spec with only numbers changed so that this issue passes (found by simulation), and what changed.
    fix: Any = Field(default=None, exclude=True)
    fix_note: str | None = Field(default=None, exclude=True)


class ValidationResult(BaseModel):
    valid: bool
    issues: list[ValidationIssue] = Field(default_factory=list)
