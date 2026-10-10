"""Reproducible sampling of catalog-grounded cut-in inputs before the LLM.

Only the site, vehicle blueprints, and weather preset come from a selected
CARLA snapshot. Speed ranges and road friction are explicit agent2 policies,
not measurements inferred from the map or the weather preset.
"""

from __future__ import annotations

import random
import secrets
from collections.abc import Sequence
from dataclasses import dataclass

from app.catalog.models import Blueprint, CutInSite, SelectedSnapshot, SnapshotRef
from app.contracts import MapFailure
from app.cut_in.model import EnvironmentSelection, PromptConstraints, SampledContext
from pydantic import BaseModel, Field, model_validator

FRICTION_BY_SURFACE = {"dry": 1.0, "wet": 0.8, "slippery": 0.6}
LIGHTING_ALIASES = {"day": "day", "daytime": "day", "noon": "day", "night": "night", "sunset": "sunset"}
WEATHER_ALIASES = {
    "sunny": "sunny", "clear": "sunny", "rain": "rain", "rainy": "rain",
    "cloudy": "cloudy", "wet": "wet", "dust": "dust", "fog": "fog", "foggy": "fog",
}
MOTORCYCLE_ID_HINTS = ("motorcycle", "yamaha", "kawasaki", "vespa", "harley-davidson")


class SamplingError(ValueError):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


class SampledVariant(BaseModel):
    site: CutInSite
    context: SampledContext

    @model_validator(mode="after")
    def matches_site(self) -> SampledVariant:
        if self.context.site_id != self.site.site_id:
            raise ValueError("Sampled context site_id does not match the selected site")
        return self


class SamplingResult(BaseModel):
    seed: int = Field(ge=0)
    variants: list[SampledVariant] = Field(default_factory=list)
    map_failures: list[MapFailure] = Field(default_factory=list)


@dataclass(frozen=True)
class _Preset:
    name: str
    conditions: frozenset[str]
    lighting: str
    hour: float


@dataclass(frozen=True)
class _EligibleMap:
    snapshot: SelectedSnapshot
    sites: list[CutInSite]
    cars: list[Blueprint]
    motorcycles: list[Blueprint]
    presets: list[_Preset]


def _preset(name: str) -> _Preset | None:
    if name == "Default":
        return None
    if name.endswith("Noon"):
        lighting, hour = "day", 12.0
    elif name.endswith("Night"):
        lighting, hour = "night", 22.0
    elif name.endswith("Sunset"):
        lighting, hour = "sunset", 18.0
    elif name == "DustStorm":
        lighting, hour = "day", 12.0
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
    return _Preset(name=name, conditions=frozenset(conditions), lighting=lighting, hour=hour) if conditions else None


# Fixed shuffle of the matching presets, walked by seed: the Builder runs variant k with seed k, so consecutive
# variants get different presets (all intensities and times of day) instead of rng.choice repeating some.
PRESET_ORDER_SEED = 20261010


def _rotated(presets: list[_Preset], position: int) -> _Preset:
    order = sorted(presets, key=lambda item: item.name)
    random.Random(PRESET_ORDER_SEED).shuffle(order)
    return order[position % len(order)]


def available_environments(snapshots: Sequence[SelectedSnapshot]) -> list[str]:
    """Weather/lighting combinations the selected CARLA catalogs have a preset for, e.g. "rain+wet, night"."""
    combos = {(tuple(sorted(item.conditions)), item.lighting)
              for snapshot in snapshots for name in snapshot.catalog.weather_presets if (item := _preset(name))}
    return [f"{'+'.join(conditions)}, {lighting}" for conditions, lighting in sorted(combos)]


def is_motorcycle_blueprint(blueprint: Blueprint) -> bool:
    base_type = (blueprint.base_type or "").lower()
    if base_type:
        return base_type == "motorcycle"
    return blueprint.number_of_wheels == 2 and any(hint in blueprint.id.lower() for hint in MOTORCYCLE_ID_HINTS)


def _canonical_constraints(constraints: PromptConstraints) -> tuple[set[str], str | None, str | None]:
    if constraints.unsupported_requirements:
        raise SamplingError("UNSUPPORTED_REQUIREMENT", "Prompt chứa yêu cầu chưa được hỗ trợ: " + "; ".join(constraints.unsupported_requirements))
    weather = set()
    for raw in constraints.weather_conditions:
        condition = WEATHER_ALIASES.get(raw.strip().lower())
        if condition is None:
            raise SamplingError("UNSUPPORTED_WEATHER_CONDITION", f"Điều kiện thời tiết chưa được hỗ trợ: {raw}")
        weather.add(condition)
    lighting = None
    if constraints.lighting is not None:
        lighting = LIGHTING_ALIASES.get(constraints.lighting.strip().lower())
        if lighting is None:
            raise SamplingError("UNSUPPORTED_LIGHTING", f"Điều kiện ánh sáng chưa được hỗ trợ: {constraints.lighting}")
    surface = None
    if constraints.road_surface is not None:
        surface = constraints.road_surface.strip().lower()
        if surface not in FRICTION_BY_SURFACE:
            raise SamplingError("UNSUPPORTED_ROAD_SURFACE", f"Mặt đường chưa được hỗ trợ: {constraints.road_surface}")
    if surface == "dry" and ("rain" in weather or "wet" in weather):
        raise SamplingError("ENVIRONMENT_CONFLICT", "Mặt đường khô mâu thuẫn với điều kiện mưa hoặc mặt đường ướt.")
    for name, speed in (("ego", constraints.ego_speed_kmh), ("motorcycle", constraints.motorcycle_speed_kmh)):
        if speed is not None and not 0 < speed <= 120:
            raise SamplingError("UNSUPPORTED_SPEED", f"Tốc độ {name} phải lớn hơn 0 và không quá 120 km/h.")
    return weather, lighting, surface


def _ref_key(ref: SnapshotRef) -> tuple[int, str, str]:
    return ref.snapshot_id, ref.map_name, ref.content_hash


def sample_variants(
    selected_snapshots: Sequence[SelectedSnapshot],
    sites: Sequence[CutInSite],
    constraints: PromptConstraints,
    *,
    target_count: int,
    seed: int | None = None,
    ego_blueprint_id: str | None = None,
    weather_preset: str | None = None,
) -> SamplingResult:
    """Sample one variant at a time, covering each usable map per cycle.

    ``sites`` must already be filtered by ``find_cut_in_sites``. No location
    inference or prompt parsing is performed here.
    """

    if not 1 <= target_count <= 100:
        raise ValueError("target_count must be between 1 and 100")
    if seed is not None and seed < 0:
        raise ValueError("seed must be non-negative")
    required_weather, lighting, surface = _canonical_constraints(constraints)
    actual_seed = secrets.randbits(64) if seed is None else seed
    rng = random.Random(actual_seed)
    selected = {_ref_key(item.ref): item for item in selected_snapshots}
    if len(selected) != len(selected_snapshots):
        raise ValueError("selected_snapshots contains duplicate snapshot references")
    by_map: dict[tuple[int, str, str], list[CutInSite]] = {key: [] for key in selected}
    for site in sites:
        key = _ref_key(site.snapshot)
        if key not in selected:
            raise ValueError(f"Site {site.site_id} is not from a selected snapshot")
        by_map[key].append(site)

    eligible: list[_EligibleMap] = []
    failures: list[MapFailure] = []
    for key, snapshot in sorted(selected.items(), key=lambda item: item[0]):
        map_sites = sorted(by_map[key], key=lambda item: item.site_id)
        if not map_sites:
            continue  # The site finder owns missing-site failures.
        cars = sorted((vehicle for vehicle in snapshot.catalog.vehicles if (vehicle.base_type or "").lower() == "car"), key=lambda item: item.id)
        motorcycles = sorted((vehicle for vehicle in snapshot.catalog.vehicles if is_motorcycle_blueprint(vehicle)), key=lambda item: item.id)
        if ego_blueprint_id is not None:
            cars = [vehicle for vehicle in cars if vehicle.id == ego_blueprint_id]
        if not cars or not motorcycles:
            failures.append(MapFailure(snapshot=snapshot.ref, code="VEHICLE_NOT_AVAILABLE",
                                       message="Catalog của map thiếu blueprint ô tô hoặc xe máy xác định được."))
            continue
        presets = sorted((item for name in set(snapshot.catalog.weather_presets) if (item := _preset(name)) is not None), key=lambda item: item.name)
        presets = [item for item in presets
                   if required_weather.issubset(item.conditions)
                   and (lighting is None or item.lighting == lighting)
                   and (surface != "wet" or bool(item.conditions & {"rain", "wet"}))
                   and (surface != "dry" or not item.conditions & {"rain", "wet"})]
        if weather_preset is not None:
            presets = [item for item in presets if item.name == weather_preset]
        if not presets:
            asked = ", ".join([*sorted(required_weather), *([lighting] if lighting else []), *([surface] if surface else [])])
            failures.append(MapFailure(snapshot=snapshot.ref, code="WEATHER_NOT_AVAILABLE",
                                       message=f"CARLA không có weather preset cho: {asked or weather_preset}."))
            continue
        eligible.append(_EligibleMap(snapshot, map_sites, cars, motorcycles, presets))

    variants: list[SampledVariant] = []
    while eligible and len(variants) < target_count:
        cycle = list(eligible)
        rng.shuffle(cycle)
        for item in cycle:
            if len(variants) >= target_count:
                break
            site = rng.choice(item.sites)
            preset = _rotated(item.presets, actual_seed + len(variants))
            actual_surface = surface or ("wet" if preset.conditions & {"rain", "wet"} else "dry")
            environment = EnvironmentSelection(
                profile_id=f"carla_preset:{preset.name}",
                weather_conditions=sorted(preset.conditions),
                weather_preset=preset.name,
                time_of_day_hour=preset.hour,
                road_surface=actual_surface,
                friction_scale_factor=FRICTION_BY_SURFACE[actual_surface],
                sources={
                    "weather_conditions": "user" if required_weather else "random",
                    "weather_preset": "random",
                    "time_of_day_hour": "user" if lighting else "derived",
                    "road_surface": "user" if surface else "derived",
                    "friction_scale_factor": "derived",
                },
            )
            context = SampledContext(
                site_id=site.site_id,
                ego_blueprint_id=rng.choice(item.cars).id,
                motorcycle_blueprint_id=rng.choice(item.motorcycles).id,
                ego_speed_kmh=constraints.ego_speed_kmh if constraints.ego_speed_kmh is not None else float(rng.randint(25, 55)),
                motorcycle_speed_kmh=constraints.motorcycle_speed_kmh if constraints.motorcycle_speed_kmh is not None else float(rng.randint(25, 65)),
                environment=environment,
                sources={
                    "site_id": "random", "ego_blueprint_id": "random", "motorcycle_blueprint_id": "random",
                    "ego_speed_kmh": "user" if constraints.ego_speed_kmh is not None else "random",
                    "motorcycle_speed_kmh": "user" if constraints.motorcycle_speed_kmh is not None else "random",
                },
            )
            variants.append(SampledVariant(site=site, context=context))
    return SamplingResult(seed=actual_seed, variants=variants, map_failures=failures)
