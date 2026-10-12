"""Tools of the central LLM node. None of them contains an LLM.

- Backend reads (Tầng A: Agent → Backend HTTP → DB snapshot): search_cut_in_sites, get_site_*.
- Lane-graph reads (find_ego_starts, describe_position): answered from the waypoints of the snapshot the run
  received, the same stored snapshot by content hash, to spare a Backend round trip per question.
Complete inventories (map summary, vehicles, weather presets) are not tools: the `prefetch` node puts them in the
first message, and every choice among them stays with the LLM.

``submit_scenario`` (a ScenarioSpec) is how the LLM says it has enough information: the graph routes it to
``build_xosc``, which checks it by simulation and renders the XOSC. ``reject_request`` ends the run for a prompt
asking what the system cannot build.
"""

from __future__ import annotations

import json
import re
from typing import Any, Literal
from urllib.parse import quote

import httpx
from app.catalog.models import SnapshotRef
from app.scenario.explore import describe, find_starts
from app.scenario.lanes import LaneGraph
from app.scenario.spec import ScenarioSpec
from openai import pydantic_function_tool
from pydantic import BaseModel, Field, ValidationError

# Fields are all required with null meaning "not set": the API's strict tool schema does not allow defaults.

SUBMIT_TOOL = "submit_scenario"
REJECT_TOOL = "reject_request"


class SearchCutInSites(BaseModel):
    """Tìm cặp làn kề cùng chiều đã xác minh (hợp với mọi tình huống có xe từ làn bên cạnh). Mỗi site có vị trí ego
    (ego_road_id, ego_lane_id, ego_s) và phía của làn kề. Mọi bộ lọc là null trừ khi prompt NÓI RÕ điều đó."""

    map_name: str = Field(description="Một trong các map đã chọn.")
    tags: list[str] | None = Field(description="Chỉ khi prompt nói loại đường. Tất cả phải có: straight, curve, junction, "
                                               "junction_approach, intersection_4way.")
    min_length_m: float | None = Field(description="Chiều dài hành lang tối thiểu (m). Thường để null.")
    speed_kmh_min: float | None = Field(description="Giới hạn tốc độ của ĐƯỜNG (biển báo/OpenDRIVE), KHÔNG phải tốc độ xe. "
                                                    "Chỉ khi prompt nói về giới hạn tốc độ đường.")
    speed_kmh_max: float | None = Field(description="Giới hạn tốc độ của ĐƯỜNG, KHÔNG phải tốc độ xe. "
                                                    "Chỉ khi prompt nói về giới hạn tốc độ đường.")
    target_side: Literal["left", "right"] | None = Field(description="Phía xe từ làn kề lấn sang; chỉ khi prompt nói rõ.")
    lane_change_allowed: bool | None = Field(description="Chỉ khi prompt nói về vạch kẻ cho phép/cấm đổi làn.")
    near_junction: bool | None = Field(description="Chỉ khi prompt nói gần/không gần giao lộ; không cần đặt cùng lúc với tags giao lộ/ngã tư.")
    limit: int | None = Field(description="Số site trả về, 1-20 (mặc định 10).")


class SiteRef(BaseModel):
    map_name: str = Field(description="Một trong các map đã chọn.")
    site_id: str = Field(description="Sao chép nguyên chuỗi site_id từ kết quả search_cut_in_sites; không dùng road_id.")


class GetSiteGeometry(SiteRef):
    """Hình học hành lang của một site: chiều dài trước/sau, bề rộng làn, độ cong, vạch kẻ, điểm kết thúc."""


class GetSiteSpeed(SiteRef):
    """Giới hạn tốc độ trên hành lang của một site: OpenDRIVE và biển tốc độ theo along_m."""


class GetSiteControls(SiteRef):
    """Đèn giao thông, biển dừng/nhường, vạch qua đường trên hành lang của một site, theo along_m."""


class GetSiteJunctions(SiteRef):
    """Giao lộ trên hành lang của một site, theo along_m."""


class FindEgoStarts(BaseModel):
    """Tìm vị trí xuất phát cho ô tô ego trên mọi làn xe chạy của map (đã xáo theo biến thể). Mỗi vị trí có tags
    (straight, curve, gentle_curve, junction_approach, intersection_4way_approach, multi_lane), quãng đường đi tiếp được, số làn cùng chiều
    bên trái/phải. Mọi bộ lọc là null trừ khi kịch bản cần."""

    map_name: str = Field(description="Một trong các map đã chọn.")
    tags: list[str] | None = Field(description="Tất cả phải có.")
    min_path_m: float | None = Field(description="Quãng đường tối thiểu ego đi tiếp được (m).")
    min_left_lanes: int | None = Field(description="Số làn cùng chiều tối thiểu bên trái ego; để null nếu làn bên phải cũng được.")
    min_right_lanes: int | None = Field(description="Số làn cùng chiều tối thiểu bên phải ego; để null nếu làn bên trái cũng được.")
    limit: int | None = Field(description="Số vị trí trả về, 1-20 (mặc định 10).")


class DescribePosition(BaseModel):
    """Mô tả một vị trí làn: tags, quãng đường đi tiếp, giao lộ phía trước, số làn cùng chiều trái/phải."""

    map_name: str = Field(description="Một trong các map đã chọn.")
    road_id: int
    lane_id: int
    s: float


class RejectRequest(BaseModel):
    """Từ chối CHỈ khi (1) prompt yêu cầu rõ điều hệ thống chưa dựng được: người đi bộ, tác nhân không phải phương tiện,
    rẽ qua giao lộ, đèn giao thông, thời tiết không có preset (tuyết...); hoặc (2) catalog.can_host cho thấy map không
    có điều kiện cần (tạt đầu cần multi_lane, ngã tư cần four_way). Không dùng khi chỉ là tìm chưa ra vị trí."""

    quote: str = Field(description="Nguyên văn đoạn trong prompt nêu yêu cầu này.")
    reason: str


# What the system can build: vehicles on driving lanes and their speed / lane-change / braking manoeuvres. A
# rejection quoting nothing beyond these words is the model giving up (e.g. after a search with filters the user
# never asked for), not a request outside what is supported.
SCENARIO_WORDS = re.compile(
    r"xe máy|mô ?tô|xe hai bánh|xe gắn máy|ô tô|xe hơi|xe con|xe tải|xe container|xe buýt|xe bus|xe khách|xe đạp|xe van|"
    r"bán tải|tạt đầu|cắt ngang|cắt đầu|chen ngang|chen|lấn làn|lấn|cut-?in|phanh gấp|phanh|dừng đột ngột|dừng|"
    r"vượt|chạy chậm|đổi làn|chuyển làn|tăng tốc|giảm tốc|ego|"
    # weather, light and road kinds are built too (presets, the lighting field, lane positions)
    r"bão bụi|bụi mù|bụi|mưa to|mưa lớn|mưa nhỏ|mưa|đổ|nắng|sương mù|sương|nhiều mây|âm u|đường ướt|ướt|trơn|"
    r"ban đêm|buổi tối|trời tối|đêm|tối|hoàng hôn|chiều tối|ban ngày|buổi trưa|buổi sáng|trời|"
    r"khúc cua|đường cong|đường thẳng|giao lộ|ngã tư|ngã ba|nút giao|"
    r"cao tốc|xa lộ|quốc lộ|đường lớn|đô thị|nội thành|ngoại ô|trong phố|thành phố|nông thôn|"
    r"\b(?:xe|đầu|vào|làn|phía|trước|sau|trên|đường|của|một|khi|đang|bị|bất ngờ|đột ngột|gấp|bên|trái|phải|"
    r"và|lúc|ở|tại|có|rất|cùng|lúc|trong|nhau|hai|ba|nhiều)\b"
)


def _words(text: str) -> str:
    return " ".join(text.lower().split())


def reject_problem(quote: str, prompt: str) -> str | None:
    """Why a reject_request is not accepted, or None when it quotes a real unsupported requirement."""
    text = _words(quote)
    if not text or text not in _words(prompt):
        return "reject_request phải trích nguyên văn đoạn trong prompt nêu yêu cầu ngoài miền hỗ trợ."
    if not re.sub(r"[\W_]+", " ", SCENARIO_WORDS.sub(" ", text)).strip():
        return ("Đoạn trích chỉ mô tả phương tiện trên làn xe, là điều hệ thống dựng được: không được từ chối. "
                "Nếu không tìm ra vị trí, bỏ các bộ lọc người dùng không yêu cầu và tìm lại.")
    return None


# Catalog tool name -> (arguments model, site section).
SECTION_TOOLS: dict[str, tuple[type[SiteRef], str]] = {
    "get_site_geometry": (GetSiteGeometry, "geometry"),
    "get_site_speed": (GetSiteSpeed, "speed"),
    "get_site_controls": (GetSiteControls, "controls"),
    "get_site_junctions": (GetSiteJunctions, "junctions"),
}
LANE_TOOLS: dict[str, type[BaseModel]] = {"find_ego_starts": FindEgoStarts, "describe_position": DescribePosition}
CATALOG_TOOLS: dict[str, type[BaseModel]] = {
    **LANE_TOOLS,
    "search_cut_in_sites": SearchCutInSites,
    **{name: model for name, (model, _) in SECTION_TOOLS.items()},
}


class ToolError(RuntimeError):
    pass


class CatalogApi:
    """The Backend's internal endpoints for the Agent: catalog reads and the scenario save."""

    def __init__(self, base_url: str, api_key: str | None, *, timeout: float = 10.0, client: httpx.Client | None = None):
        headers = {"X-API-Key": api_key} if api_key else {}
        self._client = client or httpx.Client(base_url=base_url.rstrip("/"), headers=headers, timeout=timeout)

    def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        try:
            response = self._client.request(method, f"/api/v1/internal/agent{path}", **kwargs)
        except httpx.HTTPError as exc:
            raise ToolError(f"Backend không phản hồi: {type(exc).__name__}") from exc
        if response.status_code == 404:
            raise ToolError("Không tìm thấy dữ liệu yêu cầu trong snapshot.")
        if response.status_code not in (200, 201):
            raise ToolError(f"Backend trả lỗi {response.status_code}.")
        return response.json()

    def summary(self, snapshot_id: int) -> dict[str, Any]:
        return self._request("GET", f"/catalog/snapshots/{snapshot_id}/summary")

    def search_sites(self, snapshot_id: int, filters: dict[str, Any]) -> dict[str, Any]:
        return self._request("GET", f"/catalog/snapshots/{snapshot_id}/cut-in-sites", params=filters)

    def site_section(self, snapshot_id: int, site_id: str, section: str) -> dict[str, Any]:
        try:
            return self._request("GET", f"/catalog/snapshots/{snapshot_id}/cut-in-sites/{quote(site_id, safe='')}/{section}")
        except ToolError as exc:
            if str(exc) == "Không tìm thấy dữ liệu yêu cầu trong snapshot.":
                raise ToolError(f"site_id '{site_id}' không tồn tại: hãy dùng nguyên chuỗi site_id trong kết quả "
                                "search_cut_in_sites, không dùng road_id hoặc số thứ tự.") from exc
            raise

    def vehicles(self, snapshot_id: int, kind: str, seed: int | None = None) -> dict[str, Any]:
        params = {"kind": kind, **({"seed": seed} if seed is not None else {})}
        return self._request("GET", f"/catalog/snapshots/{snapshot_id}/vehicles", params=params)

    def weather_presets(self, snapshot_id: int) -> dict[str, Any]:
        return self._request("GET", f"/catalog/snapshots/{snapshot_id}/weather-presets")

    def save_scenario(self, payload: dict[str, Any]) -> int:
        return int(self._request("POST", "/scenario-generations", json=payload)["generation_id"])


def _spec(model: type[BaseModel], name: str) -> dict[str, Any]:
    return pydantic_function_tool(model, name=name)


def _arguments(model: type[BaseModel], arguments: str) -> BaseModel:
    try:
        return model.model_validate_json(arguments)
    except ValidationError as exc:
        raise ToolError(f"Tham số không hợp lệ: {exc.errors()[0]['msg']}") from exc


class CatalogTools:
    """Runs the catalog tool calls of this run: Backend reads, and lane-graph reads on the selected snapshots."""

    def __init__(self, api: CatalogApi, refs: list[SnapshotRef], *, seed: int | None = None,
                 graphs: dict[str, LaneGraph] | None = None, prompt: str = ""):
        self._api, self._seed, self._graphs = api, seed, graphs or {}
        self._prompt = _words(prompt)
        self._snapshots = {ref.map_name: ref.snapshot_id for ref in refs}

    @staticmethod
    def specs() -> list[dict[str, Any]]:
        return [*(_spec(model, name) for name, model in CATALOG_TOOLS.items()),
                _spec(ScenarioSpec, SUBMIT_TOOL), _spec(RejectRequest, REJECT_TOOL)]

    def _snapshot_id(self, map_name: str) -> int:
        snapshot_id = self._snapshots.get(map_name)
        if snapshot_id is None:
            raise ToolError(f"Map phải là một trong: {', '.join(sorted(self._snapshots))}.")
        return snapshot_id

    def run(self, name: str, arguments: str) -> dict[str, Any]:
        model = CATALOG_TOOLS.get(name)
        if model is None:
            raise ToolError(f"Không có tool {name}.")
        args = _arguments(model, arguments)
        snapshot_id = self._snapshot_id(args.map_name)
        if name in LANE_TOOLS:
            graph = self._graphs.get(args.map_name)
            if graph is None:
                raise ToolError("Chưa có dữ liệu làn đường của map này.")
            if name == "find_ego_starts":
                left, right = args.min_left_lanes, args.min_right_lanes
                tags = list(args.tags or [])
                ignored = []
                if self._prompt and left and right and not re.search(r"cả hai bên|hai bên|hai phía|cả trái (?:lẫn|và) phải", self._prompt):
                    left = right = None
                    ignored = ["min_left_lanes", "min_right_lanes"]
                    if "multi_lane" not in tags:
                        tags.append("multi_lane")
                answer = find_starts(graph, tags=tags, min_path_m=args.min_path_m, min_left_lanes=left,
                                     min_right_lanes=right, seed=self._seed, limit=args.limit)
                if ignored:
                    answer["ignored_filters"] = ignored
                    answer["hint"] = "Tạt đầu chỉ cần làn kề ở một phía; xem left_lanes_same_direction hoặc right_lanes_same_direction."
                return answer
            found = describe(graph, args.road_id, args.lane_id, args.s)
            if found is None:
                raise ToolError("Không có làn xe chạy tại vị trí này.")
            return found.as_dict()
        if name == "search_cut_in_sites":
            filters = args.model_dump(exclude={"map_name"}, exclude_none=True)
            ignored = []
            if self._prompt:
                if not re.search(r"giới hạn tốc độ|tốc độ tối đa (?:của )?đường|biển (?:báo )?tốc độ", self._prompt):
                    ignored += [key for key in ("speed_kmh_min", "speed_kmh_max") if key in filters]
                if not re.search(r"bên trái|bên phải|từ trái|từ phải|phía trái|phía phải", self._prompt):
                    ignored += [key for key in ("target_side",) if key in filters]
                if not re.search(r"vạch kẻ|vạch liền|vạch đứt|cấm đổi làn|cho phép đổi làn", self._prompt):
                    ignored += [key for key in ("lane_change_allowed",) if key in filters]
                for key in ignored:
                    filters.pop(key)
                if "multi_lane" in filters.get("tags", []):
                    filters["tags"] = [tag for tag in filters["tags"] if tag != "multi_lane"]
                    ignored.append("tags=multi_lane")  # Every cut-in site already has two adjacent lanes.
                if "junction" in filters.get("tags", []):
                    filters["tags"] = [tag for tag in filters["tags"] if tag != "junction"]
                    # A junction may be tagged by its approach or intersection type rather than literally "junction".
                    filters["near_junction"] = True
                    ignored.append("tags=junction (using near_junction)")
            # The run's seed orders the matches, so parallel Builder variants are offered different sites.
            answer = self._api.search_sites(snapshot_id, {**filters, **({"seed": self._seed} if self._seed is not None else {})})
            if ignored:
                answer["ignored_filters"] = ignored
            return answer
        return self._api.site_section(snapshot_id, args.site_id, SECTION_TOOLS[name][1])


def tool_result(tools: CatalogTools, name: str, arguments: str) -> str:
    """JSON for the tool message; failures are reported to the model instead of aborting the generation."""
    try:
        result = tools.run(name, arguments)
    except ToolError as exc:
        result = {"error": str(exc)}
    return json.dumps(result, ensure_ascii=False, separators=(",", ":"))
