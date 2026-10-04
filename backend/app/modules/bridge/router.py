from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Header, Request, WebSocket, WebSocketDisconnect, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.bridge.hub import hub
from app.modules.bridge.schemas import (
    BridgeConnectionResponse,
    BridgePairRequest,
    BridgePairResponse,
    ConnectionInfo,
    PairCodeResponse,
)
from app.modules.bridge.service import (
    PAIR_CODE_TTL,
    active_connections,
    bridge_from_token,
    cancel_pair_code,
    issue_pair_code,
    limiter,
    now,
    redeem_pair_code,
)
from app.modules.identity.dependencies import Principal, principal_from_token, require
from app.shared.domain.errors import DomainError, NotFound, ValidationFailed
from app.shared.infrastructure.audit import record_audit
from app.shared.infrastructure.db import SessionFactory, get_session
from app.shared.infrastructure.models import Bridge, BridgeConnection, User

logger = logging.getLogger(__name__)

# Any project member may pair, list, use and unpair the project's Bridges (no per-Bridge permissions).
router = APIRouter(prefix="/bridges", tags=["bridges"])
# Called by the Bridge itself (device token, not a user) and by browsers over WebSocket (token in the query).
public_router = APIRouter(tags=["bridges"])


def bearer_token(value: str | None) -> str | None:
    if value and value.lower().startswith("bearer "):
        return value[7:].strip() or None
    return None


def connection_view(connection: BridgeConnection, names: dict[int, str]) -> BridgeConnectionResponse:
    bridge = connection.bridge
    return BridgeConnectionResponse(
        connection_uid=connection.uid,
        bridge_uid=bridge.uid,
        name=bridge.name,
        hostname=bridge.hostname,
        os=bridge.os,
        bridge_version=bridge.bridge_version,
        online=hub.is_online(bridge.id),
        carla_host=bridge.carla_host,
        carla_port=bridge.carla_port,
        carla_reachable=bridge.carla_reachable,
        last_seen_at=bridge.last_seen_at,
        paired_at=connection.paired_at,
        paired_by=connection.paired_by,
        paired_by_name=names.get(connection.paired_by),
    )


async def project_connections(session: AsyncSession, project_id: int) -> list[BridgeConnectionResponse]:
    connections = (
        await session.scalars(
            select(BridgeConnection)
            .join(Bridge, Bridge.id == BridgeConnection.bridge_id)
            .where(BridgeConnection.project_id == project_id, BridgeConnection.revoked_at.is_(None), Bridge.revoked_at.is_(None))
            .order_by(BridgeConnection.paired_at)
        )
    ).all()
    user_ids = {item.paired_by for item in connections}
    users = (await session.scalars(select(User).where(User.id.in_(user_ids)))).all() if user_ids else []
    names = {user.id: user.display_name or user.email for user in users}
    return [connection_view(item, names) for item in connections]


async def publish_bridge_status(session: AsyncSession, bridge: Bridge, event: str) -> None:
    """Tells every project linked to the Bridge that its state changed (online / offline / CARLA probe)."""
    for connection in await active_connections(session, bridge.id):
        await hub.publish_project(
            connection.project_id,
            {
                "type": event,
                "connection_uid": connection.connection_uid,
                "bridge_uid": bridge.uid,
                "online": hub.is_online(bridge.id),
                "carla_reachable": bridge.carla_reachable,
            },
        )


# ---- Frontend (project-scoped REST) ----


@router.post("/pair-codes", response_model=PairCodeResponse, status_code=status.HTTP_201_CREATED)
async def create_pair_code(actor: Principal = Depends(require("testcase:read")), session: AsyncSession = Depends(get_session)) -> PairCodeResponse:
    row, code = await issue_pair_code(session, actor.project_id, actor.id)
    await session.commit()
    return PairCodeResponse(id=row.id, code=code, expires_at=row.expires_at, ttl_seconds=int(PAIR_CODE_TTL.total_seconds()))


@router.delete("/pair-codes/{code_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_pair_code(code_id: int, actor: Principal = Depends(require("testcase:read")), session: AsyncSession = Depends(get_session)) -> None:
    await cancel_pair_code(session, actor.project_id, code_id)
    await session.commit()


@router.get("", response_model=list[BridgeConnectionResponse])
async def list_bridges(actor: Principal = Depends(require("testcase:read")), session: AsyncSession = Depends(get_session)) -> list[BridgeConnectionResponse]:
    return await project_connections(session, actor.project_id)


@router.delete("/{connection_uid}", status_code=status.HTTP_204_NO_CONTENT)
async def unpair_bridge(connection_uid: str, actor: Principal = Depends(require("testcase:read")), session: AsyncSession = Depends(get_session)) -> None:
    connection = await session.scalar(
        select(BridgeConnection).where(
            BridgeConnection.uid == connection_uid,
            BridgeConnection.project_id == actor.project_id,
            BridgeConnection.revoked_at.is_(None),
        )
    )
    if connection is None:
        raise NotFound("Bridge connection not found")
    connection.revoked_at = now()
    await record_audit(
        session, project_id=actor.project_id, actor_user_id=actor.id, action="bridge.unpair",
        entity_type="bridge_connection", entity_id=connection.id, before_data={"connection_uid": connection.uid},
    )
    await session.commit()
    await hub.send_bridge(connection.bridge_id, {"type": "connection.revoked", "connection_uid": connection.uid, "project_id": actor.project_id})
    await hub.publish_project(actor.project_id, {"type": "bridge.unpaired", "connection_uid": connection.uid})


# ---- Bridge (device) ----


@public_router.post("/bridge/pair", response_model=BridgePairResponse)
async def pair_bridge(
    body: BridgePairRequest,
    request: Request,
    authorization: str | None = Header(default=None),
    session: AsyncSession = Depends(get_session),
) -> BridgePairResponse:
    """`pair <code>` on the Bridge: redeems the OTP and links the machine to the code's project."""
    client = request.client.host if request.client else "unknown"
    limiter.check(client)
    try:
        bridge, connection, pair, project, new_token = await redeem_pair_code(session, body, bearer_token(authorization))
    except ValidationFailed:
        limiter.fail(client)
        raise
    await record_audit(
        session, project_id=project.id, actor_user_id=pair.created_by, action="bridge.pair",
        entity_type="bridge_connection", entity_id=connection.id,
        after_data={"connection_uid": connection.uid, "bridge_uid": bridge.uid, "hostname": bridge.hostname},
    )
    await session.commit()
    limiter.reset(client)

    info = ConnectionInfo(connection_uid=connection.uid, project_id=project.id, project_name=project.name, paired_at=connection.paired_at)
    paired = next((item for item in await project_connections(session, project.id) if item.connection_uid == connection.uid), None)
    await hub.publish_project(
        project.id,
        {"type": "bridge.paired", "pair_code_id": pair.id, "connection": paired.model_dump(mode="json") if paired else None},
    )
    # A Bridge already online (paired to other projects) starts serving this project without reconnecting.
    await hub.send_bridge(bridge.id, {"type": "connection.added", "connection": info.model_dump(mode="json")})
    return BridgePairResponse(bridge_uid=bridge.uid, device_token=new_token, connection=info)


@public_router.delete("/bridge/connections/{connection_uid}", status_code=status.HTTP_204_NO_CONTENT)
async def bridge_unpair(
    connection_uid: str, authorization: str | None = Header(default=None), session: AsyncSession = Depends(get_session)
) -> None:
    """`unpair` on the Bridge: the machine leaves one project."""
    bridge = await bridge_from_token(session, bearer_token(authorization))
    connection = await session.scalar(
        select(BridgeConnection).where(
            BridgeConnection.uid == connection_uid, BridgeConnection.bridge_id == bridge.id, BridgeConnection.revoked_at.is_(None)
        )
    )
    if connection is None:
        raise NotFound("Bridge connection not found")
    connection.revoked_at = now()
    await record_audit(
        session, project_id=connection.project_id, actor_user_id=None, action="bridge.unpair",
        entity_type="bridge_connection", entity_id=connection.id, before_data={"connection_uid": connection.uid, "by": "bridge"},
    )
    await session.commit()
    await hub.publish_project(connection.project_id, {"type": "bridge.unpaired", "connection_uid": connection.uid})


@public_router.websocket("/bridge/ws")
async def bridge_socket(websocket: WebSocket) -> None:
    """Long-lived Bridge channel. Auth: `Authorization: Bearer <device token>`."""
    try:
        async with SessionFactory() as session:
            bridge = await bridge_from_token(session, bearer_token(websocket.headers.get("authorization")))
            bridge_id = bridge.id
    except DomainError:
        await websocket.close(code=1008, reason="invalid bridge token")
        return
    await websocket.accept()
    previous = await hub.attach_bridge(bridge_id, websocket)
    if previous is not None:
        await previous.close(code=4000, reason="replaced by a newer connection")
    try:
        async with SessionFactory() as session:
            bridge = await session.get(Bridge, bridge_id)
            bridge.last_seen_at = now()
            await session.commit()
            connections = await active_connections(session, bridge_id)
            await websocket.send_json(
                {"type": "bridge.welcome", "bridge_uid": bridge.uid, "connections": [item.model_dump(mode="json") for item in connections]}
            )
            await publish_bridge_status(session, bridge, "bridge.online")
        while True:
            message = await websocket.receive_json()
            kind = message.get("type")
            if kind == "bridge.heartbeat":
                carla = message.get("carla") or {}
                async with SessionFactory() as session:
                    bridge = await session.get(Bridge, bridge_id)
                    if bridge is None or bridge.revoked_at is not None:
                        await websocket.close(code=1008, reason="bridge revoked")
                        return
                    changed = "reachable" in carla and carla.get("reachable") != bridge.carla_reachable
                    if "reachable" in carla:
                        bridge.carla_reachable = bool(carla["reachable"])
                        bridge.carla_host = str(carla.get("host") or bridge.carla_host or "")[:255] or None
                        port = carla.get("port")
                        bridge.carla_port = port if isinstance(port, int) else bridge.carla_port
                    bridge.last_seen_at = now()
                    await session.commit()
                    if changed:
                        await publish_bridge_status(session, bridge, "bridge.status")
                await websocket.send_json({"type": "bridge.heartbeat.ack", "server_time": now().isoformat()})
            else:
                await websocket.send_json({"type": "error", "code": "UNSUPPORTED_MESSAGE", "ref_type": kind})
    except WebSocketDisconnect:
        pass
    except Exception:  # noqa: BLE001 - malformed frames close this Bridge only
        logger.exception("Bridge %s socket failed", bridge_id)
    finally:
        if await hub.detach_bridge(bridge_id, websocket):
            async with SessionFactory() as session:
                bridge = await session.get(Bridge, bridge_id)
                if bridge is not None:
                    await publish_bridge_status(session, bridge, "bridge.offline")


# ---- Frontend live events ----


@public_router.websocket("/projects/{project_id}/bridges/events")
async def bridge_events(websocket: WebSocket, project_id: int) -> None:
    """Start up / Test Suite pages: pair success, online/offline and CARLA status of the project's Bridges.

    Browsers cannot set headers on WebSockets, so the access token comes in `?token=`.
    """
    try:
        async with SessionFactory() as session:
            await principal_from_token(session, websocket.query_params.get("token"), project_id)
    except DomainError:
        await websocket.close(code=1008, reason="unauthorized")
        return
    await websocket.accept()
    await hub.add_listener(project_id, websocket)
    try:
        await websocket.send_json({"type": "ready"})
        while True:
            # The page only listens; reading keeps the socket alive and notices the tab closing.
            message = await websocket.receive_text()
            if message == "ping":
                await websocket.send_text("pong")
    except WebSocketDisconnect:
        pass
    finally:
        await hub.remove_listener(project_id, websocket)
