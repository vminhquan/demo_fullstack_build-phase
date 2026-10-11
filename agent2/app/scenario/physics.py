"""What each kind of road user can physically do, and its size when the catalog does not say.

These are vehicle limits, not scenarios: any scenario the LLM composes is checked against the same table.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.catalog.models import Blueprint

MOTORCYCLE_ID_HINTS = ("motorcycle", "yamaha", "kawasaki", "vespa", "harley-davidson")


@dataclass(frozen=True)
class Limits:
    max_speed_kmh: float
    max_accel_mps2: float
    max_decel_mps2: float
    # Sideways speed during a lane change; a 3.5 m lane change takes at least 3.5 / this.
    max_lateral_mps: float
    length_m: float
    width_m: float
    height_m: float
    category: str  # OpenSCENARIO vehicleCategory


LIMITS: dict[str, Limits] = {
    "car": Limits(130, 4.0, 9.0, 2.0, 4.5, 1.9, 1.5, "car"),
    "van": Limits(110, 3.0, 8.0, 1.6, 5.0, 2.0, 2.0, "van"),
    "truck": Limits(100, 2.0, 6.0, 1.2, 8.0, 2.5, 3.2, "truck"),
    "bus": Limits(90, 1.5, 6.0, 1.2, 11.0, 2.6, 3.2, "bus"),
    "motorcycle": Limits(120, 5.0, 8.0, 2.5, 2.2, 0.8, 1.5, "motorbike"),
    "bicycle": Limits(35, 1.5, 4.0, 1.0, 1.8, 0.6, 1.7, "bicycle"),
}


def kind_of(blueprint: Blueprint) -> str | None:
    """car, van, truck, bus, motorcycle, bicycle; None for a blueprint whose kind is unknown."""
    base_type = (blueprint.base_type or "").lower()
    if base_type in LIMITS:
        return base_type
    if not base_type and blueprint.number_of_wheels == 2:
        return "motorcycle" if any(hint in blueprint.id.lower() for hint in MOTORCYCLE_ID_HINTS) else "bicycle"
    if not base_type and blueprint.number_of_wheels == 4:
        return "car"
    return None


def size(blueprint: Blueprint, kind: str) -> tuple[float, float, float]:
    """(length, width, height) from the catalog's bounding box, else the kind's typical size."""
    extra = blueprint.model_extra or {}
    limits = LIMITS[kind]
    return (float(extra.get("length_m") or limits.length_m), float(extra.get("width_m") or limits.width_m),
            float(extra.get("height_m") or limits.height_m))
