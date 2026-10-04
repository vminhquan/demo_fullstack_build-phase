"""Install the shipped default CARLA data (app/modules/catalog/default_data/*.json) as DEFAULT snapshots.

Idempotent: a file whose content is already stored is skipped. Add more maps by dropping catalog.v1
files into default_data/ (convert a raw export with `python -m app.modules.catalog.importers`).
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from app.modules.catalog.service import ingest_snapshot, parse_catalog
from app.shared.infrastructure.db import SessionFactory
from app.shared.infrastructure.models import CatalogSource

DEFAULT_DATA_DIR = Path(__file__).resolve().parent / "modules" / "catalog" / "default_data"


async def seed_default_catalogs() -> None:
    files = sorted(DEFAULT_DATA_DIR.glob("*.json"))
    if not files:
        print(f"No default catalog files in {DEFAULT_DATA_DIR}")
        return
    async with SessionFactory() as session:
        for path in files:
            catalog = parse_catalog(json.loads(path.read_text(encoding="utf-8")))
            snapshot, created = await ingest_snapshot(session, catalog, source=CatalogSource.DEFAULT, project_id=None, label=path.stem)
            print(f"{'installed' if created else 'unchanged'}: {path.name} -> snapshot {snapshot.id} ({catalog.map_name}, CARLA {catalog.carla_version})")
        await session.commit()


if __name__ == "__main__":
    asyncio.run(seed_default_catalogs())
