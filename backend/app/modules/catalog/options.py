"""Metadata choices (map, ego vehicle, adversary, environment) derived from the CARLA catalogs a project can see.

Pure functions over the light parts of each catalog (no waypoints), so the lists always reflect whatever
data is installed: shipped defaults, admin/creator imports, and later Worker syncs.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

# Matches generation/mapping.py: what the Agent writes into adversary_type / environment_code.
ADVERSARY_FROM_BASE_TYPE = {"car": "car", "van": "van", "truck": "truck", "bus": "bus", "motorcycle": "motorcycle", "bicycle": "cyclist"}
ADVERSARY_LABELS = {
    "car": "Ô tô", "van": "Xe van", "truck": "Xe tải", "bus": "Xe buýt",
    "motorcycle": "Xe máy", "cyclist": "Xe đạp", "pedestrian": "Người đi bộ",
}
ADVERSARY_ORDER = ["pedestrian", "motorcycle", "cyclist", "car", "truck", "van", "bus"]
EGO_BASE_TYPES = ("car", "van", "truck", "bus")

# Conditions the Agent generates (WeatherPreset); available on every CARLA install.
STANDARD_ENVIRONMENTS = [
    ("clear", "Trời quang"), ("rain", "Mưa"), ("heavy_rain", "Mưa lớn"),
    ("fog", "Sương mù"), ("night", "Ban đêm"), ("dusk", "Hoàng hôn"),
]
_PRESET_WORDS = [
    ("HardRain", "mưa to"), ("MidRainy", "mưa vừa"), ("MidRain", "mưa vừa"), ("SoftRain", "mưa nhẹ"),
    ("WetCloudy", "ướt, nhiều mây"), ("Wet", "đường ướt"), ("Cloudy", "nhiều mây"), ("Clear", "trời quang"),
    ("DustStorm", "bão bụi"), ("Noon", "buổi trưa"), ("Sunset", "hoàng hôn"), ("Night", "ban đêm"),
]


def preset_label(name: str) -> str:
    rest, words = name, []
    while rest:
        match = next(((token, label) for token, label in _PRESET_WORDS if rest.startswith(token)), None)
        if match is None:
            return name
        words.append(match[1])
        rest = rest[len(match[0]):]
    return ", ".join(words).capitalize()


def vehicle_label(blueprint_id: str) -> str:
    parts = blueprint_id.split(".")[1:]
    return " ".join(re.sub(r"[_-]+", " ", part).title() for part in parts) or blueprint_id


def _new_map(code: str) -> dict[str, Any]:
    return {"code": code, "has_lane_data": False, "snapshot_ids": set(), "sources": set(), "lane_versions": set(), "listed_versions": set()}


@dataclass
class _Merged:
    sources: set[str] = field(default_factory=set)
    snapshot_ids: set[int] = field(default_factory=set)


def build_options(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """rows: one dict per snapshot with id, source, is_default, map_name, carla_version, label,
    vehicles, walkers, available_maps, weather_presets."""
    maps: dict[str, dict[str, Any]] = {}
    vehicles: dict[str, dict[str, Any]] = {}
    adversaries: dict[str, _Merged] = {}
    presets: dict[str, _Merged] = {}

    for row in rows:
        origin = "DEFAULT" if row["is_default"] else row["source"]
        entry = maps.setdefault(row["map_name"], _new_map(row["map_name"]))
        entry["has_lane_data"] = True
        entry["snapshot_ids"].add(row["id"])
        entry["sources"].add(origin)
        entry["lane_versions"].add(row["carla_version"])
        for other in row.get("available_maps") or []:
            listed = maps.setdefault(other, _new_map(other))
            listed["sources"].add(origin)
            listed["listed_versions"].add(row["carla_version"])
        for vehicle in row.get("vehicles") or []:
            base = (vehicle.get("base_type") or "").lower()
            if base in ADVERSARY_FROM_BASE_TYPE:
                merged = adversaries.setdefault(ADVERSARY_FROM_BASE_TYPE[base], _Merged())
                merged.sources.add(origin)
                merged.snapshot_ids.add(row["id"])
            if base in EGO_BASE_TYPES:
                item = vehicles.setdefault(vehicle["id"], {"code": vehicle["id"], "label": vehicle_label(vehicle["id"]), "base_type": base, "snapshot_ids": set()})
                item["snapshot_ids"].add(row["id"])
        if row.get("walkers"):
            merged = adversaries.setdefault("pedestrian", _Merged())
            merged.sources.add(origin)
            merged.snapshot_ids.add(row["id"])
        for preset in row.get("weather_presets") or []:
            if preset != "Default":
                merged = presets.setdefault(preset, _Merged())
                merged.sources.add(origin)
                merged.snapshot_ids.add(row["id"])

    def finish(item: dict[str, Any]) -> dict[str, Any]:
        return {**item, **{key: sorted(value) for key, value in item.items() if isinstance(value, set)}}

    def finish_map(item: dict[str, Any]) -> dict[str, Any]:
        # A map with lane data reports the CARLA versions of that data, not of servers that merely list it.
        versions = item.pop("lane_versions") if item["has_lane_data"] else item.pop("listed_versions")
        item.pop("lane_versions", None)
        item.pop("listed_versions", None)
        return finish({**item, "carla_versions": versions})

    base_rank = {base: index for index, base in enumerate(EGO_BASE_TYPES)}
    return {
        "maps": [finish_map(item) for item in sorted(maps.values(), key=lambda m: (not m["has_lane_data"], m["code"]))],
        "ego_vehicles": [finish(item) for item in sorted(vehicles.values(), key=lambda v: (base_rank[v["base_type"]], v["label"]))],
        "adversary_types": [
            {"code": code, "label": ADVERSARY_LABELS.get(code, code), "snapshot_ids": sorted(adversaries[code].snapshot_ids)}
            for code in ADVERSARY_ORDER if code in adversaries
        ],
        "environments": [
            *({"code": code, "label": label, "group": "standard", "snapshot_ids": sorted({row["id"] for row in rows})} for code, label in STANDARD_ENVIRONMENTS),
            *({"code": name, "label": preset_label(name), "group": "carla_preset", "snapshot_ids": sorted(presets[name].snapshot_ids)} for name in sorted(presets)),
        ] if rows else [],
    }
