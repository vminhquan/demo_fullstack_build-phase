from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from app.modules.testcase.schemas import TestCaseResponse
from app.shared.infrastructure.models import BuilderSessionStatus, DangerLevel


class BuilderMapOptions(BaseModel):
    """One picked map and the values the user ticked for it. A new session needs an ego (BuilderSessionCreate);
    any other empty list leaves that category to the Agent."""

    map_code: str = Field(min_length=1, max_length=120)
    # The CARLA data the screen listed the choices from, so generation uses that exact data (None on older sessions).
    catalog_snapshot_id: int | None = None
    ego_vehicle_codes: list[str] = Field(default_factory=list, max_length=50)
    adversary_types: list[str] = Field(default_factory=list, max_length=50)
    environment_codes: list[str] = Field(default_factory=list, max_length=50)
    danger_levels: list[DangerLevel] = Field(default_factory=list, max_length=4)


class BuilderSessionCreate(BaseModel):
    title: str | None = Field(default=None, max_length=290)
    description: str = Field(min_length=3, max_length=3800)
    catalog_source: Literal["DEFAULT", "PROJECT"] = "DEFAULT"
    # The one ego of the session, picked above the maps; it is copied onto every map (BuilderMapOptions keeps a list
    # so older sessions with several egos still load).
    ego_vehicle_code: str | None = Field(default=None, min_length=1, max_length=255)
    maps: list[BuilderMapOptions] = Field(min_length=1, max_length=20)
    tag_names: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("maps")
    @classmethod
    def unique_maps(cls, maps: list[BuilderMapOptions]) -> list[BuilderMapOptions]:
        codes = [item.map_code for item in maps]
        if len(set(codes)) != len(codes):
            raise ValueError("Each map can be picked only once")
        return maps

    @model_validator(mode="after")
    def single_ego(self) -> BuilderSessionCreate:
        # Ego is fixed by the user, never sampled by the Agent: exactly one vehicle, the same on every map.
        if self.ego_vehicle_code:
            for item in self.maps:
                item.ego_vehicle_codes = [self.ego_vehicle_code]
        egos = {code for item in self.maps for code in item.ego_vehicle_codes}
        if any(not item.ego_vehicle_codes for item in self.maps) or len(egos) != 1:
            raise ValueError("Pick exactly one ego vehicle for the session")
        self.ego_vehicle_code = egos.pop()
        return self


class BuilderError(BaseModel):
    variant_no: int
    map_code: str
    code: str
    message: str


class BuilderSessionSummary(BaseModel):
    id: int
    title: str
    title_source: str
    prompt: str
    catalog_source: str
    maps: list[BuilderMapOptions]
    tag_names: list[str]
    target_count: int
    status: BuilderSessionStatus
    succeeded_count: int
    failed_count: int
    # Saved test cases still waiting for a reviewer (status PENDING).
    pending_count: int = 0
    created_by: int
    created_by_name: str | None = None
    created_at: datetime
    updated_at: datetime
    finished_at: datetime | None


class BuilderSessionDetail(BuilderSessionSummary):
    errors: list[BuilderError]
    test_cases: list[TestCaseResponse]
