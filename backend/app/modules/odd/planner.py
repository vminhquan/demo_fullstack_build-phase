"""Spread N generated test cases over the declared ODD. Pure functions — no I/O.

* Discrete dimensions (road type, weather, lighting, adversary): greedy pairwise selection, so every
  feasible pair of values (e.g. rain x intersection) appears in some case before any repeats.
* Continuous ranges (ego/actor speed, initial gap, trigger distance): Latin-hypercube style strata,
  so each case gets its own slice of every range instead of N copies of the same numbers.
* Map: among the ODD's CARLA snapshots that can host the case's road type and adversary, the least used.
"""
from __future__ import annotations

from collections import Counter
from itertools import combinations, product
from math import gcd

from app.modules.odd.schemas import MAX_PLAN_CASES, MapCapability, NumberRange, OddDeclaration, OddGap, PlanForm, PlannedCase, PlanResponse
from app.shared.domain.errors import ValidationFailed

DIMENSIONS = ("road_type", "weather", "lighting", "adversary_type")


def _clip(value: NumberRange, limit: NumberRange) -> NumberRange:
    low, high = max(value.min, limit.min), min(value.max, limit.max)
    return NumberRange(min=low, max=high) if low <= high else NumberRange(min=limit.min, max=limit.max)


def _within_odd(form: PlanForm, odd: OddDeclaration | None) -> tuple[PlanForm, list[OddGap]]:
    if odd is None:
        return form, []
    outside: list[OddGap] = []
    allowed = {"road_types": odd.road_types, "weather": odd.weather, "lighting": odd.lighting, "adversary_types": odd.adversary_types}
    update: dict = {}
    for field, values in allowed.items():
        chosen = getattr(form, field)
        kept = [item for item in chosen if item in values]
        outside += [OddGap(dimension=field, value=item, reason="Ngoài ODD đã khai báo, bỏ qua") for item in chosen if item not in values]
        if not kept:
            raise ValidationFailed(f"No value of '{field}' lies inside the declared ODD")
        update[field] = kept
    for field, limit in (("ego_speed_kmh", odd.ego_speed_kmh), ("actor_speed_kmh", odd.actor_speed_kmh)):
        clipped = _clip(getattr(form, field), limit)
        if clipped != getattr(form, field):
            outside.append(OddGap(dimension=field, value=f"{getattr(form, field).min:g}–{getattr(form, field).max:g}",
                                  reason=f"Kẹp vào dải ODD {limit.min:g}–{limit.max:g}"))
        update[field] = clipped
    if form.ego_blueprint and form.ego_blueprint not in odd.ego_vehicles:
        outside.append(OddGap(dimension="ego_blueprint", value=form.ego_blueprint, reason="Xe ego ngoài ODD, dùng xe ego đầu tiên của ODD"))
        update["ego_blueprint"] = None
    if not (update.get("ego_blueprint", form.ego_blueprint)):
        update["ego_blueprint"] = odd.ego_vehicles[0]
    return form.model_copy(update=update), outside


def _hosts(capability: MapCapability, road: str, adversary: str) -> bool:
    return capability.error is None and road in capability.road_types and adversary in capability.adversary_types


def _strata(count: int, dimension: int) -> list[int]:
    """A permutation of 0..count-1, different per dimension (Latin hypercube without randomness)."""
    if count == 1:
        return [0]
    step = next(s for s in range(dimension + 1, dimension + count + 2) if gcd(s, count) == 1)
    return [(i * step + dimension) % count for i in range(count)]


def _slice(value: NumberRange, stratum: int, count: int) -> dict[str, float]:
    width = (value.max - value.min) / count
    low = value.min + stratum * width
    return {"min": round(low, 1), "max": round(low + width, 1)}


def plan_cases(form: PlanForm, count: int | None, capabilities: list[MapCapability], odd: OddDeclaration | None) -> PlanResponse:
    form, outside = _within_odd(form, odd)
    usable = [cap for cap in capabilities if cap.error is None and (odd is None or cap.snapshot_id in odd.catalog_snapshot_ids)]
    if not usable:
        raise ValidationFailed("No CARLA data with lane data is available for the ODD")

    infeasible: list[OddGap] = []
    roads = [road for road in form.road_types if any(road in cap.road_types for cap in usable)]
    infeasible += [OddGap(dimension="road_type", value=road, reason="Không map nào trong ODD dựng được loại đường này")
                   for road in form.road_types if road not in roads]
    adversaries = [adv for adv in form.adversary_types if any(adv in cap.adversary_types for cap in usable)]
    infeasible += [OddGap(dimension="adversary_type", value=adv, reason="Không có blueprint trong dữ liệu CARLA của ODD")
                   for adv in form.adversary_types if adv not in adversaries]
    feasible_pairs = {(road, adv) for road in roads for adv in adversaries if any(_hosts(cap, road, adv) for cap in usable)}
    infeasible += [OddGap(dimension="road_type × adversary_type", value=f"{road} × {adv}", reason="Không map nào có cả hai")
                   for road in roads for adv in adversaries if (road, adv) not in feasible_pairs]
    if not feasible_pairs:
        raise ValidationFailed("No map in the ODD can host the selected road types with the selected adversaries")

    values = {"road_type": roads, "weather": form.weather, "lighting": form.lighting, "adversary_type": adversaries}
    candidates = [dict(zip(DIMENSIONS, combo)) for combo in product(*(values[d] for d in DIMENSIONS))
                  if (combo[0], combo[3]) in feasible_pairs]

    def pairs_of(cell: dict[str, str]) -> set[tuple]:
        return {((a, cell[a]), (b, cell[b])) for a, b in combinations(DIMENSIONS, 2)}

    universe = set().union(*(pairs_of(cell) for cell in candidates))
    covered: set[tuple] = set()
    used: Counter = Counter()
    chosen: list[dict[str, str]] = []
    auto = count is None
    while len(chosen) < (count or MAX_PLAN_CASES) and not (auto and chosen and covered >= universe):
        def score(cell: dict[str, str]) -> tuple:
            fresh = len(pairs_of(cell) - covered)
            repeats = sum(used[(d, cell[d])] for d in DIMENSIONS)
            return (-fresh, chosen.count(cell), repeats)

        best = min(candidates, key=score)
        chosen.append(best)
        covered |= pairs_of(best)
        used.update((d, best[d]) for d in DIMENSIONS)

    count = len(chosen)
    ranges = ("ego_speed_kmh", "actor_speed_kmh", "initial_gap_m", "trigger_distance_m")
    strata = {name: _strata(count, index) for index, name in enumerate(ranges)}
    # A far start goes with a far trigger, so the trigger never lands beyond the actor's start.
    strata["trigger_distance_m"] = strata["initial_gap_m"]
    map_use: Counter = Counter()
    cases: list[PlannedCase] = []
    for index, cell in enumerate(chosen):
        hosts = [cap for cap in usable if _hosts(cap, cell["road_type"], cell["adversary_type"])]
        cap = min(hosts, key=lambda item: (map_use[item.snapshot_id], item.snapshot_id))
        map_use[cap.snapshot_id] += 1
        spec = {
            "goal": form.goal,
            "scenario_type": form.scenario_type,
            "adversary_type": cell["adversary_type"],
            "direction": form.direction,
            "road_type": cell["road_type"],
            "weather": [cell["weather"]],
            "lighting": cell["lighting"],
            "objective": form.objective,
            "ego_blueprint": form.ego_blueprint if form.ego_blueprint in cap.ego_vehicles else None,
            **{name: _slice(getattr(form, name), strata[name][index], count) for name in ranges},
        }
        cases.append(PlannedCase(index=index, catalog_snapshot_id=cap.snapshot_id, map_name=cap.map_name, cell=cell, spec=spec))

    coverage = {d: {value: sum(1 for cell in chosen if cell[d] == value) for value in values[d]} for d in DIMENSIONS}
    missing = sorted(universe - covered)
    return PlanResponse(
        auto=auto,
        cases=cases,
        coverage=coverage,
        pairs_total=len(universe),
        pairs_covered=len(universe & covered),
        uncovered_pairs=[f"{a[1]} × {b[1]}" for a, b in missing],
        infeasible=infeasible,
        outside_odd=outside,
    )
