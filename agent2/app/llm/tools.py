"""Tool calls the LLM may make while generating.

Map facts come from the stored snapshot through the Backend (Tầng A: Agent → Backend HTTP → DB snapshot), never
from the catalog copy agent2 received. ``check_cut_in`` is the exception: it runs the same deterministic
validator the graph uses afterwards, so the model can test its numbers before answering.
"""

from __future__ import annotations

import json
from typing import Any, Literal, Protocol
from urllib.parse import quote

import httpx
from app.catalog.models import SelectedSnapshot, SnapshotRef
from app.cut_in.model import CutInPlan
from app.cut_in.sample import SampledVariant
from app.cut_in.validate import validate_cut_in
from openai import pydantic_function_tool
from pydantic import BaseModel, Field, ValidationError

# Fields are all required with null meaning "not set": the API's strict tool schema does not allow defaults.


class SearchCutInSites(BaseModel):
    """Tìm các đoạn đường (site) đã được xác minh có thể diễn ra xe máy tạt đầu trên một map đã chọn.
    Trả về tối đa `limit` site dài nhất khớp bộ lọc và tóm tắt những gì cả map có."""

    map_name: str = Field(description="Một trong các map đã chọn.")
    tags: list[str] | None = Field(description="Tất cả phải có: straight, curve, junction.")
    min_length_m: float | None = Field(description="Chiều dài hành lang tối thiểu (m).")
    speed_kmh_min: float | None = Field(description="Giới hạn tốc độ OpenDRIVE tối thiểu; site không rõ tốc độ bị loại.")
    speed_kmh_max: float | None = Field(description="Giới hạn tốc độ OpenDRIVE tối đa; site không rõ tốc độ bị loại.")
    target_side: Literal["left", "right"] | None = Field(description="Phía xe máy tạt vào so với ô tô.")
    lane_change_allowed: bool | None = Field(description="Vạch kẻ cho phép đổi làn suốt hành lang.")
    near_junction: bool | None = Field(description="Hành lang có chạm giao lộ.")
    limit: int | None = Field(description="Số site trả về, 1-20 (mặc định 10).")


class GetSiteContext(BaseModel):
    """Bối cảnh quanh một site: giới hạn tốc độ (OpenDRIVE và biển báo), đèn, biển dừng, vạch qua đường,
    giao lộ trên hành lang, theo khoảng cách dọc từ vị trí ô tô."""

    site_id: str


class CheckCutIn(BaseModel):
    """Kiểm tra bốn tham số bằng chính bộ kiểm tra của hệ thống trước khi trả lời. Trả về valid và lỗi nếu có."""

    motorcycle_start_offset_m: float
    trigger_time_s: float
    lane_change_duration_s: float
    desired_lead_gap_m: float


class ToolError(RuntimeError):
    pass


class Toolbox(Protocol):
    specs: list[dict[str, Any]]

    def run(self, name: str, arguments: str) -> dict[str, Any]: ...


class CatalogApi:
    """The Backend's read-only catalog endpoints for the Agent."""

    def __init__(self, base_url: str, api_key: str | None, *, timeout: float = 10.0, client: httpx.Client | None = None):
        headers = {"X-API-Key": api_key} if api_key else {}
        self._client = client or httpx.Client(base_url=base_url.rstrip("/"), headers=headers, timeout=timeout)

    def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        try:
            response = self._client.get(f"/api/v1/internal/agent{path}", params=params)
        except httpx.HTTPError as exc:
            raise ToolError(f"Backend không phản hồi: {type(exc).__name__}") from exc
        if response.status_code == 404:
            raise ToolError("Không tìm thấy dữ liệu yêu cầu trong snapshot.")
        if response.status_code != 200:
            raise ToolError(f"Backend trả lỗi {response.status_code}.")
        return response.json()

    def search_sites(self, snapshot_id: int, filters: dict[str, Any]) -> dict[str, Any]:
        return self._get(f"/catalog/snapshots/{snapshot_id}/cut-in-sites", filters)

    def site_context(self, snapshot_id: int, site_id: str) -> dict[str, Any]:
        return self._get(f"/catalog/snapshots/{snapshot_id}/cut-in-sites/{quote(site_id, safe='')}/context")


def _spec(model: type[BaseModel], name: str) -> dict[str, Any]:
    return pydantic_function_tool(model, name=name)


def _arguments(model: type[BaseModel], arguments: str) -> BaseModel:
    try:
        return model.model_validate_json(arguments)
    except ValidationError as exc:
        raise ToolError(f"Tham số không hợp lệ: {exc.errors()[0]['msg']}") from exc


class ExtractionTools:
    """While reading the prompt: what the selected maps can host."""

    def __init__(self, api: CatalogApi, refs: list[SnapshotRef]):
        self._api = api
        self._snapshots = {ref.map_name: ref.snapshot_id for ref in refs}
        self.specs = [_spec(SearchCutInSites, "search_cut_in_sites")]

    def run(self, name: str, arguments: str) -> dict[str, Any]:
        if name != "search_cut_in_sites":
            raise ToolError(f"Không có tool {name}.")
        args = _arguments(SearchCutInSites, arguments)
        snapshot_id = self._snapshots.get(args.map_name)
        if snapshot_id is None:
            raise ToolError(f"Map phải là một trong: {', '.join(sorted(self._snapshots))}.")
        filters = args.model_dump(exclude={"map_name"}, exclude_none=True)
        return self._api.search_sites(snapshot_id, filters)


class ProposalTools:
    """While choosing the four maneuver numbers for one locked variant."""

    def __init__(self, api: CatalogApi | None, variant: SampledVariant, snapshot: SelectedSnapshot):
        self._api, self._variant, self._snapshot = api, variant, snapshot
        self.specs = [_spec(CheckCutIn, "check_cut_in")]
        if api is not None:
            self.specs.append(_spec(GetSiteContext, "get_site_context"))

    def run(self, name: str, arguments: str) -> dict[str, Any]:
        if name == "check_cut_in":
            args = _arguments(CheckCutIn, arguments)
            try:
                plan = CutInPlan.model_validate({**self._variant.context.model_dump(), **args.model_dump()})
            except ValidationError as exc:
                return {"valid": False, "issues": [{"code": "INVALID_VALUE", "message": exc.errors()[0]["msg"]}]}
            result = validate_cut_in(plan, self._variant, self._snapshot)
            return {"valid": result.valid,
                    "issues": [{"code": issue.code, "message": issue.message, "field": issue.field} for issue in result.issues]}
        if name == "get_site_context" and self._api is not None:
            args = _arguments(GetSiteContext, arguments)
            return self._api.site_context(self._variant.site.snapshot.snapshot_id, args.site_id)
        raise ToolError(f"Không có tool {name}.")


def tool_result(toolbox: Toolbox, name: str, arguments: str) -> str:
    """JSON for the tool message; failures are reported to the model instead of aborting the generation."""
    try:
        result = toolbox.run(name, arguments)
    except ToolError as exc:
        result = {"error": str(exc)}
    return json.dumps(result, ensure_ascii=False, separators=(",", ":"))
