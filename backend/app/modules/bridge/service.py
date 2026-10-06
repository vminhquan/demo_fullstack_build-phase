from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from collections import defaultdict, deque
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.bridge.schemas import BridgePairRequest, ConnectionInfo
from app.shared.config import get_settings
from app.shared.domain.errors import DomainError, NotFound, Unauthorized, ValidationFailed
from app.shared.infrastructure.models import Bridge, BridgeConnection, BridgePairCode, Project

PAIR_CODE_TTL = timedelta(minutes=10)
CODE_DIGITS = 6


class TooManyAttempts(DomainError):
    code, status_code = "TOO_MANY_ATTEMPTS", 429


def now() -> datetime:
    return datetime.now(UTC)


# The Bridge sends a heartbeat every 15 s; three missed beats means the process holding it is gone.
ONLINE_WINDOW = timedelta(seconds=45)


def bridge_online(bridge: Bridge) -> bool:
    """Online in any backend process: a process holds its socket and heard from it recently."""
    from app.modules.bridge.hub import hub

    if hub.is_local(bridge.id):
        return True
    return bool(bridge.online_since and bridge.last_seen_at and now() - bridge.last_seen_at < ONLINE_WINDOW)


def new_code() -> str:
    return f"{secrets.randbelow(10**CODE_DIGITS):0{CODE_DIGITS}d}"


def hash_code(code: str) -> str:
    """Keyed hash: a leaked table cannot be reversed by trying all 10^6 codes without the server secret."""
    return hmac.new(get_settings().jwt_secret.encode(), f"bridge-pair:{code}".encode(), hashlib.sha256).hexdigest()


def hash_device_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def new_uid(prefix: str) -> str:
    return f"{prefix}-{secrets.token_hex(5).upper()}"


class AttemptLimiter:
    """Failed `pair` attempts per client address (in memory): 10^6 codes must not be guessable by brute force."""

    def __init__(self, limit: int = 10, window_seconds: float = 600) -> None:
        self.limit = limit
        self.window = window_seconds
        self._failures: dict[str, deque[float]] = defaultdict(deque)

    def _trim(self, key: str, at: float) -> deque[float]:
        failures = self._failures[key]
        while failures and at - failures[0] > self.window:
            failures.popleft()
        return failures

    def check(self, key: str, at: float | None = None) -> None:
        if len(self._trim(key, time.monotonic() if at is None else at)) >= self.limit:
            raise TooManyAttempts("Nhập sai mã quá nhiều lần. Hãy thử lại sau ít phút.")

    def fail(self, key: str, at: float | None = None) -> None:
        moment = time.monotonic() if at is None else at
        self._trim(key, moment).append(moment)

    def reset(self, key: str) -> None:
        self._failures.pop(key, None)


limiter = AttemptLimiter()


async def issue_pair_code(session: AsyncSession, project_id: int, user_id: int) -> tuple[BridgePairCode, str]:
    # Only one open code per project: issuing a new one cancels the previous one.
    await session.execute(
        update(BridgePairCode)
        .where(BridgePairCode.project_id == project_id, BridgePairCode.used_at.is_(None), BridgePairCode.cancelled_at.is_(None))
        .values(cancelled_at=now())
    )
    for _ in range(20):
        code = new_code()
        clash = await session.scalar(
            select(BridgePairCode.id).where(
                BridgePairCode.code_hash == hash_code(code),
                BridgePairCode.expires_at > now(),
                BridgePairCode.used_at.is_(None),
                BridgePairCode.cancelled_at.is_(None),
            )
        )
        if clash is None:
            break
    else:  # pragma: no cover - 20 clashes in a row needs ~10^6 open codes
        raise ValidationFailed("Không tạo được mã ghép nối, hãy thử lại")
    row = BridgePairCode(project_id=project_id, created_by=user_id, code_hash=hash_code(code), expires_at=now() + PAIR_CODE_TTL)
    session.add(row)
    await session.flush()
    return row, code


async def cancel_pair_code(session: AsyncSession, project_id: int, code_id: int) -> None:
    row = await session.scalar(select(BridgePairCode).where(BridgePairCode.id == code_id, BridgePairCode.project_id == project_id))
    if row is None:
        raise NotFound("Pair code not found")
    if row.used_at is None and row.cancelled_at is None:
        row.cancelled_at = now()


async def redeem_pair_code(
    session: AsyncSession, body: BridgePairRequest, device_token: str | None
) -> tuple[Bridge, BridgeConnection, BridgePairCode, Project, str | None]:
    """Validates the OTP, registers the machine (or reuses it from its device token) and links it to the project."""
    pair = await session.scalar(
        select(BridgePairCode)
        .where(
            BridgePairCode.code_hash == hash_code(body.code),
            BridgePairCode.expires_at > now(),
            BridgePairCode.used_at.is_(None),
            BridgePairCode.cancelled_at.is_(None),
        )
        .with_for_update()
    )
    if pair is None:
        raise ValidationFailed("Mã ghép nối không đúng hoặc đã hết hạn")
    project = await session.get(Project, pair.project_id)
    if project is None or project.deleted_at is not None:
        raise ValidationFailed("Project của mã ghép nối không còn tồn tại")

    bridge = None
    if device_token:
        bridge = await session.scalar(select(Bridge).where(Bridge.token_hash == hash_device_token(device_token), Bridge.revoked_at.is_(None)))
    new_token = None
    if bridge is None:
        new_token = secrets.token_urlsafe(32)
        bridge = Bridge(uid=new_uid("BRG"), token_hash=hash_device_token(new_token), name=body.name or body.hostname or "Scenario Forge Bridge")
        session.add(bridge)
    bridge.name = body.name or bridge.name
    bridge.hostname = body.hostname or bridge.hostname
    bridge.os = body.os or bridge.os
    bridge.bridge_version = body.bridge_version or bridge.bridge_version
    bridge.last_seen_at = now()
    if body.carla:
        bridge.carla_host, bridge.carla_port, bridge.carla_reachable = body.carla.host, body.carla.port, body.carla.reachable
    await session.flush()

    connection = await session.scalar(
        select(BridgeConnection).where(BridgeConnection.bridge_id == bridge.id, BridgeConnection.project_id == project.id)
    )
    if connection is None:
        connection = BridgeConnection(uid=new_uid("CONN"), bridge_id=bridge.id, project_id=project.id, paired_by=pair.created_by)
        session.add(connection)
    else:
        # Pairing again after an unpair: same machine and project keep their connection id.
        connection.revoked_at = None
        connection.paired_by = pair.created_by
        connection.paired_at = now()
    await session.flush()
    pair.used_at = now()
    pair.connection_id = connection.id
    return bridge, connection, pair, project, new_token


async def bridge_from_token(session: AsyncSession, token: str | None) -> Bridge:
    if not token:
        raise Unauthorized("Bridge token is required")
    bridge = await session.scalar(select(Bridge).where(Bridge.token_hash == hash_device_token(token), Bridge.revoked_at.is_(None)))
    if bridge is None:
        raise Unauthorized("Bridge token is invalid or revoked")
    return bridge


async def active_connections(session: AsyncSession, bridge_id: int) -> list[ConnectionInfo]:
    rows = (
        await session.execute(
            select(BridgeConnection, Project.name)
            .join(Project, Project.id == BridgeConnection.project_id)
            .where(BridgeConnection.bridge_id == bridge_id, BridgeConnection.revoked_at.is_(None), Project.deleted_at.is_(None))
            .order_by(BridgeConnection.paired_at)
        )
    ).tuples().all()
    return [ConnectionInfo(connection_uid=row.uid, project_id=row.project_id, project_name=name, paired_at=row.paired_at) for row, name in rows]
