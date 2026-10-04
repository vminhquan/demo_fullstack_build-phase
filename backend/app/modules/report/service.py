"""Generation report: valid-scenario rate, failure reasons, review outcome, danger, ODD coverage, LLM cost.

Pure functions over plain dicts so they are unit-testable; the router does the queries.
"""
from __future__ import annotations

from collections import Counter
from itertools import combinations
from statistics import median
from typing import Any

from app.modules.generation.mapping import adversary_type, danger_level
from app.modules.odd.service import ROAD_CATEGORIES

COVERAGE_DIMENSIONS = ("road_type", "weather", "lighting", "adversary_type")


def is_valid(result: dict[str, Any]) -> bool:
    """A usable scenario: passes the safety guardrails and the OpenSCENARIO 1.0 schema."""
    return bool((result.get("validation") or {}).get("is_valid")) and bool((result.get("xosc_validation") or {}).get("schema_valid"))


def odd_cell(ir: dict[str, Any]) -> dict[str, str]:
    weather = str(ir.get("weather", "clear"))
    hour = int(ir.get("time_of_day_hour", 12))
    lighting = "night" if weather == "night" or hour >= 19 or hour < 6 else "dusk" if weather == "dusk" or hour >= 17 else "day"
    return {
        "road_type": ROAD_CATEGORIES.get(str((ir.get("ego") or {}).get("road_type", "")), "other"),
        "weather": "clear" if weather in ("night", "dusk") else weather,
        "lighting": lighting,
        "adversary_type": adversary_type(ir),
    }


def coverage(cells: list[dict[str, str]], declared: dict[str, list[str]] | None) -> dict[str, Any]:
    values = {d: list(declared.get(d, [])) if declared else sorted({cell[d] for cell in cells}) for d in COVERAGE_DIMENSIONS}
    counts = {d: {value: sum(1 for cell in cells if cell[d] == value) for value in values[d]} for d in COVERAGE_DIMENSIONS}
    outside = sorted({f"{d}={cell[d]}" for cell in cells for d in COVERAGE_DIMENSIONS if cell[d] not in values[d]})
    universe = {((a, x), (b, y)) for a, b in combinations(COVERAGE_DIMENSIONS, 2) for x in values[a] for y in values[b]}
    seen = {((a, cell[a]), (b, cell[b])) for cell in cells for a, b in combinations(COVERAGE_DIMENSIONS, 2)}
    covered = universe & seen
    return {
        "source": "odd" if declared else "observed",
        "values": counts,
        "values_total": sum(len(v) for v in values.values()),
        "values_covered": sum(1 for d in COVERAGE_DIMENSIONS for n in counts[d].values() if n),
        "pairs_total": len(universe),
        "pairs_covered": len(covered),
        "uncovered_pairs": [f"{a[1]} × {b[1]}" for a, b in sorted(universe - covered)][:50],
        "outside_odd": outside,
    }


def percentile(values: list[int], q: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(round(q * (len(ordered) - 1))))]


def build_report(
    calls: list[dict[str, Any]],
    generations: list[dict[str, Any]],
    versions: list[dict[str, Any]],
    declared: dict[str, list[str]] | None,
    price_in: float,
    price_out: float,
) -> dict[str, Any]:
    generate_calls = [c for c in calls if c["kind"] == "GENERATE"]
    refine_calls = [c for c in calls if c["kind"] == "REFINE"]
    completed = [g for g in generations]
    valid = [g for g in completed if is_valid(g["result"])]
    on_lanes = [g for g in completed if (g["result"].get("grounding") or {}).get("fully_on_lanes")]
    attempts = len(generate_calls)

    failures = Counter(c["error_code"] or c["status"] for c in generate_calls if c["status"] != "COMPLETED")
    invalid_reasons = Counter()
    for g in completed:
        if not (g["result"].get("validation") or {}).get("is_valid"):
            invalid_reasons["GUARDRAIL_INVALID"] += 1
        if not (g["result"].get("xosc_validation") or {}).get("schema_valid"):
            invalid_reasons["XSD_INVALID"] += 1

    review = Counter(v["status"] for v in versions)
    decided = review.get("APPROVED", 0) + review.get("REJECTED", 0)

    danger = Counter(danger_level(g["result"].get("threat_score") or {}, g["result"].get("scenario_ir") or {}).value for g in completed)
    verdicts = Counter(((g["result"].get("scenario_ir") or {}).get("expected_outcome") or {}).get("expected_verdict") or "—" for g in completed)
    threats = [float((g["result"].get("threat_score") or {}).get("weighted_threat", 0.0)) for g in completed]

    tokens_in = sum(c["input_tokens"] for c in calls)
    tokens_out = sum(c["output_tokens"] for c in calls)
    cost = tokens_in / 1e6 * price_in + tokens_out / 1e6 * price_out
    paid = [c for c in generate_calls if c["status"] == "COMPLETED" and not c["cache_hit"]]
    avg_generate_tokens = (sum(c["input_tokens"] + c["output_tokens"] for c in paid) / len(paid)) if paid else 0
    cache_hits = sum(1 for c in generate_calls if c["cache_hit"])
    durations = [c["duration_ms"] for c in paid]

    days: dict[str, Counter] = {}
    for c in generate_calls:
        days.setdefault(c["day"], Counter())["attempts"] += 1
    for g in completed:
        days.setdefault(g["day"], Counter())["completed"] += 1
        if is_valid(g["result"]):
            days[g["day"]]["valid"] += 1

    rate = lambda n, d: round(n / d, 4) if d else None  # noqa: E731
    return {
        "attempts": attempts,
        "completed": len(completed),
        "valid": len(valid),
        "fully_on_lanes": len(on_lanes),
        "valid_rate": rate(len(valid), attempts),
        "valid_rate_of_completed": rate(len(valid), len(completed)),
        "completion_rate": rate(len(completed), attempts),
        "failures": dict(failures.most_common()),
        "invalid_reasons": dict(invalid_reasons),
        "by_mode": dict(Counter(g["generation_mode"] for g in completed)),
        "from_form": sum(1 for c in generate_calls if c["from_form"]),
        "review": {
            "saved": len(versions),
            "statuses": dict(review),
            "approval_rate": rate(review.get("APPROVED", 0), decided),
        },
        "danger": {
            "levels": dict(danger),
            "expected_verdicts": dict(verdicts),
            "mean_threat": round(sum(threats) / len(threats), 3) if threats else None,
            "median_threat": round(median(threats), 3) if threats else None,
        },
        "coverage": coverage([odd_cell(v["scenario_ir"]) for v in versions if v.get("scenario_ir")], declared),
        "cost": {
            "refine_calls": len(refine_calls),
            "input_tokens": tokens_in,
            "output_tokens": tokens_out,
            "usd": round(cost, 4),
            "usd_per_valid": round(cost / len(valid), 4) if valid else None,
            "tokens_per_generation": round(avg_generate_tokens),
            "cache_hits": cache_hits,
            "tokens_saved_by_cache": round(avg_generate_tokens * cache_hits),
            "latency_p50_ms": percentile(durations, 0.5),
            "latency_p95_ms": percentile(durations, 0.95),
            "price_input_per_mtok": price_in,
            "price_output_per_mtok": price_out,
        },
        "daily": [{"day": day, **{k: counts.get(k, 0) for k in ("attempts", "completed", "valid")}} for day, counts in sorted(days.items())],
    }
