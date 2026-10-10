"""OpenAI structured-output boundary for prompt parsing and maneuver proposals."""

from __future__ import annotations

import re
from typing import Any, Literal

from app.config import Settings, get_settings
from app.cut_in.model import CutInPlan, PromptConstraints
from app.cut_in.sample import SampledVariant
from app.llm.prompts import extraction_messages, proposal_messages
from app.llm.tools import Toolbox, tool_result
from pydantic import BaseModel, Field, ValidationError


# Rounds in which the model may call tools; the round after them must answer.
MAX_TOOL_ROUNDS = 4


class LLMCallError(RuntimeError):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


class _Unsupported(BaseModel):
    quote: str = Field(description="Nguyên văn đoạn trong prompt nêu yêu cầu này.")
    reason: str


def _words(text: str) -> str:
    return " ".join(text.lower().split())


# Kinds of road the cut-in sites do not encode: the run picks any verified site instead of refusing the prompt
# (gpt-4o-mini kept listing "cao tốc" as unsupported even when told to ignore it).
ROAD_KIND_WORDS = re.compile(r"cao tốc|xa lộ|quốc lộ|đường lớn|đô thị|nội thành|ngoại ô|trong phố|thành phố|nông thôn")


# Vietnamese words for values the extraction fields can hold. A blocked quote naming a value the model also put in
# a field is the model contradicting itself ("trời âm u" blocked next to weather=cloudy); whether CARLA has a
# preset for it is the sampler's call (WEATHER_NOT_AVAILABLE), not the model's.
FIELD_TERMS = {
    "nắng": "sunny", "quang": "sunny", "mưa": "rain", "mây": "cloudy", "âm u": "cloudy", "ướt": "wet", "bụi": "dust",
    "sương": "fog", "đêm": "night", "tối": "night", "hoàng hôn": "sunset", "chiều": "sunset", "ban ngày": "day",
    "trưa": "day", "trơn": "slippery", "khô": "dry",
}


def _blocks(quote: str, prompt: str, extracted: set[str]) -> bool:
    """An unsupported requirement only counts when quoted from the prompt, not just a kind of road, and not a
    condition the model extracted into a field."""
    text = _words(quote)
    if not text or text not in _words(prompt) or ROAD_KIND_WORDS.search(text):
        return False
    return not any(term in text and value in extracted for term, value in FIELD_TERMS.items())


class _PromptExtraction(BaseModel):
    """All fields required for the API's strict JSON schema; null means absent.

    Closed vocabularies are enums in that schema, so the model cannot answer a value the sampler would reject."""

    location_tags: list[Literal["straight", "curve", "junction", "junction_approach", "intersection_4way"]]
    weather_conditions: list[Literal["sunny", "rain", "cloudy", "wet", "dust", "fog"]]
    lighting: Literal["day", "night", "sunset"] | None
    road_surface: Literal["dry", "wet", "slippery"] | None
    ego_speed_kmh: float | None
    motorcycle_speed_kmh: float | None
    ambiguities: list[str] = Field(
        description="Chỉ điều người dùng ĐÃ NÓI nhưng mâu thuẫn hoặc hiểu nhiều cách. Không ghi thông tin bị thiếu.")
    unsupported_requirements: list[_Unsupported] = Field(
        description="Chỉ điều kiện người dùng ĐÃ YÊU CẦU nằm ngoài miền hỗ trợ (ví dụ tuyết, tình huống khác). "
                    "Không ghi thông tin bị thiếu; hệ thống tự chọn phần thiếu.")


class _ManeuverProposal(BaseModel):
    motorcycle_start_offset_m: float
    trigger_time_s: float
    lane_change_duration_s: float
    desired_lead_gap_m: float


class OpenAILLM:
    def __init__(self, *, model_name: str = "gpt-4o-mini", api_key: str | None = None, sdk_client: Any | None = None):
        self.model_name = model_name
        # Names of the tools the model called, in order, for tests and diagnostics.
        self.tool_calls: list[str] = []
        if sdk_client is not None:
            self._client = sdk_client
        else:
            if not api_key:
                raise LLMCallError("OPENAI_NOT_CONFIGURED", "Thiếu OPENAI_API_KEY cho agent2.")
            from openai import OpenAI

            self._client = OpenAI(api_key=api_key, timeout=30.0, max_retries=2)

    def _parse(self, messages: list[dict[str, Any]], schema: type[BaseModel], tools: Toolbox | None = None) -> BaseModel:
        messages = list(messages)
        for round_no in range(MAX_TOOL_ROUNDS + 1):
            offered: dict[str, Any] = {}
            if tools is not None and tools.specs:
                # The last round keeps the tool list (the history refers to it) but forbids calling it.
                offered = {"tools": tools.specs, **({"tool_choice": "none"} if round_no == MAX_TOOL_ROUNDS else {})}
            completion = self._client.chat.completions.parse(
                model=self.model_name,
                messages=messages,
                response_format=schema,
                temperature=0,
                **offered,
            )
            if not completion.choices:
                raise LLMCallError("LLM_EMPTY_RESPONSE", "OpenAI không trả kết quả.")
            choice = completion.choices[0]
            calls = getattr(choice.message, "tool_calls", None)
            if not (tools is not None and calls):
                return self._answer(choice, schema)
            messages.append({"role": "assistant", "content": choice.message.content, "tool_calls": [
                {"id": call.id, "type": "function",
                 "function": {"name": call.function.name, "arguments": call.function.arguments}}
                for call in calls
            ]})
            for call in calls:
                self.tool_calls.append(call.function.name)
                messages.append({"role": "tool", "tool_call_id": call.id,
                                 "content": tool_result(tools, call.function.name, call.function.arguments)})
        raise LLMCallError("LLM_TOOL_LOOP", "OpenAI vẫn gọi tool sau khi đã bị cấm.")

    def _answer(self, choice: Any, schema: type[BaseModel]) -> BaseModel:
        if choice.finish_reason != "stop":
            raise LLMCallError("LLM_INCOMPLETE_RESPONSE", f"OpenAI kết thúc trước khi có kết quả đầy đủ: {choice.finish_reason}")
        if choice.message.refusal:
            raise LLMCallError("LLM_REFUSAL", "OpenAI từ chối yêu cầu.")
        if choice.message.parsed is None:
            raise LLMCallError("LLM_UNPARSEABLE_RESPONSE", "OpenAI không trả dữ liệu đúng schema.")
        try:
            return schema.model_validate(choice.message.parsed)
        except ValidationError as exc:
            raise LLMCallError("LLM_UNPARSEABLE_RESPONSE", "OpenAI trả dữ liệu không hợp lệ theo schema.") from exc

    def extract_constraints(
        self, prompt: str, *, tools: Toolbox | None = None, map_names: list[str] | None = None,
        environments: list[str] | None = None,
    ) -> PromptConstraints:
        if not prompt.strip():
            raise ValueError("prompt must not be empty")
        messages = extraction_messages(prompt, map_names if tools else None, environments)
        parsed = self._parse(messages, _PromptExtraction, tools)
        # A requirement only blocks when the model quotes where the user asked for it; "nothing was said about X"
        # has no quote, so missing details are sampled instead of rejected.
        extracted = {*parsed.weather_conditions, parsed.lighting, parsed.road_surface}
        unsupported = [f'"{item.quote.strip()}": {item.reason}' for item in parsed.unsupported_requirements
                       if _blocks(item.quote, prompt, extracted)]
        try:
            return PromptConstraints.model_validate({**parsed.model_dump(), "unsupported_requirements": unsupported})
        except ValidationError as exc:
            raise LLMCallError("LLM_INVALID_CONSTRAINTS", "Ràng buộc trích từ prompt không hợp lệ.") from exc

    def propose_maneuver(
        self, prompt: str, variant: SampledVariant, *, feedback: list[str] | None = None, tools: Toolbox | None = None,
    ) -> CutInPlan:
        names = [spec["function"]["name"] for spec in tools.specs] if tools else []
        proposal = self._parse(proposal_messages(prompt, variant, feedback, tool_names=names), _ManeuverProposal, tools)
        try:
            return CutInPlan.model_validate({**variant.context.model_dump(), **proposal.model_dump()})
        except ValidationError as exc:
            raise LLMCallError("LLM_INVALID_PROPOSAL", "Tham số tạt đầu do LLM đề xuất không hợp lệ.") from exc


def from_settings(settings: Settings | None = None) -> OpenAILLM:
    config = settings or get_settings()
    key = config.openai_api_key.get_secret_value() if config.openai_api_key else None
    return OpenAILLM(model_name=config.model_name, api_key=key)
