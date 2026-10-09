"""Filter verified cut-in sites from the CARLA snapshots selected for a run.

The Bridge establishes topology while connected to CARLA. This module never
infers adjacency from independently sampled waypoint coordinates.
"""

from __future__ import annotations

from collections.abc import Sequence

from pydantic import BaseModel, Field

from app.catalog.models import CutInSite, SelectedSnapshot
from app.contracts import MapFailure


MIN_CUT_IN_LENGTH_M = 60.0


class SiteSearchResult(BaseModel):
    sites: list[CutInSite] = Field(default_factory=list)
    map_failures: list[MapFailure] = Field(default_factory=list)


def find_cut_in_sites(
    selected_snapshots: Sequence[SelectedSnapshot],
    location_constraints: Sequence[str] = (),
    *,
    min_length_m: float = MIN_CUT_IN_LENGTH_M,
) -> SiteSearchResult:
    """Return only verified sites from the selected snapshots.

    Every requested location tag must match. In particular, ``junction`` is
    not treated as ``intersection_4way``. Missing tags impose no location
    restriction, allowing a later sampler to draw across eligible maps.
    """

    if min_length_m <= 0:
        raise ValueError("min_length_m must be positive")
    required_tags = {tag.strip().lower() for tag in location_constraints if tag.strip()}
    sites: list[CutInSite] = []
    failures: list[MapFailure] = []
    for selected in selected_snapshots:
        records = selected.catalog.cut_in_sites
        if records is None:
            failures.append(MapFailure(
                snapshot=selected.ref,
                code="SITE_INDEX_MISSING",
                message="Snapshot này chưa có chỉ mục vị trí tạt đầu; cần đồng bộ lại catalog từ Bridge.",
            ))
            continue
        if not records:
            failures.append(MapFailure(
                snapshot=selected.ref,
                code="NO_VERIFIED_SITES",
                message="Bridge không tìm thấy vị trí tạt đầu đã xác minh trên map này.",
            ))
            continue
        matching_location = [
            record for record in records
            if required_tags.issubset({tag.lower() for tag in record.location_tags})
        ]
        if not matching_location:
            failures.append(MapFailure(
                snapshot=selected.ref,
                code="LOCATION_NOT_AVAILABLE",
                message="Map này không có vị trí tạt đầu đã xác minh phù hợp với địa điểm yêu cầu.",
            ))
            continue
        long_enough = [record for record in matching_location if record.available_length_m >= min_length_m]
        if not long_enough:
            failures.append(MapFailure(
                snapshot=selected.ref,
                code="INSUFFICIENT_SITE_LENGTH",
                message="Các vị trí phù hợp trên map này không đủ chiều dài để thực hiện tạt đầu.",
            ))
            continue
        sites.extend(
            CutInSite.model_validate({**record.model_dump(), "snapshot": selected.ref})
            for record in long_enough
        )
    return SiteSearchResult(sites=sites, map_failures=failures)
