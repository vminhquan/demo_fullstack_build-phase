from __future__ import annotations

from types import SimpleNamespace

import pytest
from sqlalchemy.dialects import postgresql

from app.modules.bridge.router import bearer_token
from app.modules.bridge.runs_router import job_view
from app.modules.bridge.service import (
    AttemptLimiter, TooManyAttempts, _replaced_connections_query, hash_code, hash_device_token, new_code, new_uid,
)
from app.modules.bridge.verdict import collision_verdict
from app.shared.infrastructure.models import RunVerdict


def test_codes_are_six_digits_with_leading_zeros_kept() -> None:
    codes = {new_code() for _ in range(500)}
    assert all(len(code) == 6 and code.isdigit() for code in codes)
    assert len(codes) > 400  # random, not a counter


def test_code_hash_is_keyed_and_stable() -> None:
    assert hash_code("012345") == hash_code("012345")
    assert hash_code("012345") != hash_code("012346")
    # Keyed (HMAC), so it is not the plain sha256 a leaked table could be brute-forced against.
    assert hash_code("012345") != hash_device_token("012345")


def test_uids_carry_prefix() -> None:
    assert new_uid("CONN").startswith("CONN-") and len(new_uid("CONN")) == 15


def test_limiter_blocks_after_limit_and_window_expires() -> None:
    limiter = AttemptLimiter(limit=3, window_seconds=60)
    for moment in (0, 1, 2):
        limiter.check("1.2.3.4", at=moment)
        limiter.fail("1.2.3.4", at=moment)
    with pytest.raises(TooManyAttempts):
        limiter.check("1.2.3.4", at=3)
    limiter.check("5.6.7.8", at=3)  # other clients unaffected
    limiter.check("1.2.3.4", at=100)  # window passed


def test_limiter_reset_after_success() -> None:
    limiter = AttemptLimiter(limit=1, window_seconds=60)
    limiter.fail("ip", at=0)
    limiter.reset("ip")
    limiter.check("ip", at=1)


def test_bearer_token_parsing() -> None:
    assert bearer_token("Bearer abc") == "abc"
    assert bearer_token("bearer  abc ") == "abc"
    assert bearer_token("Basic abc") is None
    assert bearer_token(None) is None


def test_replaced_connections_lock_only_the_connection_table() -> None:
    # The ORM eagerly outer-joins bridges; PostgreSQL rejects a bare FOR UPDATE on that query.
    sql = str(_replaced_connections_query(1, 2).compile(dialect=postgresql.dialect()))
    assert "LEFT OUTER JOIN bridges" in sql
    assert "FOR UPDATE OF bridge_connections" in sql


def test_collision_criterion_corrects_legacy_bridge_verdict() -> None:
    no_collision = {"collision": False, "criteria": [
        {"name": "CollisionTest", "actual": 0}, {"name": "KeepLaneTest", "actual": 1, "success": False},
    ]}
    assert collision_verdict(RunVerdict.FAIL, no_collision) is RunVerdict.PASS
    assert collision_verdict(RunVerdict.PASS, {"criteria": [{"name": "CollisionTest", "actual": 1}]}) is RunVerdict.FAIL
    assert collision_verdict(RunVerdict.ERROR, no_collision) is RunVerdict.ERROR
    assert collision_verdict(RunVerdict.FAIL, {"criteria": [{"name": "Duration", "actual": 10}]}) is RunVerdict.FAIL


def test_simulator_result_page_shows_no_collision_as_pass() -> None:
    from app.shared.infrastructure.models import DangerLevel, RunJobStatus

    case = SimpleNamespace(id=7, case_key="TC-000007", title="Tạt đầu", map_code="Town03",
                           adversary_type="motorcycle", environment_code="HardRainNoon", danger_level=DangerLevel.LOW,
                           revision=1)
    job = SimpleNamespace(status=RunJobStatus.COMPLETED, error_code=None, error_message=None,
                          started_at=None, finished_at=None)
    result = SimpleNamespace(revision=1, verdict=RunVerdict.FAIL,
                             metrics={"collision": False, "collision_count": 0,
                                      "criteria": [{"name": "CollisionTest", "actual": 0}]},
                             duration_ms=10000, scenario_runner_exit_code=0)
    view = job_view(job, case, result)
    assert view.verdict is RunVerdict.PASS and view.collision is False


def test_gunzip_limited_accepts_catalogs_and_rejects_bombs_and_garbage() -> None:
    import gzip

    from app.modules.bridge.router import gunzip_limited
    from app.shared.domain.errors import ValidationFailed

    body = b'{"catalog": {"waypoints": [' + b",".join([b'{"x": 1.0, "y": 2.0}'] * 2000) + b"]}}"
    assert gunzip_limited(gzip.compress(body), limit=len(body)) == body
    bomb = gzip.compress(b"0" * 5_000_000)  # ~5 KB on the wire
    with pytest.raises(ValidationFailed, match="exceeds"):
        gunzip_limited(bomb, limit=1_000_000)
    with pytest.raises(ValidationFailed, match="gzip"):
        gunzip_limited(b"not gzip", limit=1000)


def test_agent_link_is_offered_only_when_configured(monkeypatch) -> None:
    import hashlib
    import hmac

    from app.modules.bridge import service
    from app.shared.config import Settings

    monkeypatch.setattr(service, "get_settings", lambda: Settings(agent_bridge_ws_url="", bridge_agent_secret="s3cret"))
    assert service.agent_link("BRG-1") is None
    monkeypatch.setattr(service, "get_settings", lambda: Settings(agent_bridge_ws_url="ws://agent2:8100/v1/bridge/ws",
                                                                    bridge_agent_secret="s3cret"))
    link = service.agent_link("BRG-1")
    assert link["ws_url"] == "ws://agent2:8100/v1/bridge/ws"
    # The format agent2's verify_bridge_token checks: <bridge_uid>.<expires>.<hmac-sha256 of "<bridge_uid>.<expires>">.
    uid, expires, signature = link["token"].rsplit(".", 2)
    assert uid == "BRG-1" and int(expires) > 0
    assert signature == hmac.new(b"s3cret", f"{uid}.{expires}".encode(), hashlib.sha256).hexdigest()


def test_a_simulator_run_must_name_a_known_ads() -> None:
    import asyncio

    from pydantic import ValidationError

    from app.modules.bridge.runs_router import list_ads
    from app.modules.bridge.schemas import SimulatorRunCreate

    assert SimulatorRunCreate(connection_uid="CONN-1", test_case_ids=[1], ads="simple").ads == "simple"
    for body in ({"connection_uid": "CONN-1", "test_case_ids": [1]},
                 {"connection_uid": "CONN-1", "test_case_ids": [1], "ads": "autoware"}):
        with pytest.raises(ValidationError):
            SimulatorRunCreate(**body)
    assert [option.id for option in asyncio.run(list_ads(actor=None))] == ["simple"]
