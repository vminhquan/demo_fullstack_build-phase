"""Convert raw CARLA dumps into catalog.v1.

Supported inputs:
- the per-chapter export written by P-065 `connect_carla/carla_export.py` (directory, or the same folder zipped);
- the `context` payload of P-065 `src/services/carla_worker.py` (JSON), which the future Worker can reuse;
- a catalog.v1 JSON document as is.

CLI:  python -m app.modules.catalog.importers <export_dir> <out.json>
"""
from __future__ import annotations

import hashlib
import io
import json
import re
import sys
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from app.modules.catalog.contract import CATALOG_FORMAT, CatalogV1
from app.shared.domain.errors import ValidationFailed

_ATTRIBUTE_VALUE = re.compile(r"value=([^()]*)\(")
# Fallback when a dump has blueprint ids only (worker context): infer CARLA base_type from the id.
_BASE_TYPE_HINTS: tuple[tuple[str, str], ...] = (
    ("yamaha", "motorcycle"), ("kawasaki", "motorcycle"), ("harley", "motorcycle"), ("vespa", "motorcycle"),
    ("crossbike", "bicycle"), ("century", "bicycle"), ("omafiets", "bicycle"),
    ("european_hgv", "truck"), ("carlacola", "truck"), ("firetruck", "truck"), ("cybertruck", "truck"),
    ("fusorosa", "bus"), ("sprinter", "van"), ("t2", "van"), ("ambulance", "van"),
)


def _r(value: Any, digits: int = 2) -> float:
    return round(float(value), digits)


def short_map_name(name: str) -> str:
    return name.rsplit("/", 1)[-1]


def _attribute(attributes: dict[str, Any], key: str) -> str | None:
    item = attributes.get(key)
    if not item:
        return None
    if isinstance(item, dict):
        recommended = item.get("recommended_values") or []
        if recommended:
            return str(recommended[0])
        match = _ATTRIBUTE_VALUE.search(str(item.get("value", "")))
        return match.group(1) if match else None
    return str(item)


def _hinted_base_type(blueprint_id: str) -> str:
    return next((base for hint, base in _BASE_TYPE_HINTS if hint in blueprint_id), "car")


def _blueprints(items: list[dict[str, Any]]) -> tuple[list[dict], list[dict]]:
    vehicles, walkers = [], []
    for item in items:
        blueprint_id = item["id"]
        attributes = item.get("attributes", {})
        if blueprint_id.startswith("vehicle."):
            wheels = _attribute(attributes, "number_of_wheels")
            vehicles.append({
                "id": blueprint_id,
                "base_type": (_attribute(attributes, "base_type") or "").lower() or _hinted_base_type(blueprint_id),
                "number_of_wheels": int(wheels) if wheels and wheels.isdigit() else None,
            })
        elif blueprint_id.startswith("walker.pedestrian"):
            walkers.append({"id": blueprint_id})
    return sorted(vehicles, key=lambda b: b["id"]), sorted(walkers, key=lambda b: b["id"])


def _waypoint(raw: dict[str, Any]) -> dict[str, Any]:
    location, rotation = raw["transform"]["location"], raw["transform"]["rotation"]
    return {
        "x": _r(location["x"]), "y": _r(location["y"]), "z": _r(location["z"]), "yaw": _r(rotation["yaw"]),
        "road_id": int(raw["road_id"]), "section_id": int(raw.get("section_id", 0)), "lane_id": int(raw["lane_id"]),
        "s": _r(raw.get("s", 0.0)), "lane_width": _r(raw.get("lane_width", 3.5)),
        "is_junction": bool(raw.get("is_junction", False)), "lane_type": str(raw.get("lane_type", "Driving")),
    }


EXPORT_STATE_DIR = "02_server_actor_state"
EXPORT_REQUIRED = ("01_server.json", "03_available_maps.json", "04_blueprints.json", "06_map_summary.json", "07_waypoints.json")


def from_carla_export(read: Callable[[str], bytes | None]) -> CatalogV1:
    """Build a catalog from export files; `read(name)` returns a file of 02_server_actor_state/ or None."""
    missing = [name for name in EXPORT_REQUIRED if read(name) is None]
    if missing:
        raise ValidationFailed(f"CARLA export is missing {', '.join(missing)} (folder {EXPORT_STATE_DIR}/)")
    load = lambda name: json.loads(read(name))  # noqa: E731
    server = load("01_server.json")
    summary = load("06_map_summary.json")
    vehicles, walkers = _blueprints(load("04_blueprints.json"))
    xodr = read("05_current_map.xodr")
    weather = read("12_weather_profiles.json")
    return CatalogV1(
        carla_version=str(server.get("server_version") or server.get("client_version")),
        map_name=short_map_name(summary["name"]),
        available_maps=sorted(load("03_available_maps.json")),
        opendrive_hash=hashlib.sha256(xodr).hexdigest() if xodr else None,
        vehicles=vehicles,
        walkers=walkers,
        spawn_points=[
            {"x": _r(p["location"]["x"]), "y": _r(p["location"]["y"]), "z": _r(p["location"]["z"]), "yaw": _r(p["rotation"]["yaw"])}
            for p in summary["spawn_points"]
        ],
        waypoints=[_waypoint(item) for item in load("07_waypoints.json")],
        weather_presets=sorted(json.loads(weather)) if weather else [],
    )


def from_carla_export_dir(directory: Path) -> CatalogV1:
    state = directory / EXPORT_STATE_DIR

    def read(name: str) -> bytes | None:
        path = state / name
        return path.read_bytes() if path.is_file() else None

    return from_carla_export(read)


def from_carla_export_zip(content: bytes) -> CatalogV1:
    """The export folder zipped as-is (any nesting); only 02_server_actor_state/ files are read."""
    try:
        archive = zipfile.ZipFile(io.BytesIO(content))
    except zipfile.BadZipFile as exc:
        raise ValidationFailed("File is not a valid .zip archive") from exc
    with archive:
        members = {
            Path(info.filename).name: info
            for info in archive.infolist()
            if not info.is_dir() and Path(info.filename).parent.name == EXPORT_STATE_DIR
        }

        def read(name: str) -> bytes | None:
            info = members.get(name)
            return archive.read(info) if info is not None else None

        return from_carla_export(read)


def from_worker_context(context: dict[str, Any]) -> CatalogV1:
    static = context["static"]
    blueprint_ids = static.get("blueprints", [])
    return CatalogV1(
        carla_version=str(context.get("health", {}).get("server_version", "unknown")),
        map_name=short_map_name(static["map"]),
        available_maps=sorted(short_map_name(item) for item in static.get("available_maps", [])),
        opendrive_hash=static.get("opendrive_hash"),
        vehicles=[{"id": b, "base_type": _hinted_base_type(b)} for b in sorted(blueprint_ids) if b.startswith("vehicle.")],
        walkers=[{"id": b} for b in sorted(blueprint_ids) if b.startswith("walker.pedestrian")],
        spawn_points=[{"x": _r(p["x"]), "y": _r(p["y"]), "z": _r(p["z"]), "yaw": _r(p["yaw_deg"])} for p in static["spawn_points"]],
        waypoints=[
            {"x": _r(w["x"]), "y": _r(w["y"]), "z": _r(w["z"]), "yaw": _r(w["yaw_deg"]), "road_id": w["road_id"],
             "lane_id": w["lane_id"], "lane_width": _r(w["lane_width"]), "is_junction": bool(w["junction"]),
             "lane_type": str(w.get("lane_type", "Driving")).rsplit(".", 1)[-1]}
            for w in static["waypoints"]
        ],
    )


def from_upload(filename: str, content: bytes) -> CatalogV1:
    """Recognise what the user uploaded and turn it into a catalog.v1 document."""
    try:
        return _from_upload(filename, content)
    except ValidationFailed:
        raise
    except (KeyError, TypeError, ValueError) as exc:  # malformed export/context files
        raise ValidationFailed(f"CARLA data file is malformed ({type(exc).__name__}: {str(exc)[:200]})") from exc


def _from_upload(filename: str, content: bytes) -> CatalogV1:
    if filename.lower().endswith(".zip"):
        return from_carla_export_zip(content)
    try:
        document = json.loads(content)
    except (UnicodeDecodeError, ValueError) as exc:
        raise ValidationFailed("Upload a .zip of the CARLA export folder or a .json catalog") from exc
    if isinstance(document, dict) and document.get("format") == CATALOG_FORMAT:
        try:
            return CatalogV1.model_validate(document)
        except ValueError as exc:
            raise ValidationFailed("The catalog.v1 document is invalid", {"errors": str(exc)[:2000]}) from exc
    if isinstance(document, dict) and isinstance(document.get("static"), dict):
        return from_worker_context(document)
    if isinstance(document, dict) and "static_summary" in document:
        raise ValidationFailed("export_manifest.json only lists the export; zip the whole export folder and upload the .zip")
    raise ValidationFailed("Unrecognised file: expected a CARLA export .zip, a catalog.v1 .json or a worker context .json")


def main(argv: list[str]) -> None:
    if len(argv) != 3:
        raise SystemExit("usage: python -m app.modules.catalog.importers <export_dir> <out.json>")
    catalog = from_carla_export_dir(Path(argv[1]))
    Path(argv[2]).write_text(json.dumps(catalog.model_dump(mode="json", exclude_none=True), separators=(",", ":")), encoding="utf-8")
    print(f"{catalog.map_name}: {len(catalog.spawn_points)} spawn points, {len(catalog.waypoints)} waypoints, "
          f"{len(catalog.vehicles)} vehicles, {len(catalog.walkers)} walkers -> {argv[2]}")


if __name__ == "__main__":
    main(sys.argv)
