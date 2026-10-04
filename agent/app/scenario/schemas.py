"""
ScenarioForge — ScenarioIR, Kinematic Constraints & Step Schemas
================================================================
Canonical data models for autonomous driving scenarios, actors,
pre-defined expectations, and 6-step Decomposed DAG generation.
"""

from __future__ import annotations

from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field


# --- Enums for constrained choices ---

class WeatherPreset(str, Enum):
    CLEAR = "clear"
    RAIN = "rain"
    HEAVY_RAIN = "heavy_rain"
    FOG = "fog"
    NIGHT = "night"
    DUSK = "dusk"


class ActorType(str, Enum):
    CAR = "car"
    MOTORCYCLE = "motorcycle"
    BICYCLE = "bicycle"
    PEDESTRIAN = "pedestrian"
    TRUCK = "truck"


class RelativePosition(str, Enum):
    """Where an actor is relative to the ego vehicle."""
    AHEAD_SAME_LANE = "ahead_same_lane"
    AHEAD_ADJACENT_LEFT = "ahead_adjacent_left"
    AHEAD_ADJACENT_RIGHT = "ahead_adjacent_right"
    ONCOMING = "oncoming"
    CROSSING_FROM_LEFT = "crossing_from_left"
    CROSSING_FROM_RIGHT = "crossing_from_right"
    BEHIND_SAME_LANE = "behind_same_lane"


class TriggerType(str, Enum):
    """What causes the dangerous event."""
    CUT_IN = "cut_in"
    SUDDEN_BRAKE = "sudden_brake"
    JAYWALKING = "jaywalking"
    RED_LIGHT_VIOLATION = "red_light_violation"
    LANE_DEPARTURE = "lane_departure"
    DOOR_OPENING = "door_opening"


class ExpectedVerdict(str, Enum):
    """Pre-defined verdict expectations for deterministic test grading."""
    PASS = "PASS"
    COLLISION_EXPECTED = "COLLISION_EXPECTED"
    NEAR_MISS = "NEAR_MISS"
    REJECT_INVALID = "REJECT_INVALID"


class SafetyViolationType(str, Enum):
    """Types of safety violations expected or observed during evaluation."""
    NONE = "NONE"
    COLLISION = "COLLISION"
    TTC_CRITICAL = "TTC_CRITICAL"
    DRAC_EXCEEDED = "DRAC_EXCEEDED"
    KINEMATIC_VIOLATION = "KINEMATIC_VIOLATION"
    OUT_OF_BOUNDS = "OUT_OF_BOUNDS"


# --- Core IR Models ---

class ExpectedOutcome(BaseModel):
    """Pre-defined expectation declared before simulation execution (Kỳ vọng duyệt trước)."""
    expected_verdict: ExpectedVerdict = Field(
        default=ExpectedVerdict.PASS,
        description="Declared expected outcome: PASS, COLLISION_EXPECTED, NEAR_MISS, or REJECT_INVALID",
    )
    expected_min_ttc_range: Optional[tuple[float, float]] = Field(
        default=None,
        description="Expected minimum Time-to-Collision range [min, max] in seconds",
    )
    expected_safety_violation: Optional[SafetyViolationType] = Field(
        default=SafetyViolationType.NONE,
        description="Expected safety violation type if applicable",
    )
    rationale: Optional[str] = Field(
        default=None,
        description="Physical and tactical explanation of why this verdict is expected",
    )


class ActorSpec(BaseModel):
    """One non-ego actor in the scenario."""
    actor_type: ActorType
    relative_position: RelativePosition
    initial_distance_m: float = Field(ge=5, le=200, description="Distance from ego in meters")
    initial_speed_kmh: float = Field(ge=0, le=150)
    trigger: Optional[TriggerType] = None
    trigger_distance_m: Optional[float] = Field(
        default=None, ge=3, le=100,
        description="Distance from ego when trigger activates",
    )


class EgoSpec(BaseModel):
    """The autonomous vehicle under test."""
    initial_speed_kmh: float = Field(ge=10, le=130)
    road_type: str = Field(description="e.g., 'intersection_4way', 'highway_straight', 'urban_curve'")


class ScenarioIR(BaseModel):
    """The complete Intermediate Representation — canonical scenario specification."""
    name: str
    description: str = Field(description="Original NL description")
    ego: EgoSpec
    actors: list[ActorSpec] = Field(min_length=1, max_length=6)
    weather: WeatherPreset
    time_of_day_hour: int = Field(ge=0, le=23)
    map_name: str = Field(default="Town03", description="CARLA map name")
    occlusions: list[str] = Field(
        default_factory=list,
        description="Objects blocking ego's view, e.g., 'parked_van_on_right'",
    )
    expected_outcome: Optional[ExpectedOutcome] = Field(
        default=None,
        description="Pre-defined outcome expectation for deterministic regression grading",
    )


# --- Decomposed 6-Step Generation Data Models ---

class ScenarioInterpretation(BaseModel):
    """Step 1 Output: Structured semantic interpretation of user prompt (Pre-Generation HITL)."""
    intent_type: str = Field(
        default="SCENARIO_REQUEST",
        description="Either 'SCENARIO_REQUEST' or 'REGULATION_QUERY'",
    )
    scenario_name: str = Field(..., description="Descriptive snake_case identifier")
    original_description: str = Field(..., description="Verbatim user prompt")
    summary: str = Field(..., description="Concise 1-sentence tactical summary")
    road_type: str = Field(default="urban_straight", description="Inferred CARLA road geometry")
    ego_summary: str = Field(default="", description="Intended ego speed and maneuver")
    actor_summaries: list[str] = Field(
        default_factory=list,
        description="List of adversarial actors and their maneuvers",
    )
    spatial_summary: str = Field(default="", description="Spatial topology and relative positions")
    trigger_summary: str = Field(default="", description="Trigger conditions and distances")
    weather_hint: str = Field(default="clear", description="Inferred weather condition")
    applicable_regulations: list[str] = Field(
        default_factory=list,
        description="Relevant safety standard IDs retrieved via RAG",
    )
    similar_executions: list[str] = Field(
        default_factory=list,
        description="Historical similar simulation run IDs retrieved via RAG",
    )


class SpatialStepOutput(BaseModel):
    """Step 2 Output: Spatial & Map configuration."""
    map_name: str = Field(default="Town03")
    road_type: str = Field(default="urban_straight")
    occlusions: list[str] = Field(default_factory=list)


class EgoStepOutput(BaseModel):
    """Step 3 Output: Ego vehicle configuration."""
    ego: EgoSpec


class ActorsStepOutput(BaseModel):
    """Step 4 Output: Adversarial actors placement."""
    actors: list[ActorSpec] = Field(..., min_length=1, max_length=6)


class TriggersStepOutput(BaseModel):
    """Step 5 Output: Refined trigger parameters and expected safety outcome."""
    actors: list[ActorSpec] = Field(..., min_length=1, max_length=6)
    expected_outcome: Optional[ExpectedOutcome] = None


class WeatherEnvStepOutput(BaseModel):
    """Step 6 Output: Weather and lighting parameters."""
    weather: WeatherPreset = Field(default=WeatherPreset.CLEAR)
    time_of_day_hour: int = Field(default=12, ge=0, le=23)


# Example IR instance for reference / testing
EXAMPLE_IR = {
    "name": "motorcycle_cut_in_rain_intersection",
    "description": "xe máy tạt đầu khi trời mưa ở ngã tư",
    "ego": {
        "initial_speed_kmh": 40,
        "road_type": "intersection_4way",
    },
    "actors": [
        {
            "actor_type": "motorcycle",
            "relative_position": "ahead_adjacent_right",
            "initial_distance_m": 25,
            "initial_speed_kmh": 35,
            "trigger": "cut_in",
            "trigger_distance_m": 10,
        }
    ],
    "weather": "rain",
    "time_of_day_hour": 14,
    "map_name": "Town03",
    "occlusions": [],
    "expected_outcome": {
        "expected_verdict": "NEAR_MISS",
        "expected_min_ttc_range": [0.8, 1.8],
        "expected_safety_violation": "TTC_CRITICAL",
        "rationale": "Aggressive cut-in by motorcycle at 10m requires hard braking under wet conditions.",
    },
}
