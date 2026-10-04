from app.modules.report.service import build_report, coverage, is_valid, odd_cell


def ir(road="intersection_4way", weather="rain", hour=14, actor="motorcycle"):
    return {"ego": {"road_type": road, "initial_speed_kmh": 40}, "weather": weather, "time_of_day_hour": hour,
            "actors": [{"actor_type": actor, "trigger": "cut_in"}], "expected_outcome": {"expected_verdict": "NEAR_MISS"}}


def result(valid=True, schema=True, threat=0.5, **kwargs):
    return {"validation": {"is_valid": valid}, "xosc_validation": {"schema_valid": schema}, "grounding": {"fully_on_lanes": True},
            "threat_score": {"weighted_threat": threat}, "scenario_ir": ir(**kwargs)}


def call(kind="GENERATE", status="COMPLETED", error=None, tokens=(9000, 500), cache=False):
    return {"kind": kind, "status": status, "error_code": error, "from_form": False, "cache_hit": cache,
            "input_tokens": 0 if cache else tokens[0], "output_tokens": 0 if cache else tokens[1], "duration_ms": 0 if cache else 12000, "day": "2026-10-04"}


def test_valid_needs_guardrail_and_schema() -> None:
    assert is_valid(result()) and not is_valid(result(valid=False)) and not is_valid(result(schema=False))


def test_odd_cell_reads_lighting_from_hour_and_preset() -> None:
    assert odd_cell(ir(weather="night")) == {"road_type": "intersection", "weather": "clear", "lighting": "night", "adversary_type": "motorcycle"}
    assert odd_cell(ir(road="urban_straight", hour=18, actor="bicycle"))["lighting"] == "dusk"
    assert odd_cell(ir(actor="bicycle"))["adversary_type"] == "cyclist"


def test_coverage_against_declared_odd() -> None:
    declared = {"road_type": ["intersection", "straight"], "weather": ["rain"], "lighting": ["day"], "adversary_type": ["motorcycle"]}
    report = coverage([odd_cell(ir())], declared)
    assert report["values_covered"] == 4 and report["values_total"] == 5
    assert report["pairs_covered"] == 6 and report["pairs_total"] == 9
    assert "straight × rain" in report["uncovered_pairs"]


def test_report_rates_failures_and_cost() -> None:
    calls = [call(), call(), call(cache=True), call(status="REJECTED", error="GUARDRAIL_FAILED"), call(status="FAILED", error="AGENT_UNAVAILABLE"),
             call(kind="REFINE", tokens=(1000, 200))]
    generations = [{"result": result(), "generation_mode": "llm", "day": "2026-10-04"} for _ in range(2)]
    generations.append({"result": result(schema=False), "generation_mode": "llm", "day": "2026-10-04"})
    versions = [{"status": "APPROVED", "scenario_ir": ir()}, {"status": "REJECTED", "scenario_ir": ir()}, {"status": "DRAFT", "scenario_ir": ir()}]
    report = build_report(calls, generations, versions, None, 0.15, 0.60)
    assert report["attempts"] == 5 and report["completed"] == 3 and report["valid"] == 2
    assert report["valid_rate"] == 0.4 and report["failures"] == {"GUARDRAIL_FAILED": 1, "AGENT_UNAVAILABLE": 1}
    assert report["invalid_reasons"] == {"XSD_INVALID": 1}
    assert report["review"]["approval_rate"] == 0.5
    cost = report["cost"]
    assert cost["input_tokens"] == 9000 * 4 + 1000 and cost["cache_hits"] == 1 and cost["tokens_saved_by_cache"] == 9500
    assert cost["usd"] == round((37000 * 0.15 + (500 * 4 + 200) * 0.60) / 1e6, 4)
    assert report["daily"] == [{"day": "2026-10-04", "attempts": 5, "completed": 3, "valid": 2}]
