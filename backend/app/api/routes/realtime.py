"""WebSocket endpoint streaming live updates for one event.

Protocol (JSON text frames):

server -> client
    {"type": "hello", "event_id": "...", "data": {<EventPublic>}}
    {"type": "photo.created", "event_id": "...", "data": {<PhotoPublic>}}
    {"type": "photo.deleted", "event_id": "...", "data": {"id": "..."}}
    {"type": "event.updated", "event_id": "...", "data": {<EventPublic>}}
    {"type": "event.deleted", "event_id": "..."}
    {"type": "pong"}

client -> server
    {"type": "ping"}   (optional heartbeat; anything else is ignored)

Subprotocols: clients should offer ``gambarkita.v1``. A host wall display may
also offer ``gambarkita.host.<host_token>`` to use the per-event slots
reserved for hosts. The token travels in a handshake header, never in the URL.

Clients should subscribe first and then fetch ``GET /api/events/{id}/photos``,
de-duplicating by photo id, so nothing uploaded in between is missed.
Close codes: 4404 unknown event (do not retry), 4410 event deleted (do not
retry), 4429 / 1013 rate or capacity limited (retry with backoff).
"""

import json

from fastapi import APIRouter, WebSocket
from starlette.websockets import WebSocketDisconnect

from app.core.errors import EventNotFoundError
from app.core.logging import get_logger
from app.core.rate_limit import client_ip
from app.core.security import is_plausible_token, verify_token
from app.models import EventRecord
from app.presenters import present_event
from app.services.container import Services
from app.services.realtime import (
    CLOSE_EVENT_DELETED,
    CLOSE_EVENT_NOT_FOUND,
    CLOSE_MESSAGE_TOO_BIG,
    CLOSE_POLICY_VIOLATION,
    CLOSE_RATE_LIMITED,
    CLOSE_TRY_AGAIN_LATER,
    CLOSE_UNSUPPORTED_DATA,
    WS_HOST_SUBPROTOCOL_PREFIX,
    WS_SUBPROTOCOL,
    CapacityExceeded,
)

router = APIRouter()
logger = get_logger("ws")


def _offered_subprotocols(websocket: WebSocket) -> list[str]:
    return [str(protocol) for protocol in websocket.scope.get("subprotocols", [])]


def _offered_host_token(offered: list[str]) -> str | None:
    for protocol in offered:
        if protocol.startswith(WS_HOST_SUBPROTOCOL_PREFIX):
            token = protocol[len(WS_HOST_SUBPROTOCOL_PREFIX) :]
            return token if is_plausible_token(token) else None
    return None


async def _reject(websocket: WebSocket, subprotocol: str | None, code: int, reason: str) -> None:
    # Accept first so the browser receives the application close code and reason
    # (a handshake rejection only surfaces as an opaque error).
    await websocket.accept(subprotocol=subprotocol)
    await websocket.close(code=code, reason=reason)


@router.websocket("/ws/events/{event_id}")
async def event_updates(websocket: WebSocket, event_id: str) -> None:
    services: Services = websocket.app.state.services
    guard = services.ws_guard
    ip = client_ip(websocket)
    offered = _offered_subprotocols(websocket)
    # Browsers fail the handshake if they offered subprotocols and none is selected.
    subprotocol = WS_SUBPROTOCOL if WS_SUBPROTOCOL in offered else None

    if not guard.origin_allowed(websocket.headers.get("origin")):
        logger.warning("Rejected websocket from disallowed origin %r", websocket.headers.get("origin"))
        await websocket.close(code=CLOSE_POLICY_VIOLATION)  # before accept: HTTP 403 handshake
        return

    # A verified host is charged to a per-event host bucket, so guests flooding
    # the shared per-IP attempt budget (at a venue everyone shares one IP) cannot
    # lock the wall display out. Everyone else, including clients offering an
    # invalid token, pays the per-IP attempt budget.
    event: EventRecord | None = None
    looked_up = False
    is_host = False
    host_token = _offered_host_token(offered)
    # Token checks read the database, so they have their own per-IP budget,
    # charged before the read; when it is exhausted the client is treated as a guest.
    if host_token is not None and guard.allow_host_verification(ip):
        looked_up = True
        try:
            event = await services.events.get(event_id)
        except EventNotFoundError:
            event = None
        is_host = event is not None and verify_token(host_token, event.host_token_hash)
    allowed = guard.allow_host_connection_attempt(event_id) if is_host else guard.allow_connection_attempt(ip)
    if not allowed:
        await _reject(websocket, subprotocol, CLOSE_RATE_LIMITED, "Too many connection attempts")
        return
    if event is None:
        try:
            if looked_up:
                raise EventNotFoundError()
            event = await services.events.get(event_id)
        except EventNotFoundError:
            await _reject(websocket, subprotocol, CLOSE_EVENT_NOT_FOUND, "Event not found")
            return
    try:
        subscriber = services.realtime.register(websocket, event.id, ip, host=is_host)
    except CapacityExceeded as exc:
        await _reject(websocket, subprotocol, CLOSE_TRY_AGAIN_LATER, str(exc))
        return

    try:
        await websocket.accept(subprotocol=subprotocol)
        services.realtime.start(subscriber)
        # Re-read after registering: an event deleted between the lookup above and
        # register() would otherwise leave this socket attached to a deleted event.
        # A deletion committed after this read reaches us through close_event().
        summary = await services.db.get_event_summary(event.id)
        if summary is None:
            services.realtime.request_close(subscriber, CLOSE_EVENT_DELETED, "Event deleted")
        else:
            current, photo_count = summary
            services.realtime.send(
                subscriber,
                {
                    "type": "hello",
                    "event_id": current.id,
                    "data": present_event(current, photo_count, services.settings).model_dump(mode="json"),
                },
            )
        budget = guard.message_budget()
        while True:
            message = await websocket.receive()
            if message["type"] == "websocket.disconnect":
                break
            text = message.get("text")
            if text is None:
                await services.realtime.close(subscriber, CLOSE_UNSUPPORTED_DATA, "Text frames only")
                break
            if len(text.encode("utf-8")) > guard.max_message_bytes:
                await services.realtime.close(subscriber, CLOSE_MESSAGE_TOO_BIG, "Message too large")
                break
            if budget is not None and not budget.consume():
                await services.realtime.close(subscriber, CLOSE_POLICY_VIOLATION, "Too many messages")
                break
            try:
                payload = json.loads(text)
            except ValueError:
                continue
            if isinstance(payload, dict) and payload.get("type") == "ping":
                services.realtime.send(subscriber, {"type": "pong"})
    except WebSocketDisconnect:
        pass
    finally:
        await services.realtime.unregister(subscriber)
