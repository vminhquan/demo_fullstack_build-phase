"""LangGraph workflow for one catalog-grounded test scenario composed by the LLM.

    START → prefetch             complete inventories (no LLM, no pick): map summary, vehicles, weather presets
              ↓
            llm ⇄ tools          the LLM searches sites and reads site details, several tools per turn if it wants
              ↓ submit_scenario
           build_xosc → llm      simulation checks fail: the issues and numbers go back to the LLM; then the
              ↓ valid             project's Bridge runs the XOSC on CARLA with a no-brain ego, which must collide
            save → END           the XOSC is stored through the Backend

Only the `llm` node calls the model and makes choices. `prefetch` and `tools` are plain Backend reads; `build_xosc`
and `save` are deterministic.
The Backend's Builder runs this graph once per variant. Catalogs and clients live in runtime context, outside state.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Protocol, cast

from app.bridges import CarlaCheck
from app.contracts import GeneratedScenario, GenerationRequest, GraphContext, GraphState, MapFailure
from app.llm.client import LLMCallError
from app.logs import log, short
from app.llm.prompts import initial_messages
from app.llm.tools import REJECT_TOOL, SUBMIT_TOOL, CatalogApi, CatalogTools, ToolError, reject_problem, tool_result
from app.scenario.check import map_cannot_host, tune
from app.scenario.explore import capabilities
from app.scenario.lanes import lane_graph
from app.scenario.render import render_xosc
from app.scenario.spec import ScenarioSpec, ValidationResult
from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime
from pydantic import ValidationError

# LLM turns per run (reads + submissions); the last one must submit. In the measured 10-variant run one
# variant spent 17 turns repeating invalid site reads. The observed valid variant needed seven turns.
MAX_LLM_TURNS = 10


class ChatLLM(Protocol):
    model_name: str

    def chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]], tool_choice: Any = "required") -> dict[str, Any]: ...


def initial_state(request: GenerationRequest, seed: int) -> GraphState:
    return {
        "session_id": request.session_id, "seed": seed, "messages": initial_messages(request, seed),
        "llm_turns": 0, "usage": {"input_tokens": 0, "cached_input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
        "submit_attempts": 0, "pending_submit": None, "scenario": None, "map_failures": [],
    }


def _failed(runtime: Runtime[GraphContext], code: str, message: str) -> dict[str, Any]:
    log(runtime.context["request"].session_id, "✗ FAILED %s: %s", code, message, level=logging.WARNING)
    return {"status": "failed", "map_failures": [
        MapFailure(snapshot=item.ref, code=code, message=message) for item in runtime.context["selected_snapshots"]
    ]}


def _calls(state: GraphState) -> list[dict[str, Any]]:
    return state["messages"][-1].get("tool_calls") or []


def prefetch(state: GraphState, runtime: Runtime[GraphContext]) -> dict[str, Any]:
    """Put the complete, prompt-independent inventories of each map in the first message.

    Whole lists in a neutral order only (no subset, no seed shuffle, no suggestion): this node cannot read the prompt,
    so every choice among them stays with the LLM. Sites are not here: which ones fit depends on the prompt.
    """
    api, request, session = runtime.context["catalog_api"], runtime.context["request"], state["session_id"]
    if (request.actor_type or "").lower() in ("pedestrian", "walker"):
        return _failed(runtime, "UNSUPPORTED_ACTOR", "Chưa dựng được kịch bản người đi bộ (mới hỗ trợ phương tiện trên làn xe).")
    # The lane graph already came with the request. Reject an impossible location before paying for a model turn
    # (and before loading inventories); the Builder can route this variant to another selected map.
    if unfit := map_cannot_host(request.prompt, runtime.context["lane_graphs"]):
        return _failed(runtime, "LOCATION_NOT_AVAILABLE", f'"{request.prompt}": {unfit}')
    refs = [item.ref for item in runtime.context["selected_snapshots"]]
    jobs = {(ref.map_name, key): job for ref in refs for key, job in (
        ("summary", lambda ref=ref: api.summary(ref.snapshot_id)),
        ("vehicles", lambda ref=ref: api.vehicles(ref.snapshot_id, "all")),
        ("weather_presets", lambda ref=ref: api.weather_presets(ref.snapshot_id)),
    )}
    log(session, "── [node prefetch] đọc %d danh mục đầy đủ (không LLM, không chọn hộ)", len(jobs))
    started = time.perf_counter()
    try:
        with ThreadPoolExecutor(max_workers=len(jobs)) as pool:
            results = dict(zip(jobs, pool.map(lambda job: job(), jobs.values()), strict=True))
    except ToolError as exc:
        return _failed(runtime, "CATALOG_UNAVAILABLE", str(exc))
    catalog: dict[str, dict[str, Any]] = {}
    for (map_name, key), value in results.items():
        entry = catalog.setdefault(map_name, {})
        if key == "summary":
            entry["summary"] = {name: value.get(name) for name in ("total_sites", "tags", "speed_limits_kmh", "max_length_m")}
        elif key == "weather_presets":
            entry["weather_presets"] = value.get("weather_presets", [])
        else:
            vehicles = value.get("vehicles", [])
            entry["vehicles"] = vehicles
            # A locked ego is a fact, not a list to choose from.
            entry["ego"] = ([item for item in vehicles if item["id"] == request.ego_blueprint_id] if request.ego_blueprint_id
                            else [item for item in vehicles if item.get("kind") == "car"])
    for map_name, entry in catalog.items():
        # Facts about the whole map (from its lane graph), so the model knows early what it cannot build there.
        entry["can_host"] = capabilities(runtime.context["lane_graphs"][map_name])
        log(session, "   %s · %.0f ms: %s", map_name, (time.perf_counter() - started) * 1000, short(entry))
    user = {**json.loads(state["messages"][1]["content"]), "catalog": catalog}
    return {"messages": [state["messages"][0], {"role": "user", "content": json.dumps(user, ensure_ascii=False)}]}


def after_prefetch(state: GraphState) -> str:
    return END if state.get("status") else "llm"


def llm(state: GraphState, runtime: Runtime[GraphContext]) -> dict[str, Any]:
    turn = state["llm_turns"] + 1
    last = turn >= MAX_LLM_TURNS
    choice = {"type": "function", "function": {"name": SUBMIT_TOOL}} if last else "required"
    session = state["session_id"]
    chat_llm = cast(ChatLLM, runtime.context["llm"])
    log(session, "── [node llm] lượt %d/%d · gửi %d message (%d ký tự)%s", turn, MAX_LLM_TURNS, len(state["messages"]),
        sum(len(str(item.get("content") or "")) for item in state["messages"]), " · BẮT BUỘC submit" if last else "")
    started = time.perf_counter()
    try:
        message = chat_llm.chat(state["messages"], CatalogTools.specs(), choice)
    except LLMCallError as exc:
        return {"llm_turns": turn, **_failed(runtime, exc.code, str(exc))}
    calls = message["tool_calls"]
    last_usage = getattr(chat_llm, "last_usage", None) or {}
    usage = {"input_tokens": state["usage"]["input_tokens"] + int(last_usage.get("prompt_tokens", 0)),
             "cached_input_tokens": state["usage"]["cached_input_tokens"] + int(last_usage.get("cached_prompt_tokens", 0)),
             "output_tokens": state["usage"]["output_tokens"] + int(last_usage.get("completion_tokens", 0))}
    usage["total_tokens"] = usage["input_tokens"] + usage["output_tokens"]
    log(session, "   LLM trả lời sau %.1f s · token %s · gọi %d tool", time.perf_counter() - started,
        short(getattr(chat_llm, "last_usage", None)), len(calls))
    if message.get("content"):
        log(session, "   LLM ghi chú: %s", short(message["content"]))
    for call in calls:
        log(session, "   → %s(%s)", call["function"]["name"], short(call["function"]["arguments"]))
    rejected = next((call for call in calls if call["function"]["name"] == REJECT_TOOL), None)
    if rejected is not None and (args := _reject_args(rejected)) is not None:
        quote, prompt = args.get("quote", ""), runtime.context["request"].prompt
        # The map itself lacks what the prompt needs (checked on the lane graph): the Builder moves to another map.
        unfit = map_cannot_host(quote, runtime.context["lane_graphs"]) if _words_in(quote, prompt) else None
        if unfit is not None:
            return {"llm_turns": turn, "usage": usage, "messages": [*state["messages"], message],
                    **_failed(runtime, "LOCATION_NOT_AVAILABLE", f'"{quote.strip()}": {unfit}')}
        if reject_problem(quote, prompt) is None:
            reason = f'"{quote.strip()}": {args.get("reason", "")}'
            return {"llm_turns": turn, "usage": usage, "messages": [*state["messages"], message],
                    **_failed(runtime, "UNSUPPORTED_REQUIREMENT", reason)}
    if rejected is not None:
        log(session, "   reject_request không hợp lệ: trả lỗi về LLM thay vì dừng", level=logging.WARNING)
    submit = next((call for call in calls if call["function"]["name"] == SUBMIT_TOOL), None)
    return {"llm_turns": turn, "usage": usage, "messages": [*state["messages"], message],
            "pending_submit": {"id": submit["id"], "arguments": submit["function"]["arguments"]} if submit else None}


def _words_in(quote: str, prompt: str) -> bool:
    quote = " ".join(quote.lower().split())
    return bool(quote) and quote in " ".join(prompt.lower().split())


def _reject_args(call: dict[str, Any]) -> dict[str, Any] | None:
    try:
        args = json.loads(call["function"]["arguments"])
    except ValueError:
        return None
    return args if isinstance(args, dict) else None


def after_llm(state: GraphState) -> str:
    if state.get("status"):
        return END
    pending = state.get("pending_submit")
    if any(pending is None or call["id"] != pending["id"] for call in _calls(state)):
        log(state["session_id"], "   ⇒ sang node tools")
        return "tools"
    log(state["session_id"], "   ⇒ sang node build_xosc")
    return "build_xosc"


def tools(state: GraphState, runtime: Runtime[GraphContext]) -> dict[str, Any]:
    """Run every catalog call of the last turn (no LLM here); a submit waits for build_xosc."""
    catalog = CatalogTools(runtime.context["catalog_api"], [item.ref for item in runtime.context["selected_snapshots"]],
                           seed=state["seed"], graphs=runtime.context["lane_graphs"], prompt=runtime.context["request"].prompt)
    pending = state.get("pending_submit")
    session = state["session_id"]
    log(session, "── [node tools] chạy song song %d tool đọc DB",
        sum(pending is None or call["id"] != pending["id"] for call in _calls(state)))
    prompt = runtime.context["request"].prompt

    def answer(call: dict[str, Any]) -> dict[str, Any]:
        name, arguments = call["function"]["name"], call["function"]["arguments"]
        started = time.perf_counter()
        if name == SUBMIT_TOOL:
            content = json.dumps({"error": "Chỉ nộp một submit_scenario mỗi lượt."}, ensure_ascii=False)
        elif name == REJECT_TOOL:
            # Reached only when the llm node refused the rejection; tell the model why.
            args = _reject_args(call) or {}
            problem = reject_problem(args.get("quote", ""), prompt) or "Không từ chối cùng lượt với tool khác."
            content = json.dumps({"error": problem}, ensure_ascii=False)
        else:
            content = tool_result(catalog, name, arguments)
        log(session, "   ← %s · %.0f ms · %d ký tự: %s", name, (time.perf_counter() - started) * 1000, len(content), short(content))
        return {"role": "tool", "tool_call_id": call["id"], "content": content}

    todo = [call for call in _calls(state) if pending is None or call["id"] != pending["id"]]
    # Independent reads: run them at once, keep the answers in call order.
    with ThreadPoolExecutor(max_workers=max(1, len(todo))) as pool:
        answers = list(pool.map(answer, todo))
    return {"messages": [*state["messages"], *answers]}


def after_tools(state: GraphState) -> str:
    target = "build_xosc" if state.get("pending_submit") else "llm"
    log(state["session_id"], "   ⇒ sang node %s", target)
    return target


def build_xosc(state: GraphState, runtime: Runtime[GraphContext]) -> dict[str, Any]:
    """Check the submitted scenario by simulation and render the XOSC; failures go back to the LLM as the tool result."""
    request = runtime.context["request"]
    pending = cast(dict[str, str], state["pending_submit"])
    attempts = state["submit_attempts"] + 1
    out_of_attempts = attempts >= request.max_proposal_attempts or state["llm_turns"] >= MAX_LLM_TURNS
    session = state["session_id"]
    log(session, "── [node build_xosc] lần nộp %d/%d: %s", attempts, request.max_proposal_attempts, short(pending["arguments"]))
    scenario, feedback = None, None
    started = time.perf_counter()
    try:
        spec = ScenarioSpec.model_validate_json(pending["arguments"])
    except ValidationError as exc:
        feedback = {"valid": False, "issues": [{"code": "INVALID_ARGUMENTS", "message": exc.errors()[0]["msg"]}]}
    else:
        # The LLM's structure, with numbers tuned by simulation when only numbers are wrong.
        checked = tune(spec, request, runtime.context["selected_snapshots"], runtime.context["lane_graphs"])
        if isinstance(checked, ValidationResult):
            feedback = {"valid": False, "issues": [issue.model_dump() for issue in checked.issues]}
        else:
            snapshot = next(item for item in runtime.context["selected_snapshots"] if item.ref.map_name == spec.map_name)
            xml, digest = render_xosc(checked, spec.map_name)
            scenario = GeneratedScenario(
                scenario_id=hashlib.sha256(f"{session}:{digest}".encode()).hexdigest()[:24],
                variant_no=1, snapshot=snapshot.ref, site_id=checked.start_id, plan=checked, xosc=xml, xosc_sha256=digest,
            )
            feedback = {"valid": True, "xosc_sha256": digest, "incident": checked.metrics, "auto_tuned": checked.tuning}
    log(session, "   mô phỏng + kiểm tra: %.0f ms", (time.perf_counter() - started) * 1000)
    unavailable = None
    if scenario is not None:
        scenario, carla_issue, unavailable = _carla_validate(session, runtime, scenario)
        if carla_issue is not None:
            feedback = {"valid": False, "issues": [carla_issue]}
        elif unavailable is not None:
            feedback = {"valid": False, "issues": [{"code": "CARLA_VALIDATION_UNAVAILABLE", "message": unavailable}]}
        elif scenario is not None and scenario.carla_validation is not None:
            feedback["carla"] = scenario.carla_validation
    if scenario is not None:
        plan = scenario.plan
        log(session, "   ✓ hợp lệ · %s · ego %s %g km/h · %s · %s", plan.title, plan.ego.blueprint_id, plan.ego.speed_kmh,
            ", ".join(f"{a.id}={a.kind} {a.speed_kmh:g} km/h" for a in plan.actors), plan.environment.weather_preset)
        log(session, "   sự cố (%s): %s", plan.critical_actor, short(plan.metrics))
        if plan.tuning:
            log(session, "   tự chỉnh số liệu: %s", "; ".join(plan.tuning))
        log(session, "   XOSC %d ký tự · sha256=%s", len(scenario.xosc), scenario.xosc_sha256[:12])
        log(session, "   XOSC:\n%s", scenario.xosc, level=logging.DEBUG)
    else:
        log(session, "   ✗ chưa hợp lệ: %s", short(feedback["issues"]))
    answer = {"role": "tool", "tool_call_id": pending["id"], "content": json.dumps(feedback, ensure_ascii=False)}
    update: dict[str, Any] = {"messages": [*state["messages"], answer], "submit_attempts": attempts,
                              "pending_submit": None, "scenario": scenario}
    if unavailable is not None:
        update.update(_failed(runtime, "CARLA_VALIDATION_UNAVAILABLE", unavailable))
    elif scenario is None and out_of_attempts:
        codes = "; ".join(f"{item['code']}: {item['message']}" for item in feedback["issues"])
        update.update(_failed(runtime, "NO_VALID_PLAN", codes or "Không tạo được kịch bản hợp lệ."))
    return update


def _carla_validate(session: str, runtime: Runtime[GraphContext],
                    scenario: GeneratedScenario) -> tuple[GeneratedScenario | None, dict[str, Any] | None, str | None]:
    """Run the simulation-valid XOSC on the project's Bridge CARLA with the no-brain ego (it keeps lane and speed).

    Returns (scenario with its CARLA verdict, or None when the ego did not collide; the issue for the LLM then;
    why the run must stop when CARLA is required but gave no verdict)."""
    check: CarlaCheck | None = runtime.context.get("carla")
    bridge_uid = runtime.context["request"].validator_bridge_uid
    if check is None:
        return scenario, None, None
    if bridge_uid is None:
        if check.mode == "required":
            return None, None, "Project chưa ghép Bridge nên không kiểm tra được kịch bản trên CARLA."
        log(session, "   CARLA: bỏ qua (project chưa ghép Bridge)")
        return scenario, None, None
    log(session, "── [CARLA] chạy XOSC trên Bridge %s với ego không phản ứng…", bridge_uid)
    started = time.perf_counter()
    verdict = check.validator.validate(bridge_uid, scenario.xosc, scenario.xosc_sha256, check.timeout_s)
    log(session, "   CARLA %.1f s: %s", time.perf_counter() - started, verdict.as_metrics())
    checked = scenario.model_copy(update={"carla_validation": verdict.as_metrics()})
    if not verdict.ran:
        reason = f"{verdict.code}: {verdict.message or 'không có kết quả'}"
        if check.mode == "required":
            return None, None, f"Không kiểm tra được trên CARLA ({reason})."
        log(session, "   CARLA: không có kết quả, vẫn lưu theo mô phỏng (%s)", reason, level=logging.WARNING)
        return checked, None, None
    if verdict.collision:
        return checked, None, None
    critical = scenario.plan.critical_actor
    return None, {"code": "NO_COLLISION_IN_CARLA", "message": (
        f"Đã chạy kịch bản trên CARLA với ego KHÔNG phản ứng (giữ làn, giữ tốc độ) trong "
        f"{verdict.scenario_duration_s or scenario.plan.duration_s:g} s: ego không va chạm. Test case chỉ hợp lệ khi ego "
        f"không phản ứng thì va chạm với {critical}: cho {critical} vào đường của ego gần hơn hoặc muộn hơn, hoặc tăng "
        f"chênh lệch tốc độ, nhưng lúc vào làn vẫn để ego còn cơ hội né."), "field": "actions"}, None


def after_build(state: GraphState) -> str:
    target = "save" if state.get("scenario") is not None else END if state.get("status") else "llm"
    log(state["session_id"], "   ⇒ %s", "kết thúc" if target == END else f"sang node {target}")
    return target


def save(state: GraphState, runtime: Runtime[GraphContext]) -> dict[str, Any]:
    """Store the XOSC through the Backend for the project and user that asked; without save_to it is only returned."""
    request = runtime.context["request"]
    scenario = cast(GeneratedScenario, state["scenario"])
    session = state["session_id"]
    if request.save_to is None:
        log(session, "── [node save] không có save_to: chỉ trả kết quả, không ghi DB")
        return {"status": "completed"}
    log(session, "── [node save] ghi DB qua Backend · project=%d · user=%d · snapshot=%d", request.save_to.project_id,
        request.save_to.user_id, scenario.snapshot.snapshot_id)
    payload = {
        "save_to": request.save_to.model_dump(), "snapshot_id": scenario.snapshot.snapshot_id,
        "model_name": runtime.context["llm"].model_name, "scenario": scenario.model_dump(mode="json"),
    }
    try:
        generation_id = cast(CatalogApi, runtime.context["catalog_api"]).save_scenario(payload)
    except ToolError as exc:
        return {"scenario": None, **_failed(runtime, "SAVE_FAILED", str(exc))}
    log(session, "   ✓ đã lưu ScenarioGeneration id=%d", generation_id)
    return {"status": "completed", "scenario": scenario.model_copy(update={"generation_id": generation_id})}


def build_graph():
    builder = StateGraph(GraphState, context_schema=GraphContext)
    builder.add_node("prefetch", prefetch)
    builder.add_node("llm", llm)
    builder.add_node("tools", tools)
    builder.add_node("build_xosc", build_xosc)
    builder.add_node("save", save)
    builder.add_edge(START, "prefetch")
    builder.add_conditional_edges("prefetch", after_prefetch, ["llm", END])
    builder.add_conditional_edges("llm", after_llm, ["tools", "build_xosc", END])
    builder.add_conditional_edges("tools", after_tools, ["llm", "build_xosc"])
    builder.add_conditional_edges("build_xosc", after_build, ["save", "llm", END])
    builder.add_edge("save", END)
    return builder.compile(name="motorcycle_cut_in_v2")


GENERATION_GRAPH = build_graph()


def run_graph(request: GenerationRequest, llm_client: ChatLLM, seed: int, catalog_api: CatalogApi,
              carla: CarlaCheck | None = None) -> GraphState:
    state = initial_state(request, seed)
    session = request.session_id
    log(session, "══ BẮT ĐẦU · model=%s · seed=%d · maps=%s", llm_client.model_name, seed,
        [item.ref.map_name for item in request.selected_snapshots])
    log(session, "   system prompt:\n%s", state["messages"][0]["content"], level=logging.DEBUG)
    log(session, "   user: %s", state["messages"][1]["content"])
    started = time.perf_counter()
    # prefetch, then each LLM turn can be followed by tools and build_xosc (three steps per turn), then save.
    final = cast(GraphState, GENERATION_GRAPH.invoke(
        state,
        context={"request": request, "selected_snapshots": request.selected_snapshots, "llm": llm_client,
                 "catalog_api": catalog_api, "carla": carla,
                 "lane_graphs": {item.ref.map_name: lane_graph(item.ref.content_hash, item.catalog)
                                 for item in request.selected_snapshots}},
        config={"recursion_limit": 3 * MAX_LLM_TURNS + 6},
    ))
    log(session, "══ KẾT THÚC · status=%s · %d lượt LLM · %d lần nộp · usage=%s · %.1f s", final.get("status"), final["llm_turns"],
        final["submit_attempts"], final["usage"], time.perf_counter() - started)
    return final
