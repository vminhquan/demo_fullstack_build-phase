"""Scenario-generation benchmark.

    cd agent
    .venv/bin/python -m benchmark.run                       # in-process Agent, LLM if OPENAI_API_KEY is set
    .venv/bin/python -m benchmark.run --offline             # deterministic rules only, no OpenAI cost
    .venv/bin/python -m benchmark.run --url http://localhost:8100 --api-key $AGENT_API_KEY
    .venv/bin/python -m benchmark.run --repeat 3 --tags cut_in,jaywalking --concurrency 3

Writes benchmark/results/<timestamp>/{results.jsonl,summary.json,report.md}. See benchmark/README.md.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import statistics
import sys
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from benchmark.scoring import score_case

AGENT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_CATALOG = AGENT_DIR.parent / "backend" / "app" / "modules" / "catalog" / "default_data" / "Town10HD_Opt.json"
DEFAULT_CASES = Path(__file__).resolve().parent / "cases.json"


def load_env_file(path: Path) -> None:
    """Pick up OPENAI_API_KEY/MODEL_NAME from the repo .env without overriding the shell."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            if key.strip() in {"OPENAI_API_KEY", "MODEL_NAME", "OPENAI_BASE_URL"} and not os.environ.get(key.strip()):
                os.environ[key.strip()] = value.strip().strip("'\"")


def make_caller(args: argparse.Namespace) -> tuple[Callable[[dict], tuple[int, dict]], Callable[[], None], dict]:
    if args.url:
        import httpx

        client = httpx.Client(timeout=args.timeout)
        headers = {"X-API-Key": args.api_key} if args.api_key else {}
        info = client.get(f"{args.url.rstrip('/')}/health").json()

        def call(payload: dict) -> tuple[int, dict]:
            response = client.post(f"{args.url.rstrip('/')}/v1/scenarios/generate", json=payload, headers=headers)
            return response.status_code, response.json()

        return call, client.close, info

    os.environ["AGENT_API_KEY"] = ""  # in-process: no auth between benchmark and app
    from fastapi.testclient import TestClient

    from app.config import get_settings
    from app.main import app

    get_settings.cache_clear()
    client = TestClient(app)
    client.__enter__()  # run lifespan (RAG seeding) once
    info = client.get("/health").json()

    def call(payload: dict) -> tuple[int, dict]:
        response = client.post("/v1/scenarios/generate", json=payload)
        return response.status_code, response.json()

    return call, lambda: client.__exit__(None, None, None), info


def percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round(q * (len(ordered) - 1))))
    return ordered[index]


def summarize(results: list[dict[str, Any]]) -> dict[str, Any]:
    scenario = [r for r in results if r["kind"] == "scenario"]
    answered = [r for r in scenario if r["status"] == 200]
    negative = [r for r in results if r["kind"] == "negative"]
    latencies = [r["latency_s"] for r in results]
    rate = lambda items, key: round(sum(1 for r in items if r["technical"][key]) / len(items), 3) if items else None  # noqa: E731
    by_tag: dict[str, list[dict]] = defaultdict(list)
    for r in results:
        for tag in r["tags"]:
            by_tag[tag].append(r)
    errors: dict[str, int] = defaultdict(int)
    for r in scenario:
        if r["status"] != 200:
            errors[r.get("error_code") or str(r["status"])] += 1
    return {
        "runs": len(results),
        "pass_rate": round(sum(r["passed"] for r in results) / len(results), 3) if results else 0,
        "scenario_runs": len(scenario),
        "generation_success_rate": round(len(answered) / len(scenario), 3) if scenario else None,
        "guardrail_valid_rate": rate(answered, "guardrail_valid"),
        "xsd_valid_rate": rate(answered, "xsd_valid"),
        "on_lanes_rate": rate(answered, "on_lanes"),
        "placement_ok_rate": rate(answered, "placement_ok"),
        "mean_fidelity": round(statistics.mean(r["fidelity_score"] for r in answered), 3) if answered else None,
        "exact_fidelity_rate": round(sum(r["fidelity_score"] == 1.0 for r in answered) / len(answered), 3) if answered else None,
        "extra_actor_rate": round(sum(any(f.startswith("no_extra_actors") for f in r["fidelity_failures"]) for r in answered) / len(answered), 3) if answered else None,
        "negative_pass_rate": round(sum(r["passed"] for r in negative) / len(negative), 3) if negative else None,
        "latency_s": {"p50": percentile(latencies, 0.5), "p95": percentile(latencies, 0.95), "max": max(latencies, default=0.0), "mean": round(statistics.mean(latencies), 2) if latencies else 0.0},
        "generation_modes": sorted({r.get("generation_mode") for r in answered if r.get("generation_mode")}),
        "errors": dict(errors),
        "by_tag": {
            tag: {
                "runs": len(items),
                "pass_rate": round(sum(r["passed"] for r in items) / len(items), 3),
                "mean_fidelity": round(statistics.mean(r["fidelity_score"] for r in items if "fidelity_score" in r), 3) if any("fidelity_score" in r for r in items) else None,
            }
            for tag, items in sorted(by_tag.items())
        },
    }


def pct(value: float | None) -> str:
    return "—" if value is None else f"{value * 100:.0f}%"


def write_report(path: Path, meta: dict[str, Any], summary: dict[str, Any], results: list[dict[str, Any]]) -> None:
    lines = [
        f"# Scenario generation benchmark — {meta['started_at']}",
        "",
        f"- Target: `{meta['target']}` · mode: {', '.join(summary['generation_modes']) or '—'} · model: {meta['health'].get('model') or '—'}",
        f"- Catalog: `{meta['catalog']}` (map {meta['map_name']}, CARLA {meta['carla_version']}, hash {meta['catalog_hash'][:12]})",
        f"- Cases: {meta['cases']} × repeat {meta['repeat']} = {summary['runs']} runs",
        "",
        "## Tổng quan",
        "",
        "| Chỉ số | Giá trị |",
        "|---|---|",
        f"| **Pass (kỹ thuật + bám prompt 100%)** | **{pct(summary['pass_rate'])}** |",
        f"| Sinh thành công (HTTP 200) | {pct(summary['generation_success_rate'])} |",
        f"| Guardrail SOTIF hợp lệ | {pct(summary['guardrail_valid_rate'])} |",
        f"| XOSC hợp lệ XSD | {pct(summary['xsd_valid_rate'])} |",
        f"| Mọi actor trên làn thật/mép đường | {pct(summary['on_lanes_rate'])} |",
        f"| Vị trí khớp IR (phía, hướng, khoảng cách) | {pct(summary['placement_ok_rate'])} |",
        f"| Điểm bám prompt trung bình | {pct(summary['mean_fidelity'])} |",
        f"| Bám prompt 100% | {pct(summary['exact_fidelity_rate'])} |",
        f"| Có actor ngoài yêu cầu | {pct(summary['extra_actor_rate'])} |",
        f"| Ca phủ định đúng mã lỗi | {pct(summary['negative_pass_rate'])} |",
        f"| Độ trễ p50 / p95 / max | {summary['latency_s']['p50']}s / {summary['latency_s']['p95']}s / {summary['latency_s']['max']}s |",
        "",
        "## Theo tag",
        "",
        "| Tag | Runs | Pass | Bám prompt TB |",
        "|---|---|---|---|",
        *[f"| {tag} | {item['runs']} | {pct(item['pass_rate'])} | {pct(item['mean_fidelity'])} |" for tag, item in summary["by_tag"].items()],
        "",
        "## Từng lượt chạy",
        "",
        "| Case | Lần | Kết quả | Bám prompt | Kỹ thuật lỗi | Ghi chú | Thời gian |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in results:
        tech = ", ".join(k for k, v in (r.get("technical") or {}).items() if not v) or "—"
        notes = "; ".join(r.get("fidelity_failures") or []) or r.get("detail") or ""
        bad = [p for p in r.get("placements", []) if not p["ok"]]
        if bad:
            notes += ("; " if notes else "") + "; ".join(f"{p['entity']} {p['position']}: along {p['along']} lat {p['lateral']} dyaw {p['dyaw']}" for p in bad)
        fidelity = pct(r.get("fidelity_score")) if "fidelity_score" in r else "—"
        lines.append(f"| `{r['id']}` | {r['repeat']} | {'✅' if r['passed'] else '❌'} | {fidelity} | {tech} | {notes.replace('|', '/')} | {r['latency_s']}s |")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Benchmark the scenario-generation Agent")
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG, help="catalog.v1 JSON the scenarios are grounded on")
    parser.add_argument("--url", help="running Agent base URL; omit to run the Agent in-process")
    parser.add_argument("--api-key", default=os.environ.get("AGENT_API_KEY", ""))
    parser.add_argument("--offline", action="store_true", help="deterministic generator only (no OpenAI calls)")
    parser.add_argument("--repeat", type=int, default=1, help="runs per case, to measure stability")
    parser.add_argument("--concurrency", type=int, default=1)
    parser.add_argument("--tags", help="comma-separated: run cases having any of these tags")
    parser.add_argument("--ids", help="comma-separated case ids")
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--timeout", type=float, default=180.0)
    parser.add_argument("--out", type=Path, default=Path(__file__).resolve().parent / "results")
    parser.add_argument("--min-pass-rate", type=float, default=None, help="exit 1 when pass_rate is below this (0..1), for CI")
    args = parser.parse_args(argv)

    load_env_file(AGENT_DIR.parent / ".env")
    cases = json.loads(args.cases.read_text(encoding="utf-8"))["cases"]
    if args.tags:
        wanted = {t.strip() for t in args.tags.split(",")}
        cases = [c for c in cases if wanted & set(c.get("tags", []))]
    if args.ids:
        wanted_ids = {i.strip() for i in args.ids.split(",")}
        cases = [c for c in cases if c["id"] in wanted_ids]
    if not cases:
        print("No cases selected", file=sys.stderr)
        return 2

    catalog = json.loads(args.catalog.read_text(encoding="utf-8"))
    canonical = {k: v for k, v in catalog.items() if k != "content_hash"}
    catalog["content_hash"] = hashlib.sha256(json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

    call, close, health = make_caller(args)
    jobs = [(case, n) for n in range(1, args.repeat + 1) for case in cases]
    print(f"Agent {'at ' + args.url if args.url else 'in-process'} · llm_enabled={health.get('llm_enabled')} model={health.get('model')} · "
          f"{len(cases)} cases × {args.repeat} = {len(jobs)} runs · offline={args.offline}\n")

    def run(job: tuple[dict, int]) -> dict[str, Any]:
        case, repeat = job
        payload = {"prompt": case["prompt"], "catalog": catalog, "offline_mode": args.offline, "auto_repair": True, "seed": args.seed}
        started = time.perf_counter()
        try:
            status, body = call(payload)
        except Exception as exc:  # network/timeout: record, keep benchmarking
            status, body = 0, {"error": {"code": type(exc).__name__, "message": str(exc)[:300]}}
        result = score_case(case, status, body, time.perf_counter() - started)
        result["repeat"] = repeat
        mark = "PASS" if result["passed"] else "FAIL"
        extra = f"fidelity {result['fidelity_score']:.0%}" if "fidelity_score" in result else (result.get("detail") or "")
        print(f"[{mark}] {case['id']:<40} #{repeat} {result['latency_s']:>5.1f}s  {extra}", flush=True)
        return result

    started_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        with ThreadPoolExecutor(max_workers=max(1, args.concurrency)) as pool:
            results = list(pool.map(run, jobs))
    finally:
        close()

    summary = summarize(results)
    out = args.out / datetime.now().strftime("%Y%m%d-%H%M%S")
    out.mkdir(parents=True, exist_ok=True)
    meta = {
        "started_at": started_at,
        "target": args.url or "in-process",
        "health": health,
        "catalog": str(args.catalog),
        "catalog_hash": catalog["content_hash"],
        "map_name": catalog.get("map_name"),
        "carla_version": catalog.get("carla_version"),
        "cases": len(cases),
        "repeat": args.repeat,
        "offline": args.offline,
    }
    with (out / "results.jsonl").open("w", encoding="utf-8") as handle:
        for item in results:
            handle.write(json.dumps(item, ensure_ascii=False) + "\n")
    (out / "summary.json").write_text(json.dumps({"meta": meta, "summary": summary}, ensure_ascii=False, indent=2), encoding="utf-8")
    write_report(out / "report.md", meta, summary, results)

    print(f"\nPass {pct(summary['pass_rate'])} · sinh OK {pct(summary['generation_success_rate'])} · XSD {pct(summary['xsd_valid_rate'])} · "
          f"vị trí {pct(summary['placement_ok_rate'])} · bám prompt TB {pct(summary['mean_fidelity'])} · actor thừa {pct(summary['extra_actor_rate'])} · "
          f"p50 {summary['latency_s']['p50']}s p95 {summary['latency_s']['p95']}s")
    print(f"Báo cáo: {out / 'report.md'}")
    if args.min_pass_rate is not None and summary["pass_rate"] < args.min_pass_rate:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
