"""OpenAI structured-output boundary for prompt parsing and maneuver proposals."""

from __future__ import annotations

from typing import Any

from app.config import Settings, get_settings
from app.cut_in.model import CutInPlan, PromptConstraints
from app.cut_in.sample import SampledVariant
from app.llm.prompts import extraction_messages, proposal_messages
from pydantic import BaseModel, ValidationError


class LLMCallError(RuntimeError):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


class _PromptExtraction(BaseModel):
    """All fields required for the API's strict JSON schema; null means absent."""

    location_tags: list[str]
    weather_conditions: list[str]
    lighting: str | None
    road_surface: str | None
    ego_speed_kmh: float | None
    motorcycle_speed_kmh: float | None
    ambiguities: list[str]
    unsupported_requirements: list[str]


class _ManeuverProposal(BaseModel):
    motorcycle_start_offset_m: float
    trigger_time_s: float
    lane_change_duration_s: float
    desired_lead_gap_m: float


class OpenAILLM:
    def __init__(self, *, model_name: str = "gpt-4o-mini", api_key: str | None = None, sdk_client: Any | None = None):
        self.model_name = model_name
        if sdk_client is not None:
            self._client = sdk_client
        else:
            if not api_key:
                raise LLMCallError("OPENAI_NOT_CONFIGURED", "Thiếu OPENAI_API_KEY cho agent2.")
            from openai import OpenAI

            self._client = OpenAI(api_key=api_key, timeout=30.0, max_retries=2)

    def _parse(self, messages: list[dict[str, str]], schema: type[BaseModel]) -> BaseModel:
        completion = self._client.chat.completions.parse(
            model=self.model_name,
            messages=messages,
            response_format=schema,
            temperature=0,
        )
        if not completion.choices:
            raise LLMCallError("LLM_EMPTY_RESPONSE", "OpenAI không trả kết quả.")
        choice = completion.choices[0]
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

    def extract_constraints(self, prompt: str) -> PromptConstraints:
        if not prompt.strip():
            raise ValueError("prompt must not be empty")
        parsed = self._parse(extraction_messages(prompt), _PromptExtraction)
        try:
            return PromptConstraints.model_validate(parsed.model_dump())
        except ValidationError as exc:
            raise LLMCallError("LLM_INVALID_CONSTRAINTS", "Ràng buộc trích từ prompt không hợp lệ.") from exc

    def propose_maneuver(self, prompt: str, variant: SampledVariant, *, feedback: list[str] | None = None) -> CutInPlan:
        proposal = self._parse(proposal_messages(prompt, variant, feedback), _ManeuverProposal)
        try:
            return CutInPlan.model_validate({**variant.context.model_dump(), **proposal.model_dump()})
        except ValidationError as exc:
            raise LLMCallError("LLM_INVALID_PROPOSAL", "Tham số tạt đầu do LLM đề xuất không hợp lệ.") from exc


def from_settings(settings: Settings | None = None) -> OpenAILLM:
    config = settings or get_settings()
    key = config.openai_api_key.get_secret_value() if config.openai_api_key else None
    return OpenAILLM(model_name=config.model_name, api_key=key)
