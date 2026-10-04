"""Development-only mock data: approved test cases so the Test Suite page has content.

Run: docker compose exec backend python -m app.seed_test_suite [PROJECT_CODE]
Without a project code every active project gets the data. Re-running skips titles that already exist.
"""
from __future__ import annotations

import asyncio
import secrets
import sys
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.shared.infrastructure.db import SessionFactory
from app.shared.infrastructure.models import (
    CommentType,
    DangerLevel,
    Project,
    ProjectStatus,
    ReviewComment,
    ReviewDecision,
    ReviewRequest,
    Tag,
    TestCase,
    TestCaseVersion,
    VersionStatus,
)

SEED_NOTE = "seed:test-suite"

# (title, description, map, ego, adversary, environment, danger, tags)
CASES: list[tuple[str, str, str, str, str, str, DangerLevel, list[str]]] = [
    ("Xe máy tạt đầu ở ngã tư khi trời mưa", "Xe máy tạt đầu từ làn phải khi ego 40 km/h vào ngã tư trời mưa, cách 15 m.", "Town03", "vehicle.tesla.model3", "motorcycle", "MidRainSunset", DangerLevel.HIGH, ["intersection", "rain", "cut-in"]),
    ("Người đi bộ băng ngang đường ban đêm", "Người đi bộ băng qua đường từ bên trái vào ban đêm, ego 35 km/h.", "Town10HD_Opt", "vehicle.toyota.prius", "pedestrian", "MidRainyNight", DangerLevel.HIGH, ["pedestrian", "night"]),
    ("Xe tải phanh gấp trên cao tốc", "Xe tải phía trước phanh gấp trên đường thẳng, ego 50 km/h.", "Town04", "vehicle.tesla.model3", "vehicle", "ClearNoon", DangerLevel.CRITICAL, ["highway", "hard-brake"]),
    ("Ô tô vượt đèn đỏ cắt ngang từ bên trái", "Ô tô vượt đèn đỏ cắt ngang tại ngã tư, ego 40 km/h.", "Town05", "vehicle.nissan.micra", "vehicle", "HardRainSunset", DangerLevel.HIGH, ["red-light", "intersection"]),
    ("Xe đạp đi ngược chiều ở đường hẹp", "Xe đạp đi ngược chiều sát tim đường hai làn hẹp, ego 30 km/h, mưa to ban đêm.", "Town02", "vehicle.mini.cooper_s", "cyclist", "HardRainNight", DangerLevel.MEDIUM, ["cyclist", "oncoming"]),
    ("Trẻ em chạy ra từ sau xe đỗ", "Trẻ em chạy ra từ sau ô tô đỗ ven đường khu dân cư, ego 30 km/h.", "Town01", "vehicle.audi.a2", "pedestrian", "ClearNoon", DangerLevel.CRITICAL, ["pedestrian", "occlusion", "residential"]),
    ("Xe khách chuyển làn đột ngột trên cao tốc", "Xe khách bên trái chuyển làn sang làn ego, khoảng cách 10 m, ego 70 km/h.", "Town04", "vehicle.lincoln.mkz_2020", "vehicle", "CloudyNoon", DangerLevel.HIGH, ["highway", "lane-change"]),
    ("Xe máy vượt phải ở vòng xuyến", "Xe máy vượt phải khi ego đi vào vòng xuyến, sương mù nhẹ.", "Town03", "vehicle.tesla.model3", "motorcycle", "SoftRainSunset", DangerLevel.MEDIUM, ["roundabout", "overtake"]),
    ("Xe phía trước dừng đột ngột khi tắc đường", "Xe con phía trước dừng gấp trong dòng xe đông, ego 25 km/h, khoảng cách 6 m.", "Town10HD_Opt", "vehicle.toyota.prius", "vehicle", "WetCloudyNoon", DangerLevel.MEDIUM, ["traffic-jam", "hard-brake"]),
    ("Người đi bộ băng qua vạch kẻ khi đèn xanh cho xe", "Người đi bộ băng qua vạch kẻ đường khi đèn đang xanh cho xe, ego 40 km/h.", "Town05", "vehicle.nissan.micra", "pedestrian", "ClearSunset", DangerLevel.HIGH, ["pedestrian", "crosswalk", "intersection"]),
    ("Xe rẽ trái cắt mặt tại ngã tư không đèn", "Xe ngược chiều rẽ trái cắt mặt ego tại ngã tư không đèn, ego 45 km/h.", "Town07", "vehicle.audi.a2", "vehicle", "ClearNoon", DangerLevel.HIGH, ["intersection", "left-turn"]),
    ("Xe đạp lạng lách giữa hai làn xe", "Xe đạp đi giữa hai làn rồi lạng sang làn ego, ego 35 km/h.", "Town10HD_Opt", "vehicle.mini.cooper_s", "cyclist", "CloudySunset", DangerLevel.MEDIUM, ["cyclist", "lane-split"]),
    ("Xe nhập làn từ đường nhánh cao tốc", "Xe từ đường nhánh nhập làn với tốc độ thấp ngay trước ego 80 km/h.", "Town04", "vehicle.tesla.model3", "vehicle", "MidRainyNoon", DangerLevel.CRITICAL, ["highway", "merge"]),
    ("Người đi bộ say rượu đi dọc lòng đường", "Người đi bộ đi lảo đảo dọc mép đường ban đêm, ego 40 km/h.", "Town02", "vehicle.toyota.prius", "pedestrian", "ClearNight", DangerLevel.HIGH, ["pedestrian", "night", "erratic"]),
    ("Xe máy chở hàng cồng kềnh lùi ra đường", "Xe máy chở hàng lùi ra từ hẻm nhỏ, ego 25 km/h.", "Town01", "vehicle.nissan.micra", "motorcycle", "WetNoon", DangerLevel.LOW, ["alley", "reverse"]),
    ("Ô tô quay đầu giữa đường", "Ô tô phía trước quay đầu bất ngờ ở dải phân cách mở, ego 50 km/h.", "Town05", "vehicle.lincoln.mkz_2020", "vehicle", "CloudyNoon", DangerLevel.HIGH, ["u-turn"]),
    ("Xe cứu thương vượt đèn đỏ", "Xe cứu thương vượt đèn đỏ từ bên phải tại ngã tư, ego 40 km/h.", "Town03", "vehicle.audi.a2", "vehicle", "ClearSunset", DangerLevel.CRITICAL, ["emergency", "intersection", "red-light"]),
    ("Người đi bộ mở cửa xe đỗ ven đường", "Cửa xe đỗ mở ra và người bước xuống ngay trước ego 30 km/h.", "Town10HD_Opt", "vehicle.mini.cooper_s", "pedestrian", "SoftRainNoon", DangerLevel.MEDIUM, ["dooring", "occlusion"]),
    ("Xe tải lùi ra từ bãi đỗ", "Xe tải lùi ra từ bãi đỗ bên phải, che khuất tầm nhìn, ego 20 km/h.", "Town07", "vehicle.toyota.prius", "vehicle", "ClearNoon", DangerLevel.LOW, ["parking", "reverse"]),
    ("Xe máy ngã trước mặt khi đường trơn", "Xe máy phía trước trượt ngã trên mặt đường ướt, ego 35 km/h.", "Town03", "vehicle.tesla.model3", "motorcycle", "HardRainNoon", DangerLevel.HIGH, ["rain", "fall"]),
    ("Xe con chạy chậm ở làn trái cao tốc", "Xe con chạy 40 km/h ở làn trái, ego 90 km/h phải giảm tốc.", "Town04", "vehicle.lincoln.mkz_2020", "vehicle", "ClearNoon", DangerLevel.LOW, ["highway", "slow-vehicle"]),
    ("Nhóm người đi bộ băng qua đường đông", "Nhóm 3 người đi bộ băng qua đường không vạch kẻ, ego 35 km/h, chạng vạng.", "Town05", "vehicle.nissan.micra", "pedestrian", "CloudySunset", DangerLevel.HIGH, ["pedestrian", "group"]),
    ("Xe đạp rẽ trái không xi nhan", "Xe đạp phía trước rẽ trái đột ngột không báo hiệu, ego 30 km/h.", "Town02", "vehicle.audi.a2", "cyclist", "WetCloudySunset", DangerLevel.MEDIUM, ["cyclist", "left-turn"]),
    ("Ô tô đi ngược chiều trên đường một chiều", "Ô tô đi ngược chiều trên đường một chiều ban đêm, ego 40 km/h.", "Town01", "vehicle.toyota.prius", "vehicle", "ClearNight", DangerLevel.CRITICAL, ["wrong-way", "night"]),
]


async def tag_for(session, project_id: int, name: str, cache: dict[str, Tag]) -> Tag:
    if name not in cache:
        tag = await session.scalar(select(Tag).where(Tag.project_id == project_id, Tag.name == name))
        if tag is None:
            tag = Tag(project_id=project_id, name=name)
            session.add(tag)
            await session.flush()
        cache[name] = tag
    return cache[name]


async def seed_project(session, project: Project) -> int:
    existing = set(
        (await session.scalars(select(TestCase.title).where(TestCase.project_id == project.id))).all()
    )
    tags: dict[str, Tag] = {}
    now = datetime.now(UTC)
    created = 0
    for index, (title, description, map_code, ego, adversary, environment, danger, tag_names) in enumerate(CASES):
        if title in existing:
            continue
        decided_at = now - timedelta(days=index % 12, hours=index * 3 % 24)
        submitted_at = decided_at - timedelta(hours=2)
        case = TestCase(
            project_id=project.id,
            case_key=f"TEMP-{secrets.token_hex(8).upper()}",
            title=title,
            description=description,
            created_by=project.created_by,
            versions=[],
        )
        session.add(case)
        await session.flush()
        case.case_key = f"TC-{case.id:06d}"
        version = TestCaseVersion(
            project_id=project.id,
            test_case_id=case.id,
            version_no=1,
            status=VersionStatus.APPROVED,
            map_code=map_code,
            ego_vehicle_code=ego,
            adversary_type=adversary,
            environment_code=environment,
            danger_level=danger,
            scenario_input={},
            change_note=SEED_NOTE,
            created_by=project.created_by,
            submitted_at=submitted_at,
            decided_at=decided_at,
            decided_by=project.created_by,
            tags=[await tag_for(session, project.id, name, tags) for name in tag_names],
        )
        session.add(version)
        await session.flush()
        review = ReviewRequest(
            project_id=project.id,
            version_id=version.id,
            requested_by=project.created_by,
            requested_at=submitted_at,
            resolved_by=project.created_by,
            resolved_at=decided_at,
            decision=ReviewDecision.APPROVED,
        )
        session.add(review)
        await session.flush()
        session.add(
            ReviewComment(
                project_id=project.id,
                review_request_id=review.id,
                creator_id=project.created_by,
                body="Dữ liệu mẫu: đã duyệt.",
                comment_type=CommentType.DECISION,
            )
        )
        created += 1
    return created


async def seed(project_code: str | None) -> None:
    async with SessionFactory() as session:
        statement = select(Project).where(Project.status == ProjectStatus.ACTIVE, Project.deleted_at.is_(None))
        if project_code:
            statement = statement.where(Project.code == project_code)
        projects = (await session.scalars(statement)).all()
        if not projects:
            print("No matching active project.")
            return
        for project in projects:
            count = await seed_project(session, project)
            print(f"Project {project.code}: added {count} approved test cases")
        await session.commit()


if __name__ == "__main__":
    asyncio.run(seed(sys.argv[1] if len(sys.argv) > 1 else None))
