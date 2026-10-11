"""CARLA weather presets as conditions, lighting and hour, and the road friction of a surface.

The preset name is CARLA's; conditions and the hour are read from it (e.g. HardRainNight: rain, night, 22 h).
"""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel, Field

FRICTION_BY_SURFACE = {"dry": 1.0, "wet": 0.8, "slippery": 0.6}


@dataclass(frozen=True)
class Preset:
    name: str
    conditions: frozenset[str]
    lighting: str
    hour: float


LIGHTING_HOUR = {"day": 12.0, "sunset": 18.0, "night": 22.0}


class EnvironmentSelection(BaseModel):
    profile_id: str = Field(min_length=1)
    weather_conditions: list[str] = Field(default_factory=list)
    # The CARLA preset when it is used as is; None when its lighting was replaced (e.g. DustStorm at night).
    weather_preset: str | None = None
    base_preset: str | None = None
    lighting: str | None = None
    time_of_day_hour: float = Field(ge=0, lt=24)
    road_surface: str = Field(min_length=1)
    friction_scale_factor: float = Field(gt=0)


def preset(name: str) -> Preset | None:
    """None for "Default" (whatever the map loaded with) and names that say nothing about the weather."""
    if name == "Default":
        return None
    if name.endswith("Noon") or name == "DustStorm":
        lighting, hour = "day", 12.0
    elif name.endswith("Night"):
        lighting, hour = "night", 22.0
    elif name.endswith("Sunset"):
        lighting, hour = "sunset", 18.0
    else:
        return None
    conditions = set()
    if name.startswith("Clear"):
        conditions.add("sunny")
    if "Rain" in name:
        conditions.add("rain")
    if "Cloudy" in name:
        conditions.add("cloudy")
    if "Wet" in name:
        conditions.add("wet")
    if name == "DustStorm":
        conditions.add("dust")
    if "Fog" in name:
        conditions.add("fog")
    return Preset(name, frozenset(conditions), lighting, hour) if conditions else None


def environment(name: str, surface: str | None, lighting: str | None = None) -> EnvironmentSelection | None:
    """The preset's weather, at the preset's light or at `lighting` (the XOSC sets weather and time of day itself)."""
    found = preset(name)
    if found is None:
        return None
    surface = surface or ("wet" if found.conditions & {"rain", "wet"} else "dry")
    light = lighting or found.lighting
    kept = light == found.lighting
    return EnvironmentSelection(
        profile_id=f"carla_preset:{found.name}" + ("" if kept else f"@{light}"), weather_conditions=sorted(found.conditions),
        weather_preset=found.name if kept else None, base_preset=found.name, lighting=light,
        time_of_day_hour=found.hour if kept else LIGHTING_HOUR[light], road_surface=surface,
        friction_scale_factor=FRICTION_BY_SURFACE[surface],
    )
