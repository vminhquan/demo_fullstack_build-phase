"""Judge simulator runs by the ego collision criterion, including reports from older Bridges."""

from __future__ import annotations

import math
from typing import Any

from app.shared.infrastructure.models import RunVerdict


def collision_count(metrics: dict[str, Any]) -> float | None:
    tests = [item for item in (metrics.get("criteria") or []) if isinstance(item, dict)
             and item.get("name") == "CollisionTest"]
    if not tests:
        return None
    try:
        counts = [float(item["actual"]) for item in tests]
    except (KeyError, TypeError, ValueError):
        return None
    if any(not math.isfinite(count) or count < 0 for count in counts):
        return None
    return sum(counts)


def collision_verdict(verdict: RunVerdict, metrics: dict[str, Any]) -> RunVerdict:
    """Keep execution errors; otherwise CollisionTest determines Pass/Fail when present."""
    if verdict is RunVerdict.ERROR:
        return verdict
    collisions = collision_count(metrics)
    if collisions is None:
        return verdict
    return RunVerdict.FAIL if collisions > 0 else RunVerdict.PASS
