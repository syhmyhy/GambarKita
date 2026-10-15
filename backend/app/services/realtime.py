"""Real-time fan-out of event updates over WebSockets.

Design:

* Each subscriber has a bounded outbound queue drained by its own writer task.
  Publishing never awaits network I/O, so one slow client cannot stall a broadcast.
  A client whose queue overflows is disconnected (close code 1013).
* A single writer per socket preserves message order and avoids concurrent sends.
* Connection caps (global, per event, per IP), a per-IP connection-attempt rate
  limit, a per-connection inbound message budget and an Origin allow-list
  (browsers do not apply CORS to WebSockets) protect against abuse.
* A few extra per-event slots are reserved for host-authenticated wall
  displays, so anonymous viewers cannot lock the host out of their own wall.
  The host proves identity by offering the subprotocol
  ``gambarkita.host.<host_token>`` next to ``gambarkita.v1``.

State lives in process memory, so run a single worker process. Scaling out
would need a shared pub/sub backend (for example Redis) behind ``publish``.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from limits import parse
from limits.storage import MemoryStorage
from limits.strategies import SlidingWindowCounterRateLimiter
from starlette.websockets import WebSocket, WebSocketState

from app.core.config import Settings, normalize_origin
from app.core.logging import get_logger

logger = get_logger("realtime")

# Close codes. 4000-4999 are application-defined; the frontend uses them to
# decide whether reconnecting makes sense.
CLOSE_GOING_AWAY = 1001
CLOSE_UNSUPPORTED_DATA = 1003
CLOSE_POLICY_VIOLATION = 1008
CLOSE_MESSAGE_TOO_BIG = 1009
CLOSE_INTERNAL_ERROR = 1011
CLOSE_TRY_AGAIN_LATER = 1013
CLOSE_EVENT_NOT_FOUND = 4404
CLOSE_EVENT_DELETED = 4410
CLOSE_RATE_LIMITED = 4429

_CLOSE_TIMEOUT = 5.0

WS_SUBPROTOCOL = "gambarkita.v1"
WS_HOST_SUBPROTOCOL_PREFIX = "gambarkita.host."


class CapacityExceeded(Exception):
    """Raised when accepting a connection would exceed a configured cap."""


@dataclass(frozen=True, slots=True)
class _CloseRequest:
    code: int
    reason: str


@dataclass(eq=False, slots=True)
class Subscriber:
    websocket: WebSocket
    event_id: str
    client_ip: str
    queue: asyncio.Queue[str | _CloseRequest]
    writer: asyncio.Task[None] | None = None
    closing: bool = False
    host_slot: bool = False  # holds one of the event's reserved host slots
    connected_at: float = field(default_factory=time.monotonic)


def encode_message(message: Mapping[str, Any]) -> str:
    return json.dumps(message, separators=(",", ":"), ensure_ascii=False, default=str)


class ConnectionManager:
    def __init__(
        self,
        *,
        max_total: int,
        max_per_event: int,
        max_per_ip: int,
        queue_size: int,
        send_timeout: float,
        reserved_host_slots: int = 0,
    ) -> None:
        self._max_total = max_total
        self._max_per_event = max_per_event
        self._max_per_ip = max_per_ip
        self._reserved_host_slots = reserved_host_slots
        self._queue_size = queue_size
        self._send_timeout = send_timeout
        self._subscribers: dict[str, set[Subscriber]] = {}
        self._per_ip: dict[str, int] = {}
        self._host_slots: dict[str, int] = {}  # event id -> reserved host slots in use
        self._total = 0
        self._background: set[asyncio.Task[None]] = set()

    @classmethod
    def from_settings(cls, settings: Settings) -> ConnectionManager:
        return cls(
            max_total=settings.ws_max_connections_total,
            max_per_event=settings.ws_max_connections_per_event,
            max_per_ip=settings.ws_max_connections_per_ip,
            queue_size=settings.ws_send_queue_size,
            send_timeout=settings.ws_send_timeout,
            reserved_host_slots=settings.ws_reserved_host_connections,
        )

    # --- Introspection ------------------------------------------------------------------

    @property
    def total_connections(self) -> int:
        return self._total

    def connections_for_event(self, event_id: str) -> int:
        return len(self._subscribers.get(event_id, ()))

    # --- Lifecycle ------------------------------------------------------------------------

    def register(self, websocket: WebSocket, event_id: str, client_ip: str, *, host: bool = False) -> Subscriber:
        """Reserve a slot for a new connection (call before ``accept``).

        A verified host connection may take one of the event's reserved host
        slots, which sit beyond the per-event cap. A network may exceed its
        per-IP cap by at most ``reserved_host_slots`` in total, so a client
        cannot multiply its connections by creating many events of its own.
        Further host connections are treated like anyone else. The global cap
        applies to all.
        """
        in_use = self._host_slots.get(event_id, 0)
        host_slot = host and in_use < self._reserved_host_slots
        if self._total >= self._max_total:
            raise CapacityExceeded("The server has too many live connections.")
        if host_slot:
            if self.connections_for_event(event_id) >= self._max_per_event + self._reserved_host_slots:
                raise CapacityExceeded("This event has too many live viewers.")
            if self._per_ip.get(client_ip, 0) >= self._max_per_ip + self._reserved_host_slots:
                raise CapacityExceeded("Too many live connections from your network.")
        else:
            if self.connections_for_event(event_id) - in_use >= self._max_per_event:
                raise CapacityExceeded("This event has too many live viewers.")
            if self._per_ip.get(client_ip, 0) >= self._max_per_ip:
                raise CapacityExceeded("Too many live connections from your network.")
        subscriber = Subscriber(websocket, event_id, client_ip, asyncio.Queue(maxsize=self._queue_size))
        subscriber.host_slot = host_slot
        self._subscribers.setdefault(event_id, set()).add(subscriber)
        self._per_ip[client_ip] = self._per_ip.get(client_ip, 0) + 1
        if host_slot:
            self._host_slots[event_id] = in_use + 1
        self._total += 1
        return subscriber

    def start(self, subscriber: Subscriber) -> None:
        """Start the writer task (call after ``accept``)."""
        if subscriber.writer is None:
            subscriber.writer = asyncio.create_task(
                self._write_loop(subscriber), name=f"ws-writer:{subscriber.event_id}"
            )

    async def unregister(self, subscriber: Subscriber) -> None:
        subscribers = self._subscribers.get(subscriber.event_id)
        if subscribers is not None and subscriber in subscribers:
            subscribers.discard(subscriber)
            if not subscribers:
                del self._subscribers[subscriber.event_id]
            remaining = self._per_ip.get(subscriber.client_ip, 1) - 1
            if remaining > 0:
                self._per_ip[subscriber.client_ip] = remaining
            else:
                self._per_ip.pop(subscriber.client_ip, None)
            if subscriber.host_slot:
                host_slots = self._host_slots.get(subscriber.event_id, 1) - 1
                if host_slots > 0:
                    self._host_slots[subscriber.event_id] = host_slots
                else:
                    self._host_slots.pop(subscriber.event_id, None)
            self._total -= 1
        writer = subscriber.writer
        if writer is not None and not writer.done() and writer is not asyncio.current_task():
            writer.cancel()
            await asyncio.wait({writer})

    # --- Messaging ------------------------------------------------------------------------

    def publish(self, event_id: str, message: Mapping[str, Any]) -> int:
        """Queue ``message`` for every subscriber of ``event_id``; return the recipient count."""
        subscribers = self._subscribers.get(event_id)
        if not subscribers:
            return 0
        payload = encode_message(message)
        delivered = 0
        for subscriber in tuple(subscribers):
            if self._enqueue(subscriber, payload):
                delivered += 1
        return delivered

    def send(self, subscriber: Subscriber, message: Mapping[str, Any]) -> bool:
        return self._enqueue(subscriber, encode_message(message))

    def request_close(self, subscriber: Subscriber, code: int, reason: str) -> None:
        """Close after already-queued messages have been flushed."""
        if subscriber.closing:
            return
        try:
            subscriber.queue.put_nowait(_CloseRequest(code, reason))
        except asyncio.QueueFull:
            self._close_in_background(subscriber, code, reason)

    def close_event(self, event_id: str, code: int, reason: str) -> None:
        for subscriber in tuple(self._subscribers.get(event_id, ())):
            self.request_close(subscriber, code, reason)

    async def close(self, subscriber: Subscriber, code: int, reason: str = "") -> None:
        """Close immediately (idempotent, never raises)."""
        if subscriber.closing:
            return
        subscriber.closing = True
        websocket = subscriber.websocket
        if websocket.application_state == WebSocketState.CONNECTED:
            try:
                await asyncio.wait_for(websocket.close(code=code, reason=reason), timeout=_CLOSE_TIMEOUT)
            except Exception as exc:  # noqa: BLE001 - the peer may already be gone
                logger.debug("Error while closing websocket: %s", exc)

    async def close_all(self, code: int = CLOSE_GOING_AWAY, reason: str = "Server shutting down") -> None:
        subscribers = [sub for subs in self._subscribers.values() for sub in subs]
        await asyncio.gather(*(self.close(sub, code, reason) for sub in subscribers), return_exceptions=True)
        for task in tuple(self._background):
            task.cancel()

    # --- Internals ------------------------------------------------------------------------

    def _enqueue(self, subscriber: Subscriber, payload: str) -> bool:
        if subscriber.closing:
            return False
        try:
            subscriber.queue.put_nowait(payload)
            return True
        except asyncio.QueueFull:
            logger.info("Disconnecting slow websocket client for event %s", subscriber.event_id)
            self._close_in_background(subscriber, CLOSE_TRY_AGAIN_LATER, "Client is not keeping up")
            return False

    def _close_in_background(self, subscriber: Subscriber, code: int, reason: str) -> None:
        task = asyncio.create_task(self.close(subscriber, code, reason))
        self._background.add(task)
        task.add_done_callback(self._background.discard)

    async def _write_loop(self, subscriber: Subscriber) -> None:
        websocket = subscriber.websocket
        try:
            while True:
                item = await subscriber.queue.get()
                if isinstance(item, _CloseRequest):
                    await self.close(subscriber, item.code, item.reason)
                    return
                await asyncio.wait_for(websocket.send_text(item), timeout=self._send_timeout)
        except asyncio.CancelledError:
            raise
        except TimeoutError:
            await self.close(subscriber, CLOSE_TRY_AGAIN_LATER, "Send timed out")
        except Exception as exc:  # noqa: BLE001 - peer vanished or the socket is already closed
            logger.debug("Websocket writer stopped: %s", exc)
            await self.close(subscriber, CLOSE_INTERNAL_ERROR, "Send failed")


class TokenBucket:
    """Per-connection inbound message budget."""

    __slots__ = ("_capacity", "_rate", "_tokens", "_updated")

    def __init__(self, capacity: int, per_seconds: float) -> None:
        self._capacity = float(capacity)
        self._rate = capacity / per_seconds
        self._tokens = float(capacity)
        self._updated = time.monotonic()

    def consume(self) -> bool:
        now = time.monotonic()
        self._tokens = min(self._capacity, self._tokens + (now - self._updated) * self._rate)
        self._updated = now
        if self._tokens >= 1.0:
            self._tokens -= 1.0
            return True
        return False


class WebSocketGuard:
    """Admission control for WebSocket connections."""

    def __init__(self, settings: Settings) -> None:
        self._enabled = settings.rate_limit_enabled
        # Sliding-window counters expire their keys (moving windows never do).
        self._limiter = SlidingWindowCounterRateLimiter(MemoryStorage())
        self._connect_limit = parse(settings.ws_connect_rate)
        message_limit = parse(settings.ws_message_rate)
        self._message_capacity = message_limit.amount
        self._message_window = float(message_limit.get_expiry())
        origins = settings.allowed_origins
        self._any_origin = "*" in origins
        self._origins = frozenset(origin for origin in origins if origin != "*")
        self._allow_missing_origin = settings.ws_allow_missing_origin
        self.max_message_bytes = settings.ws_max_message_bytes

    def origin_allowed(self, origin: str | None) -> bool:
        # Browsers always send Origin on WebSocket handshakes; non-browser clients
        # can forge it anyway, so a missing Origin is a policy choice.
        if not origin:
            return self._allow_missing_origin
        if self._any_origin:
            return True
        try:
            return normalize_origin(origin) in self._origins
        except ValueError:  # e.g. "null" from sandboxed documents
            return False

    def allow_connection_attempt(self, client_ip: str) -> bool:
        if not self._enabled:
            return True
        return self._limiter.hit(self._connect_limit, "ws-connect", client_ip)

    def allow_host_verification(self, client_ip: str) -> bool:
        """Budget for checking offered host tokens (each check reads the database).
        Separate from the guest attempt budget, so guests cannot exhaust it."""
        if not self._enabled:
            return True
        return self._limiter.hit(self._connect_limit, "ws-host-verify", client_ip)

    def allow_host_connection_attempt(self, event_id: str) -> bool:
        """Verified hosts use their own bucket, so guests sharing the venue IP
        cannot exhaust the host's reconnect budget."""
        if not self._enabled:
            return True
        return self._limiter.hit(self._connect_limit, "ws-connect-host", event_id)

    def message_budget(self) -> TokenBucket | None:
        if not self._enabled:
            return None
        return TokenBucket(self._message_capacity, self._message_window)
